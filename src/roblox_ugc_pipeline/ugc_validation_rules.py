"""Roblox's OWN marketplace validation rules, vendored from the UGCValidation
Luau module (the code Toolbox/RCC actually runs at upload time).

Source: MaximumADHD/Roblox-Client-Tracker mirror, commit f56a1b632e
(Studio 0.629, 2024-06-11) — the last revision before the numeric rules moved
behind the server-fed AvatarCreationService:GetValidationRules(). Files:
  LuaPackages/Packages/_Index/UGCValidation/UGCValidation/Constants.lua
  .../validation/validateBodyPartChildAttachmentBounds.lua
  .../util/ValidationRulesUtil.lua
  .../flags/getFIntMeshDivision*.lua

Flag resolution baked into the literals below:
  - UGCValidateMeshDivision = 9         -> 0.09
  - UGCValidateMeshDivisionMedium = 9   -> 0.09
  - UGCValidateMeshDivisionFull = 159   -> 1.59
  - UGCValidateMeshDivisionFullExtended = 159 -> 1.59
  - UGCValidateMeshDivisionNarrow = 45  -> 0.45
  - UGCValidateRestrictAttachmentPositions -> True (narrow/center variants;
    strictly inside the unrestricted boxes, so safe under either behavior)
  - UGCValidationAdjustLegBounds -> True (confirmed live via
    clientsettingscdn.roblox.com on 2026-06-12: leg max height 3.3)

Coordinate convention: everything here is ROBLOX mesh-space — the attachment
position minus the part-mesh bbox center, divided per-axis by the bbox half
size (so the mesh itself spans [-1, 1]). Axes: +X = character RIGHT,
+Y = up, -Z = front. Blender Z-up world maps as (x_b, y_b, z_b) ->
(X=x_b, Y=z_b, Z=y_b).
"""

from __future__ import annotations

Vec3 = tuple[float, float, float]
Box = tuple[Vec3, Vec3]  # (min, max) in normalized mesh-space

_FULL = 1.59
_DIV = 0.09
_MED = 0.09
_NARROW = 0.45

FULL_MESH: Box = ((-_FULL, -_FULL, -_FULL), (_FULL, _FULL, _FULL))
FULL_MESH_EXTENDED: Box = FULL_MESH
LEFT_MESH_MEDIUM: Box = ((-_FULL, -_FULL, -_FULL), (-_MED, _FULL, _FULL))
RIGHT_MESH_MEDIUM: Box = ((_MED, -_FULL, -_FULL), (_FULL, _FULL, _FULL))
TOP_MESH: Box = ((-_FULL, _DIV, -_FULL), (_FULL, _FULL, _FULL))
TOP_MESH_EXTENDED: Box = TOP_MESH
TOP_LEFT_MESH_NARROW: Box = ((-_FULL, _DIV, -_NARROW), (-_DIV, _FULL, _NARROW))
TOP_RIGHT_MESH_NARROW: Box = ((_DIV, _DIV, -_NARROW), (_FULL, _FULL, _NARROW))
TOP_CENTER_MESH: Box = ((-_NARROW, _DIV, -_NARROW), (_NARROW, _FULL, _NARROW))
BOTTOM_MESH: Box = ((-_FULL, -_FULL, -_FULL), (_FULL, -_DIV, _FULL))
BOTTOM_LEFT_MESH_NARROW: Box = ((-_FULL, -_FULL, -_NARROW), (-_DIV, -_DIV, _NARROW))
BOTTOM_RIGHT_MESH_NARROW: Box = ((_DIV, -_FULL, -_NARROW), (_FULL, -_DIV, _NARROW))
BOTTOM_CENTER_MESH: Box = ((-_NARROW, -_FULL, -_NARROW), (_NARROW, -_DIV, _NARROW))
FRONT_MESH: Box = ((-_FULL, -_FULL, -_FULL), (_FULL, _FULL, -_DIV))
BACK_MESH: Box = ((-_FULL, -_FULL, _DIV), (_FULL, _FULL, _FULL))


# Per R15 part: every attachment the upload gate expects in that part's mesh,
# and the normalized mesh-space box its position must fall inside. Rig
# attachments (joints) appear in TWO parts — the child lists it as its
# rig-attachment-to-parent, the parent lists it under otherAttachments — and
# the SAME world position must satisfy both parts' boxes.
BODY_ATTACHMENT_BOUNDS: dict[str, dict[str, Box]] = {
    "Head": {
        "NeckRigAttachment": BOTTOM_MESH,
        "FaceFrontAttachment": FRONT_MESH,
        "HatAttachment": TOP_MESH,
        "HairAttachment": TOP_MESH,
        "FaceCenterAttachment": FULL_MESH,
    },
    "UpperTorso": {
        "WaistRigAttachment": BOTTOM_CENTER_MESH,
        "NeckRigAttachment": TOP_MESH,
        "LeftShoulderRigAttachment": TOP_LEFT_MESH_NARROW,
        "RightShoulderRigAttachment": TOP_RIGHT_MESH_NARROW,
        "LeftCollarAttachment": LEFT_MESH_MEDIUM,
        "RightCollarAttachment": RIGHT_MESH_MEDIUM,
        "BodyFrontAttachment": FRONT_MESH,
        "BodyBackAttachment": BACK_MESH,
        "NeckAttachment": TOP_MESH_EXTENDED,
    },
    "LowerTorso": {
        "RootRigAttachment": FULL_MESH,
        "WaistRigAttachment": TOP_CENTER_MESH,
        "LeftHipRigAttachment": BOTTOM_LEFT_MESH_NARROW,
        "RightHipRigAttachment": BOTTOM_RIGHT_MESH_NARROW,
        "WaistCenterAttachment": FULL_MESH,
        "WaistFrontAttachment": FRONT_MESH,
        "WaistBackAttachment": BACK_MESH,
    },
    "LeftUpperArm": {
        "LeftShoulderRigAttachment": TOP_MESH,
        "LeftShoulderAttachment": TOP_MESH,
        "LeftElbowRigAttachment": BOTTOM_MESH,
    },
    "LeftLowerArm": {
        "LeftElbowRigAttachment": TOP_MESH,
        "LeftWristRigAttachment": BOTTOM_MESH,
    },
    "LeftHand": {
        "LeftWristRigAttachment": TOP_MESH,
        "LeftGripAttachment": FULL_MESH_EXTENDED,
    },
    "RightUpperArm": {
        "RightShoulderRigAttachment": TOP_MESH,
        "RightShoulderAttachment": TOP_MESH,
        "RightElbowRigAttachment": BOTTOM_MESH,
    },
    "RightLowerArm": {
        "RightElbowRigAttachment": TOP_MESH,
        "RightWristRigAttachment": BOTTOM_MESH,
    },
    "RightHand": {
        "RightWristRigAttachment": TOP_MESH,
        "RightGripAttachment": FULL_MESH_EXTENDED,
    },
    "LeftUpperLeg": {
        "LeftHipRigAttachment": TOP_MESH,
        "LeftKneeRigAttachment": BOTTOM_MESH,
    },
    "LeftLowerLeg": {
        "LeftKneeRigAttachment": TOP_MESH,
        "LeftAnkleRigAttachment": BOTTOM_MESH,
    },
    "LeftFoot": {
        "LeftAnkleRigAttachment": TOP_MESH,
        "LeftFootAttachment": FULL_MESH,
    },
    "RightUpperLeg": {
        "RightHipRigAttachment": TOP_MESH,
        "RightKneeRigAttachment": BOTTOM_MESH,
    },
    "RightLowerLeg": {
        "RightKneeRigAttachment": TOP_MESH,
        "RightAnkleRigAttachment": BOTTOM_MESH,
    },
    "RightFoot": {
        "RightAnkleRigAttachment": TOP_MESH,
        "RightFootAttachment": FULL_MESH,
    },
}

# Which attachment joins each part to its parent part (ValidationRulesUtil).
RIG_ATTACHMENT_TO_PARENT: dict[str, str] = {
    "Head": "NeckRigAttachment",
    "UpperTorso": "WaistRigAttachment",
    "LowerTorso": "RootRigAttachment",
    "RightHand": "RightWristRigAttachment",
    "RightLowerArm": "RightElbowRigAttachment",
    "RightUpperArm": "RightShoulderRigAttachment",
    "LeftHand": "LeftWristRigAttachment",
    "LeftLowerArm": "LeftElbowRigAttachment",
    "LeftUpperArm": "LeftShoulderRigAttachment",
    "RightFoot": "RightAnkleRigAttachment",
    "RightLowerLeg": "RightKneeRigAttachment",
    "RightUpperLeg": "RightHipRigAttachment",
    "LeftFoot": "LeftAnkleRigAttachment",
    "LeftLowerLeg": "LeftKneeRigAttachment",
    "LeftUpperLeg": "LeftHipRigAttachment",
}

# Every part each attachment name must validate against (rig attachments
# appear in two parts; the same world position must satisfy all boxes).
ATTACHMENT_CONSTRAINTS: dict[str, tuple[tuple[str, Box], ...]] = {}
for _part, _atts in BODY_ATTACHMENT_BOUNDS.items():
    for _name, _box in _atts.items():
        ATTACHMENT_CONSTRAINTS[_name] = ATTACHMENT_CONSTRAINTS.get(_name, ()) + ((_part, _box),)


# Per-asset size bounds (studs) from the same Constants.lua — the table behind
# "no valid scale passes individual body part requirements". An asset's bbox
# is the union over its parts (an Arm asset = upper + lower + hand). NOTE:
# these diverge from the 2026 creator-docs numbers in roblox_spec.py (e.g.
# Torso Classic max 3.5x3.25x2 here vs 4.0x3.8x2.0 in the docs) — this table
# is what the validator itself enforced; treat the intersection as safe.
UGC_ASSET_SIZE_BOUNDS: dict[str, dict[str, tuple[Vec3, Vec3]]] = {
    "Classic": {
        "Head": ((0.5, 0.5, 0.5), (1.5, 1.75, 2.0)),
        "Torso": ((1.0, 2.0, 0.7), (3.5, 3.25, 2.0)),
        "Arm": ((0.25, 1.5, 0.25), (2.0, 3.0, 2.0)),
        "Leg": ((0.25, 2.0, 0.5), (1.5, 2.75, 2.0)),
    },
    "Slender": {
        "Head": ((0.5, 0.5, 0.5), (2.0, 2.0, 2.0)),
        "Torso": ((1.0, 2.0, 0.7), (2.5, 3.0, 2.0)),
        "Arm": ((0.25, 1.5, 0.25), (1.5, 4.0, 2.0)),
        "Leg": ((0.25, 2.0, 0.5), (1.5, 3.3, 2.0)),
    },
    "Normal": {
        "Head": ((0.5, 0.5, 0.5), (3.0, 2.0, 2.0)),
        "Torso": ((1.0, 2.0, 0.7), (4.0, 3.0, 2.25)),
        "Arm": ((0.25, 1.5, 0.25), (2.0, 4.5, 2.0)),
        "Leg": ((0.25, 2.0, 0.5), (1.5, 3.3, 2.0)),
    },
}


# validatePose.lua (FInt defaults): limb direction = top rig attachment ->
# bottom rig attachment, projected onto the world XY (frontal) plane.
# Out-of-plane: angle between the limb and its frontal projection.
# In-plane: signed angle from the outward X axis (+X right limbs, -X left),
# 0 = horizontal T-pose, -90 = straight down I-pose, positive = above
# horizontal. I/A/T poses all fall inside these windows by design.
POSE_OUT_OF_PLANE_MAX_DEG: float = 20.0
POSE_IN_PLANE_RANGE_DEG: dict[str, tuple[float, float]] = {
    "Arm": (-90.0, 30.0),
    "Leg": (-93.0, -60.0),
}

# validateBodyPartExtentsRelativeToParent.lua: within an asset, a lower part
# may not extend above its upper part's bbox top, and the upper part may not
# extend below the lower part's bbox bottom (strict, no tolerance). Pairs are
# (upper, lower); UpperTorso counts as "upper" relative to LowerTorso.
EXTENTS_UPPER_LOWER_PAIRS: tuple[tuple[str, str], ...] = (
    ("UpperTorso", "LowerTorso"),
    ("LeftUpperArm", "LeftLowerArm"), ("LeftLowerArm", "LeftHand"),
    ("RightUpperArm", "RightLowerArm"), ("RightLowerArm", "RightHand"),
    ("LeftUpperLeg", "LeftLowerLeg"), ("LeftLowerLeg", "LeftFoot"),
    ("RightUpperLeg", "RightLowerLeg"), ("RightLowerLeg", "RightFoot"),
)


def att_to_roblox_name(name: str) -> str:
    """Map this repo's `*_Att` naming to the engine attachment name.

    Root_Att deliberately does NOT map to RootRigAttachment: Root_Att is the
    FBX ground/origin marker (pinned at world origin, between the feet), while
    the LowerTorso's RootRigAttachment is derived by Studio's importer at hip
    height — validating or clamping Root_Att against the RootRigAttachment box
    produces false failures. Names already in engine form pass through.
    """
    if name.endswith("Attachment"):
        return name
    if not name.endswith("_Att"):
        return name
    stem = name[: -len("_Att")]
    if stem == "Root":
        return name
    return f"{stem}Attachment"


def normalize_to_mesh_space(
    position: Vec3, bbox_min: Vec3, bbox_max: Vec3,
) -> Vec3 | None:
    """Blender-world position -> Roblox normalized mesh-space (the exact
    transform validateBodyPartChildAttachmentBounds applies). None when the
    part bbox is degenerate on any axis."""
    center = [(bbox_min[i] + bbox_max[i]) / 2 for i in range(3)]
    half = [(bbox_max[i] - bbox_min[i]) / 2 for i in range(3)]
    if any(h <= 1e-9 for h in half):
        return None
    n = [(position[i] - center[i]) / half[i] for i in range(3)]
    return (n[0], n[2], n[1])  # Blender (x,y,z) -> Roblox (X=x, Y=z, Z=y)


def box_contains(box: Box, p: Vec3) -> bool:
    return all(box[0][i] <= p[i] <= box[1][i] for i in range(3))


def clamp_to_box(p: Vec3, box: Box, margin: float = 0.001) -> Vec3:
    return tuple(
        min(max(p[i], box[0][i] + margin), box[1][i] - margin) for i in range(3)
    )  # type: ignore[return-value]
