"""Provider abstraction.

Two execution modes:

  * **live**: invoked by the assistant via Blender MCP tools. The assistant
    drives generation interactively and writes a manifest row when an asset
    is dropped into a run directory.
  * **headless**: provider has a `submit()` that the CLI can call directly.
    Currently no free provider supports this without Blender being up;
    Hyper3D Rodin and Hunyuan3D both pipe through the BlenderMCP addon.

Subclasses implement `live_workflow()` returning the step-by-step instructions
the assistant should follow to drive generation for this provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


Modality = Literal["text", "image", "multi"]


@dataclass
class GenerationRequest:
    prompt: str
    modality: Modality
    image_paths: list[Path] = field(default_factory=list)
    target: Literal["avatar", "accessory", "prop"] = "accessory"
    accessory_category: str | None = None
    seed: int | None = None
    notes: str | None = None


@dataclass
class GenerationResult:
    provider: str
    run_dir: Path
    asset_path: Path
    request: GenerationRequest
    extra: dict = field(default_factory=dict)


class Provider:
    name: str = "base"
    free_tier: bool = False
    notes: str = ""

    def live_workflow(self, req: GenerationRequest) -> list[str]:
        """Return ordered steps the assistant should perform via MCP tools."""
        raise NotImplementedError

    def cost_hint(self) -> str:
        return "unknown"
