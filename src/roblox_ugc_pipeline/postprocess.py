"""Clean up artifacts from single-image 3D reconstructions.

Single-view image-to-3D models (TRELLIS, InstantMesh, Hunyuan3D from one image)
commonly emit two kinds of junk for geometry the camera never saw:

  * **planes** — large paper-thin sheets (the hallucinated front/back backdrop),
  * **needles / wires** — thin strands trailing off the silhouette.

This module loads a mesh, splits it into connected components, and drops the
components whose bounding box is plane-like or needle-like, keeping the real
body. Pure-python (trimesh) — no Blender, so the CLI can call it headless.

Heuristic only: thresholds are relative to each component's own longest edge,
so it scales with the model. Tune via the keyword args if a real part gets
eaten or an artifact survives.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def _trimesh():
    try:
        import trimesh  # type: ignore
    except ImportError as e:
        raise RuntimeError("trimesh not installed. Run: pip install -e .[gen]") from e
    return trimesh


@dataclass
class CleanReport:
    components_in: int
    components_kept: int
    removed_planes: int
    removed_needles: int
    tris_in: int
    tris_out: int


def clean_mesh(
    in_path: str | Path,
    out_path: str | Path,
    plane_ratio: float = 0.06,   # min/max bbox extent below this AND large -> plane
    needle_ratio: float = 0.10,  # two extents this small relative to max -> needle
    min_keep_frac: float = 0.02,  # drop dust: components < this frac of biggest
) -> CleanReport:
    """Strip plane/needle components from a mesh and write the cleaned result.

    Returns a CleanReport. The output format follows out_path's suffix
    (.glb/.obj/.ply...)."""
    trimesh = _trimesh()
    scene = trimesh.load(str(in_path), force="scene")
    parts = []
    for geom in scene.geometry.values():
        parts.extend(geom.split(only_watertight=False))
    if not parts:
        raise RuntimeError(f"No mesh geometry found in {in_path}")

    sizes = [float(p.extents.max()) for p in parts]
    biggest = max(sizes)

    kept, planes, needles = [], 0, 0
    for p, longest in zip(parts, sizes):
        ext = sorted(float(e) for e in p.extents)  # [thin, mid, long]
        long = ext[2] or 1e-9
        if longest < biggest * min_keep_frac:
            continue  # dust speck
        is_plane = ext[0] / long < plane_ratio and longest > biggest * 0.4
        is_needle = ext[1] / long < needle_ratio
        if is_plane:
            planes += 1
        elif is_needle:
            needles += 1
        else:
            kept.append(p)

    if not kept:  # never return nothing — fall back to the largest part
        kept = [parts[sizes.index(biggest)]]

    combined = trimesh.util.concatenate(kept)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    combined.export(str(out_path))

    return CleanReport(
        components_in=len(parts),
        components_kept=len(kept),
        removed_planes=planes,
        removed_needles=needles,
        tris_in=sum(len(p.faces) for p in parts),
        tris_out=len(combined.faces),
    )
