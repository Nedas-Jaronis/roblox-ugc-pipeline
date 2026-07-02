"""From-scratch ports of the gate checks that live in Roblox's C++ engine —
the ones our Luau-derived validators couldn't see until now. Measurements are
taken in Blender by inspect.py; this module only applies thresholds.

1. Origin offset: Studio's importer re-centers each mesh and records the
   removed offset as ImportOrigin/CageOrigin, whose PositionMagnitude is
   capped at 8 / 10 — so the pre-export origin-to-bbox-center distance is
   what must stay under those caps. (The 0.001 validateMeshIsAtOrigin gate
   applies to the POST-import mesh space, which the importer guarantees;
   Roblox's own template .blend has offsets up to ~2.4 and passes.)
2. Opacity (validateAssetTransparency): Studio renders 6 orthographic views
   of each limb asset and fails when the opaque fraction of the asset bbox is
   below a per-view threshold. We approximate with a silhouette raster —
   material-independent, so treat marginal results as warnings.
3. Cage fit: max cage-vertex distance to the render mesh (0.60 head / 0.30
   parts) and render-vs-cage bbox size difference (max 1.00 per axis,
   Constants.RenderVsWrapMeshMaxDiff).

Thresholds calibrated from live Studio validation output (2026-06-12 runs).
"""

from __future__ import annotations

from ..report import Finding, MeshReport

_IMPORT_ORIGIN_CAP = 8.0
_CAGE_ORIGIN_CAP = 10.0

# Live-observed per-view opacity minimums: sides/front/back 0.30 (head 0.35),
# top/bottom 0.10.
_FILL_SIDES = {"Head": 0.35}
_FILL_SIDES_DEFAULT = 0.30
_FILL_TOPBOTTOM = 0.10

_CAGE_DIST_MAX = {"Head_OuterCage": 0.60}
_CAGE_DIST_DEFAULT = 0.30
_CAGE_SIZE_DIFF_MAX = 1.00


def _origin_findings(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    for o in report.objects:
        if o.origin_offset is None:
            continue
        if o.name.endswith("_OuterCage"):
            cap, prop = _CAGE_ORIGIN_CAP, "CageOrigin"
        elif o.name.endswith("_Geo"):
            cap, prop = _IMPORT_ORIGIN_CAP, "ImportOrigin"
        else:
            continue
        if o.origin_offset <= cap:
            continue
        out.append(Finding(
            validator="render.mesh_origin",
            severity="error",
            message=(
                f"{o.name} geometry sits {o.origin_offset:.2f} studs from its "
                f"object origin — Studio's {prop}.PositionMagnitude cap is {cap:.0f}"
            ),
            remediation=(
                "Move the model to the world origin before export, or re-origin "
                "the part to its bounds center (the autorig does this automatically)"
            ),
        ))
    return out


def _opacity_findings(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    for group, fill in report.group_view_fill.items():
        sides_thr = _FILL_SIDES.get(group, _FILL_SIDES_DEFAULT)
        checks = (
            ("x", "Left/Right", sides_thr),
            ("y", "Front/Back", sides_thr),
            ("z", "Top/Bottom", _FILL_TOPBOTTOM),
        )
        for key, label, thr in checks:
            v = fill.get(key)
            if v is None or v >= thr:
                continue
            # Silhouette raster is an approximation of Studio's render-based
            # opacity — clear misses are errors, marginal ones warnings.
            sev = "error" if v < 0.7 * thr else "warn"
            out.append(Finding(
                validator="render.opacity",
                severity=sev,
                message=(
                    f"{group} silhouette fills only {v:.2f} of its bbox from the "
                    f"{label} view (Studio requires opacity > {thr:.2f}); "
                    "sparse geometry relative to the bbox fails the gate"
                ),
                remediation=(
                    "Remove stray geometry inflating the bbox, or thicken the "
                    "part along the failing axis — opacity is fill-fraction "
                    "RELATIVE to the part bbox"
                ),
            ))
    return out


def _cage_findings(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    by_name = {o.name: o for o in report.objects}
    for o in report.objects:
        if not o.name.endswith("_OuterCage"):
            continue
        cap = _CAGE_DIST_MAX.get(o.name, _CAGE_DIST_DEFAULT)
        if o.cage_max_dist is not None and o.cage_max_dist > cap:
            out.append(Finding(
                validator="render.cage_distance",
                severity="error",
                message=(
                    f"{o.name} has a vertex {o.cage_max_dist:.2f} studs from the "
                    f"render mesh (max {cap:.2f})"
                ),
                remediation="Shrinkwrap the cage to the render mesh so it hugs the surface",
            ))
        geo = by_name.get(o.name[: -len("_OuterCage")] + "_Geo")
        if geo is not None:
            for axis in range(3):
                gs = geo.bbox_max[axis] - geo.bbox_min[axis]
                cs = o.bbox_max[axis] - o.bbox_min[axis]
                if abs(gs - cs) > _CAGE_SIZE_DIFF_MAX:
                    out.append(Finding(
                        validator="render.cage_size",
                        severity="error",
                        message=(
                            f"{o.name} differs from its render mesh by "
                            f"{abs(gs - cs):.2f} studs on axis {'XYZ'[axis]} "
                            f"(max {_CAGE_SIZE_DIFF_MAX:.2f})"
                        ),
                        remediation="Regenerate the cage from the final decimated mesh",
                    ))
                    break
    return out


def check_avatar(report: MeshReport) -> list[Finding]:
    return (
        _origin_findings(report)
        + _opacity_findings(report)
        + _cage_findings(report)
    )
