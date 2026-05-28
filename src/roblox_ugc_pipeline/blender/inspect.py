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
    # Heuristic: Roblox studs are ~0.28m; if scene is "NONE" with scale 1.0 and
    # objects are sized between 1 and 50, likely studs already.
    if sysu == "METRIC":
        if abs(scale - 1.0) < 1e-3:
            return "meters"
        if abs(scale - 0.01) < 1e-3:
            return "centimeters"
        if abs(scale - 0.28) < 1e-3:
            return "studs"
    return "unknown"


def _object_report(obj):
    tris = 0
    verts = 0
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
    }


def __vec(v):
    from mathutils import Vector  # type: ignore
    return Vector(v)


def _attachments() -> list[dict]:
    out: list[dict] = []
    for obj in bpy.data.objects:
        # Two Roblox conventions: rigid accessories use `*Attachment`,
        # avatar body uses `*_Att`. Capture both so validators can branch.
        if obj.type == "EMPTY" and (obj.name.endswith("Attachment") or obj.name.endswith("_Att")):
            parent_bone = None
            if obj.parent and obj.parent_type == "BONE":
                parent_bone = obj.parent_bone
            elif obj.parent and obj.parent.type == "ARMATURE":
                parent_bone = obj.parent_bone or None
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
        "notes": [],
    }

    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"[inspect] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
