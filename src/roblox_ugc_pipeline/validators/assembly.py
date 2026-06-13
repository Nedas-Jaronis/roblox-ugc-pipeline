"""Assembly-level body checks ported from UGCValidation: limb pose windows
(validatePose), upper/lower part extent ordering
(validateBodyPartExtentsRelativeToParent), and leg overlap
(ValidateLegsSeparation).

All three are pure geometry over the rest-pose export. The gate measures pose
and extents in straightened-limb space; for our I-pose exports (a_pose=False)
that space equals world space, and for mild A-poses the windows are wide
enough that world-space measurement stays faithful. Axes: report data is
Blender Z-up, so Roblox Y (height) = Blender z and Roblox Z (depth) =
Blender y.
"""

from __future__ import annotations

import math

from ..report import Finding, MeshReport
from ..ugc_validation_rules import (
    EXTENTS_UPPER_LOWER_PAIRS,
    POSE_IN_PLANE_RANGE_DEG,
    POSE_OUT_OF_PLANE_MAX_DEG,
    att_to_roblox_name,
)

_LIMBS: tuple[tuple[str, str, str, str], ...] = (
    # (label, kind, top attachment, bottom attachment)
    ("LeftArm", "Arm", "LeftShoulderRigAttachment", "LeftWristRigAttachment"),
    ("RightArm", "Arm", "RightShoulderRigAttachment", "RightWristRigAttachment"),
    ("LeftLeg", "Leg", "LeftHipRigAttachment", "LeftAnkleRigAttachment"),
    ("RightLeg", "Leg", "RightHipRigAttachment", "RightAnkleRigAttachment"),
)

_LEG_PARTS = {
    "Left": ("LeftUpperLeg_Geo", "LeftLowerLeg_Geo", "LeftFoot_Geo"),
    "Right": ("RightUpperLeg_Geo", "RightLowerLeg_Geo", "RightFoot_Geo"),
}

# Boundary slack: I-pose arms sit exactly on the -90 deg window edge, so a
# float hair past it is noise, not a failure.
_EDGE_EPS_DEG = 0.5


def _pose_findings(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    atts = {att_to_roblox_name(a.name): a.position for a in report.attachments}
    for label, kind, top_name, bottom_name in _LIMBS:
        top = atts.get(top_name)
        bottom = atts.get(bottom_name)
        if top is None or bottom is None:
            continue
        # Blender world -> Roblox axes: X=x, Y(height)=z, Z(depth)=y.
        d = (bottom[0] - top[0], bottom[2] - top[2], bottom[1] - top[1])
        length = math.sqrt(sum(c * c for c in d))
        if length < 1e-6:
            out.append(Finding(
                validator="assembly.pose",
                severity="error",
                message=f"{label}: top and bottom rig attachments coincide; pose undefined",
                remediation=f"Separate {top_name} and {bottom_name} along the limb",
            ))
            continue
        proj_len = math.hypot(d[0], d[1])
        if proj_len < 1e-6:
            out.append(Finding(
                validator="assembly.pose",
                severity="error",
                message=f"{label} points straight along the depth axis; the gate rejects this",
                remediation="Limbs must lie near the frontal (XY) plane",
            ))
            continue
        out_of_plane = math.degrees(math.atan2(abs(d[2]), proj_len))
        if out_of_plane > POSE_OUT_OF_PLANE_MAX_DEG:
            out.append(Finding(
                validator="assembly.pose",
                severity="error",
                message=(
                    f"{label} leans {out_of_plane:.1f} deg out of the frontal plane "
                    f"(max {POSE_OUT_OF_PLANE_MAX_DEG} deg)"
                ),
                remediation="Keep limbs in the XY plane at rest (no forward/back lean)",
            ))
        x_sign = 1.0 if label.startswith("Right") else -1.0
        px, py = d[0] / proj_len, d[1] / proj_len
        angle = math.degrees(math.acos(max(-1.0, min(1.0, px * x_sign))))
        if py <= 0:
            angle = -angle
        lo, hi = POSE_IN_PLANE_RANGE_DEG[kind]
        if angle < lo - _EDGE_EPS_DEG or angle > hi + _EDGE_EPS_DEG:
            out.append(Finding(
                validator="assembly.pose",
                severity="error",
                message=(
                    f"{label} rest angle {angle:.1f} deg is outside the accepted "
                    f"window [{lo}, {hi}] (0 = T-pose horizontal, -90 = I-pose down)"
                ),
                remediation=(
                    "Re-pose the limb: arms anywhere from straight down to 30 deg "
                    "above horizontal; legs near vertical (<=30 deg outward spread)"
                ),
            ))
    return out


def _extents_findings(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    by_name = {o.name: o for o in report.objects}
    eps = 1e-3  # float ties from FBX round-trips are not real violations
    for upper, lower in EXTENTS_UPPER_LOWER_PAIRS:
        u = by_name.get(f"{upper}_Geo")
        lo_ = by_name.get(f"{lower}_Geo")
        if u is None or lo_ is None:
            continue
        # Roblox Y (height) = Blender z (index 2).
        if lo_.bbox_max[2] > u.bbox_max[2] + eps:
            out.append(Finding(
                validator="assembly.extents",
                severity="error",
                message=(
                    f"{lower}_Geo extends above {upper}_Geo "
                    f"(top {lo_.bbox_max[2]:.3f} > {u.bbox_max[2]:.3f})"
                ),
                remediation=f"Trim or re-bucket geometry so {lower} stays below {upper}'s top",
            ))
        if u.bbox_min[2] < lo_.bbox_min[2] - eps:
            out.append(Finding(
                validator="assembly.extents",
                severity="error",
                message=(
                    f"{upper}_Geo extends below {lower}_Geo "
                    f"(bottom {u.bbox_min[2]:.3f} < {lo_.bbox_min[2]:.3f})"
                ),
                remediation=f"Trim or re-bucket geometry so {upper} stays above {lower}'s bottom",
            ))
    return out


def _leg_overlap_findings(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    by_name = {o.name: o for o in report.objects}
    sides: dict[str, tuple[float, float]] = {}
    for side, members in _LEG_PARTS.items():
        objs = [by_name[m] for m in members if m in by_name]
        if not objs:
            return out
        mn = min(o.bbox_min[0] for o in objs)
        mx = max(o.bbox_max[0] for o in objs)
        sides[side] = (mn, mx)
    l_mn, l_mx = sides["Left"]
    r_mn, r_mx = sides["Right"]
    l_dim, r_dim = l_mx - l_mn, r_mx - r_mn
    x_diff = (r_mn + r_mx) / 2 - (l_mn + l_mx) / 2
    overlap = (l_dim + r_dim) / 2 - x_diff
    if overlap > 0:
        out.append(Finding(
            validator="assembly.legs",
            severity="warn",
            message=(
                f"Left/right legs overlap by {overlap:.3f} studs on X; the gate "
                "tolerates only a small server-tuned fraction of leg width"
            ),
            remediation=(
                "Separate the legs at rest (the autorig's midline trim normally "
                "guarantees zero overlap)"
            ),
        ))
    return out


def check_avatar(report: MeshReport) -> list[Finding]:
    return (
        _pose_findings(report)
        + _extents_findings(report)
        + _leg_overlap_findings(report)
    )
