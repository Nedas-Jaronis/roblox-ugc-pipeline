from __future__ import annotations

from .base import GenerationRequest, Provider


class InstantMeshProvider(Provider):
    """Image-to-3D via TencentARC/InstantMesh on a free HF Space.

    Pairs naturally with Cube3D: cube3d handles text-to-3D, InstantMesh covers
    image-to-3D. Output is GLB with vertex colors (no texture maps). ZeroGPU
    quotas apply; see https://huggingface.co/docs/hub/en/spaces-zerogpu.
    """

    name = "instantmesh"
    free_tier = True
    notes = (
        "Image-to-3D via HF Space `TencentARC/InstantMesh`. Free on ZeroGPU "
        "(~300s/day for logged-in free users). Output: GLB with vertex colors. "
        "Roblox-friendly axis fix may be required (vertices[:, [1, 2, 0]])."
    )

    HF_SPACE_ID = "TencentARC/InstantMesh"

    def live_workflow(self, req: GenerationRequest) -> list[str]:
        if req.modality not in ("image", "multi"):
            raise ValueError("InstantMesh is image-to-3D. Use cube3d for text.")
        if not req.image_paths:
            raise ValueError("Need at least one input image.")
        img = req.image_paths[0]
        return [
            f"1. Optional: remove background via rembg or "
            "`mcp__blender__execute_blender_code` running a Pillow snippet.",
            f"2. Call HF Space `{self.HF_SPACE_ID}` via gradio_client, predict "
            f"endpoint `/generate_mvs` then `/make3d`, with image={img}.",
            "3. Download the returned `.glb` into the run directory.",
            "4. Import into Blender: "
            "`bpy.ops.import_scene.gltf(filepath='...')` via "
            "`mcp__blender__execute_blender_code`.",
            "5. Apply Roblox axis fix if needed (rotate -90 X, or swap axes).",
            "6. `roblox-ugc prep` to decimate to category cap + center + rescale.",
            "7. Add Attachments per category, export FBX, validate.",
        ]

    def cost_hint(self) -> str:
        return "free (HF ZeroGPU, rate-limited)"


class TripoSRProvider(Provider):
    name = "triposr"
    free_tier = True
    notes = (
        "Lightweight image-to-3D (Stability + Tripo). Faster than InstantMesh "
        "on the same hardware (~1s on 6-8GB VRAM). Lower mesh quality though. "
        "Pair with `hansyan/perflow-triposr` Space for text-to-3D in one shot."
    )

    HF_SPACE_ID = "stabilityai/TripoSR"
    TEXT_TO_3D_SPACE_ID = "hansyan/perflow-triposr"

    def live_workflow(self, req: GenerationRequest) -> list[str]:
        if req.modality == "text":
            return [
                f"1. Call HF Space `{self.TEXT_TO_3D_SPACE_ID}` via gradio_client "
                f"with prompt={req.prompt!r}.",
                "2. Download returned mesh, import into Blender, run prep.",
            ]
        if not req.image_paths:
            raise ValueError("Image modality requires at least one image.")
        return [
            f"1. Call HF Space `{self.HF_SPACE_ID}` via gradio_client with "
            f"image={req.image_paths[0]}.",
            "2. Download mesh, import, prep.",
        ]

    def cost_hint(self) -> str:
        return "free (HF ZeroGPU)"
