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
    return importlib.import_module("roblox_ugc_pipeline.roblox_spec")


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
    # Shoulder/hip markers anchor to the FITTED limb bones (offset 0), not
    # template-fixed X offsets: Studio derives the limb pose angle from the
    # shoulder->grip / hip->foot attachment vectors, and a shoulder stamped
    # 1.0 studs out while the fitted arm sits at ±0.4 reads as a bent arm
    # (-155 deg "must be I/A/T pose") no matter what the mesh looks like.
    ("LeftShoulder_Att",        "LeftUpperArm", (0.0, 0.0, 0.0)),
    ("RightShoulder_Att",       "RightUpperArm",(0.0, 0.0, 0.0)),
    ("LeftShoulderRig_Att",     "LeftUpperArm", (0.0, 0.0, 0.0)),
    ("RightShoulderRig_Att",    "RightUpperArm",(0.0, 0.0, 0.0)),
    ("WaistRig_Att",            "UpperTorso",(0.0, 0.0, 0.0)),

    # LowerTorso (head 0,0,2.4 ; tail 0,0,3.0).
    ("Root_Att",                "LowerTorso",(0.0, 0.0, 0.0)),  # MUST be at origin (overridden post-hoc)
    ("WaistFront_Att",          "LowerTorso",(0.0, -0.4, 0.4)),
    ("WaistBack_Att",           "LowerTorso",(0.0, 0.4, 0.4)),
    ("WaistCenter_Att",         "LowerTorso",(0.0, 0.0, 0.4)),
    ("LeftHipRig_Att",          "LeftUpperLeg", (0.0, 0.0, 0.0)),
    ("RightHipRig_Att",         "RightUpperLeg",(0.0, 0.0, 0.0)),

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

def fit_mesh_to_template(mesh: bpy.types.Object, target_height_studs: float = 5.0) -> dict:
    """Orient + uniform-scale + center the mesh to fit the R15 template.

    `target_height_studs` is in Roblox studs (the R15 default character is
    ~5.4 studs tall). Internally converted to meters at 0.28 m/stud, since
    Blender's world coordinates are in meters.
    """
    log: dict = {}

    # Unparent + bake any existing transforms so subsequent math works in world space.
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    if mesh.parent is not None:
        bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    # Decide orientation. For a humanoid in T-pose, the arms-span axis can be
    # AS WIDE as the body height. So "tallest axis" alone isn't reliable;
    # treat Z as already-up if it's within 85% of the largest extent.
    ex = _extents(mesh)
    largest = max(ex.x, ex.y, ex.z)
    import math
    if ex.z >= 0.85 * largest:
        log["orient"] = (
            f"no rotation (Z already-up: ex={tuple(round(v,3) for v in ex)})"
        )
    else:
        # Z is clearly NOT the up axis — pick whichever of X/Y is taller.
        if ex.x >= ex.y:
            bpy.ops.transform.rotate(value=-math.pi / 2, orient_axis="Y")
            log["orient"] = (
                f"rotated -90° about Y (X was tallest; ex={tuple(round(v,3) for v in ex)})"
            )
        else:
            bpy.ops.transform.rotate(value=math.pi / 2, orient_axis="X")
            log["orient"] = (
                f"rotated +90° about X (Y was tallest; ex={tuple(round(v,3) for v in ex)})"
            )
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)

    # Build at NATIVE STUD SCALE: 1 Blender unit == 1 stud. Roblox reads raw FBX
    # coords as studs, so building in studs (not meters) + a clean unit-less export
    # lands the avatar at its true size with NO global_scale trickery (which baked a
    # scaled armature and broke Avatar Setup's attachment generation).
    target_height_units = target_height_studs
    ex = _extents(mesh)
    if ex.z > 1e-6:
        s = target_height_units / ex.z
        bpy.ops.transform.resize(value=(s, s, s))
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        log["scale_factor"] = round(s, 4)
        log["target_height_studs"] = target_height_studs

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
    top_k: int = 3,
    # power 2 blended so broadly that posing the arms dragged big flaps of
    # side-torso fabric along (winged silhouette + extra validation warnings);
    # power 4 keeps blending at true joints but lets the nearest bone dominate.
    falloff_power: float = 4.0,
) -> dict[str, int]:
    """Assign each vertex multi-bone falloff weights for smooth joint deformation.

    For each vertex:
      1. Compute distance to every bone segment (head->tail).
      2. Take the `top_k` nearest bones (capped at Roblox's max of 4
         influences per vertex).
      3. Convert distances to weights via inverse-distance with `falloff_power`
         exponent: w_i = 1 / (d_i + eps)^power, then normalize so weights
         sum to 1.

    This gives smooth blending at joints (e.g. a vertex near the elbow may end
    up 0.6 LowerArm + 0.4 UpperArm). For splitting the mesh into per-bone
    pieces later, the DOMINANT bone (highest weight) is used.

    Returns a histogram of vertex counts per dominant bone.
    """
    top_k = max(1, min(int(top_k), 4))  # Roblox caps influences/vertex at 4.

    bones = [b for b in armature.data.bones if b.name not in _NON_GEOMETRY_BONES]
    arm_world = armature.matrix_world
    segments: list[tuple[str, Vector, Vector]] = []
    for b in bones:
        head_w = arm_world @ b.head_local
        tail_w = arm_world @ b.tail_local
        segments.append((b.name, head_w, tail_w))

    for name, _, _ in segments:
        if name not in mesh.vertex_groups:
            mesh.vertex_groups.new(name=name)

    histogram: dict[str, int] = {name: 0 for name, _, _ in segments}
    eps = 1e-4
    mesh_world = mesh.matrix_world
    for v_idx, v in enumerate(mesh.data.vertices):
        p = mesh_world @ v.co
        per_bone_distance: list[tuple[str, float]] = []
        for name, h, t in segments:
            cp = _closest_point_on_segment(p, h, t)
            per_bone_distance.append((name, (p - cp).length))
        per_bone_distance.sort(key=lambda x: x[1])
        top = per_bone_distance[:top_k]
        raw_weights = [(name, 1.0 / (d + eps) ** falloff_power) for name, d in top]
        total = sum(w for _, w in raw_weights) or 1.0
        for name, w in raw_weights:
            mesh.vertex_groups[name].add([v_idx], w / total, "REPLACE")
        # Dominant = top entry by inverse distance (= shortest distance).
        histogram[top[0][0]] += 1

    has_mod = any(m.type == "ARMATURE" for m in mesh.modifiers)
    if not has_mod:
        amod = mesh.modifiers.new(name="rc_armature", type="ARMATURE")
        amod.object = armature
    mesh.parent = armature
    return histogram


def fit_template_widths_to_mesh(
    armature: bpy.types.Object,
    mesh: bpy.types.Object,
    shoulder_inset_ratio: float = 0.10,
    hip_width_ratio: float = 0.45,
) -> dict:
    """Move arm/leg bones inward so they actually lie inside the mesh silhouette.

    Strategy: measure the mesh's actual half-width in a slab around shoulder
    height (78% up the mesh), then move the arm bones to
    (mesh_half_width * (1 - shoulder_inset_ratio)). For hips, use a fraction
    of the same. All math is unit-agnostic — works whether the mesh and
    armature are in meters or studs.
    """
    bb_min, bb_max = _world_bbox(mesh)
    mesh_height = bb_max.z - bb_min.z
    if mesh_height <= 1e-6:
        return {"skipped": "mesh has zero height"}
    # Shoulder height ~78% up the mesh; sample within an 8%-height band.
    shoulder_z = bb_min.z + 0.78 * mesh_height
    band = 0.08 * mesh_height

    H = mesh.matrix_world
    xs_at_shoulder = []
    for v in mesh.data.vertices:
        wp = H @ v.co
        if shoulder_z - band <= wp.z <= shoulder_z + band:
            xs_at_shoulder.append(wp.x)
    if not xs_at_shoulder:
        return {"skipped": "no verts in shoulder band"}
    measured_half_width = max(abs(min(xs_at_shoulder)), abs(max(xs_at_shoulder)))
    new_shoulder_x = max(0.05 * mesh_height,
                         measured_half_width * (1 - shoulder_inset_ratio))
    new_hip_x = max(0.04 * mesh_height, measured_half_width * hip_width_ratio)

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
    return {"shoulder_x": round(new_shoulder_x, 4),
            "hip_x": round(new_hip_x, 4),
            "measured_half_width": round(measured_half_width, 4),
            "mesh_height": round(mesh_height, 4)}


def attach_armature(
    mesh: bpy.types.Object, scale: float = 1.0,
) -> tuple[bpy.types.Object, dict[str, int], dict]:
    """Build R15 template, fit limb widths to mesh, weight by nearest bone."""
    arm = r15_mod.build_r15_armature(scale=scale)
    fit_log = fit_template_widths_to_mesh(arm, mesh)
    histogram = assign_weights_by_proximity(mesh, arm)
    return arm, histogram, fit_log


# --- step 3.5: A-pose ------------------------------------------------------

def _rotate_pose_bone_world(armature, bone_name: str, angle_rad: float) -> None:
    """Rotate a pose bone (and thus its whole chain) about world +Y at its head.

    Rotation about +Y by a POSITIVE angle swings a down-pointing limb toward
    -X (the character's left); negative angles swing toward +X (right).
    """
    from mathutils import Matrix  # type: ignore
    pb = armature.pose.bones[bone_name]
    head_world = armature.matrix_world @ pb.head
    R = (Matrix.Translation(head_world)
         @ Matrix.Rotation(angle_rad, 4, "Y")
         @ Matrix.Translation(-head_world))
    pb.matrix = armature.matrix_world.inverted() @ R @ armature.matrix_world @ pb.matrix
    bpy.context.view_layer.update()


def _angle_from_down(armature, top_bone: str, end_bone: str) -> float:
    """Degrees between the limb chain (top head -> end tail) and straight down."""
    import math
    v = (r15_mod.bone_world_position(armature, end_bone, "tail")
         - r15_mod.bone_world_position(armature, top_bone, "head"))
    if v.length < 1e-9:
        return 0.0
    return math.degrees(v.normalized().angle(Vector((0.0, 0.0, -1.0))))


def pose_to_a_pose(
    armature: bpy.types.Object,
    mesh: bpy.types.Object,
    # 30 deg is still comfortably inside the accepted A-pose band while moving
    # far less fabric on chunky bodies than 45 did.
    arm_angle_deg: float = 30.0,
    leg_angle_deg: float = 6.0,
) -> dict:
    """Swing the arms out to ~45° and spread the legs slightly, then bake that
    as the new REST pose (mesh geometry follows, skinning stays intact).

    Marketplace validation only accepts I/A/T poses (arms within -100..30° of
    horizontal; legs near vertical), and Avatar Auto-Setup wants "an upright
    A-pose or T-Pose" with limbs not overlapping from the front — an A-pose
    satisfies both. Must run while the mesh is still ONE object with a live
    Armature modifier (i.e. between attach_armature and split_mesh_by_bone).
    """
    import math
    log: dict = {"before": {
        "left_arm_deg": round(_angle_from_down(armature, "LeftUpperArm", "LeftHand"), 1),
        "right_arm_deg": round(_angle_from_down(armature, "RightUpperArm", "RightHand"), 1),
    }}

    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="POSE")
    _rotate_pose_bone_world(armature, "LeftUpperArm", math.radians(arm_angle_deg))
    _rotate_pose_bone_world(armature, "RightUpperArm", -math.radians(arm_angle_deg))
    _rotate_pose_bone_world(armature, "LeftUpperLeg", math.radians(leg_angle_deg))
    _rotate_pose_bone_world(armature, "RightUpperLeg", -math.radians(leg_angle_deg))
    bpy.ops.object.mode_set(mode="OBJECT")

    # Bake the posed deformation into the mesh while KEEPING skinning: duplicate
    # the armature modifier, apply the original (freezes the current pose into
    # the verts), keep the copy bound to the armature.
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh
    arm_mod = next(m for m in mesh.modifiers if m.type == "ARMATURE")
    keep = mesh.modifiers.new(name="rc_armature_keep", type="ARMATURE")
    keep.object = arm_mod.object
    bpy.ops.object.modifier_apply(modifier=arm_mod.name)

    # Make the current pose the new rest pose; the kept modifier now evaluates
    # to zero additional deformation against it.
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.pose.armature_apply(selected=False)
    bpy.ops.object.mode_set(mode="OBJECT")

    log["after"] = {
        "left_arm_deg": round(_angle_from_down(armature, "LeftUpperArm", "LeftHand"), 1),
        "right_arm_deg": round(_angle_from_down(armature, "RightUpperArm", "RightHand"), 1),
        "left_leg_deg": round(_angle_from_down(armature, "LeftUpperLeg", "LeftFoot"), 1),
        "right_leg_deg": round(_angle_from_down(armature, "RightUpperLeg", "RightFoot"), 1),
    }
    return log


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

    # Re-bucket hip-band fabric so each LEG part fits its bbox cap. On wide
    # organic bodies (onesies, skirts) the proximity weights pull side-belly
    # fabric into the legs, blowing the Leg X<=1.5 cap and making the body
    # scale-INFEASIBLE (Studio: "no valid scale passes ..."). Bucketing those
    # same faces as LowerTorso keeps the silhouette pixel-identical while every
    # part's bbox fits — parts are buckets, not geometry edits.
    mesh_world = mesh.matrix_world
    hip_z = {s: (r15_mod.bone_world_position(armature, f"{s}UpperLeg", "head")).z
             for s in ("Left", "Right")}
    wx = [0.0] * vertex_count
    wz = [0.0] * vertex_count
    z_lo = z_hi = None
    for v_idx, v in enumerate(mesh.data.vertices):
        w = mesh_world @ v.co
        wx[v_idx], wz[v_idx] = w.x, w.z
        z_lo = w.z if z_lo is None else min(z_lo, w.z)
        z_hi = w.z if z_hi is None else max(z_hi, w.z)
    rebucketed = 0
    for v_idx, name in enumerate(dominant):
        if "Leg" in name or name.endswith("Foot"):
            side = "Left" if name.startswith("Left") else "Right"
            if wz[v_idx] > hip_z[side]:
                dominant[v_idx] = "LowerTorso"
                rebucketed += 1
    # Backstop: the feasibility condition Leg * scale <= cap with scale >=
    # 3.6/total_height bounds each leg axis at (cap/3.6) * total_height. The
    # 0.85 margin absorbs boundary-face majority-vote slop AND post-split
    # decimation dragging border verts outward (measured ~0.3 studs combined).
    total_h = (z_hi - z_lo) or 1.0
    leg_x_cap = (1.5 / 3.6) * total_h * 0.80          # Leg X max 1.5
    leg_y_half_cap = (2.0 / 3.6) * total_h * 0.80 / 2  # Leg Z (depth) max 2.0
    wy = [0.0] * vertex_count
    for v_idx, v in enumerate(mesh.data.vertices):
        wy[v_idx] = (mesh_world @ v.co).y
    for v_idx, name in enumerate(dominant):
        if "Leg" in name or name.endswith("Foot"):
            if abs(wx[v_idx]) > leg_x_cap or abs(wy[v_idx]) > leg_y_half_cap:
                dominant[v_idx] = "LowerTorso"
                rebucketed += 1
    if rebucketed:
        print(f"[split] re-bucketed {rebucketed} leg-band verts -> LowerTorso "
              f"(leg X cap {leg_x_cap:.2f}, depth half-cap {leg_y_half_cap:.2f})")

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
        # Drop orphan verts left behind by face deletion — these stray points
        # sit far from the part surface and otherwise inflate its bbox/cage
        # (the source of "cage vertex N studs from render mesh" warnings).
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.delete_loose()
        bpy.ops.object.mode_set(mode="OBJECT")

        # Re-origin the piece to its bone JOINT. Each piece is a DUPLICATE of the
        # whole mesh with other bones' faces deleted, so it keeps the source
        # pivot at world (0,0,0) while this part's geometry sits several studs
        # away. Roblox derives each MeshPart's ImportOrigin (and the cage's
        # CageOrigin) from that pivot->geometry offset and rejects PositionMagnitude
        # > 8 / > 10 — one warning per part. Snapping the origin to the bone head
        # collapses that offset to ~0. origin_set preserves WORLD vertex positions,
        # so the armature modifier + skin weights are unaffected.
        head_world = r15_mod.bone_world_position(armature, bone, "head")
        _prev_cursor = tuple(bpy.context.scene.cursor.location)
        bpy.context.scene.cursor.location = head_world
        bpy.ops.object.select_all(action="DESELECT")
        piece.select_set(True)
        bpy.context.view_layer.objects.active = piece
        bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
        bpy.context.scene.cursor.location = _prev_cursor

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


# --- spec bbox clamp + de-overlap + stray-component drop -------------------

# Map each R15 bone to its CLASSIC_BODY_BOUNDS group.
_BONE_GROUP: dict[str, str] = {
    "Head": "Head",
    "UpperTorso": "Torso", "LowerTorso": "Torso",
    "LeftUpperArm": "Arm", "LeftLowerArm": "Arm", "LeftHand": "Arm",
    "RightUpperArm": "Arm", "RightLowerArm": "Arm", "RightHand": "Arm",
    "LeftUpperLeg": "Leg", "LeftLowerLeg": "Leg", "LeftFoot": "Leg",
    "RightUpperLeg": "Leg", "RightLowerLeg": "Leg", "RightFoot": "Leg",
}


def _keep_largest_component(piece: bpy.types.Object) -> int:
    """Drop all but the largest connected component of a piece (kills the stray
    far-away shards that inflate the part bbox -> 'low visibility geometry' +
    'not opaque enough', since opacity is fill-fraction RELATIVE to the bbox)."""
    bpy.ops.object.select_all(action="DESELECT")
    piece.select_set(True)
    bpy.context.view_layer.objects.active = piece
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.mesh.separate(type="LOOSE")
    parts = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    if len(parts) <= 1:
        return 0
    parts.sort(key=lambda o: len(o.data.polygons), reverse=True)
    dropped = len(parts) - 1
    for o in parts[1:]:
        bpy.data.objects.remove(o, do_unlink=True)
    return dropped


def clamp_pieces_to_spec_bounds(pieces: dict[str, bpy.types.Object]) -> dict:
    """Trim each leg to its own side of the body midline and drop stray
    components, so the per-part bbox fits Roblox's caps.

    The proximity weighting pulls the wide onesie belly/skirt fabric into BOTH
    legs, so each leg's X extent exceeds the 1.5-stud Leg cap, the two legs
    interpenetrate ('legs overlap by N studs'), and far shards inflate the box
    ('low visibility geometry' + the per-view opacity fill). Cutting every leg
    face that crosses X=0 makes each leg one-sided (de-overlaps + shrinks X),
    and keep-largest-component removes the shards.
    """
    log: dict = {}
    for bone, piece in pieces.items():
        if _BONE_GROUP.get(bone) != "Leg":
            continue
        is_left = bone.startswith("Left")
        M = piece.matrix_world
        bpy.ops.object.select_all(action="DESELECT")
        piece.select_set(True)
        bpy.context.view_layer.objects.active = piece
        bpy.ops.object.mode_set(mode="OBJECT")
        deleted = 0
        for poly in piece.data.polygons:
            verts = poly.vertices
            cx = sum((M @ piece.data.vertices[vi].co).x for vi in verts) / len(verts)
            # keep left leg on -X, right leg on +X (0.05-stud tolerance at the seam)
            poly.select = (cx > 0.05) if is_left else (cx < -0.05)
            deleted += 1 if poly.select else 0
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.delete(type="FACE")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.delete_loose()
        bpy.ops.object.mode_set(mode="OBJECT")
        dropped = _keep_largest_component(piece)
        log[bone] = {"trimmed_faces": deleted, "dropped_components": dropped}
    return log


# --- cages: per-part WrapTarget so Roblox doesn't auto-generate bad ones ----

def generate_outer_cages(
    pieces: dict[str, bpy.types.Object],
    armature: bpy.types.Object,
) -> dict[str, str]:
    """Create a `<Bone>_OuterCage` skinned duplicate of each final piece.

    Roblox needs a WrapTarget cage per body part. With NO cage in the FBX,
    Studio auto-generates fallback cages whose ImportOrigin/CageOrigin
    PositionMagnitude, cage-to-mesh distance, and size-difference all fail (the
    ~25 `*WrapTarget` warnings). A duplicate of the FINAL decimated piece hugs
    the render mesh exactly (distance ~0 << 0.3 cap, size-diff 0 << 1.0) and
    inherits the piece's joint origin, so those checks pass. Skinned to the same
    armature so it deforms with the body; NOT decimated (Roblox forbids altering
    cage topology, and a copy of the already-budgeted piece is fine).
    """
    spec = _spec()
    cages: dict[str, bpy.types.Object] = {}
    for bone, piece in pieces.items():
        bpy.ops.object.select_all(action="DESELECT")
        piece.select_set(True)
        bpy.context.view_layer.objects.active = piece
        bpy.ops.object.duplicate(linked=False)
        cage = bpy.context.view_layer.objects.active
        cage.name = spec.outer_cage_name(bone)
        cage.data.name = cage.name
        found = False
        for mod in cage.modifiers:
            if mod.type == "ARMATURE":
                mod.object = armature
                found = True
                break
        if not found:
            m = cage.modifiers.new(name="rc_armature", type="ARMATURE")
            m.object = armature
        cage.parent = armature
        cages[bone] = cage
    return {b: c.name for b, c in cages.items()}


# --- opacity: strip inherited transparency before export ------------------

def force_opaque_materials(objs) -> None:
    """Make every piece's material fully opaque + double-sided.

    The reconstructed mesh arrives with Hunyuan/TripoSG's material, which uses
    backface culling + HASHED (alpha-clip) blending and an alpha channel. Roblox
    renders each body part from front/back/left/right and measures how much of
    the bounding box it fills; a transparent/single-sided material reads as empty
    and fails the "not opaque enough" (>= 0.30 fill) check. Force opaque here so
    the exported parts render solid.
    """
    for o in objs:
        for mat in o.data.materials:
            if mat is None:
                continue
            try:
                mat.use_backface_culling = False
            except (AttributeError, TypeError):
                pass
            for attr, val in (("blend_method", "OPAQUE"),
                              ("shadow_method", "OPAQUE"),
                              ("show_transparent_back", False)):
                try:
                    setattr(mat, attr, val)
                except (AttributeError, TypeError):
                    pass
            if mat.use_nodes:
                bsdf = next((n for n in mat.node_tree.nodes
                             if n.type == "BSDF_PRINCIPLED"), None)
                if bsdf is not None and "Alpha" in bsdf.inputs:
                    alpha = bsdf.inputs["Alpha"]
                    for link in list(alpha.links):
                        mat.node_tree.links.remove(link)
                    alpha.default_value = 1.0


# --- step 8: export --------------------------------------------------------

def export_fbx(armature: bpy.types.Object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # SCALE: the rig is built at stud scale (1 unit = 1 stud). apply_unit_scale=False
    # writes raw Blender units, so a 5-stud avatar exports as ~5 and Roblox imports it
    # at true R15 size with no rescale — and origin magnitudes stay small. We do NOT
    # use global_scale (it bakes a scaled armature that breaks Avatar Setup's
    # attachment generation); building natively at stud scale avoids that entirely.
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=False,
        apply_unit_scale=False,
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
    generate_cages: bool = True,
    # OFF by default: arms-down IS a valid I-pose (the "must be I/A/T pose"
    # readings came from mis-anchored shoulder attachments, since fixed), and
    # posing deforms organic bodies visibly (dragged side fabric). Enable only
    # for clean humanoids that need Avatar Auto-Setup's preferred A-pose.
    a_pose: bool = False,
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
    log["fit"] = fit_mesh_to_template(mesh, target_height_studs=target_height)
    # The R15 template is defined at ~5 units; the mesh is now built at stud scale
    # (1 unit = 1 stud), so the armature scale is just target/5 (no meter conversion).
    armature_scale = target_height / 5.0
    armature, weight_hist, width_fit = attach_armature(mesh, scale=armature_scale)
    log["armature"] = armature.name
    log["template_width_fit"] = width_fit
    log["weights_per_bone"] = weight_hist
    # A-pose BEFORE splitting (needs the single mesh + live armature modifier):
    # marketplace validation only accepts I/A/T poses and Avatar Auto-Setup
    # wants an A/T pose with limbs clear of the torso from the front.
    if a_pose:
        log["a_pose"] = pose_to_a_pose(armature, mesh)
    pieces = split_mesh_by_bone(mesh, armature)
    # Trim legs to one side of the midline + drop stray shards BEFORE decimation
    # so the tri budget rebalances over the kept geometry (fixes leg X>1.5 cap,
    # inter-leg overlap, stray low-visibility geometry, and bbox-relative opacity).
    log["clamp"] = clamp_pieces_to_spec_bounds(pieces)
    reattach_pieces_to_armature(pieces, armature)
    log["pieces"] = {bone: o.name for bone, o in pieces.items()}
    log["attachments_stamped"] = stamp_body_attachments(armature)
    if decimate:
        log["decimate"] = decimate_per_group(pieces)
    # Generate per-part WrapTarget cages AFTER decimation so each cage hugs the
    # final render mesh (clears the ~25 *WrapTarget origin/distance/size warnings).
    if generate_cages:
        log["cages"] = generate_outer_cages(pieces, armature)

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

    # Strip any inherited transparency/backface-culling so Roblox's per-view
    # opacity check passes (applies to both textured + untextured pieces).
    force_opaque_materials(pieces.values())

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
