"""Clean up artifacts from single-image 3D reconstructions.

Single-view image-to-3D models (TRELLIS, InstantMesh, Hunyuan3D from one image)
emit three kinds of junk for geometry the camera never saw:

  * **planes** — large paper-thin sheets (the hallucinated front/back backdrop),
  * **needles / wires** — thin strands trailing off the silhouette,
  * **floating scraps** — small blobs detached from the body, hanging in space.

The body itself is usually NOT one connected mesh — these models fragment it
into dozens/hundreds of pieces — so "keep the largest connected component"
deletes most of the model. Instead we:

  1. drop obvious planes / needles / dust by bounding-box shape,
  2. spatially CLUSTER the survivors (pieces whose bounding boxes nearly touch
     belong to the same object), and keep only the largest cluster — the body.

Isolated floaters form their own tiny clusters and get dropped, while the
fragmented-but-contiguous body survives intact. Pure-python (trimesh +
networkx), no Blender, so the CLI can run it headless.

Thresholds are relative to the model's overall size, so they scale. Tune via
the keyword args if a real part gets eaten or an artifact survives.
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
    removed_floaters: int
    clusters: int
    tris_in: int
    tris_out: int


def _aabb_touch(a, b, gap: float) -> bool:
    """True if two axis-aligned bounding boxes overlap when each is grown by gap."""
    (amin, amax), (bmin, bmax) = a, b
    for i in range(3):
        if amin[i] - gap > bmax[i] + gap or bmin[i] - gap > amax[i] + gap:
            return False
    return True


def _largest_cluster(parts, gap: float):
    """Cluster parts whose (gap-expanded) bounding boxes touch; return the
    indices of the cluster with the most total surface area."""
    import networkx as nx

    bounds = [p.bounds for p in parts]
    g = nx.Graph()
    g.add_nodes_from(range(len(parts)))
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            if _aabb_touch(bounds[i], bounds[j], gap):
                g.add_edge(i, j)
    comps = list(nx.connected_components(g))
    if not comps:
        return set(), 0
    best = max(comps, key=lambda c: sum(float(parts[i].area) for i in c))
    return best, len(comps)


def clean_mesh(
    in_path: str | Path,
    out_path: str | Path,
    plane_ratio: float = 0.06,   # min/max bbox extent below this AND large -> plane
    needle_ratio: float = 0.10,  # mid extent this small relative to max -> needle
    min_keep_frac: float = 0.015,  # drop dust smaller than this frac of the biggest part
    core_frac: float = 0.12,     # parts >= this frac of the biggest survivor define the body bbox
    bbox_margin: float = 0.06,   # keep parts within (body bbox + this frac of size)
    cluster_gap: float = 0.03,   # pieces within this frac of overall size are "connected"
) -> CleanReport:
    """Strip planes / needles / floating scraps from a mesh; write the cleaned
    result. Returns a CleanReport. Output format follows out_path's suffix."""
    trimesh = _trimesh()
    scene = trimesh.load(str(in_path), force="scene")
    parts = []
    for geom in scene.geometry.values():
        parts.extend(geom.split(only_watertight=False))
    if not parts:
        raise RuntimeError(f"No mesh geometry found in {in_path}")

    tris_in = sum(len(p.faces) for p in parts)
    sizes = [float(p.extents.max()) for p in parts]
    biggest = max(sizes)

    # --- stage 1: shape-based removal (planes / needles / dust) ---
    survivors, planes, needles = [], 0, 0
    for p, longest in zip(parts, sizes):
        ext = sorted(float(e) for e in p.extents)  # [thin, mid, long]
        long = ext[2] or 1e-9
        if longest < biggest * min_keep_frac:
            continue  # dust speck
        if ext[0] / long < plane_ratio and longest > biggest * 0.4:
            planes += 1
        elif ext[1] / long < needle_ratio:
            needles += 1
        else:
            survivors.append(p)
    if not survivors:
        survivors = [parts[sizes.index(biggest)]]

    # --- stage 2: drop floaters that sit outside the body's bounding box ---
    # The body's substantial fragments define where the real model is; small
    # pieces whose centroid falls outside that box (+margin) are hallucinated
    # scraps floating in space. Robust to the body being fragmented.
    import numpy as np

    areas = np.array([float(p.area) for p in survivors])
    amax = areas.max()
    core = [p for p, a in zip(survivors, areas) if a >= core_frac * amax]
    cb = trimesh.util.concatenate(core).bounds
    size = float((cb[1] - cb[0]).max())
    margin = size * bbox_margin
    lo, hi = cb[0] - margin, cb[1] + margin

    inside = []
    floaters = 0
    for p in survivors:
        c = p.centroid
        if bool((c >= lo).all() and (c <= hi).all()):
            inside.append(p)
        else:
            floaters += 1

    # --- stage 3: keep only the largest spatial cluster of what remains ---
    overall = trimesh.util.concatenate(inside).bounds
    overall_size = float((overall[1] - overall[0]).max())
    keep_idx, n_clusters = _largest_cluster(inside, gap=overall_size * cluster_gap)
    kept = [inside[i] for i in keep_idx] if keep_idx else inside
    floaters += len(inside) - len(kept)

    combined = trimesh.util.concatenate(kept)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    combined.export(str(out_path))

    return CleanReport(
        components_in=len(parts),
        components_kept=len(kept),
        removed_planes=planes,
        removed_needles=needles,
        removed_floaters=floaters,
        clusters=n_clusters,
        tris_in=tris_in,
        tris_out=len(combined.faces),
    )
