"""Can ANY uniform scale fit every body part inside its min/max range?

Replicates Studio's "there is no valid scale that passes individual body part
requirements" check locally. Marketplace body validation searches for ONE
uniform scale factor at which every assembled part group (Head, Torso, each
Arm, each Leg — and the total body) fits its [min, max] size box
simultaneously. Per group the admissible scales form an interval

    [ max_axis(min_size/size),  min_axis(max_size/size) ]

and a body is feasible iff the intersection of all group intervals is
non-empty. Because the check is scale-invariant, it tells you whether the
model's PROPORTIONS can ever pass — long before a Studio round-trip — and,
when they can't, which two parts are in conflict and by how much.
"""

from __future__ import annotations

from ..report import Finding, MeshReport
from ..roblox_spec import (
    BODY_GROUP_MEMBERS,
    BODY_SCALE_BOUNDS,
    PartScaleBounds,
    SCALE_GROUP_FOR_BODY_GROUP,
)
from ..ugc_validation_rules import UGC_ASSET_SIZE_BOUNDS

# Second rule set: the bounds Roblox's own UGCValidation module enforced
# (vendored from the Client-Tracker mirror). Differs from the creator-docs
# table in places (e.g. Torso Classic max 3.5x3.25 vs docs 4.0x3.8); a body
# feasible under BOTH sets is safe regardless of which the live gate uses.
# No "Total" row — the mirror checks per-asset bounds only.
_MIRROR_BOUNDS: dict[str, dict[str, PartScaleBounds]] = {
    body_type: {part: PartScaleBounds(mn, mx) for part, (mn, mx) in parts.items()}
    for body_type, parts in UGC_ASSET_SIZE_BOUNDS.items()
}

_EPS = 1e-9


def _studs_factor(units: str) -> float:
    if units == "meters":
        return 1.0 / 0.28
    if units == "centimeters":
        return 1.0 / 28.0
    return 1.0  # studs or unknown (verdict is scale-invariant either way)


def _group_sizes_studs(report: MeshReport) -> dict[str, tuple[float, float, float]]:
    """Union bbox per validation group, converted to studs and ROBLOX axes.

    inspect.py emits Blender Z-up world coords; Roblox validates in Y-up, so
    (x, y, z)_blender maps to (X=width, Y=height, Z=depth) as (x, z, y).
    """
    f = _studs_factor(report.units)
    by_name = {o.name: o for o in report.objects}
    sizes: dict[str, tuple[float, float, float]] = {}
    all_min = [float("inf")] * 3
    all_max = [float("-inf")] * 3
    for group, members in BODY_GROUP_MEMBERS.items():
        objs = [by_name[m] for m in members if m in by_name]
        if not objs:
            continue
        mn = [min(o.bbox_min[i] for o in objs) for i in range(3)]
        mx = [max(o.bbox_max[i] for o in objs) for i in range(3)]
        for i in range(3):
            all_min[i] = min(all_min[i], mn[i])
            all_max[i] = max(all_max[i], mx[i])
        ex = [(mx[i] - mn[i]) * f for i in range(3)]
        sizes[group] = (ex[0], ex[2], ex[1])  # Blender (x,y,z) -> Roblox (X,Y,Z)
    if sizes:
        ex = [(all_max[i] - all_min[i]) * f for i in range(3)]
        sizes["Total"] = (ex[0], ex[2], ex[1])
    return sizes


def feasible_scale_range(
    report: MeshReport, body_type: str,
    bounds_table: dict[str, dict[str, PartScaleBounds]] = BODY_SCALE_BOUNDS,
) -> tuple[float, float, str, str] | None:
    """Return (lo, hi, lo_binder, hi_binder) for this body type, or None if
    the report has no recognizable body groups. lo > hi means infeasible;
    the binder strings name the part+axis forcing each end of the interval."""
    sizes = _group_sizes_studs(report)
    if not sizes:
        return None
    bounds = bounds_table[body_type]
    lo, hi = 0.0, float("inf")
    lo_binder, hi_binder = "", ""
    for group, size in sizes.items():
        spec_key = "Total" if group == "Total" else SCALE_GROUP_FOR_BODY_GROUP[group]
        b = bounds.get(spec_key)
        if b is None:
            continue
        for axis in range(3):
            if size[axis] <= _EPS:
                continue
            axis_name = "XYZ"[axis]
            g_lo = b.min_size[axis] / size[axis]
            g_hi = b.max_size[axis] / size[axis]
            if g_lo > lo:
                lo, lo_binder = g_lo, f"{group} {axis_name} (min {b.min_size[axis]} / size {size[axis]:.2f})"
            if g_hi < hi:
                hi, hi_binder = g_hi, f"{group} {axis_name} (max {b.max_size[axis]} / size {size[axis]:.2f})"
    return lo, hi, lo_binder, hi_binder


def check_avatar(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    sizes = _group_sizes_studs(report)
    if not sizes:
        out.append(Finding(
            validator="scale.feasibility",
            severity="warn",
            message="No <Bone>_Geo body groups found; cannot evaluate scale feasibility",
            remediation="Run on an autorig-produced report with Head_Geo/UpperTorso_Geo/... objects",
        ))
        return out

    for validator_name, table, label in (
        ("scale.feasibility", BODY_SCALE_BOUNDS, "creator-docs bounds"),
        ("scale.feasibility.ugc", _MIRROR_BOUNDS, "UGCValidation-mirror bounds"),
    ):
        feasible: dict[str, tuple[float, float]] = {}
        tightest: tuple[float, str, str] | None = None  # (gap, lo_binder, hi_binder)
        for body_type in table:
            rng = feasible_scale_range(report, body_type, table)
            if rng is None:
                continue
            lo, hi, lo_b, hi_b = rng
            if lo <= hi:
                feasible[body_type] = (lo, hi)
            else:
                gap = lo / hi
                if tightest is None or gap < tightest[0]:
                    tightest = (gap, lo_b, hi_b)

        if feasible:
            ranges = ", ".join(f"{bt} x[{lo:.2f}, {hi:.2f}]" for bt, (lo, hi) in feasible.items())
            out.append(Finding(
                validator=validator_name,
                severity="info",
                message=(
                    f"A valid marketplace scale EXISTS under {label} — "
                    f"feasible uniform-scale ranges: {ranges}"
                ),
                remediation=None,
            ))
        else:
            gap, lo_b, hi_b = tightest  # type: ignore[misc]
            out.append(Finding(
                validator=validator_name,
                severity="error",
                message=(
                    f"No uniform scale fits every body part in its min/max range "
                    f"under {label} "
                    "(Studio: 'no valid scale passes individual body part requirements'). "
                    f"Binding conflict: scale must be >= {lo_b} but <= {hi_b} "
                    f"— proportions are off by a factor of {gap:.2f}."
                ),
                remediation=(
                    "Fix PROPORTIONS, not overall size: shrink the part forcing the "
                    "lower bound or grow the part forcing the upper bound until the "
                    "ranges overlap."
                ),
            ))
    return out
