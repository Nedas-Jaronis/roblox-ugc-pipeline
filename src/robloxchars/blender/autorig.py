"""Auto-rig an arbitrary humanoid mesh onto a R15 template skeleton.

Pipeline (`run_inplace`):
    1. Fit the input mesh to the R15 template proportions
       (orient tallest axis up, uniform-scale to height ≈ 5 studs, center at
       origin with feet on the ground plane).
    2. Build the R15 template armature next to the mesh.
    3. Parent mesh -> armature with `Armature Auto Weights` so Blender's
       heat-diffusion algorithm seeds vertex weights.
    4. Split the mesh into named `<Bone>_Geo` pieces by each vertex's
       dominant bone weight.
    5. Re-parent every piece to the armature with an Armature modifier so
       skin weights survive export.
    6. Stamp the body `*_Att` attachment empties at the spec-defined positions
       on the right bones (Root_Att at world origin, Hat_Att on top of head,
       Grip_Atts on hands, etc.).
    7. Per-group decimation to the avatar tri budget (Head 4k, Torso group
       1.75k, each arm/leg group 1.248k).
    8. Export-friendly FBX with Y-up axes.

Single-pass — no iterative retopo, no inner cage generation. Designed to
get a generic humanoid mesh into shape for Roblox marketplace validation;
the user should still hand-tune skin weights at joints in Blender afterward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import bpy  # type: ignore
from mathutils import Vector  # type: ignore

from . import r15_armature as r15_mod


# --- spec data borrowed at import time (avoid late lookups in hot paths) ---

def _spec():
    """Lazy import so this module loads even outside the repo's src layout."""
    import importlib
    return importlib.import_module("robloxchars.roblox_spec")


# Map each R15 bone name to its `*_Geo` mesh name (the marketplace convention).
def _geo_name(bone_name: str) -> str:
    return f"{bone_name}_Geo"


# Bones that should NOT receive their own mesh segment. HumanoidRootPart is
# a logical reference, not a body part.
_NON_GEOMETRY_BONES: frozenset[str] = frozenset({"HumanoidRootPart"})


# Body-attachment placement: spec-mandated positions on a 5-stud template.
# For each (attachment_name, parent_bone), an offset from the bone HEAD
# in world space (Blender Z-up).
_BODY_ATTACHMENTS: tuple[tuple[str, str, tuple[float, float, float]], ...] = (
    # Head bone (head pos 0,0,4.0 ; tail 0,0,5.0).
    ("FaceCenter_Att",          "Head",      (0.0, 0.0, 0.5)),
    ("FaceFront_Att",           "Head",      (0.0, -0.5, 0.5)),
    ("Hat_Att",                 "Head",      (0.0, 0.0, 1.0)),
    ("Hair_Att",                "Head",      (0.0, 0.0, 1.0)),
    ("HatBack_Att",             "Head",      (0.0, 0.5, 1.0)),
    ("Mouth_Att",               "Head",      (0.0, -0.5, 0.3)),
    ("NeckRig_Att",             "Head",      (0.0, 0.0, 0.0)),

    # UpperTorso (head 0,0,3.0 ; tail 0,0,4.0).
    ("LeftCollar_Att",          "UpperTorso",(-0.5, 0.0, 1.0)),
    ("RightCollar_Att",         "UpperTorso",(0.5, 0.0, 1.0)),
    ("Neck_Att",                "UpperTorso",(0.0, 0.0, 1.0)),
    ("BodyFront_Att",           "UpperTorso",(0.0, -0.5, 0.5)),
    ("BodyBack_Att",            "UpperTorso",(0.0, 0.5, 0.5)),
    ("LeftShoulder_Att",        "UpperTorso",(-1.0, 0.0, 0.9)),
    ("RightShoulder_Att",       "UpperTorso",(1.0, 0.0, 0.9)),
    ("LeftShoulderRig_Att",     "UpperTorso",(-1.0, 0.0, 0.9)),
    ("RightShoulderRig_Att",    "UpperTorso",(1.0, 0.0, 0.9)),
    ("WaistRig_Att",            "UpperTorso",(0.0, 0.0, 0.0)),

    # LowerTorso (head 0,0,2.4 ; tail 0,0,3.0).
    ("Root_Att",                "LowerTorso",(0.0, 0.0, 0.0)),  # MUST be at origin (overridden post-hoc)
    ("WaistFront_Att",          "LowerTorso",(0.0, -0.4, 0.4)),
    ("WaistBack_Att",           "LowerTorso",(0.0, 0.4, 0.4)),
    ("WaistCenter_Att",         "LowerTorso",(0.0, 0.0, 0.4)),
    ("LeftHipRig_Att",          "LowerTorso",(-0.5, 0.0, 0.0)),
    ("RightHipRig_Att",         "LowerTorso",(0.5, 0.0, 0.0)),

    # Hands.
    ("LeftGrip_Att",            "LeftHand",  (0.0, 0.0, 0.0)),
    ("LeftWristRig_Att",        "LeftHand",  (0.0, 0.0, 0.4)),
    ("RightGrip_Att",           "RightHand", (0.0, 0.0, 0.0)),
    ("RightWristRig_Att",       "RightHand", (0.0, 0.0, 0.4)),

    # Feet.
    ("LeftFoot_Att",            "LeftFoot",  (0.0, 0.0, 0.0)),
    ("LeftAnkleRig_Att",        "LeftFoot",  (0.0, 0.0, 0.0)),
    ("RightFoot_Att",           "RightFoot", (0.0, 0.0, 0.0)),
    ("RightAnkleRig_Att",       "RightFoot", (0.0, 0.0, 0.0)),
)


# --- helpers ---------------------------------------------------------------

def _world_bbox(obj) -> tuple[Vector, Vector]:
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [v.x for v in corners]; ys = [v.y for v in corners]; zs = [v.z for v in corners]
    return Vector((min(xs), min(ys), min(zs))), Vector((max(xs), max(ys), max(zs)))


def _extents(obj) -> Vector:
    a, b = _world_bbox(obj)
    return b - a


# --- step 1: fit mesh to template ------------------------------------------

def fit_mesh_to_template(mesh: bpy.types.Object, target_height: float = 5.0) -> dict:
    """Orient + uniform-scale + center the mesh to fit the R15 template.

    Heuristic: the mesh's tallest world-axis becomes Z (Blender up). Then it
    is uniformly scaled so the Z extent equals `target_height` and translated
    so the bbox bottom touches Z = 0 and bbox center lies on the world axis.
    """
    log: dict = {}

    # Unparent + bake any existing transforms so subsequent math works in world space.
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    if mesh.parent is not None:
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    # Pick the world-axis with the largest extent as "up", rotate to Z.
    ex = _extents(mesh)
    tallest = max(range(3), key=lambda i: ex[i])
    import math
    if tallest == 0:    # X tallest -> rotate -90 about Y so X becomes Z
        bpy.ops.transform.rotate(value=-math.pi / 2, orient_axis="Y")
        log["orient"] = "rotated -90° about Y (X was tallest)"
    elif tallest == 1:  # Y tallest -> rotate +90 about X so Y becomes Z
        bpy.ops.transform.rotate(value=math.pi / 2, orient_axis="X")
        log["orient"] = "rotated +90° about X (Y was tallest)"
    else:
        log["orient"] = "no rotation (Z already tallest)"
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    # Uniform scale so Z extent equals target_height.
    ex = _extents(mesh)
    if ex.z > 1e-6:
        s = target_height / ex.z
        bpy.ops.transform.resize(value=(s, s, s))
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        log["scale_factor"] = round(s, 4)

    # Center bbox at X=Y=0, base at Z=0.
    bb_min, bb_max = _world_bbox(mesh)
    cx = (bb_min.x + bb_max.x) / 2
    cy = (bb_min.y + bb_max.y) / 2
    mesh.location.x -= cx
    mesh.location.y -= cy
    mesh.location.z -= bb_min.z
    bpy.context.view_layer.update()
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

    ex = _extents(mesh)
    log["extents_after"] = [round(ex.x, 3), round(ex.y, 3), round(ex.z, 3)]
    return log


# --- step 2 + 3: build armature + proximity-based weighting --------------

def _closest_point_on_segment(p: Vector, a: Vector, b: Vector) -> Vector:
    ab = b - a
    ab_len_sq = ab.dot(ab)
    if ab_len_sq < 1e-12:
        return a
    t = (p - a).dot(ab) / ab_len_sq
    t = max(0.0, min(1.0, t))
    return a + ab * t


def assign_weights_by_proximity(
    mesh: bpy.types.Object,
    armature: bpy.types.Object,
) -> dict[str, int]:
    """Assign each vertex weight=1.0 to the nearest bone segment (head→tail).

    Skipping HumanoidRootPart (it's a reference, no geometry). Returns a
    histogram of vertex counts per bone for the run log.
    """
    bones = [b for b in armature.data.bones if b.name not in _NON_GEOMETRY_BONES]
    # Bone segments in world space.
    arm_world = armature.matrix_world
    segments: list[tuple[str, Vector, Vector]] = []
    for b in bones:
        head_w = arm_world @ b.head_local
        tail_w = arm_world @ b.tail_local
        segments.append((b.name, head_w, tail_w))

    # Ensure a vertex group per bone (the Armature modifier will use these).
    for name, _, _ in segments:
        if name not in mesh.vertex_groups:
            mesh.vertex_groups.new(name=name)

    histogram: dict[str, int] = {name: 0 for name, _, _ in segments}
    mesh_world = mesh.matrix_world
    for v_idx, v in enumerate(mesh.data.vertices):
        p = mesh_world @ v.co
        best_name = segments[0][0]
        best_d2 = float("inf")
        for name, h, t in segments:
            cp = _closest_point_on_segment(p, h, t)
            d2 = (p - cp).length_squared
            if d2 < best_d2:
                best_d2 = d2
                best_name = name
        mesh.vertex_groups[best_name].add([v_idx], 1.0, "REPLACE")
        histogram[best_name] += 1

    # Attach an Armature modifier so the rig deforms the mesh.
    has_mod = any(m.type == "ARMATURE" for m in mesh.modifiers)
    if not has_mod:
        amod = mesh.modifiers.new(name="rc_armature", type="ARMATURE")
        amod.object = armature
    mesh.parent = armature
    return histogram


def fit_template_widths_to_mesh(
    armature: bpy.types.Object,
    mesh: bpy.types.Object,
    shoulder_inset: float = 0.10,
    hip_width_ratio: float = 0.45,
) -> dict:
    """Move arm/leg bones inward so they actually lie inside the mesh silhouette.

    The standard template has shoulders at ±1.0 and hips at ±0.5, sized for a
    classic Roblox blocky body (~2 studs wide). Real-world humanoid meshes are
    usually narrower (~1-1.4 studs at chest). If we leave the template alone,
    proximity-based weighting puts all the arm/leg verts into the central
    torso bones.

    Strategy: measure the mesh's actual half-width at shoulder Z, then move
    the arm bones to (mesh_half_width - shoulder_inset). For hips, use a
    fraction of the same.
    """
    spec = _spec()  # not strictly needed here, but symmetric with other fns
    proportions = r15_mod.proportions()

    # Sample mesh vertices in a Z slab around shoulder height to estimate
    # shoulder half-width. Use world-space.
    H = mesh.matrix_world
    shoulder_z = proportions.shoulder_z
    band = 0.4  # studs
    xs_at_shoulder = []
    for v in mesh.data.vertices:
        wp = H @ v.co
        if shoulder_z - band <= wp.z <= shoulder_z + band:
            xs_at_shoulder.append(wp.x)
    if not xs_at_shoulder:
        return {"shoulder_x": None, "hip_x": None, "skipped": "no verts in shoulder band"}
    measured_half_width = max(abs(min(xs_at_shoulder)), abs(max(xs_at_shoulder)))
    new_shoulder_x = max(0.2, measured_half_width - shoulder_inset)
    new_hip_x = max(0.15, measured_half_width * hip_width_ratio)

    # Edit-mode tweak of bone X positions on both sides.
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    ebs = armature.data.edit_bones
    for side, sign in (("Left", -1), ("Right", 1)):
        for bn in (f"{side}UpperArm", f"{side}LowerArm", f"{side}Hand"):
            eb = ebs[bn]
            eb.head.x = sign * new_shoulder_x
            eb.tail.x = sign * new_shoulder_x
        for bn in (f"{side}UpperLeg", f"{side}LowerLeg", f"{side}Foot"):
            eb = ebs[bn]
            eb.head.x = sign * new_hip_x
            eb.tail.x = sign * new_hip_x
    bpy.ops.object.mode_set(mode="OBJECT")
    return {"shoulder_x": round(new_shoulder_x, 3), "hip_x": round(new_hip_x, 3),
            "measured_half_width": round(measured_half_width, 3)}


def attach_armature(
    mesh: bpy.types.Object, scale: float = 1.0,
) -> tuple[bpy.types.Object, dict[str, int], dict]:
    """Build R15 template, fit limb widths to mesh, weight by nearest bone."""
    arm = r15_mod.build_r15_armature(scale=scale)
    fit_log = fit_template_widths_to_mesh(arm, mesh)
    histogram = assign_weights_by_proximity(mesh, arm)
    return arm, histogram, fit_log


# --- step 4: split mesh by dominant bone weight ---------------------------

def split_mesh_by_bone(
    mesh: bpy.types.Object,
    armature: bpy.types.Object,
) -> dict[str, bpy.types.Object]:
    """Separate `mesh` into one object per R15 bone, named '<Bone>_Geo'.

    Each vertex is assigned to the bone with the highest weight on it.
    Vertices whose dominant bone is HumanoidRootPart (or has no weights)
    are reassigned to LowerTorso so they don't get dropped.
    """
    # Build a per-vertex "dominant bone name" mapping.
    vgroups = mesh.vertex_groups
    bone_names = [b.name for b in armature.data.bones if b.name not in _NON_GEOMETRY_BONES]
    fallback = "LowerTorso"
    vertex_count = len(mesh.data.vertices)
    dominant = [fallback] * vertex_count
    # vgroups don't index by vertex; we walk vertex.groups for each vertex.
    for v_idx, v in enumerate(mesh.data.vertices):
        best_name = fallback
        best_w = 0.0
        for g in v.groups:
            try:
                name = vgroups[g.group].name
            except IndexError:
                continue
            if name in _NON_GEOMETRY_BONES:
                continue
            if name not in bone_names:
                continue
            if g.weight > best_w:
                best_w = g.weight
                best_name = name
        dominant[v_idx] = best_name

    # For each bone, build a vertex-index set; select those verts and separate.
    by_bone: dict[str, list[int]] = {b: [] for b in bone_names}
    for v_idx, name in enumerate(dominant):
        by_bone.setdefault(name, []).append(v_idx)

    # Strategy: rather than `bpy.ops.mesh.separate` repeatedly (which is slow
    # and re-creates objects), we duplicate the mesh once per bone, then keep
    # only that bone's faces on each duplicate. Faster + cleaner.
    base_mesh = mesh.data
    base_name = mesh.name

    # Build a vert -> dominant_name lookup as a set per bone for fast checks.
    bone_to_verts: dict[str, set[int]] = {b: set(idxs) for b, idxs in by_bone.items()}

    pieces: dict[str, bpy.types.Object] = {}
    for bone in bone_names:
        verts_for_bone = bone_to_verts.get(bone, set())
        if not verts_for_bone:
            continue
        # Duplicate the mesh object.
        bpy.ops.object.select_all(action="DESELECT")
        mesh.select_set(True)
        bpy.context.view_layer.objects.active = mesh
        bpy.ops.object.duplicate(linked=False)
        piece = bpy.context.view_layer.objects.active
        piece.name = _geo_name(bone)
        piece.data.name = piece.name

        # In edit mode, select faces whose vertices are mostly in `verts_for_bone`,
        # invert selection, delete the rest.
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.object.mode_set(mode="OBJECT")

        # Mark polygon selection by majority vote of its vertices.
        for poly in piece.data.polygons:
            in_set = sum(1 for vi in poly.vertices if vi in verts_for_bone)
            poly.select = in_set > (len(poly.vertices) // 2)

        bpy.ops.object.mode_set(mode="EDIT")
        # Invert selection -> delete (keep only this bone's polys).
        bpy.ops.mesh.select_all(action="INVERT")
        bpy.ops.mesh.delete(type="FACE")
        bpy.ops.object.mode_set(mode="OBJECT")

        pieces[bone] = piece

    # Now delete the original combined mesh — we've cloned all needed segments.
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.ops.object.delete(use_global=False)
    return pieces


# --- step 5: re-parent each piece to armature -----------------------------

def unwrap_pieces(pieces: dict[str, bpy.types.Object]) -> None:
    """Smart-UV-Project each piece so its UVs don't overlap.

    The original joined mesh's UV map often has different body regions
    overlapping in UV space (Sketchfab multi-piece models commonly suffer
    from this). Each split piece inherits that overlapping layout, and
    baking writes one body part's texture onto another's UV island.
    Re-unwrapping fresh per-piece guarantees each piece occupies a unique
    region of its own texture sheet.
    """
    for piece in pieces.values():
        bpy.ops.object.select_all(action="DESELECT")
        piece.select_set(True)
        bpy.context.view_layer.objects.active = piece
        # Drop existing UV layers to start clean.
        while piece.data.uv_layers:
            piece.data.uv_layers.remove(piece.data.uv_layers[0])
        # Smart UV Project creates a fresh layer.
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=66.0, island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")


def reattach_pieces_to_armature(
    pieces: dict[str, bpy.types.Object],
    armature: bpy.types.Object,
) -> None:
    """Ensure every *_Geo piece has an Armature modifier referencing the rig."""
    for piece in pieces.values():
        # Pieces are duplicates of the original mesh; they already have an
        # Armature modifier from the parent_set. Ensure it points to our rig.
        found = False
        for mod in piece.modifiers:
            if mod.type == "ARMATURE":
                mod.object = armature
                found = True
                break
        if not found:
            mod = piece.modifiers.new(name="rc_armature", type="ARMATURE")
            mod.object = armature
        # Parent in the Outliner sense (visual organization).
        piece.parent = armature


# --- step 6: stamp body attachments ---------------------------------------

def stamp_body_attachments(armature: bpy.types.Object) -> list[str]:
    """Create the standard R15 body Attachment empties at spec positions.

    Each empty is parented to its bone via `parent_set(type='BONE',
    keep_transform=True)` so Blender computes matrix_parent_inverse for us.
    Without keep_transform, bone-parented empties snap to the bone tail
    (Blender quirk) and end up offset from the intended spec position.
    """
    from mathutils import Matrix  # type: ignore
    stamped: list[str] = []
    # First pass: build all empties at their world positions, unparented.
    for att_name, bone_name, offset in _BODY_ATTACHMENTS:
        if att_name in bpy.data.objects:
            continue
        bone_head_world = r15_mod.bone_world_position(armature, bone_name, "head")
        world_pos = bone_head_world + Vector(offset)
        if att_name == "Root_Att":  # spec: must lie at world origin
            world_pos = Vector((0.0, 0.0, 0.0))

        empty = bpy.data.objects.new(name=att_name, object_data=None)
        empty.empty_display_type = "PLAIN_AXES"
        empty.empty_display_size = 0.1
        bpy.context.scene.collection.objects.link(empty)
        empty.matrix_world = Matrix.Translation(world_pos)
        stamped.append(att_name)

    bpy.context.view_layer.update()

    # Second pass: parent each empty to its bone with keep_transform.
    for att_name, bone_name, _offset in _BODY_ATTACHMENTS:
        if att_name not in bpy.data.objects:
            continue
        empty = bpy.data.objects[att_name]
        # Select empty as child, armature as active parent; set active bone.
        bpy.ops.object.select_all(action="DESELECT")
        empty.select_set(True)
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature
        armature.data.bones.active = armature.data.bones[bone_name]
        bpy.ops.object.parent_set(type="BONE", keep_transform=True)
    return stamped


# --- step 7: per-group decimation -----------------------------------------

def decimate_per_group(pieces: dict[str, bpy.types.Object]) -> dict:
    """Decimate each body group to fit its tri budget."""
    spec = _spec()
    log: dict = {}
    for group_name, members in spec.BODY_GROUP_MEMBERS.items():
        budget = spec.BODY_GROUP_TRI_BUDGET[group_name]
        objs = []
        for mesh_name in members:
            # mesh_name is like "Head_Geo"; the bone is "Head".
            bone_name = mesh_name[:-len("_Geo")]
            if bone_name in pieces:
                objs.append(pieces[bone_name])
        if not objs:
            continue
        total_tris = sum(_count_tris(o) for o in objs)
        log[group_name] = {"before": total_tris, "budget": budget}
        if total_tris <= budget:
            log[group_name]["after"] = total_tris
            continue
        ratio = max(0.01, budget / total_tris)
        for o in objs:
            mod = o.modifiers.new(name="rc_decimate", type="DECIMATE")
            mod.decimate_type = "COLLAPSE"
            mod.ratio = ratio
            bpy.ops.object.select_all(action="DESELECT")
            o.select_set(True)
            bpy.context.view_layer.objects.active = o
            bpy.ops.object.modifier_apply(modifier=mod.name)
        after = sum(_count_tris(o) for o in objs)
        log[group_name]["after"] = after
        log[group_name]["ratio"] = round(ratio, 4)
    return log


def _count_tris(obj: bpy.types.Object) -> int:
    m = obj.to_mesh()
    try:
        m.calc_loop_triangles()
        return len(m.loop_triangles)
    finally:
        obj.to_mesh_clear()


# --- step 8: export --------------------------------------------------------

def export_fbx(armature: bpy.types.Object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=False,
        apply_unit_scale=True,
        bake_space_transform=True,
        object_types={"MESH", "EMPTY", "ARMATURE"},
        add_leaf_bones=False,
        mesh_smooth_type="FACE",
        axis_forward="-Z",
        axis_up="Y",
        path_mode="COPY",
        embed_textures=True,
        primary_bone_axis="Y",
        secondary_bone_axis="X",
    )


# --- orchestration ---------------------------------------------------------

@dataclass
class AutoRigResult:
    log: dict = field(default_factory=dict)
    armature_name: str = ""
    pieces: dict[str, str] = field(default_factory=dict)  # bone -> obj name


def run_inplace(
    mesh_name: str | None = None,
    target_height: float = 5.0,
    decimate: bool = True,
    out_fbx: str | None = None,
    texture_prompt: str | None = None,
    texture_source_image: str | None = None,
    texture_dir: str | None = None,
    texture_resolution: int = 2048,
    multi_view: bool = False,
    multi_view_sources: dict[str, str] | None = None,
) -> AutoRigResult:
    """Run the full auto-rig on the named mesh (or the only mesh in scene)."""
    if mesh_name is None:
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        if len(meshes) != 1:
            raise RuntimeError(
                f"Specify mesh_name; scene has {len(meshes)} meshes."
            )
        mesh = meshes[0]
    else:
        mesh = bpy.data.objects[mesh_name]

    log: dict = {}
    log["fit"] = fit_mesh_to_template(mesh, target_height=target_height)
    armature, weight_hist, width_fit = attach_armature(mesh, scale=target_height / 5.0)
    log["armature"] = armature.name
    log["template_width_fit"] = width_fit
    log["weights_per_bone"] = weight_hist
    pieces = split_mesh_by_bone(mesh, armature)
    reattach_pieces_to_armature(pieces, armature)
    log["pieces"] = {bone: o.name for bone, o in pieces.items()}
    log["attachments_stamped"] = stamp_body_attachments(armature)
    if decimate:
        log["decimate"] = decimate_per_group(pieces)

    if texture_prompt or texture_source_image or multi_view_sources:
        from . import project_paint
        out_dir = Path(texture_dir) if texture_dir else Path("runs/autorig/textures")
        out_dir.mkdir(parents=True, exist_ok=True)
        # Fresh Smart-UV per piece so source-mesh UV overlaps don't bleed
        # one part's color into another's texture sheet.
        unwrap_pieces(pieces)
        log["fresh_uv_unwrap"] = True

        if multi_view_sources:
            # Caller pre-generated per-view SD images.
            sources = {v: Path(p) for v, p in multi_view_sources.items()}
            log["multi_view_sources"] = {k: str(v) for k, v in sources.items()}
            log["texture_bake"] = project_paint.bake_pieces_multi_view(
                list(pieces.values()), sources, out_dir,
                resolution=texture_resolution,
            )
        elif multi_view:
            # Generate the 4 view images on host.
            from ..texture_gen import generate_multi_view
            if not texture_prompt:
                raise ValueError("multi_view requires texture_prompt")
            sources = generate_multi_view(texture_prompt, out_dir,
                                          width=768, height=1024)
            log["multi_view_sources"] = {k: str(v) for k, v in sources.items()}
            log["texture_bake"] = project_paint.bake_pieces_multi_view(
                list(pieces.values()), sources, out_dir,
                resolution=texture_resolution,
            )
        else:
            # Single-view path (legacy).
            if texture_source_image:
                src_img = Path(texture_source_image)
            else:
                from ..texture_gen import generate_via_flux, prompt_for_accessory
                full_prompt = prompt_for_accessory(texture_prompt, category="avatar")
                log["texture_prompt"] = full_prompt
                src_img_dst = out_dir / "sd_source.png"
                src_img = generate_via_flux(full_prompt, src_img_dst, width=1024, height=1024)
                log["sd_source"] = str(src_img)
            log["texture_bake"] = project_paint.bake_pieces_from_shared_camera(
                list(pieces.values()),
                src_img,
                out_dir,
                resolution=texture_resolution,
                view_axis="-Y",
            )
        _attach_baked_textures_to_pieces(pieces, out_dir)

    if out_fbx:
        export_fbx(armature, Path(out_fbx))
        log["export"] = out_fbx
    return AutoRigResult(log=log, armature_name=armature.name, pieces={b: o.name for b, o in pieces.items()})


def _attach_baked_textures_to_pieces(pieces: dict[str, bpy.types.Object], tex_dir: Path) -> None:
    """For each piece, replace its materials with a clean Principled BSDF
    sampling the baked PNG via the primary UV layer."""
    for bone_name, piece in pieces.items():
        png_path = tex_dir / f"{piece.name}_basecolor.png"
        if not png_path.exists():
            continue
        image = bpy.data.images.load(str(png_path), check_existing=True)
        mat = bpy.data.materials.new(name=f"{piece.name}_Mat")
        mat.use_nodes = True
        nt = mat.node_tree
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        out_n = nt.nodes.new("ShaderNodeOutputMaterial"); out_n.location = (300, 0)
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled"); bsdf.location = (0, 0)
        tex = nt.nodes.new("ShaderNodeTexImage");        tex.location = (-300, 0)
        tex.image = image
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        nt.links.new(bsdf.outputs["BSDF"], out_n.inputs["Surface"])
        piece.data.materials.clear()
        piece.data.materials.append(mat)
