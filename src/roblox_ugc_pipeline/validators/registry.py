from __future__ import annotations

from ..report import MeshReport, ValidationResult
from . import (
    assembly,
    attachment_bounds,
    attachments,
    bounds,
    materials,
    polycount,
    render_checks,
    rig,
    scale_feasibility,
)


def validate_avatar(report: MeshReport) -> ValidationResult:
    findings: list = []
    findings += polycount.check_avatar(report)
    findings += bounds.check_avatar(report)
    findings += scale_feasibility.check_avatar(report)
    findings += rig.check_avatar(report)
    findings += attachments.check_avatar(report)
    findings += attachment_bounds.check_avatar(report)
    findings += assembly.check_avatar(report)
    findings += render_checks.check_avatar(report)
    findings += materials.check(report)
    return ValidationResult(target="avatar", findings=findings)


def validate_accessory(report: MeshReport, category: str) -> ValidationResult:
    findings: list = []
    findings += polycount.check_accessory(report, category)
    findings += bounds.check_accessory(report, category)
    findings += rig.check_accessory(report, category)
    findings += attachments.check_accessory(report, category)
    findings += materials.check(report)
    return ValidationResult(target="accessory", accessory_category=category, findings=findings)


def validate_prop(report: MeshReport) -> ValidationResult:
    findings: list = []
    findings += materials.check(report)
    return ValidationResult(target="prop", findings=findings)


def run_all(
    report: MeshReport,
    target: str,
    accessory_category: str | None = None,
) -> ValidationResult:
    if target == "avatar":
        return validate_avatar(report)
    if target == "accessory":
        if accessory_category is None:
            raise ValueError("accessory_category is required when target='accessory'")
        return validate_accessory(report, accessory_category)
    if target == "prop":
        return validate_prop(report)
    raise ValueError(f"Unknown target: {target}")
