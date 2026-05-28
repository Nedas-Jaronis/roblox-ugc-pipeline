from __future__ import annotations

from .base import GenerationRequest, Provider


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
