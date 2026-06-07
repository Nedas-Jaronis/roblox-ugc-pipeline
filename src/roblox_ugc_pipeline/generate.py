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

    space_id = os.environ.get("ROBLOX_UGC_CUBE3D_SPACE", "Roblox/cube3d-interactive")
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


# ---- shared image-to-3D helpers ------------------------------------------

def _client(space_id: str):
    """Build a gradio Client, tolerant of gradio_client version differences."""
    Client = _gradio_client()
    token = _hf_token()
    if token:
        os.environ["HF_TOKEN"] = token
        os.environ["HUGGING_FACE_HUB_TOKEN"] = token
    try:
        return Client(space_id, hf_token=token) if token else Client(space_id)
    except TypeError:
        return Client(space_id)


def _handle(path_or_out):
    """Wrap a local path / gradio output as a gradio file input."""
    from gradio_client import handle_file
    p = path_or_out
    if isinstance(p, dict):
        p = p.get("path") or p.get("url")
    return handle_file(str(p))


def _prepare_image(path: Path, run_dir: Path, size: int = 1024,
                   remove_bg: bool = True) -> str:
    """Isolate the subject and center it on a clean square canvas.

    Two fixes that matter for reconstruction quality:
      * background removal (rembg, if installed) — a cluttered/again-colored
        background is the main cause of hallucinated backdrop planes + floaters,
      * square-pad + upscale — low-res / off-center inputs reconstruct poorly.

    Falls back gracefully (no rembg -> white-pad; no Pillow -> raw path)."""
    try:
        from PIL import Image
    except ImportError:
        return str(Path(path).resolve())

    im = Image.open(path).convert("RGBA")
    if remove_bg:
        try:
            from rembg import remove  # type: ignore
            im = remove(im)  # subject on transparent bg
            im = _autocrop_alpha(im) or im
        except Exception:  # noqa: BLE001  (rembg missing or failed -> skip)
            pass

    w, h = im.size
    side = int(max(w, h) * 1.15)  # margin so the subject isn't edge-to-edge
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    canvas.paste(im, ((side - w) // 2, (side - h) // 2), im)
    canvas = canvas.convert("RGB")
    if side < size:
        canvas = canvas.resize((size, size), Image.LANCZOS)
    out = run_dir / "input_prepared.png"
    canvas.save(out)
    return str(out)


def _autocrop_alpha(im):
    """Crop an RGBA image to its non-transparent bounding box (so padding centers
    the actual subject, not the original framing)."""
    bbox = im.getbbox()
    return im.crop(bbox) if bbox else None


def _finish(run_dir: Path, req: GenerationRequest, provider: str,
            result_path: str, extra: dict) -> GenerationResult:
    dst = run_dir / f"model{Path(result_path).suffix or '.glb'}"
    shutil.copy(result_path, dst)
    _write_meta(run_dir, req, provider=provider, asset=dst)
    return GenerationResult(provider=provider, run_dir=run_dir, asset_path=dst,
                            request=req, extra=extra)


# ---- TRELLIS (image-to-3D, PRIMARY — best free quality + textures) --------

def gen_trellis(req: GenerationRequest, project_dir: Path) -> GenerationResult:
    if req.modality not in ("image", "multi") or not req.image_paths:
        raise ValueError("TRELLIS is image-to-3D. Use cube3d for text.")
    run_dir = _new_run_dir(project_dir, "trellis")
    space_id = os.environ.get("ROBLOX_UGC_TRELLIS_SPACE", "trellis-community/TRELLIS")
    client = _client(space_id)

    img = _prepare_image(req.image_paths[0], run_dir)
    try:
        client.predict(api_name="/start_session")
    except Exception:  # noqa: BLE001  (stateless deployments don't need it)
        pass
    processed = client.predict(_handle(img), api_name="/preprocess_image")
    out = client.predict(
        _handle(processed),  # image prompt
        [],                  # multiimages
        0,                   # seed
        7.5,                 # ss_guidance_strength
        25,                  # ss_sampling_steps  (raised from 12 for detail)
        3.0,                 # slat_guidance_strength
        25,                  # slat_sampling_steps
        "stochastic",        # multiimage_algo
        0.92,                # mesh_simplify  (less aggressive than 0.95 default)
        2048,                # texture_size   (raised from 1024)
        api_name="/generate_and_extract_glb",
    )
    result_path = _coerce_path(out, prefer_ext=(".glb",))
    if not result_path:
        raise RuntimeError(f"TRELLIS Space '{space_id}' returned no GLB. out={out!r}")
    return _finish(run_dir, req, "trellis", result_path,
                   {"space_id": space_id, "input_image": img})


# ---- Hunyuan3D-2 (image-to-3D, free HF Space, MULTI-VIEW capable) ---------

def gen_hunyuan3d_space(req: GenerationRequest, project_dir: Path) -> GenerationResult:
    """tencent/Hunyuan3D-2 free Space. Pass up to 4 images as
    [front, back, left, right] for a full 360° reconstruction (the biggest
    single-image quality fix), or one image for the single-view path."""
    if req.modality not in ("image", "multi") or not req.image_paths:
        raise ValueError("Hunyuan3D Space is image-to-3D. Use cube3d for text.")
    run_dir = _new_run_dir(project_dir, "hunyuan3d")
    space_id = os.environ.get("ROBLOX_UGC_HUNYUAN_SPACE", "tencent/Hunyuan3D-2")
    client = _client(space_id)

    prepared = [_prepare_image(p, run_dir, size=1024) for p in req.image_paths]
    if len(prepared) >= 2:  # multi-view: front, back, left, right
        slots = [None, None, None, None]
        for i in range(min(len(prepared), 4)):
            slots[i] = _handle(prepared[i])
        image_args = [None, *slots]               # image=None, then 4 mv slots
    else:
        image_args = [_handle(prepared[0]), None, None, None, None]

    out = client.predict(
        None,            # caption
        *image_args,     # image, mv_front, mv_back, mv_left, mv_right
        30,              # steps
        5.0,             # guidance_scale
        1234,            # seed
        320,             # octree_resolution (raised from 256 for detail)
        True,            # check_box_rembg
        8000,            # num_chunks
        False,           # randomize_seed
        api_name="/generation_all",
    )
    result_path = _coerce_path(out, prefer_ext=(".glb", ".obj"))
    if not result_path:
        raise RuntimeError(f"Hunyuan3D Space '{space_id}' returned no mesh. out={out!r}")
    return _finish(run_dir, req, "hunyuan3d", result_path,
                   {"space_id": space_id, "input_images": prepared})


# ---- InstantMesh (image-to-3D, lighter fallback) -------------------------

def gen_instantmesh(req: GenerationRequest, project_dir: Path) -> GenerationResult:
    if req.modality not in ("image", "multi") or not req.image_paths:
        raise ValueError("InstantMesh needs at least one image.")
    run_dir = _new_run_dir(project_dir, "instantmesh")
    space_id = os.environ.get("ROBLOX_UGC_INSTANTMESH_SPACE", "TencentARC/InstantMesh")
    client = _client(space_id)

    img = _prepare_image(req.image_paths[0], run_dir)
    # Real InstantMesh pipeline: check -> preprocess -> generate_mvs -> make3d.
    try:
        client.predict(_handle(img), api_name="/check_input_image")
    except Exception:  # noqa: BLE001
        pass
    processed = client.predict(_handle(img), True, api_name="/preprocess")
    client.predict(_handle(processed), 75, 42, api_name="/generate_mvs")
    out = client.predict(api_name="/make3d")
    result_path = _coerce_path(out, prefer_ext=(".glb", ".obj"))
    if not result_path:
        raise RuntimeError(f"InstantMesh Space '{space_id}' returned no mesh. out={out!r}")
    return _finish(run_dir, req, "instantmesh", result_path,
                   {"space_id": space_id, "input_image": img})


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
    "trellis": gen_trellis,
    "hunyuan3d-space": gen_hunyuan3d_space,
    "instantmesh": gen_instantmesh,
}


def generate(req: GenerationRequest, provider: str, project_dir: Path) -> GenerationResult:
    if provider not in _DRIVERS:
        raise ValueError(
            f"No HTTP driver for provider '{provider}'. Available: {list(_DRIVERS)}. "
            "Other providers (sketchfab, hyper3d, hunyuan3d) run through "
            "Blender MCP — use the assistant + `roblox-ugc plan` for their workflow."
        )
    return _DRIVERS[provider](req, project_dir)
