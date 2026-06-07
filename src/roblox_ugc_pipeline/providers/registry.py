from __future__ import annotations

from .base import Provider
from .cube3d import Cube3DProvider
from .hunyuan3d import Hunyuan3DProvider, Hunyuan3DSpaceProvider
from .hyper3d import Hyper3DProvider
from .instantmesh import InstantMeshProvider, TripoSRProvider
from .sf3d import SF3DProvider
from .sketchfab import SketchfabProvider
from .trellis import TrellisProvider


_PROVIDERS: dict[str, Provider] = {
    # Free-tier first.
    "cube3d": Cube3DProvider(),
    "sf3d": SF3DProvider(),
    "trellis": TrellisProvider(),
    "hunyuan3d-space": Hunyuan3DSpaceProvider(),
    "instantmesh": InstantMeshProvider(),
    "triposr": TripoSRProvider(),
    "sketchfab": SketchfabProvider(),
    # Paid fallbacks.
    "hyper3d": Hyper3DProvider(),
    "hunyuan3d": Hunyuan3DProvider(),
}


def available_providers() -> dict[str, Provider]:
    return dict(_PROVIDERS)


def get_provider(name: str) -> Provider:
    if name not in _PROVIDERS:
        raise KeyError(f"Unknown provider: {name}. Available: {list(_PROVIDERS)}")
    return _PROVIDERS[name]
