"""Auto-prep an accessory mesh for Roblox marketplace submission.

Pipeline (same proven axis/unit contract as the body autorig: Blender Z-up
scene, front = -Y, character-left = +X, 1 Blender unit = 1 stud, exported
with axis_forward=-Z / axis_up=Y / apply_unit_scale=False):
  1. Join all visible mesh objects into a single mesh (per-accessory norm).
  2. Optional --yaw about Z (single-image reconstructions keep the photo's
     framing, which often points the front sideways). No automatic
     re-orientation: glTF imports already land upright in Z-up, and extent
     heuristics guess wrong on wider-than-tall items like hats.
  3. Uniform-scale to a sensible wear size (largest horizontal dimension ->
     --target-size studs, default per category), clamped to the category cap.
  4. Recenter: bbox center at origin XY, bbox bottom at Z=0.
  5. Decimate to the category triangle cap (weld first: baked GLBs arrive
     as unwelded triangle soup).
  6. Stamp the required Attachment empties at spec positions.
  7. Export FBX with Roblox-friendly settings.

Importable from inside Blender (e.g. via `execute_blender_code`):

    from roblox_ugc_pipeline.blender import autoprep
    autoprep.run_inplace(category="Hat",
                         out_fbx="runs/.../prepped.fbx")

Standalone (headless):

    blender --background --python autoprep.py -- \
        --in <model> --out <prepped.fbx> --category Hat
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import bpy  # type: ignore
from mathutils import Vector  # type: ignore


# Lazy load spec from the parent package so this script also works when
# invoked headless without the package being installed (we look at sys.path).
def _load_spec():
    import importlib
    try:
        return importlib.import_module("roblox_ugc_pipeline.roblox_spec")
    except ImportError:
        # Fallback: try to extend sys.path from this file's location.
        here = Path(__file__).resolve()
        for p in (here.parent.parent.parent, here.parent.parent):
            if (p / "roblox_ugc_pipeline").exists():
                sys.path.insert(0, str(p))
                return importlib.import_module("roblox_ugc_pipeline.roblox_spec")
        raise


# 1 stud == 0.28 m in Roblox's default scale.
STUD_M = 0.28


# ---------- helpers -----------------------------------------------------------

def _all_meshes():
    return [o for o in bpy.data.objects if o.type == "MESH"]


def _world_corners(obj):
    return [obj.matrix_world @ Vector(c) for c in obj.bound_box]


def _world_bbox(objs):
    if not objs:
        return Vector((0, 0, 0)), Vector((0, 0, 0))
    xs: list[float] = []; ys: list[float] = []; zs: list[float] = []
    for o in objs:
        for c in _world_corners(o):
            xs.append(c.x); ys.append(c.y); zs.append(c.z)
    return Vector((min(xs), min(ys), min(zs))), Vector((max(xs), max(ys), max(zs)))


def _extents(bb_min: Vector, bb_max: Vector) -> Vector:
    return Vector((bb_max.x - bb_min.x, bb_max.y - bb_min.y, bb_max.z - bb_min.z))


def _total_tris(objs) -> int:
    n = 0
    for o in objs:
        m = o.to_mesh()
        try:
            m.calc_loop_triangles()
            n += len(m.loop_triangles)
        finally:
            o.to_mesh_clear()
    return n


# ---------- pipeline steps ----------------------------------------------------

def join_meshes() -> bpy.types.Object | None:
    """Join all mesh objects into one and unparent it (keep world transform)."""
    meshes = _all_meshes()
    if not meshes:
        return None
    bpy.ops.object.select_all(action="DESELECT")
    for m in meshes:
        m.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    # Clear parent keeping the world transform, then bake any residual
    # rotation/scale into the mesh so subsequent .location math works in
    # world space without surprises.
    if obj.parent is not None:
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    # Drop the now-empty parent objects so they don't pollute the export.
    for o in list(bpy.data.objects):
        if o is obj:
            continue
        if o.type == "EMPTY" and not o.children and not (
            o.name.endswith("Attachment") or o.name.endswith("_Att")
        ):
            bpy.data.objects.remove(o, do_unlink=True)
    return obj


def orient_to_y_up(obj: bpy.types.Object, category_max_bounds_studs: tuple[float, float, float]) -> str:
    """Rotate so the mesh's tallest axis lines up with Y (most accessory cats
    have Y as the tallest allowed dimension).

    Returns a description of what happened, for the run log.
    """
    target_tallest_axis = max(range(3), key=lambda i: category_max_bounds_studs[i])
    if target_tallest_axis != 1:
        # Hair/Hat/etc all have Y tallest. We don't yet support categories
        # where the tallest cap axis is X or Z.
        return f"keeping orientation (category's tallest is axis {target_tallest_axis}, not Y)"

    # Detect current tallest axis from world bbox extents.
    bb_min, bb_max = _world_bbox([obj])
    ex = _extents(bb_min, bb_max)
    current_tallest = max(range(3), key=lambda i: (ex.x, ex.y, ex.z)[i])
    if current_tallest == 1:
        return "no rotation needed (Y already tallest)"

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if current_tallest == 0:
        # X is tallest -> rotate -90 about Z so X becomes Y.
        bpy.ops.transform.rotate(value=-math.pi / 2, orient_axis="Z")
        return "rotated -90 deg about Z (X was tallest)"
    # Z is tallest -> rotate +90 about X so Z becomes Y.
    bpy.ops.transform.rotate(value=math.pi / 2, orient_axis="X")
    return "rotated +90 deg about X (Z was tallest)"


def scale_to_bbox(
    obj: bpy.types.Object,
    max_bounds_studs: tuple[float, float, float],
    target_size_studs: float,
    margin: float = 0.02,
) -> tuple[float, Vector]:
    """Uniformly scale to wear size, clamped to the category cap.

    Scene convention: 1 Blender unit = 1 stud, Z up. Roblox caps come as
    (X width, Y height, Z depth) -> Blender (x, z, y). The wear-size target
    sets the largest HORIZONTAL dimension (a hat should relate to the
    ~1.2-stud head, not fill the 3-stud legal box).

    Returns (scale_factor, new_extents_in_studs).
    """
    bb_min, bb_max = _world_bbox([obj])
    ex = _extents(bb_min, bb_max)
    caps_blender = (max_bounds_studs[0], max_bounds_studs[2], max_bounds_studs[1])
    horizontal = max(ex.x, ex.y)
    if horizontal <= 1e-9:
        return 1.0, Vector((0, 0, 0))
    scale = target_size_studs / horizontal
    cap_ratios = [caps_blender[i] * (1 - margin) / ex[i] for i in range(3) if ex[i] > 1e-9]
    if cap_ratios:
        scale = min(scale, min(cap_ratios))

    if abs(scale - 1.0) > 1e-6:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.transform.resize(value=(scale, scale, scale))
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    bb_min, bb_max = _world_bbox([obj])
    ex = _extents(bb_min, bb_max)
    return scale, Vector((ex.x, ex.y, ex.z))


def recenter(obj: bpy.types.Object) -> None:
    """Move bbox center to origin XY, bbox bottom (min Z) to Z=0.

    Operates in WORLD space — the join step already unparented the mesh and
    baked rotation/scale, so obj.location maps 1:1 to world translation.
    """
    bb_min, bb_max = _world_bbox([obj])
    cx = (bb_min.x + bb_max.x) / 2
    cy = (bb_min.y + bb_max.y) / 2
    obj.location.x -= cx
    obj.location.y -= cy
    obj.location.z -= bb_min.z
    bpy.context.view_layer.update()
    # Bake the recenter into the mesh so subsequent attachment placement uses
    # the cleanest possible local-space coordinates.
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)


def _weld_vertices(obj: bpy.types.Object, dist: float = 1e-4) -> None:
    """Merge coincident vertices. Texture-baked GLB exports split every UV
    seam vertex (~3 verts/tri), which leaves collapse-decimation nothing to
    collapse — it stalls far above the target. Per-loop UVs survive the
    merge, so texture seams are unaffected."""
    import bmesh  # type: ignore
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def decimate_to(obj: bpy.types.Object, target: int) -> tuple[int, int]:
    """Decimate-collapse to ~`target` tris. Returns (before, after).

    Collapse gets unreliable below ~1% per pass, so million-tri generated
    meshes need several passes to actually reach the cap instead of
    stalling at the ratio floor (same fix as the body path's
    decimate_per_group).
    """
    before = _total_tris([obj])
    if before > target:
        _weld_vertices(obj)
    current = _total_tris([obj])
    passes = 0
    while current > target and current > 0 and passes < 5:
        ratio = max(0.01, target / current)
        mod = obj.modifiers.new(name="rc_decimate", type="DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=mod.name)
        current = _total_tris([obj])
        passes += 1
    return before, current


def attachment_positions(category, bb_min: Vector, bb_max: Vector) -> dict[str, Vector]:
    """Heuristic spec positions for each required attachment of a category.

    Scene convention (matches the body autorig + Roblox's template bodies):
    Z up, front = -Y, character-left = +X.
    """
    cx = (bb_min.x + bb_max.x) / 2
    cy = (bb_min.y + bb_max.y) / 2
    mid_z = (bb_min.z + bb_max.z) / 2
    bottom_z = bb_min.z
    top_z = bb_max.z
    front_y = bb_min.y
    back_y = bb_max.y

    # Build a dict for every possible attachment we might need; the caller
    # picks the ones actually required by the category spec.
    return {
        # Hat / Hair: anchor at bottom-center where it meets the head.
        "HatAttachment":          Vector((cx, cy, bottom_z)),
        "HairAttachment":         Vector((cx, cy, bottom_z)),
        # Face accessories: front and center of the face mesh.
        "FaceFrontAttachment":    Vector((cx, front_y, mid_z)),
        "FaceCenterAttachment":   Vector((cx, cy, mid_z)),
        # Neck: top center.
        "NeckAttachment":         Vector((cx, cy, top_z)),
        # Shoulder: per-side approximations (character-left = +X).
        "LeftShoulderAttachment":  Vector((bb_max.x, cy, mid_z)),
        "RightShoulderAttachment": Vector((bb_min.x, cy, mid_z)),
        "LeftCollarAttachment":    Vector((bb_max.x, cy, top_z)),
        "RightCollarAttachment":   Vector((bb_min.x, cy, top_z)),
        # Torso accessories: front/back center.
        "BodyFrontAttachment":     Vector((cx, front_y, mid_z)),
        "BodyBackAttachment":      Vector((cx, back_y, mid_z)),
        # Waist: front/center/back.
        "WaistFrontAttachment":    Vector((cx, front_y, bottom_z)),
        "WaistCenterAttachment":   Vector((cx, cy, bottom_z)),
        "WaistBackAttachment":     Vector((cx, back_y, bottom_z)),
    }


def stamp_attachments(obj: bpy.types.Object, required_names: tuple[str, ...]) -> list[str]:
    """Create empty objects for each required attachment, parented to the mesh."""
    bb_min, bb_max = _world_bbox([obj])
    positions = attachment_positions(None, bb_min, bb_max)
    stamped: list[str] = []
    for name in required_names:
        # Skip if already exists (idempotent).
        if name in bpy.data.objects:
            continue
        pos = positions.get(name)
        if pos is None:
            # Fallback: place at bbox center.
            pos = Vector(((bb_min.x + bb_max.x) / 2,
                          (bb_min.y + bb_max.y) / 2,
                          (bb_min.z + bb_max.z) / 2))
        empty = bpy.data.objects.new(name=name, object_data=None)
        empty.empty_display_type = "PLAIN_AXES"
        empty.empty_display_size = 0.1
        empty.location = pos
        empty.parent = obj
        # Keep parent-transform-inverse so the empty stays where we set it.
        empty.matrix_parent_inverse = obj.matrix_world.inverted()
        bpy.context.scene.collection.objects.link(empty)
        stamped.append(name)
    return stamped


# ---------- export ------------------------------------------------------------

def export_fbx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Scene is 1 Blender unit = 1 stud. Roblox's Import 3D applies a x100
    # unit conversion to Blender FBXs with these flags (measured: a 1.8-stud
    # hat imported at Handle.Size 180), so bake 0.01 into the data and let
    # Roblox's x100 land the mesh at true stud size.
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=False,
        global_scale=0.01,
        apply_unit_scale=False,
        bake_space_transform=True,
        object_types={"MESH", "EMPTY", "ARMATURE"},
        add_leaf_bones=False,
        mesh_smooth_type="FACE",
        axis_forward="-Z",
        axis_up="Y",
        path_mode="COPY",
        embed_textures=True,
    )


# ---------- orchestration -----------------------------------------------------

def run_inplace(
    category: str,
    out_fbx: str | None = None,
    decimate_margin: float = 0.95,
    yaw_deg: float = 0.0,
    pitch_deg: float = 0.0,
    # Largest horizontal dimension in studs. A worn hat should relate to the
    # ~1.2-stud head, not fill the 3-stud legal box.
    target_size_studs: float = 1.8,
    bake: bool = False,
    bake_png: str | None = None,
    bake_resolution: int = 2048,
    texture_prompt: str | None = None,
    texture_source_image: str | None = None,
) -> dict:
    """Run the full auto-prep on whatever is currently in the Blender scene.

    Returns a dict log suitable for stashing alongside the run.
    """
    spec = _load_spec()
    if category not in spec.ACCESSORY_CATEGORIES:
        raise ValueError(f"Unknown accessory category: {category}")
    cat = spec.ACCESSORY_CATEGORIES[category]

    log: dict = {"category": category, "steps": {}}

    obj = join_meshes()
    if obj is None:
        raise RuntimeError("No mesh objects in scene to prep.")
    log["steps"]["join"] = f"joined into '{obj.name}'"

    # No automatic re-orientation: glTF imports land upright in Z-up, and
    # extent heuristics guess wrong on wider-than-tall items (hats). Use
    # --yaw for the photo-framing spin instead.
    log["steps"]["orient"] = "identity (use --yaw to spin about the vertical)"

    if yaw_deg or pitch_deg:
        # Single-image reconstructions keep the source photo's framing: the
        # face often points sideways (fix with yaw about the vertical) and a
        # photographed-from-above product shot bakes in a forward lean (fix
        # with pitch about X; positive pitches the front UP). Applied BEFORE
        # scaling/recentering/attachments so everything downstream sees the
        # final orientation.
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        if yaw_deg:
            obj.rotation_euler.rotate_axis("Z", math.radians(yaw_deg))
        if pitch_deg:
            obj.rotation_euler.rotate_axis("X", math.radians(pitch_deg))
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
        log["steps"]["yaw"] = f"yaw {yaw_deg} deg about Z, pitch {pitch_deg} deg about X"

    scale, ex_studs = scale_to_bbox(obj, cat.max_bounds, target_size_studs)
    log["steps"]["scale"] = {
        "factor": round(scale, 4),
        "target_size_studs": target_size_studs,
        "extents_studs_after": [round(ex_studs.x, 3), round(ex_studs.y, 3), round(ex_studs.z, 3)],
        "cap_studs": list(cat.max_bounds),
    }

    recenter(obj)
    log["steps"]["recenter"] = "moved bbox center to origin XY, bottom to Y=0"

    target_tris = int(cat.max_tris * decimate_margin)
    before, after = decimate_to(obj, target_tris)
    log["steps"]["decimate"] = {"before": before, "after": after, "target": target_tris}

    required = cat.attachment if isinstance(cat.attachment, tuple) else (cat.attachment,)
    stamped = stamp_attachments(obj, required)
    log["steps"]["attachments"] = {"required": list(required), "stamped": stamped}

    # Texture generation + projection bake takes precedence over plain bake when
    # a prompt or source image is provided.
    if texture_prompt or texture_source_image:
        from . import project_paint
        # Resolve the source image: prefer explicit override, else generate one.
        if texture_source_image:
            src_img = Path(texture_source_image)
        else:
            from ..texture_gen import generate_via_flux, prompt_for_accessory
            full_prompt = prompt_for_accessory(texture_prompt, category=category)
            log["steps"]["texture_prompt"] = full_prompt
            src_img_dst = Path(bake_png).with_name("sd_source.png") if bake_png else Path("sd_source.png")
            src_img = generate_via_flux(full_prompt, src_img_dst)
            log["steps"]["sd_source"] = str(src_img)
        if bake_png is None:
            raise ValueError("texture_prompt requires bake_png path for the output PNG")
        proj_log = project_paint.project_and_bake(
            obj, src_img, Path(bake_png), resolution=bake_resolution,
        )
        log["steps"]["texture_project_bake"] = proj_log
    elif bake:
        from . import bake as bake_mod
        if bake_png is None:
            raise ValueError("bake=True requires bake_png path")
        bake_log = bake_mod.bake_basecolor(obj, Path(bake_png), resolution=bake_resolution)
        log["steps"]["bake"] = bake_log

    if out_fbx:
        out_path = Path(out_fbx)
        export_fbx(out_path)
        log["steps"]["export"] = str(out_path)

    return log


# ---------- standalone entrypoint --------------------------------------------

def _parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--out", dest="dst", required=True)
    p.add_argument("--category", required=True)
    p.add_argument("--target-size", type=float, default=1.8,
                   help="Largest horizontal dimension in studs (wear size)")
    p.add_argument("--yaw", type=float, default=0.0,
                   help="Extra rotation (deg) about the vertical axis, e.g. 90 "
                        "when the reconstructed front faces sideways")
    p.add_argument("--pitch", type=float, default=0.0,
                   help="Extra rotation (deg) about X; positive pitches the "
                        "front up (counters photographed-from-above lean)")
    p.add_argument("--bake", action="store_true", help="Bake BaseColor into a PNG")
    p.add_argument("--bake-png", default=None,
                   help="Output PNG path (default: <out_dir>/basecolor.png)")
    p.add_argument("--bake-resolution", type=int, default=2048)
    return p.parse_args(argv)


def _import(path: Path) -> None:
    ext = path.suffix.lower()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False, confirm=False)
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


def main() -> int:
    args = _parse_args()
    _import(Path(args.src).resolve())
    bake_png = args.bake_png
    if args.bake and bake_png is None:
        bake_png = str(Path(args.dst).with_name("basecolor.png"))
    log = run_inplace(
        category=args.category,
        out_fbx=args.dst,
        yaw_deg=args.yaw,
        pitch_deg=args.pitch,
        target_size_studs=args.target_size,
        bake=args.bake,
        bake_png=bake_png,
        bake_resolution=args.bake_resolution,
    )
    import json
    print(json.dumps(log, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
