from __future__ import annotations

from ..report import Finding, MeshReport
from ..roblox_spec import ACCESSORY_CATEGORIES, ATTACHMENTS_BY_BONE


def _required_attachments(spec_value) -> tuple[str, ...]:
    """Normalize AccessoryCategory.attachment (str | tuple) to a tuple."""
    if isinstance(spec_value, str):
        return (spec_value,)
    return tuple(spec_value)


def check_avatar(report: MeshReport) -> list[Finding]:
    """All `*_Att` attachments expected on a UGC avatar body."""
    out: list[Finding] = []
    present = {a.name for a in report.attachments}
    for bone, atts in ATTACHMENTS_BY_BONE.items():
        for att in atts:
            if att not in present:
                out.append(Finding(
                    validator="attachments.avatar",
                    severity="error",
                    message=f"Missing avatar attachment '{att}' (expected on {bone})",
                    remediation=f"Add an empty named '{att}' parented to {bone} in Blender",
                ))
    # Root_Att must be at origin per Roblox spec. Roblox's own RoundMale
    # template has it at ~0.012 studs off, so allow a small tolerance.
    for a in report.attachments:
        if a.name == "Root_Att":
            x, y, z = a.position
            if abs(x) > 0.05 or abs(y) > 0.05 or abs(z) > 0.05:
                out.append(Finding(
                    validator="attachments.avatar.root",
                    severity="error",
                    message=f"Root_Att must be at world origin; found at ({x:.3f}, {y:.3f}, {z:.3f})",
                    remediation="Move Root_Att empty to (0, 0, 0) before export",
                ))
    return out


def check_accessory(report: MeshReport, category: str) -> list[Finding]:
    out: list[Finding] = []
    spec = ACCESSORY_CATEGORIES.get(category)
    if spec is None:
        return out
    required = _required_attachments(spec.attachment)
    present = {a.name for a in report.attachments}
    for att in required:
        if att not in present:
            out.append(Finding(
                validator="attachments.accessory",
                severity="error",
                message=f"{spec.name} accessory missing required attachment '{att}'",
                remediation=(
                    f"Add an empty named '{att}' positioned where the accessory "
                    "should snap to the avatar"
                ),
            ))
    return out
