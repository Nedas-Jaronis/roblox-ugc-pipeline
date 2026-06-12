"""Port of Roblox's validateBodyPartChildAttachmentBounds (UGCValidation).

For every R15 part mesh, each attachment must sit inside a per-attachment
oriented box expressed in normalized mesh-space: (position - bbox_center) /
bbox_half_size, so the mesh spans [-1, 1]. Rig attachments (joints) are
checked against TWO parts — the child's box and the parent's box — at the
same world position, exactly like the upload gate. Rules vendored in
ugc_validation_rules.py.
"""

from __future__ import annotations

from ..report import Finding, MeshReport
from ..roblox_spec import ATTACHMENTS_BY_BONE
from ..ugc_validation_rules import (
    BODY_ATTACHMENT_BOUNDS,
    att_to_roblox_name,
    box_contains,
    clamp_to_box,
    normalize_to_mesh_space,
)

# Names already covered by attachments.check_avatar's missing-attachment scan;
# don't double-report those as missing here.
_DOCS_COVERED: frozenset[str] = frozenset(
    att_to_roblox_name(a) for atts in ATTACHMENTS_BY_BONE.values() for a in atts
)


def check_avatar(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    by_name = {o.name: o for o in report.objects}
    atts_by_roblox_name = {
        att_to_roblox_name(a.name): a for a in report.attachments
    }

    for part, rules in BODY_ATTACHMENT_BOUNDS.items():
        geo = by_name.get(f"{part}_Geo")
        if geo is None:
            continue
        for att_name, box in rules.items():
            att = atts_by_roblox_name.get(att_name)
            if att is None:
                if att_name not in _DOCS_COVERED:
                    out.append(Finding(
                        validator="attachments.bounds",
                        severity="error",
                        message=f"Missing attachment '{att_name}' required in {part}",
                        remediation=(
                            f"Stamp an empty named '{att_name}' (or the "
                            f"'*_Att' equivalent) on the {part} bone — the "
                            "autorig adds it when stamp_attachments=True"
                        ),
                    ))
                continue
            pos = normalize_to_mesh_space(att.position, geo.bbox_min, geo.bbox_max)
            if pos is None:
                out.append(Finding(
                    validator="attachments.bounds",
                    severity="warn",
                    message=f"{part}_Geo bbox is degenerate; cannot place '{att_name}'",
                    remediation=f"Check {part}_Geo has real volume on all three axes",
                ))
                continue
            if not box_contains(box, pos):
                closest = clamp_to_box(pos, box)
                out.append(Finding(
                    validator="attachments.bounds",
                    severity="error",
                    message=(
                        f"Attachment '{att.name}' sits outside its valid region of "
                        f"{part}_Geo: normalized mesh-space position "
                        f"({pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f}) must be within "
                        f"min {box[0]} / max {box[1]} "
                        f"(closest valid: ({closest[0]:.2f}, {closest[1]:.2f}, {closest[2]:.2f}))"
                    ),
                    remediation=(
                        "Move the attachment empty inside the box (axes: +X=character "
                        "right, +Y=up, -Z=front, in fractions of the part bbox half-size), "
                        "or re-run the autorig which clamps attachments automatically"
                    ),
                ))
    return out
