"""Headless: load an FBX, set up a turntable of 4 cameras, render PNGs.

Usage:
    blender --background --python render_previews.py -- \
        --in <fbx> --out-dir <dir> [--res 768]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import bpy  # type: ignore


def _parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--out-dir", dest="out_dir", required=True)
    p.add_argument("--res", type=int, default=768)
    return p.parse_args(argv)


def _clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False, confirm=False)
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)


def _world_bbox_of_meshes():
    mins = [+1e9] * 3
    maxs = [-1e9] * 3
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        for v in o.bound_box:
            wv = o.matrix_world @ __import__("mathutils").Vector(v)
            for i in range(3):
                mins[i] = min(mins[i], wv[i])
                maxs[i] = max(maxs[i], wv[i])
    return mins, maxs


def main() -> int:
    args = _parse()
    src = Path(args.src).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    _clear_scene()
    bpy.ops.import_scene.fbx(filepath=str(src))

    mins, maxs = _world_bbox_of_meshes()
    cx = (mins[0] + maxs[0]) / 2
    cy = (mins[1] + maxs[1]) / 2
    cz = (mins[2] + maxs[2]) / 2
    size = max(maxs[0] - mins[0], maxs[1] - mins[1], maxs[2] - mins[2])
    dist = size * 2.5

    bpy.ops.object.camera_add(location=(cx, cy - dist, cz))
    cam = bpy.context.object
    cam.data.type = "PERSP"
    cam.data.lens = 50

    bpy.ops.object.light_add(type="SUN", location=(cx + dist, cy - dist, cz + dist))
    sun = bpy.context.object
    sun.data.energy = 3.0

    scene = bpy.context.scene
    scene.camera = cam
    scene.render.resolution_x = args.res
    scene.render.resolution_y = args.res
    for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = eng
            break
        except TypeError:
            continue
    scene.render.image_settings.file_format = "PNG"

    views = {
        "front":  (0.0,           -dist, 0.0),
        "back":   (0.0,            dist, 0.0),
        "left":   (-dist,          0.0,  0.0),
        "right":  ( dist,          0.0,  0.0),
    }

    bpy.ops.object.empty_add(location=(cx, cy, cz))
    target = bpy.context.object
    track = cam.constraints.new(type="TRACK_TO")
    track.target = target
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"

    out_paths = []
    for name, (dx, dy, dz) in views.items():
        cam.location = (cx + dx, cy + dy, cz + dz)
        bpy.context.view_layer.update()
        out_path = out_dir / f"preview_{name}.png"
        scene.render.filepath = str(out_path)
        bpy.ops.render.render(write_still=True)
        out_paths.append(out_path)

    for p in out_paths:
        print(f"[render_previews] wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
