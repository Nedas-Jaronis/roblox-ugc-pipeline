from __future__ import annotations

from ..report import Finding, MeshReport
from ..roblox_spec import (
    ACCESSORY_CATEGORIES,
    AVATAR_BUDGET,
    BODY_GROUP_MEMBERS,
    BODY_GROUP_TRI_BUDGET,
)


def check_avatar(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    total = report.triangle_count
    if total > AVATAR_BUDGET.max_total_tris:
        out.append(Finding(
            validator="polycount.avatar.total",
            severity="error",
            message=f"Total triangles {total} exceeds avatar budget {AVATAR_BUDGET.max_total_tris}",
            remediation=f"Run `robloxchars prep --decimate {AVATAR_BUDGET.max_total_tris}` or decimate per-group in Blender",
        ))

    # Group-level enforcement — sum tris across the meshes in each body group.
    by_name = {o.name: o for o in report.objects}
    for group, members in BODY_GROUP_MEMBERS.items():
        members_present = [m for m in members if m in by_name]
        if not members_present:
            continue  # group entirely missing — handled by mesh-naming check below
        group_tris = sum(by_name[m].triangle_count for m in members_present)
        budget = BODY_GROUP_TRI_BUDGET[group]
        if group_tris > budget:
            out.append(Finding(
                validator="polycount.avatar.group",
                severity="error",
                message=(
                    f"{group} group ({'+'.join(members_present)}) totals "
                    f"{group_tris} tris (hard cap {budget})"
                ),
                remediation=f"Decimate one or more meshes in the {group} group to fit {budget} tris",
            ))

    # Flag unrecognized *_Geo names.
    known = {m for ms in BODY_GROUP_MEMBERS.values() for m in ms}
    for obj in report.objects:
        if obj.name.endswith("_Geo") and obj.name not in known:
            out.append(Finding(
                validator="polycount.avatar.unknown_part",
                severity="warn",
                message=f"Mesh '{obj.name}' is *_Geo-named but not in the R15 mesh list",
                remediation="Rename to match a standard R15 mesh (Head_Geo, UpperTorso_Geo, ...)",
            ))
    return out


def check_accessory(report: MeshReport, category: str) -> list[Finding]:
    out: list[Finding] = []
    spec = ACCESSORY_CATEGORIES.get(category)
    if spec is None:
        out.append(Finding(
            validator="polycount.accessory",
            severity="error",
            message=f"Unknown accessory category '{category}'",
        ))
        return out
    total = report.triangle_count
    if total > spec.max_tris:
        out.append(Finding(
            validator="polycount.accessory",
            severity="error",
            message=f"{spec.name} accessory: {total} tris exceeds hard cap {spec.max_tris}",
            remediation=f"Decimate to <= {spec.max_tris}",
        ))
    return out
