"""Roblox UGC spec values, sourced from official creator-docs.

All values below come from the Roblox/creator-docs GitHub repo as of 2026-05.
If marketplace policy changes these, update here in one place and validators
will pick up the new limits automatically.

Sources:
  - art/characters/specifications.md
  - art/accessories/specifications.md
  - art/accessories/caging-best-practices.md
  - art/accessories/clothing-specifications.md
"""

from __future__ import annotations

from dataclasses import dataclass


# --- R15 rig ----------------------------------------------------------------

# Hierarchy:
#   Root > HumanoidRootNode > LowerTorso > {UpperTorso > Head, *Arm chain},
#                                          *Leg chain
# Max 4 bone influences per vertex; no Root influence.
R15_BONES: tuple[str, ...] = (
    "HumanoidRootPart",
    "LowerTorso",
    "UpperTorso",
    "Head",
    "LeftUpperArm",
    "LeftLowerArm",
    "LeftHand",
    "RightUpperArm",
    "RightLowerArm",
    "RightHand",
    "LeftUpperLeg",
    "LeftLowerLeg",
    "LeftFoot",
    "RightUpperLeg",
    "RightLowerLeg",
    "RightFoot",
)

# Optional fidelity bones supported by the R15 rig.
R15_OPTIONAL_BONES: tuple[str, ...] = (
    "Spine", "Chest", "HeadBase",
    "LeftClavicle", "RightClavicle",
    "LeftToeBase", "RightToeBase",
    # Finger chains (1-3 per digit)
    *[f"{side}{finger}{i}"
      for side in ("Left", "Right")
      for finger in ("Thumb", "Index", "Middle", "Ring", "Pinky")
      for i in (1, 2, 3)],
)

MAX_BONE_INFLUENCES_PER_VERTEX: int = 4


# --- Mesh naming convention (avatar body) ----------------------------------

# Each R15 body part is one mesh named `<BoneName>_Geo`. The DynamicHead
# combines facial geometry under a single `Head_Geo`.
def avatar_mesh_name(bone: str) -> str:
    return f"{bone}_Geo"


AVATAR_BODY_MESHES: tuple[str, ...] = tuple(avatar_mesh_name(b) for b in R15_BONES if b != "HumanoidRootPart")


# --- Triangle budgets per body group (avatar body) -------------------------

# Roblox specs budgets per *group* of meshes, not per individual mesh:
#   - Head:  4,000 tris        (Head_Geo alone)
#   - Torso: 1,750 tris        (UpperTorso_Geo + LowerTorso_Geo combined)
#   - Arm:   1,248 tris per arm (3 meshes per arm combined)
#   - Leg:   1,248 tris per leg (3 meshes per leg combined)
# Total R15: 4000 + 1750 + 1248*4 = 10,742.
BODY_GROUP_TRI_BUDGET: dict[str, int] = {
    "Head":     4000,
    "Torso":    1750,
    "LeftArm":  1248,
    "RightArm": 1248,
    "LeftLeg":  1248,
    "RightLeg": 1248,
}

BODY_GROUP_MEMBERS: dict[str, tuple[str, ...]] = {
    "Head":     ("Head_Geo",),
    "Torso":    ("UpperTorso_Geo", "LowerTorso_Geo"),
    "LeftArm":  ("LeftUpperArm_Geo", "LeftLowerArm_Geo", "LeftHand_Geo"),
    "RightArm": ("RightUpperArm_Geo", "RightLowerArm_Geo", "RightHand_Geo"),
    "LeftLeg":  ("LeftUpperLeg_Geo", "LeftLowerLeg_Geo", "LeftFoot_Geo"),
    "RightLeg": ("RightUpperLeg_Geo", "RightLowerLeg_Geo", "RightFoot_Geo"),
}

AVATAR_TOTAL_TRI_BUDGET: int = sum(BODY_GROUP_TRI_BUDGET.values())  # 10,742


# --- Avatar body scale (studs; classic body type max) ----------------------

@dataclass(frozen=True)
class BodyPartBounds:
    name: str
    max_x: float
    max_y: float
    max_z: float


CLASSIC_BODY_BOUNDS: tuple[BodyPartBounds, ...] = (
    BodyPartBounds("Head",  1.5, 1.8, 2.0),
    BodyPartBounds("Arm",   2.0, 3.0, 2.0),
    BodyPartBounds("Torso", 4.0, 3.8, 2.0),
    BodyPartBounds("Leg",   1.5, 3.5, 2.0),
)

# Full-body bounding box maxima per body type.
BODY_TYPE_FULL_BOUNDS: dict[str, tuple[float, float, float]] = {
    "Classic":  (8.0, 9.1,  2.0),
    "Normal":   (8.6, 9.5,  2.25),
    "Slender":  (6.0, 9.5,  2.0),
}


# --- Attachments per bone (avatar body required set) -----------------------

# The avatar body mesh must include these empty/attachment markers, with the
# exact names below. Attachments use the `*_Att` suffix (NOT `*Attachment` —
# that's the rigid-accessory side convention).
ATTACHMENTS_BY_BONE: dict[str, tuple[str, ...]] = {
    "Head": ("FaceCenter_Att", "FaceFront_Att", "Hat_Att", "Hair_Att"),
    "UpperTorso": (
        "LeftCollar_Att", "RightCollar_Att",
        "Neck_Att", "BodyFront_Att", "BodyBack_Att",
        "LeftShoulder_Att", "RightShoulder_Att",
    ),
    "LowerTorso": (
        "Root_Att",  # MUST be at origin (0,0,0)
        "WaistFront_Att", "WaistCenter_Att", "WaistBack_Att",
    ),
    "LeftHand":  ("LeftGrip_Att",),   # @ (90, 0, 0) rotation per spec
    "RightHand": ("RightGrip_Att",),  # @ (90, 0, 0) rotation per spec
    "LeftFoot":  ("LeftFoot_Att",),
    "RightFoot": ("RightFoot_Att",),
}


# --- Rigid accessory categories --------------------------------------------

@dataclass(frozen=True)
class AccessoryCategory:
    name: str
    # Required Attachment name on the avatar that the accessory snaps to.
    # The accessory mesh itself must contain an Attachment (Roblox naming
    # convention uses `*Attachment` for rigid accessories — NOT `*_Att`).
    attachment: str | tuple[str, ...]
    # Bounding box in studs (x, y, z). Hard cap from accessory specs.
    max_bounds: tuple[float, float, float]
    # All rigid accessories share the 4,000 tri hard cap.
    max_tris: int = 4000
    # Sub-categories (e.g. Hair vs Hat both attach to Head but at different anchors).
    notes: str = ""


ACCESSORY_CATEGORIES: dict[str, AccessoryCategory] = {
    "Hat":  AccessoryCategory("Hat",  "HatAttachment",   (3.0, 4.0, 3.0)),
    "Hair": AccessoryCategory("Hair", "HairAttachment",  (3.0, 5.0, 3.5),
                              notes="Off-center allowance: 2u/3d, 1.5f/2b"),
    "Face": AccessoryCategory("Face",
                              ("FaceFrontAttachment", "FaceCenterAttachment"),
                              (3.0, 2.0, 2.0)),
    "Brow": AccessoryCategory("Brow",
                              ("FaceFrontAttachment", "FaceCenterAttachment"),
                              (1.5, 0.5, 0.5)),
    "Lash": AccessoryCategory("Lash",
                              ("FaceFrontAttachment", "FaceCenterAttachment"),
                              (1.5, 0.5, 0.5)),
    "Neck": AccessoryCategory("Neck", "NeckAttachment", (3.0, 3.0, 2.0)),
    "ShoulderNeck": AccessoryCategory(
        "ShoulderNeck",
        ("LeftShoulderAttachment", "RightShoulderAttachment",
         "LeftCollarAttachment", "RightCollarAttachment", "NeckAttachment"),
        (7.0, 3.0, 3.0)),
    "Shoulder": AccessoryCategory(
        "Shoulder",
        ("LeftShoulderAttachment", "RightShoulderAttachment",
         "LeftCollarAttachment", "RightCollarAttachment"),
        (3.0, 3.0, 3.0)),
    "Front": AccessoryCategory("Front", "BodyFrontAttachment", (3.0, 3.0, 3.0)),
    "Back":  AccessoryCategory("Back",  "BodyBackAttachment",  (10.0, 7.0, 4.5)),
    "Waist": AccessoryCategory(
        "Waist",
        ("WaistFrontAttachment", "WaistCenterAttachment", "WaistBackAttachment"),
        (4.0, 3.5, 7.0)),
}


# --- LayeredClothing cages -------------------------------------------------

# Cages must mirror the body-geo part list with the `_OuterCage` suffix
# (LeftUpperArm_OuterCage, etc.). Topology must match the reference Roblox
# cage — you can't freely remesh. Per-part poly budget = 10,000 tris.
def outer_cage_name(bone: str) -> str:
    return f"{bone}_OuterCage"


def inner_cage_name(bone: str) -> str:
    return f"{bone}_InnerCage"


LAYERED_CLOTHING_PER_PART_TRI_BUDGET: int = 10_000


# --- Avatar budget summary (used by validators) ----------------------------

@dataclass(frozen=True)
class AvatarBudget:
    max_total_tris: int = AVATAR_TOTAL_TRI_BUDGET  # 10,742
    max_tris_per_part: int = 4000  # head; other parts have stricter limits
    # Full-body height bounds in studs (classic body type Y extent).
    height_min: float = 4.0
    height_max: float = 9.5  # Normal/Slender max


AVATAR_BUDGET = AvatarBudget()


# --- Texture rules ----------------------------------------------------------

@dataclass(frozen=True)
class TextureRules:
    max_texture_dim: int = 2048
    required_pbr_maps: tuple[str, ...] = ("BaseColor",)
    recommended_pbr_maps: tuple[str, ...] = ("BaseColor", "Normal", "MetallicRoughness")


TEXTURE_RULES = TextureRules()


# --- Scale --------------------------------------------------------------------

# Roblox studs: 1 stud == ~0.28 meters in Studio's default scale.
STUDS_PER_METER: float = 1.0 / 0.28
