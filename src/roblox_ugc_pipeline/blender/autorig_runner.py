"""Headless Blender entrypoint for the autorig pipeline.

Invoked by the CLI as:

    blender --background --python autorig_runner.py -- \
        --in <mesh_path>           # any .glb/.fbx/.obj/.blend humanoid
        --out <prepped.fbx>        # where to write the rigged FBX
        [--height <studs>]         # default 5.0
        [--multi-view-sources <json>]  # {"front": "path", ...}
        [--texture-dir <dir>]
        [--texture-resolution 1024]
        [--no-decimate]
        [--join-prefix <prefix>]   # join meshes whose names start with this

The pipeline auto-joins all imported meshes into one before running autorig.
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
    p.add_argument("--out", dest="dst", required=True)
    p.add_argument("--height", type=float, default=5.0,
                   help="Target avatar height in studs (default 5.0)")
    p.add_argument("--multi-view-sources", default=None,
                   help='JSON dict {"front": "path", "back": "path", ...}')
    p.add_argument("--texture-dir", default=None)
    p.add_argument("--texture-resolution", type=int, default=1024)
    p.add_argument("--no-decimate", action="store_true")
    p.add_argument("--for-autosetup", action="store_true",
                   help="Export for Studio Avatar Auto-Setup: skip attachment "
                        "stamping and cage generation (auto-setup creates its "
                        "own and fights pre-stamped ones)")
    p.add_argument("--join-prefix", default=None,
                   help="Only join meshes whose name starts with this prefix")
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


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False, confirm=False)
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)


def _join_into_single_mesh(prefix: str | None = None) -> bpy.types.Object:
    candidates = [
        o for o in bpy.data.objects
        if o.type == "MESH" and (prefix is None or o.name.startswith(prefix))
    ]
    if not candidates:
        raise RuntimeError("No mesh objects found to join")
    bpy.ops.object.select_all(action="DESELECT")
    for m in candidates:
        m.select_set(True)
    bpy.context.view_layer.objects.active = candidates[0]
    if len(candidates) > 1:
        bpy.ops.object.join()
    joined = bpy.context.view_layer.objects.active
    joined.name = "rc_joined"
    if joined.parent is not None:
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    # Drop leftover empties for a clean scene.
    for o in list(bpy.data.objects):
        if o is joined:
            continue
        if o.type == "EMPTY":
            bpy.data.objects.remove(o, do_unlink=True)
    return joined


def main() -> int:
    args = _parse_args()
    src = Path(args.src).resolve()
    if not src.exists():
        print(f"[autorig_runner] source not found: {src}", file=sys.stderr)
        return 2

    _clear_scene()
    _import(src)
    _join_into_single_mesh(prefix=args.join_prefix)

    # Lazy import to avoid loading bpy-dependent modules until Blender is running.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from roblox_ugc_pipeline.blender import autorig

    mv_sources = None
    if args.multi_view_sources:
        mv_sources = json.loads(args.multi_view_sources)

    result = autorig.run_inplace(
        target_height=args.height,
        decimate=not args.no_decimate,
        stamp_attachments=not args.for_autosetup,
        generate_cages=not args.for_autosetup,
        out_fbx=str(Path(args.dst).resolve()),
        multi_view_sources=mv_sources,
        texture_dir=args.texture_dir,
        texture_resolution=args.texture_resolution,
    )

    # Drop a log next to the output.
    log_path = Path(args.dst).with_suffix(".log.json")
    log_path.write_text(json.dumps(result.log, indent=2, default=str))
    print(f"[autorig_runner] wrote {args.dst} + {log_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
