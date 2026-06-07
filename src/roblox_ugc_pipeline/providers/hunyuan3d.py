from __future__ import annotations

from .base import GenerationRequest, Provider


class Hunyuan3DSpaceProvider(Provider):
    """Hunyuan3D-2 on the FREE `tencent/Hunyuan3D-2` HF Space (image-to-3D).

    Distinct from the paid BlenderMCP/Tencent-Cloud route below. The free Space
    is the best multi-view option: pass [front, back, left, right] images and it
    reconstructs a full 360° mesh — the biggest fix for the shallow-back problem
    of single-image generation. Consistent geometry; controllable octree
    resolution. Use via `roblox-ugc gen --provider hunyuan3d-space`.
    """

    name = "hunyuan3d-space"
    free_tier = True
    notes = (
        "Image-to-3D via free HF Space `tencent/Hunyuan3D-2`. Single image OR "
        "multi-view (front/back/left/right) for full 360° geometry. Output: "
        "textured GLB. Free on ZeroGPU; set HF_TOKEN for quota."
    )

    HF_SPACE_ID = "tencent/Hunyuan3D-2"

    def live_workflow(self, req: GenerationRequest) -> list[str]:
        if req.modality not in ("image", "multi"):
            raise ValueError("Hunyuan3D Space is image-to-3D. Use cube3d for text.")
        if not req.image_paths:
            raise ValueError("Need at least one input image.")
        imgs = ", ".join(str(p) for p in req.image_paths)
        return [
            f"1. Run `roblox-ugc gen --provider hunyuan3d-space --image {req.image_paths[0]}`.",
            "   For 360°, pass up to 4 images in order: front, back, left, right "
            f"(given: [{imgs}]).",
            "2. Driver calls /generation_all (rembg + shape + texture) -> GLB.",
            "3. `roblox-ugc clean` if needed, then autoprep/autorig + validate.",
        ]

    def cost_hint(self) -> str:
        return "free (HF ZeroGPU, rate-limited)"


class Hunyuan3DProvider(Provider):
    name = "hunyuan3d"
    free_tier = False
    notes = (
        "Tencent Hunyuan3D via Blender MCP (image-to-3D). PAID — requires Tencent Cloud "
        "credits. Skip unless the user has explicitly funded the account."
    )

    def live_workflow(self, req: GenerationRequest) -> list[str]:
        steps: list[str] = [
            "1. Call `mcp__blender__get_hunyuan3d_status` to confirm the integration.",
        ]
        if req.modality == "text":
            raise ValueError(
                "Hunyuan3D in BlenderMCP only supports image-to-3D. Use Hyper3D for text."
            )
        paths = ", ".join(str(p) for p in req.image_paths)
        steps += [
            f"2. Call `mcp__blender__generate_hunyuan3d_model` with images: [{paths}].",
            "3. Poll `mcp__blender__poll_hunyuan_job_status` until done.",
            "4. Call `mcp__blender__import_generated_asset_hunyuan` to import the mesh.",
            "5. Save .blend, export FBX, append manifest row.",
        ]
        return steps

    def cost_hint(self) -> str:
        return "free tier"
