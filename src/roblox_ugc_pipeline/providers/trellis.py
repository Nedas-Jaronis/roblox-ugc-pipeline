from __future__ import annotations

from .base import GenerationRequest, Provider


class TrellisProvider(Provider):
    """Image-to-3D via the free TRELLIS HF Space — best free quality + textures.

    Microsoft TRELLIS uses a structured-latent (SLAT) representation and outputs
    a textured GLB. Strongest single-image option for stylized characters; pairs
    with cube3d (text) for full coverage. Single-view reconstructions are shallow
    on the unseen back — run `roblox-ugc clean` to strip the plane/needle
    artifacts, or feed multiple views via the Hunyuan3D-2 provider.
    """

    name = "trellis"
    free_tier = True
    notes = (
        "Image-to-3D via HF Space `trellis-community/TRELLIS`. Free on ZeroGPU "
        "(~300s/day for logged-in users; set HF_TOKEN). Output: textured GLB. "
        "Single-view back is shallow — post-process with `roblox-ugc clean`."
    )

    HF_SPACE_ID = "trellis-community/TRELLIS"

    def live_workflow(self, req: GenerationRequest) -> list[str]:
        if req.modality not in ("image", "multi"):
            raise ValueError("TRELLIS is image-to-3D. Use cube3d for text.")
        if not req.image_paths:
            raise ValueError("Need at least one input image.")
        img = req.image_paths[0]
        return [
            f"1. Run `roblox-ugc gen --provider trellis --image {img}` (square-pads "
            "and upscales the input automatically).",
            "2. The driver calls /start_session -> /preprocess_image -> "
            "/generate_and_extract_glb and saves the textured GLB to the run dir.",
            "3. Run `roblox-ugc clean <run>/model.glb --out clean.glb` to strip "
            "the front/back plane + needle artifacts.",
            "4. Import to Blender, `roblox-ugc autoprep`/`autorig`, validate.",
        ]

    def cost_hint(self) -> str:
        return "free (HF ZeroGPU, rate-limited)"
