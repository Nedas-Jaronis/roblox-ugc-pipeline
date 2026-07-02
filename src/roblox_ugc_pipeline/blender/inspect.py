"""Run inside Blender to produce a MeshReport JSON for a given source file.

Usage (invoked by the CLI):
    blender --background --python inspect.py -- --in <path> --out <report.json>

Supported inputs: .blend, .fbx, .obj, .glb, .gltf
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy  # type: ignore


def _parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--out", dest="out", required=True)
    return p.parse_args(argv)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)


def _import(path: Path) -> None:
    ext = path.suffix.lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(path))
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif ext == ".obj":
        # Blender 4.x uses wm.obj_import; 3.x uses import_scene.obj.
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=str(path))
        else:
            bpy.ops.import_scene.obj(filepath=str(path))
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        raise SystemExit(f"Unsupported source extension: {ext}")


def _units() -> str:
    u = bpy.context.scene.unit_settings
    scale = u.scale_length  # in meters
    sysu = u.system
    # Roblox reads raw FBX units as STUDS, and this pipeline builds at
    # 1 Blender unit = 1 stud, so a default scale-1.0 scene is studs — both as
    # our convention and as what Roblox will actually do with the same file.
    # (This used to return "meters", which inflated every validator size by
    # 1/0.28 on stud-scale exports.)
    if sysu == "METRIC":
        if abs(scale - 1.0) < 1e-3:
            return "studs"
        if abs(scale - 0.01) < 1e-3:
            return "centimeters"
        if abs(scale - 0.28) < 1e-3:
            return "studs"
    return "unknown"


def _object_report(obj):
    tris = 0
    verts = 0
    origin_offset = None
    if obj.type == "MESH":
        mesh = obj.to_mesh()
        try:
            mesh.calc_loop_triangles()
            tris = len(mesh.loop_triangles)
            verts = len(mesh.vertices)
        finally:
            obj.to_mesh_clear()
        corners = [obj.matrix_world @ __vec(c) for c in obj.bound_box]
        xs = [v.x for v in corners]
        ys = [v.y for v in corners]
        zs = [v.z for v in corners]
        bb_min = [min(xs), min(ys), min(zs)]
        bb_max = [max(xs), max(ys), max(zs)]
        # Mesh-space bbox center distance from the object origin — the gate's
        # validateMeshIsAtOrigin requires this <= 0.001 in the mesh file.
        local = [__vec(c) for c in obj.bound_box]
        center = sum(local, __vec((0, 0, 0))) / 8.0
        origin_offset = center.length
    else:
        bb_min = [0.0, 0.0, 0.0]
        bb_max = [0.0, 0.0, 0.0]
    return {
        "name": obj.name,
        "triangle_count": tris,
        "vertex_count": verts,
        "materials": [m.name for m in obj.material_slots if m.material],
        "parent": obj.parent.name if obj.parent else None,
        "bbox_min": bb_min,
        "bbox_max": bb_max,
        "origin_offset": origin_offset,
    }


def __vec(v):
    from mathutils import Vector  # type: ignore
    return Vector(v)


def _attachments() -> list[dict]:
    out: list[dict] = []
    for obj in bpy.data.objects:
        # Two Roblox conventions: rigid accessories use `*Attachment`,
        # avatar body uses `*_Att`. Roblox's own template bodies model the
        # markers as tiny MESH objects (not empties), so accept any type and
        # take the world bbox center when there is geometry.
        if obj.name.endswith("Attachment") or obj.name.endswith("_Att"):
            parent_bone = None
            if obj.parent and obj.parent_type == "BONE":
                parent_bone = obj.parent_bone
            elif obj.parent and obj.parent.type == "ARMATURE":
                parent_bone = obj.parent_bone or None
            if obj.type == "MESH" and obj.bound_box:
                from mathutils import Vector  # type: ignore
                corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
                loc = sum(corners, Vector()) / len(corners)
            else:
                loc = obj.matrix_world.translation
            out.append({
                "name": obj.name,
                "parent_bone": parent_bone,
                "position": [loc.x, loc.y, loc.z],
            })
    return out


def _armature() -> dict | None:
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            return {
                "name": obj.name,
                "bone_names": [b.name for b in obj.data.bones],
            }
    return None


def _materials() -> list[dict]:
    out: list[dict] = []
    # Only include materials actually used by mesh objects in the scene.
    used = {
        s.material.name
        for o in bpy.data.objects if o.type == "MESH"
        for s in o.material_slots if s.material
    }
    for mat in bpy.data.materials:
        if mat.name not in used:
            continue
        if not mat.use_nodes:
            out.append({"name": mat.name})
            continue
        base = normal = mr = None
        # Primary detection: trace the link graph from the BSDF inputs back.
        for n in mat.node_tree.nodes:
            if n.type != "BSDF_PRINCIPLED":
                continue
            base = _trace_back_to_image(n.inputs.get("Base Color"))
            normal = _trace_back_to_image(n.inputs.get("Normal"))
            mr = _trace_back_to_image(n.inputs.get("Roughness")) or \
                 _trace_back_to_image(n.inputs.get("Metallic"))
        # Fallback: label heuristic on any image-texture nodes.
        if base is None or normal is None or mr is None:
            for node in mat.node_tree.nodes:
                if node.type != "TEX_IMAGE" or node.image is None:
                    continue
                label = (node.label or node.name or "").lower()
                img = node.image.filepath_raw or node.image.name
                if base is None and any(k in label for k in ("basecolor", "albedo", "diffuse", "color")):
                    base = img
                elif normal is None and "normal" in label:
                    normal = img
                elif mr is None and any(k in label for k in ("metal", "rough", "orm", "mr")):
                    mr = img
        out.append({
            "name": mat.name,
            "base_color_texture": base,
            "normal_texture": normal,
            "metallic_roughness_texture": mr,
        })
    return out


def _trace_back_to_image(socket, depth: int = 6) -> str | None:
    """Walk backward through links from a socket to find a feeding Image node."""
    if socket is None or not getattr(socket, "is_linked", False) or depth <= 0:
        return None
    for link in socket.links:
        n = link.from_node
        if n.type == "TEX_IMAGE" and n.image is not None:
            return n.image.filepath_raw or n.image.name
        if n.type == "NORMAL_MAP":
            return _trace_back_to_image(n.inputs.get("Color"), depth - 1)
        if n.type == "SEPARATE_RGB" or n.type == "SEPARATE_COLOR":
            return _trace_back_to_image(n.inputs.get("Image") or n.inputs.get("Color"), depth - 1)
    return None


# Same grouping the validators use (kept local: this script must stay
# self-contained inside `blender --background`).
_BODY_GROUPS: dict[str, tuple[str, ...]] = {
    "Head": ("Head_Geo", "LeftEye_Geo", "RightEye_Geo",
             "UpperTeeth_Geo", "LowerTeeth_Geo", "Tongue_Geo"),
    "Torso": ("UpperTorso_Geo", "LowerTorso_Geo"),
    "LeftArm": ("LeftUpperArm_Geo", "LeftLowerArm_Geo", "LeftHand_Geo"),
    "RightArm": ("RightUpperArm_Geo", "RightLowerArm_Geo", "RightHand_Geo"),
    "LeftLeg": ("LeftUpperLeg_Geo", "LeftLowerLeg_Geo", "LeftFoot_Geo"),
    "RightLeg": ("RightUpperLeg_Geo", "RightLowerLeg_Geo", "RightFoot_Geo"),
}


def _world_triangles(objs):
    """(N,3) vertex array + (M,3) index array, world space, all objs merged."""
    import numpy as np
    pts, tris, base = [], [], 0
    for obj in objs:
        mesh = obj.to_mesh()
        try:
            mesh.calc_loop_triangles()
            mw = obj.matrix_world
            if len(mesh.vertices) == 0:
                continue
            arr = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
            mesh.vertices.foreach_get("co", arr)
            arr = arr.reshape(-1, 3)
            m = np.array(mw.to_4x4())
            arr = arr @ m[:3, :3].T + m[:3, 3]
            for t in mesh.loop_triangles:
                tris.append((t.vertices[0] + base, t.vertices[1] + base, t.vertices[2] + base))
            pts.append(arr)
            base += len(arr)
        finally:
            obj.to_mesh_clear()
    if not pts:
        return None, None
    return np.vstack(pts), np.array(tris, dtype=np.int64)


def _silhouette_fill(objs, res: int = 128) -> dict[str, float] | None:
    """Fraction of the group's bbox silhouette covered by geometry, per view
    axis ('x' = side views, 'y' = front/back, 'z' = top/bottom).

    Approximates Roblox's validateAssetTransparency raster: it renders 6
    orthographic views and fails parts whose opaque-pixel fraction of the
    part bbox is below a per-view threshold. A silhouette rasterization is
    the material-independent core of that check.
    """
    import numpy as np
    P, T = _world_triangles(objs)
    if P is None or len(T) == 0:
        return None
    out: dict[str, float] = {}
    for axis, key in ((0, "x"), (1, "y"), (2, "z")):
        u, v = [(1, 2), (0, 2), (0, 1)][axis]
        UV = P[:, [u, v]]
        mn = UV.min(axis=0)
        span = np.maximum(UV.max(axis=0) - mn, 1e-9)
        A = (UV[T[:, 0]] - mn) / span * (res - 1)
        B = (UV[T[:, 1]] - mn) / span * (res - 1)
        C = (UV[T[:, 2]] - mn) / span * (res - 1)
        grid = np.zeros((res, res), dtype=bool)
        for a, b, c in zip(A, B, C):
            lo = np.clip(np.floor(np.minimum(np.minimum(a, b), c)).astype(int), 0, res - 1)
            hi = np.clip(np.ceil(np.maximum(np.maximum(a, b), c)).astype(int), 0, res - 1)
            if hi[0] < lo[0] or hi[1] < lo[1]:
                continue
            gx, gy = np.meshgrid(np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1), indexing="ij")
            px = np.stack([gx.ravel(), gy.ravel()], axis=1).astype(np.float64)
            v0, v1 = c - a, b - a
            v2 = px - a
            d00 = v0 @ v0; d01 = v0 @ v1; d11 = v1 @ v1
            denom = d00 * d11 - d01 * d01
            if abs(denom) < 1e-12:
                continue
            d20 = v2 @ v0; d21 = v2 @ v1
            w1 = (d11 * d20 - d01 * d21) / denom
            w2 = (d00 * d21 - d01 * d20) / denom
            inside = (w1 >= -1e-9) & (w2 >= -1e-9) & (w1 + w2 <= 1 + 1e-9)
            grid[gx.ravel()[inside], gy.ravel()[inside]] = True
        out[key] = float(grid.mean())
    return out


def _group_view_fill() -> dict[str, dict[str, float]]:
    by_name = {o.name: o for o in bpy.data.objects if o.type == "MESH"}
    result: dict[str, dict[str, float]] = {}
    try:
        import numpy  # noqa: F401
    except Exception:
        return result
    for group, members in _BODY_GROUPS.items():
        objs = [by_name[m] for m in members if m in by_name]
        if not objs:
            continue
        fill = _silhouette_fill(objs)
        if fill is not None:
            result[group] = fill
    return result


def _cage_distances() -> dict[str, float]:
    """Max distance from each *_OuterCage vertex to its render mesh surface.

    Replicates the 'A vertex was found on the X's cage mesh that is N studs
    away from the closest render mesh' check (caps: 0.6 head / 0.3 parts).
    """
    from mathutils.bvhtree import BVHTree  # type: ignore
    by_name = {o.name: o for o in bpy.data.objects if o.type == "MESH"}
    out: dict[str, float] = {}
    for name, cage in by_name.items():
        if not name.endswith("_OuterCage"):
            continue
        geo = by_name.get(name[: -len("_OuterCage")] + "_Geo")
        if geo is None:
            continue
        mesh = geo.to_mesh()
        try:
            mesh.calc_loop_triangles()
            mw = geo.matrix_world
            verts = [tuple(mw @ v.co) for v in mesh.vertices]
            polys = [tuple(t.vertices) for t in mesh.loop_triangles]
        finally:
            geo.to_mesh_clear()
        if not polys:
            continue
        tree = BVHTree.FromPolygons(verts, polys)
        cage_mesh = cage.to_mesh()
        try:
            cmw = cage.matrix_world
            max_d = 0.0
            for v in cage_mesh.vertices:
                found = tree.find_nearest(cmw @ v.co)
                if found is not None and found[3] is not None:
                    max_d = max(max_d, found[3])
        finally:
            cage.to_mesh_clear()
        out[name] = max_d
    return out


def _world_bbox() -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            v = obj.matrix_world @ __vec(corner)
            xs.append(v.x); ys.append(v.y); zs.append(v.z)
    if not xs:
        return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    return [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]


def main() -> int:
    args = _parse_args()
    src = Path(args.src).resolve()
    if not src.exists():
        print(f"[inspect] source not found: {src}", file=sys.stderr)
        return 2

    _clear_scene()
    _import(src)

    objects = [_object_report(o) for o in bpy.data.objects if o.type == "MESH"]
    total_tris = sum(o["triangle_count"] for o in objects)
    total_verts = sum(o["vertex_count"] for o in objects)
    bb_min, bb_max = _world_bbox()

    cage_dist = _cage_distances()
    for o in objects:
        if o["name"] in cage_dist:
            o["cage_max_dist"] = cage_dist[o["name"]]

    report = {
        "source_path": str(src),
        "units": _units(),
        "bbox_min": bb_min,
        "bbox_max": bb_max,
        "triangle_count": total_tris,
        "vertex_count": total_verts,
        "objects": objects,
        "armature": _armature(),
        "attachments": _attachments(),
        "materials": _materials(),
        "group_view_fill": _group_view_fill(),
        "notes": [],
    }

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"[inspect] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
