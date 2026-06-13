from __future__ import annotations

from ..report import Finding, MeshReport
from ..roblox_spec import R15_BONES, R15_ROOT_ALIASES


def check_avatar(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    if report.armature is None:
        out.append(Finding(
            validator="rig.armature",
            severity="error",
            message="No armature found — UGC avatar bundles require an R15 rig",
            remediation="Add an R15 armature in Blender (Rigify-to-R15 template, or import Studio rig FBX)",
        ))
        return out

    present = set(report.armature.bone_names)
    missing = [
        b for b in R15_BONES
        if b not in present
        and not (b in R15_ROOT_ALIASES and present & R15_ROOT_ALIASES)
    ]
    if missing:
        out.append(Finding(
            validator="rig.bones",
            severity="error",
            message=f"R15 rig is missing required bones: {', '.join(missing)}",
            remediation="Rename bones to match the R15 standard exactly (case-sensitive)",
        ))

    unexpected = [b for b in present if b not in R15_BONES and b not in R15_ROOT_ALIASES]
    if unexpected:
        out.append(Finding(
            validator="rig.bones.extra",
            severity="warn",
            message=f"Armature has bones not in R15: {', '.join(sorted(unexpected))}",
            remediation="Remove extras or confirm they are intentional (e.g. face bones for FACS)",
        ))
    return out


def check_accessory(report: MeshReport, category: str) -> list[Finding]:
    out: list[Finding] = []
    if report.armature is not None and report.armature.bone_names:
        out.append(Finding(
            validator="rig.accessory",
            severity="warn",
            message="Accessory contains an armature; classic accessories should be rigid (no rig)",
            remediation="Remove the armature unless this is a LayeredClothing item",
        ))
    return out
