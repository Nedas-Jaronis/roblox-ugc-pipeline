"""Text-to-image driver for the texture-gen pipeline.

Calls a free HuggingFace Space (FLUX.1-schnell by default) and returns the
generated PNG. The actual UV projection / bake happens in
`robloxchars.blender.project_paint` (inside Blender).

Phase 1: single front-view image, accept some back-of-mesh stretching.
Phase 2 (later): multi-view + blend.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


def _gradio_client():
    try:
        from gradio_client import Client  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "gradio_client not installed. Run: pip install -e .[gen]"
        ) from e
    return Client


def _hf_token() -> str | None:
    """Resolve an HF token from env, project file, or huggingface_hub cache."""
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        tok = os.environ.get(var)
        if tok:
            return tok.strip()
    for candidate in (
        Path.cwd() / ".hf_token",
        Path(__file__).resolve().parent.parent.parent / ".hf_token",
        Path.home() / ".hf_token",
        Path.home() / ".cache" / "huggingface" / "token",
        Path.home() / ".huggingface" / "token",
    ):
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
    # huggingface_hub's own resolver — handles its cache location if it differs.
    try:
        from huggingface_hub import get_token  # type: ignore
        tok = get_token()
        if tok:
            return tok
    except Exception:
        try:
            from huggingface_hub import HfFolder  # type: ignore
            tok = HfFolder.get_token()
            if tok:
                return tok
        except Exception:
            pass
    return None


def prompt_for_accessory(user_prompt: str, category: str | None = None) -> str:
    """Wrap the user's accessory prompt so SD produces a clean texture-friendly image."""
    bits = [user_prompt]
    if category:
        bits.append(f"Roblox-style {category.lower()} accessory")
    bits += [
        "front view",
        "centered",
        "white background",
        "flat lighting",
        "no shadows",
        "high detail",
    ]
    return ", ".join(bits)


# Multi-view prompt suffixes for full-body avatar texturing. Each entry maps
# a view name to a phrase added to the user's base prompt.
_VIEW_SUFFIXES: dict[str, str] = {
    "front": "front view, full body, T-pose, centered, white background, flat lighting",
    "back":  "back view from behind, full body, T-pose, centered, white background, flat lighting",
    "left":  "left side view, profile shot, full body, T-pose, centered, white background, flat lighting",
    "right": "right side view, profile shot, full body, T-pose, centered, white background, flat lighting",
}


def crop_to_content(
    in_png: Path,
    out_png: Path | None = None,
    bg_threshold: int = 240,
    pad_ratio: float = 0.02,
    target_aspect: float | None = None,
) -> Path:
    """Crop an SD image to its non-white character content, then optionally
    pad to a target aspect ratio.

    Used to remove the breathing-room margins FLUX leaves around generated
    characters. Critical for camera projection: world-space Z coordinates
    map to image V coordinates linearly, so any white margin produces a
    systematic offset that pulls face content onto torso UVs.
    """
    from PIL import Image  # host-side only
    import numpy as np

    out_png = out_png or in_png
    with Image.open(in_png) as im:
        im = im.convert("RGBA")
        arr = np.asarray(im)
    h, w, _ = arr.shape
    # "Content" = pixels with any channel below `bg_threshold` (i.e. not pure white).
    mask = np.any(arr[:, :, :3] < bg_threshold, axis=-1)
    if not mask.any():
        return in_png  # nothing to crop
    rows = np.where(mask.any(axis=1))[0]
    cols = np.where(mask.any(axis=0))[0]
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(cols[0]), int(cols[-1]) + 1

    pad_v = int(pad_ratio * (bottom - top))
    pad_h = int(pad_ratio * (right - left))
    top    = max(0, top - pad_v)
    bottom = min(h, bottom + pad_v)
    left   = max(0, left - pad_h)
    right  = min(w, right + pad_h)

    cropped = arr[top:bottom, left:right]
    out = Image.fromarray(cropped, mode="RGBA")

    if target_aspect is not None and out.height > 0:
        cur = out.width / out.height
        if cur < target_aspect:
            # Pad sides to widen.
            target_w = int(out.height * target_aspect)
            pad = (target_w - out.width) // 2
            new = Image.new("RGBA", (target_w, out.height), (255, 255, 255, 255))
            new.paste(out, (pad, 0))
            out = new
        elif cur > target_aspect:
            # Pad top/bottom to make taller.
            target_h = int(out.width / target_aspect)
            pad = (target_h - out.height) // 2
            new = Image.new("RGBA", (out.width, target_h), (255, 255, 255, 255))
            new.paste(out, (0, pad))
            out = new

    out.save(out_png)
    return out_png


def generate_multi_view(
    base_prompt: str,
    out_dir: Path,
    views: tuple[str, ...] = ("front", "back", "left", "right"),
    width: int = 768,
    height: int = 1024,
    num_inference_steps: int = 4,
) -> dict[str, Path]:
    """Generate one SD image per view for a humanoid avatar.

    Returns a dict mapping view name -> PNG path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for view in views:
        suffix = _VIEW_SUFFIXES.get(view)
        if suffix is None:
            raise ValueError(f"Unknown view {view!r}; must be one of {list(_VIEW_SUFFIXES)}")
        prompt = f"{base_prompt}, {suffix}"
        out_png = out_dir / f"sd_{view}.png"
        generate_via_flux(prompt, out_png, width=width, height=height,
                          num_inference_steps=num_inference_steps,
                          # Re-seed each view so they're consistent style but
                          # distinct angles.
                          seed=hash(view) & 0x7FFFFFFF, randomize_seed=False)
        # Auto-crop to character content. Without this, FLUX's natural
        # whitespace margin around the character produces a systematic
        # Z-misalignment in the camera projection (face content bleeds
        # onto torso UVs).
        crop_to_content(out_png)
        paths[view] = out_png
    return paths


# Ordered Space candidates: (space_id, infer_args_builder).
# Each builder takes (prompt, seed, randomize_seed, width, height, steps) and
# returns the positional args list for that Space's primary endpoint.
def _flux_args(p, s, rs, w, h, st):
    return [p, int(s), bool(rs), int(w), int(h), int(st)]

def _sdxl_turbo_args(p, s, rs, w, h, st):
    # SDXL-Turbo Spaces typically: (prompt, negative_prompt, seed, randomize_seed, width, height, steps)
    return [p, "", int(s), bool(rs), int(w), int(h), int(st)]

DEFAULT_SPACE_CHAIN: list[tuple[str, callable]] = [  # type: ignore[type-arg]
    ("black-forest-labs/FLUX.1-schnell", _flux_args),
    ("stabilityai/stable-diffusion-3.5-large-turbo", _flux_args),
    ("stabilityai/sdxl-turbo", _sdxl_turbo_args),
]


DEFAULT_INFERENCE_MODELS: tuple[str, ...] = (
    "black-forest-labs/FLUX.1-schnell",
    "stabilityai/stable-diffusion-3.5-large-turbo",
    "stabilityai/sdxl-turbo",
    "stabilityai/stable-diffusion-xl-base-1.0",
)


def generate_via_flux(
    prompt: str,
    out_png: Path,
    width: int = 1024,
    height: int = 1024,
    num_inference_steps: int = 4,
    seed: int = 0,
    randomize_seed: bool = True,
) -> Path:
    """Generate one image via HF Inference Providers (Spaces are flaky; this is the API).

    Tries the configured model chain in order; the first that succeeds wins.
    Override the primary model via the ROBLOXCHARS_FLUX_MODEL env var.
    Falls back to the gradio_client / Spaces path if the Inference API itself
    is unavailable (older huggingface_hub).
    """
    hf_token = _hf_token()
    if not hf_token:
        raise RuntimeError(
            "No HuggingFace token found. Put one in ./.hf_token or set HF_TOKEN."
        )

    override = os.environ.get("ROBLOXCHARS_FLUX_MODEL")
    chain: list[str] = [override] if override else []
    chain += [m for m in DEFAULT_INFERENCE_MODELS if m not in chain]

    try:
        from huggingface_hub import InferenceClient  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "huggingface_hub not installed. Run: pip install -e .[gen]"
        ) from e

    errors: list[str] = []
    client = InferenceClient(token=hf_token, timeout=120)
    for model_id in chain:
        try:
            image = client.text_to_image(
                prompt,
                model=model_id,
                width=width,
                height=height,
                num_inference_steps=num_inference_steps,
            )
        except Exception as e:  # noqa: BLE001
            errors.append(f"{model_id}: {type(e).__name__}: {str(e)[:200]}")
            continue
        out_png.parent.mkdir(parents=True, exist_ok=True)
        image.save(out_png)
        return out_png
    raise RuntimeError(
        "All Inference models failed. Errors:\n  " + "\n  ".join(errors)
        + "\n\nNote: model availability changes; override via ROBLOXCHARS_FLUX_MODEL."
    )


def _coerce_image_path(out) -> str | None:
    if isinstance(out, str) and Path(out).exists():
        return out
    if isinstance(out, dict):
        for key in ("path", "name", "file", "tmp_path"):
            v = out.get(key)
            if isinstance(v, str) and Path(v).exists():
                return v
    if isinstance(out, (list, tuple)):
        for item in out:
            p = _coerce_image_path(item)
            if p:
                return p
    return None
