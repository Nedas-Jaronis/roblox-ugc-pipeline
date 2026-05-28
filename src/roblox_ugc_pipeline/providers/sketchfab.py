from __future__ import annotations

from .base import GenerationRequest, Provider


class SketchfabProvider(Provider):
    """Asset-library 'remix' workflow — pull a CC-licensed model and modify it.

    Not generation per se; this is the actually-free path for getting a starting
    mesh you can then validate + prep for Roblox. The user is logged in via the
    BlenderMCP addon (Sketchfab requires login for downloads).
    """

    name = "sketchfab"
    free_tier = True
    notes = (
        "Download a free / CC-licensed model from Sketchfab via Blender MCP. "
        "Remix-in-Blender workflow; the only fully-free path until a self-hosted "
        "generator is wired up. Confirm license is CC-BY / CC0 / similar before "
        "submitting to Roblox marketplace."
    )

    def live_workflow(self, req: GenerationRequest) -> list[str]:
        return [
            "1. Call `mcp__blender__get_sketchfab_status` to confirm login.",
            f"2. Call `mcp__blender__search_sketchfab_models` with query: {req.prompt!r}, "
            "downloadable=True, count=12.",
            "3. For promising candidates: call `mcp__blender__get_sketchfab_model_preview` "
            "and present thumbnails to the user.",
            "4. Once the user picks one, call `mcp__blender__download_sketchfab_model` with the uid.",
            "5. CRITICAL: read the model's license from the search result; refuse to add to "
            "manifest if license forbids derivative/commercial use.",
            "6. Export FBX into the run dir; record license + author + uid in manifest notes.",
            "7. Run inspect+validate against the exported FBX.",
        ]

    def cost_hint(self) -> str:
        return "free for CC-licensed models"
