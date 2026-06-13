"""Build a template R15 armature in Blender.

The skeleton has the 16 R15 bones at approximate Roblox default proportions
(~5 studs tall in arms-down rest pose). The bone NAMES are exact and must
not be changed — Roblox marketplace validation matches them case-sensitively.

Coordinate convention:
  * Blender world: +X right, +Y forward (away from front view), +Z up
  * Character base at Z = 0, head top at Z ≈ 5 (R15 default ≈ 5.4)
  * Default rest pose: arms hanging down at the sides
  * FBX export rotates to Roblox's Y-up convention via axis_up='Y' / axis_forward='-Z'
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import bpy  # type: ignore
from mathutils import Vector  # type: ignore


# Bone definitions in Blender Z-up coords: (name, head_xyz, tail_xyz, parent_name)
# Heights chosen to land at standard R15 proportions (~5 studs total).
# Arms in relaxed-down pose; this matches the way most Sketchfab humanoids ship.
# Foot tails point forward along +Y (Blender's forward axis).
R15_BONE_DEFS: tuple[tuple[str, tuple[float, float, float], tuple[float, float, float], str | None], ...] = (
    # Root reference. Short stub at the pelvis center.
    ("HumanoidRootPart", (0.0, 0.0, 2.5),  (0.0, 0.0, 2.55), None),

    # Spine.
    ("LowerTorso",       (0.0, 0.0, 2.4),  (0.0, 0.0, 3.0),  "HumanoidRootPart"),
    ("UpperTorso",       (0.0, 0.0, 3.0),  (0.0, 0.0, 4.0),  "LowerTorso"),
    ("Head",             (0.0, 0.0, 4.0),  (0.0, 0.0, 5.0),  "UpperTorso"),

    # Left arm chain. CHARACTER-left = +X in Blender: Roblox's own template
    # bodies (RoundMale.blend, verified 2026-06-12) put every Left* part and
    # *_Att at +X, because the FBX axis conversion to Roblox space is the
    # proper rotation x_r = -x_b, y_r = z_b, z_r = y_b (front -Y_b -> -Z_r).
    ("LeftUpperArm",     (1.0, 0.0, 3.9),  (1.0, 0.0, 3.0),  "UpperTorso"),
    ("LeftLowerArm",     (1.0, 0.0, 3.0),  (1.0, 0.0, 2.2),  "LeftUpperArm"),
    ("LeftHand",         (1.0, 0.0, 2.2),  (1.0, 0.0, 1.8),  "LeftLowerArm"),

    # Right arm chain (character right = -X in Blender).
    ("RightUpperArm",    (-1.0, 0.0, 3.9), (-1.0, 0.0, 3.0), "UpperTorso"),
    ("RightLowerArm",    (-1.0, 0.0, 3.0), (-1.0, 0.0, 2.2), "RightUpperArm"),
    ("RightHand",        (-1.0, 0.0, 2.2), (-1.0, 0.0, 1.8), "RightLowerArm"),

    # Left leg chain (foot tails point forward = -Y, matching the template).
    ("LeftUpperLeg",     (0.5, 0.0, 2.4),  (0.5, 0.0, 1.4),  "LowerTorso"),
    ("LeftLowerLeg",     (0.5, 0.0, 1.4),  (0.5, 0.0, 0.4),  "LeftUpperLeg"),
    ("LeftFoot",         (0.5, 0.0, 0.4),  (0.5, -0.4, 0.4), "LeftLowerLeg"),

    # Right leg chain.
    ("RightUpperLeg",    (-0.5, 0.0, 2.4), (-0.5, 0.0, 1.4), "LowerTorso"),
    ("RightLowerLeg",    (-0.5, 0.0, 1.4), (-0.5, 0.0, 0.4), "RightUpperLeg"),
    ("RightFoot",        (-0.5, 0.0, 0.4), (-0.5, -0.4, 0.4), "RightLowerLeg"),
)


@dataclass(frozen=True)
class R15Proportions:
    """Bounding box / reference heights of the canonical template.

    Computed once from R15_BONE_DEFS so the auto-rig fit step can scale
    arbitrary humanoid meshes onto this skeleton without having to recompute
    each call.
    """

    total_height: float          # 5.0 studs (Z extent of the skeleton)
    shoulder_z: float            # 3.9 studs (top of UpperArm)
    shoulder_half_width: float   # 1.0 studs (|x| of shoulder)
    hip_z: float                 # 2.4 studs (top of UpperLeg)
    hip_half_width: float        # 0.5 studs (|x| of hip)
    foot_z: float                # 0.4 studs (ankle Z)


def proportions() -> R15Proportions:
    return R15Proportions(
        total_height=5.0,
        shoulder_z=3.9,
        shoulder_half_width=1.0,
        hip_z=2.4,
        hip_half_width=0.5,
        foot_z=0.4,
    )


def build_r15_armature(name: str = "R15_Armature", scale: float = 1.0) -> bpy.types.Object:
    """Create a new armature object with the 16 R15 bones.

    Idempotent-ish: if an armature with this name already exists it is
    removed first.
    """
    if name in bpy.data.objects:
        old = bpy.data.objects[name]
        old_data = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if isinstance(old_data, bpy.types.Armature):
            bpy.data.armatures.remove(old_data)

    arm_data = bpy.data.armatures.new(name=name)
    arm_obj = bpy.data.objects.new(name=name, object_data=arm_data)
    bpy.context.scene.collection.objects.link(arm_obj)

    # Switch to edit mode to add bones.
    bpy.ops.object.select_all(action="DESELECT")
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = arm_data.edit_bones
    created: dict[str, bpy.types.EditBone] = {}
    for bone_name, head, tail, parent_name in R15_BONE_DEFS:
        eb = edit_bones.new(bone_name)
        eb.head = Vector(head) * scale
        eb.tail = Vector(tail) * scale
        if parent_name is not None and parent_name in created:
            eb.parent = created[parent_name]
            # Don't auto-connect; R15 bones often don't share endpoints
            # (e.g. shoulder is offset from the spine bone tip).
            eb.use_connect = False
        created[bone_name] = eb

    bpy.ops.object.mode_set(mode="OBJECT")
    return arm_obj


def bone_world_position(armature: bpy.types.Object, bone_name: str, point: str = "head") -> Vector:
    """Return the world-space position of a bone's head or tail."""
    if bone_name not in armature.pose.bones:
        raise KeyError(f"Bone '{bone_name}' not in armature '{armature.name}'")
    pb = armature.pose.bones[bone_name]
    if point == "head":
        return armature.matrix_world @ pb.head
    if point == "tail":
        return armature.matrix_world @ pb.tail
    raise ValueError(f"point must be 'head' or 'tail', got {point!r}")


def bone_names(armature: bpy.types.Object) -> list[str]:
    return [b.name for b in armature.data.bones]
