from __future__ import annotations

from ..report import Finding, MeshReport
from ..roblox_spec import ACCESSORY_CATEGORIES, AVATAR_BUDGET


def _extents_studs(report: MeshReport) -> tuple[float, float, float]:
    """Best-effort conversion to studs based on the reported units."""
    ex = report.extents()
    if report.units == "studs":
        return ex
    if report.units == "meters":
        # 1 stud ~= 0.28 m
        return (ex[0] / 0.28, ex[1] / 0.28, ex[2] / 0.28)
    if report.units == "centimeters":
        return (ex[0] / 28.0, ex[1] / 28.0, ex[2] / 28.0)
    return ex  # unknown — caller may add a separate warning


def check_avatar(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    if report.units == "unknown":
        out.append(Finding(
            validator="bounds.units",
            severity="warn",
            message="Could not determine units from source file; assuming studs for bounds check",
            remediation="Set scene units in Blender before exporting, or re-export with units metadata",
        ))
    _, height, _ = _extents_studs(report)
    if height < AVATAR_BUDGET.height_min:
        out.append(Finding(
            validator="bounds.avatar.height",
            severity="error",
            message=f"Avatar height {height:.2f} studs is below minimum {AVATAR_BUDGET.height_min}",
            remediation="Scale up the model; standard R15 avatars are ~5.4 studs tall",
        ))
    elif height > AVATAR_BUDGET.height_max:
        out.append(Finding(
            validator="bounds.avatar.height",
            severity="error",
            message=f"Avatar height {height:.2f} studs is above maximum {AVATAR_BUDGET.height_max}",
            remediation="Scale down the model; standard R15 avatars are ~5.4 studs tall",
        ))
    return out


def check_accessory(report: MeshReport, category: str) -> list[Finding]:
    out: list[Finding] = []
    spec = ACCESSORY_CATEGORIES.get(category)
    if spec is None:
        return out
    ex = _extents_studs(report)
    for axis, (val, lim) in enumerate(zip(ex, spec.max_bounds)):
        if val > lim:
            axis_name = "xyz"[axis]
            out.append(Finding(
                validator="bounds.accessory",
                severity="error",
                message=f"{spec.name} {axis_name}-extent {val:.2f} studs exceeds limit {lim} studs",
                remediation=f"Scale down or trim mesh along the {axis_name} axis",
            ))
    return out
