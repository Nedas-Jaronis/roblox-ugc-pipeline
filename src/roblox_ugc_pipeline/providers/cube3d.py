from __future__ import annotations

from .base import GenerationRequest, Provider
from ..roblox_spec import ACCESSORY_CATEGORIES


class Cube3DProvider(Provider):
    """Roblox's official text-to-3D foundation model (`Roblox/cube3d-v0.5`).

    Two execution paths:
      * **hf_space**: gradio_client call to the Roblox HF Space (no GPU
        required, slower, rate-limited by ZeroGPU quotas). Default for users
        without a CUDA GPU.
      * **local**: `pip install cube3d` + download weights, run
        `cube3d.inference.engine.EngineFast.t2s(...)`. Needs >=16 GB VRAM
        (24 GB recommended with `--fast-inference`).

    Output: untextured, unrigged `.obj` (vertices + faces only). The pipeline
    must add the rig, texture, and attachments afterward.

    Key pattern: pass `bounding-box-xyz` derived from the accessory category
    so the generated mesh already fits Roblox's hard size cap before we even
    import it into Blender. e.g. Hat -> (3.0, 4.0, 3.0).
    """

    name = "cube3d"
    free_tier = True
    notes = (
        "Roblox's own foundation model. Apache-style license, weights on HF "
        "(Roblox/cube3d-v0.5). Free via the HF Space, or self-host with a >=16GB "
        "VRAM GPU. TEXT INPUT ONLY (no image conditioning yet); for image-to-3D "
        "fall through to InstantMesh / TripoSR HF Space."
    )

    HF_SPACE_ID = "Roblox/cube3d-v0.5"

    def live_workflow(self, req: GenerationRequest) -> list[str]:
        if req.modality != "text":
            raise ValueError(
                "Cube3D supports text-to-3D only. For image input, use the "
                "instantmesh or triposr provider."
            )
        bbox_str = self._bbox_for_request(req)
        steps = [
            f"1. Call the HF Space `{self.HF_SPACE_ID}` via gradio_client with "
            f"prompt={req.prompt!r} and bounding box {bbox_str}.",
            "   (Or locally: `python -m cube3d.generate --gpt-ckpt-path ... "
            "--shape-ckpt-path ... --fast-inference "
            f"--prompt {req.prompt!r} --bounding-box-xyz {bbox_str}`)",
            "2. Save the returned `.obj` into the run directory as `model.obj`.",
            "3. Import the OBJ into Blender via "
            "`mcp__blender__execute_blender_code` running "
            "`bpy.ops.wm.obj_import(filepath=...)`.",
            f"4. The mesh should already fit the {req.target} bounding box, "
            "but call `roblox-ugc inspect` to confirm.",
            "5. Add required Attachments (per accessory category) as empties "
            "in Blender, using the spec positions.",
            "6. Bake a 2048^2 BaseColor texture (cube3d v0.5 does NOT emit textures).",
            "7. Export FBX, add manifest row, then `roblox-ugc validate`.",
        ]
        return steps

    def _bbox_for_request(self, req: GenerationRequest) -> str:
        if req.target == "accessory" and req.accessory_category:
            spec = ACCESSORY_CATEGORIES.get(req.accessory_category)
            if spec:
                x, y, z = spec.max_bounds
                return f"{x} {y} {z}"
        if req.target == "avatar":
            # Roblox classic body type max XY.
            return "8.0 9.1 2.0"
        # Generic prop default.
        return "4.0 4.0 4.0"

    def cost_hint(self) -> str:
        return "free (HF Space) / free local with GPU"
