from __future__ import annotations

from .base import GenerationRequest, Provider


class SF3DProvider(Provider):
    """Stable Fast 3D (Stability AI) — direct mesh reconstruction, the cleanest
    free image-to-3D for game-ready assets.

    Unlike TRELLIS (gaussian-splatting -> meshed, which leaves attached backdrop
    planes + wisps that need post-cleanup), SF3D is trained to emit a single,
    coherent, UV-unwrapped, textured mesh directly. It exposes the controls that
    matter for Roblox marketplace prep:
      * remesh_option Triangle/Quad -> clean even topology (not marching-cubes soup),
      * vertex_count cap -> born near the tri budget (rigid accessory cap 4,000),
      * texture_size up to 2048 -> matches the Roblox texture cap.

    Free HF Space `stabilityai/stable-fast-3d` (ZeroGPU, ~300s/day shared quota),
    or self-host on any >=6GB CUDA GPU (Colab T4 works) for unlimited runs and
    full-quality settings.
    """

    name = "sf3d"
    free_tier = True
    notes = (
        "Image-to-3D via `stabilityai/stable-fast-3d`. Direct mesh model "
        "(clean single mesh, no splat artifacts). Triangle/Quad remesh + "
        "vertex cap + 2048 texture. ZeroGPU rate-limited; self-host on a 6GB+ "
        "GPU (Colab) for unlimited full-quality runs."
    )

    HF_SPACE_ID = "stabilityai/stable-fast-3d"

    def live_workflow(self, req: GenerationRequest) -> list[str]:
        if req.modality not in ("image", "multi"):
            raise ValueError("SF3D is image-to-3D. Use cube3d for text.")
        if not req.image_paths:
            raise ValueError("Need at least one input image.")
        img = req.image_paths[0]
        return [
            f"1. `roblox-ugc gen --provider sf3d --image {img}` (auto bg-removal + "
            "square-pad; calls /run_button with Triangle remesh + 2048 texture).",
            "2. Output is a single clean UV-textured GLB — usually no cleanup needed.",
            "3. Import to Blender, `roblox-ugc prep` to the category tri budget, "
            "apply axis fix, then inspect/validate.",
        ]

    def cost_hint(self) -> str:
        return "free (HF ZeroGPU, or self-host on 6GB+ GPU)"
