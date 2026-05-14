from __future__ import annotations

from .base import GenerationRequest, Provider


class Hyper3DProvider(Provider):
    name = "hyper3d"
    free_tier = False
    notes = (
        "Hyper3D Rodin via Blender MCP. Text-to-3D and image-to-3D. "
        "PAID — credits required (small free trial exists but exhausts quickly). "
        "Skip unless the user has explicitly funded the account."
    )

    def live_workflow(self, req: GenerationRequest) -> list[str]:
        steps: list[str] = [
            "1. Call `mcp__blender__get_hyper3d_status` to confirm the integration is ready.",
        ]
        if req.modality == "text":
            steps.append(
                f"2. Call `mcp__blender__generate_hyper3d_model_via_text` with prompt: "
                f"{req.prompt!r}"
            )
        elif req.modality in ("image", "multi"):
            paths = ", ".join(str(p) for p in req.image_paths)
            steps.append(
                f"2. Call `mcp__blender__generate_hyper3d_model_via_images` with images: "
                f"[{paths}] and prompt: {req.prompt!r}"
            )
        else:
            raise ValueError(f"Unsupported modality: {req.modality}")
        steps += [
            "3. Poll `mcp__blender__poll_rodin_job_status` until the job is done.",
            "4. Call `mcp__blender__import_generated_asset` to bring the mesh into the scene.",
            "5. Save the .blend, then export `model.fbx` into the run directory.",
            "6. Append a manifest row via `robloxchars manifest add ...`.",
        ]
        return steps

    def cost_hint(self) -> str:
        return "free trial -> paid credits"
