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
