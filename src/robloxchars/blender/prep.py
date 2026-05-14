"""Run inside Blender to decimate, recenter, and rescale a model.

Usage:
    blender --background --python prep.py -- \
        --in <path> --out <path> [--decimate <target_tris>] \
        [--center] [--target-height <studs>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy  # type: ignore


def _parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--out", dest="dst", required=True)
    p.add_argument("--decimate", type=int, default=0,
                   help="Target total triangles; 0 = skip")
    p.add_argument("--center", action="store_true",
                   help="Move bbox center to world origin XY, base to Y=0")
    p.add_argument("--target-height", type=float, default=0.0,
                   help="Uniform scale so total Z extent matches this (in current units)")
    return p.parse_args(argv)


def _import(path: Path) -> None:
    ext = path.suffix.lower()
    if ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(path))
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=str(path))
        else:
            bpy.ops.import_scene.obj(filepath=str(path))
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        raise SystemExit(f"Unsupported source extension: {ext}")


def _meshes():
    return [o for o in bpy.data.objects if o.type == "MESH"]


def _total_tris() -> int:
    n = 0
    for o in _meshes():
        m = o.to_mesh()
        try:
            m.calc_loop_triangles()
            n += len(m.loop_triangles)
        finally:
            o.to_mesh_clear()
    return n


def _decimate_to(target: int) -> None:
    current = _total_tris()
    if current <= target or current == 0:
        print(f"[prep] decimate skip: current={current} target={target}")
        return
    ratio = max(0.01, target / current)
    print(f"[prep] decimate ratio={ratio:.4f} (from {current} -> ~{target})")
    for o in _meshes():
        mod = o.modifiers.new(name="rc_decimate", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.modifier_apply(modifier=mod.name)


def _bbox():
    from mathutils import Vector  # type: ignore
    xs: list[float] = []; ys: list[float] = []; zs: list[float] = []
    for o in _meshes():
        for c in o.bound_box:
            v = o.matrix_world @ Vector(c)
            xs.append(v.x); ys.append(v.y); zs.append(v.z)
    if not xs:
        return None
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _center_to_origin() -> None:
    bb = _bbox()
    if bb is None:
        return
    (xmin, ymin, zmin), (xmax, ymax, zmax) = bb
    cx = (xmin + xmax) / 2
    cy = (ymin + ymax) / 2
    # Move so bbox is centered on X/Y, base at Z=0.
    delta = (-cx, -cy, -zmin)
    for o in _meshes() + [o for o in bpy.data.objects if o.type == "ARMATURE"]:
        o.location.x += delta[0]
        o.location.y += delta[1]
        o.location.z += delta[2]


def _scale_to_height(target_h: float) -> None:
    bb = _bbox()
    if bb is None:
        return
    (_, _, zmin), (_, _, zmax) = bb
    current_h = zmax - zmin
    if current_h <= 0:
        return
    factor = target_h / current_h
    print(f"[prep] scale factor={factor:.4f} (from {current_h:.3f} -> {target_h})")
    for o in bpy.data.objects:
        o.scale = (o.scale.x * factor, o.scale.y * factor, o.scale.z * factor)
    bpy.context.view_layer.update()


def _export(path: Path) -> None:
    ext = path.suffix.lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    if ext == ".blend":
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
    elif ext == ".fbx":
        bpy.ops.export_scene.fbx(
            filepath=str(path),
            use_selection=False,
            apply_unit_scale=True,
            bake_space_transform=True,
            object_types={"MESH", "ARMATURE", "EMPTY"},
            add_leaf_bones=False,
            primary_bone_axis="Y",
            secondary_bone_axis="X",
            mesh_smooth_type="FACE",
        )
    elif ext in (".glb", ".gltf"):
        bpy.ops.export_scene.gltf(filepath=str(path))
    else:
        raise SystemExit(f"Unsupported output extension: {ext}")


def main() -> int:
    args = _parse_args()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    _import(Path(args.src).resolve())
    if args.decimate > 0:
        _decimate_to(args.decimate)
    if args.center:
        _center_to_origin()
    if args.target_height > 0:
        _scale_to_height(args.target_height)
    _export(Path(args.dst).resolve())
    print(f"[prep] wrote {args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
