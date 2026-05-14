"""Generation drivers — HTTP calls to free HF Spaces.

Each driver:
  * Takes a `GenerationRequest` + a run directory.
  * Returns a `GenerationResult` with the asset path on disk.

The actual gradio endpoints / parameter names change as the Spaces are
updated; if a call fails, fetch the Space's `app.py` and adjust. Endpoints
recorded here as of 2026-05.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .providers.base import GenerationRequest, GenerationResult
from .roblox_spec import ACCESSORY_CATEGORIES


def _gradio_client():
    """Lazy import so the dep is optional."""
    try:
        from gradio_client import Client  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "gradio_client not installed. Run: pip install -e .[gen]"
        ) from e
    return Client


def _hf_token() -> str | None:
    """Shared resolver — env, project .hf_token file, or huggingface_hub cache."""
    from .texture_gen import _hf_token as resolver
    return resolver()


def _bbox_for(req: GenerationRequest) -> tuple[float, float, float]:
    if req.target == "accessory" and req.accessory_category:
        spec = ACCESSORY_CATEGORIES.get(req.accessory_category)
        if spec:
            return spec.max_bounds
    if req.target == "avatar":
        return (8.0, 9.1, 2.0)
    return (4.0, 4.0, 4.0)


def _new_run_dir(project_dir: Path, provider_name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = project_dir / "runs" / f"{stamp}-{provider_name}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


# ---- cube3d (text-to-3D, Roblox official) ---------------------------------

def _normalize_bbox_for_cube3d(bbox_studs: tuple[float, float, float]) -> tuple[float, float, float]:
    """Cube3D's HF Space takes bbox params in the 0.1-2.0 range (not raw studs).

    Strategy: preserve aspect ratio, set the largest axis to 1.0 (the default).
    """
    biggest = max(bbox_studs)
    if biggest <= 0:
        return (1.0, 1.0, 1.0)
    raw = tuple(v / biggest for v in bbox_studs)
    return tuple(max(0.1, min(2.0, v)) for v in raw)  # type: ignore[return-value]


def gen_cube3d(req: GenerationRequest, project_dir: Path) -> GenerationResult:
    if req.modality != "text":
        raise ValueError("cube3d is text-to-3D only.")
    Client = _gradio_client()
    bbox_studs = _bbox_for(req)
    bx, by, bz = _normalize_bbox_for_cube3d(bbox_studs)
    run_dir = _new_run_dir(project_dir, "cube3d")

    space_id = os.environ.get("ROBLOXCHARS_CUBE3D_SPACE", "Roblox/cube3d-interactive")
    hf_token = _hf_token()
    if hf_token:
        os.environ["HF_TOKEN"] = hf_token
        os.environ["HUGGING_FACE_HUB_TOKEN"] = hf_token
    try:
        client = Client(space_id, hf_token=hf_token) if hf_token else Client(space_id)
    except TypeError:
        client = Client(space_id)

    # Endpoint shape pinned to the Roblox/cube3d-interactive app.py as of 2026-05.
    # Signature: handle_text_prompt(input_prompt, use_bbox, bbox_x, bbox_y, bbox_z, hi_res)
    last_err: Exception | None = None
    result_path: str | None = None
    for api_name in ("/handle_text_prompt", "/call/handle_text_prompt", "/predict"):
        try:
            out = client.predict(
                req.prompt,    # input_prompt
                True,          # use_bbox
                float(bx),     # bbox_x
                float(by),     # bbox_y
                float(bz),     # bbox_z
                False,         # hi_res
                api_name=api_name,
            )
            result_path = _coerce_path(out, prefer_ext=(".glb", ".obj"))
            if result_path:
                break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if not result_path:
        raise RuntimeError(
            f"Cube3D Space '{space_id}' did not return a usable mesh path. "
            f"Last error: {last_err}. Inspect the Space's app.py for the "
            "current endpoint shape and update generate.py."
        )

    dst = run_dir / f"model{Path(result_path).suffix or '.glb'}"
    shutil.copy(result_path, dst)
    _write_meta(run_dir, req, provider="cube3d", asset=dst)
    return GenerationResult(
        provider="cube3d",
        run_dir=run_dir,
        asset_path=dst,
        request=req,
        extra={"bbox_studs": list(bbox_studs), "bbox_normalized": [bx, by, bz], "space_id": space_id},
    )


# ---- InstantMesh (image-to-3D) -------------------------------------------

def gen_instantmesh(req: GenerationRequest, project_dir: Path) -> GenerationResult:
    if req.modality not in ("image", "multi") or not req.image_paths:
        raise ValueError("InstantMesh needs at least one image.")
    Client = _gradio_client()
    run_dir = _new_run_dir(project_dir, "instantmesh")
    space_id = os.environ.get("ROBLOXCHARS_INSTANTMESH_SPACE", "TencentARC/InstantMesh")
    hf_token = os.environ.get("HF_TOKEN")
    client = Client(space_id, hf_token=hf_token) if hf_token else Client(space_id)

    img_path = str(req.image_paths[0].resolve())
    # InstantMesh Space pipeline: preprocess -> generate_mvs -> make3d.
    # We try a single end-to-end endpoint first, then fall back to chained calls.
    candidates: list[tuple[str, dict]] = [
        ("/predict", dict(input_image=img_path)),
    ]
    last_err: Exception | None = None
    result_path: str | None = None
    for api_name, kwargs in candidates:
        try:
            out = client.predict(api_name=api_name, **kwargs)
            result_path = _coerce_path(out, prefer_ext=(".glb", ".obj"))
            if result_path:
                break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if not result_path:
        raise RuntimeError(
            f"InstantMesh Space '{space_id}' did not return a usable mesh. "
            f"Last error: {last_err}."
        )

    dst = run_dir / f"model{Path(result_path).suffix or '.glb'}"
    shutil.copy(result_path, dst)
    _write_meta(run_dir, req, provider="instantmesh", asset=dst)
    return GenerationResult(
        provider="instantmesh", run_dir=run_dir, asset_path=dst, request=req,
        extra={"space_id": space_id, "input_image": img_path},
    )


# ---- helpers ---------------------------------------------------------------

def _coerce_path(out, prefer_ext: tuple[str, ...] = (".obj", ".glb")) -> str | None:
    """gradio_client returns may be a string, dict, list, or tuple — normalize."""
    if isinstance(out, str) and Path(out).exists():
        return out
    if isinstance(out, dict):
        for key in ("path", "name", "file", "tmp_path"):
            v = out.get(key)
            if isinstance(v, str) and Path(v).exists():
                return v
    if isinstance(out, (list, tuple)):
        for item in out:
            p = _coerce_path(item, prefer_ext)
            if p and Path(p).suffix.lower() in prefer_ext:
                return p
        for item in out:
            p = _coerce_path(item, prefer_ext)
            if p:
                return p
    return None


def _write_meta(run_dir: Path, req: GenerationRequest, provider: str, asset: Path) -> None:
    import json
    meta = {
        "provider": provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": req.prompt,
        "modality": req.modality,
        "target": req.target,
        "accessory_category": req.accessory_category,
        "image_inputs": [str(p) for p in req.image_paths],
        "asset_path": str(asset),
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))


# ---- dispatcher ------------------------------------------------------------

_DRIVERS = {
    "cube3d": gen_cube3d,
    "instantmesh": gen_instantmesh,
}


def generate(req: GenerationRequest, provider: str, project_dir: Path) -> GenerationResult:
    if provider not in _DRIVERS:
        raise ValueError(
            f"No HTTP driver for provider '{provider}'. Available: {list(_DRIVERS)}. "
            "Other providers (sketchfab, hyper3d, hunyuan3d) run through "
            "Blender MCP — use the assistant + `robloxchars plan` for their workflow."
        )
    return _DRIVERS[provider](req, project_dir)
