from __future__ import annotations

from pathlib import Path

from ..report import Finding, MeshReport
from ..roblox_spec import TEXTURE_RULES


def _texture_too_large(path: str | None) -> tuple[bool, tuple[int, int] | None]:
    if not path:
        return False, None
    try:
        # Lazy import: Pillow is optional for material checks.
        from PIL import Image  # type: ignore
    except ImportError:
        return False, None
    p = Path(path)
    if not p.exists():
        return False, None
    try:
        with Image.open(p) as im:
            w, h = im.size
    except Exception:
        return False, None
    return (w > TEXTURE_RULES.max_texture_dim or h > TEXTURE_RULES.max_texture_dim), (w, h)


def check(report: MeshReport) -> list[Finding]:
    out: list[Finding] = []
    if not report.materials:
        out.append(Finding(
            validator="materials.present",
            severity="warn",
            message="No materials found on mesh",
            remediation="Assign at least a BaseColor material before export",
        ))
        return out

    for mat in report.materials:
        if mat.base_color_texture is None:
            out.append(Finding(
                validator="materials.basecolor",
                severity="warn",
                message=f"Material '{mat.name}' has no BaseColor texture",
                remediation="Bake or assign a BaseColor map; Roblox SurfaceAppearance expects PBR maps",
            ))
        for slot in ("normal_texture", "metallic_roughness_texture"):
            if getattr(mat, slot) is None:
                out.append(Finding(
                    validator=f"materials.{slot}",
                    severity="info",
                    message=f"Material '{mat.name}' missing {slot.replace('_', ' ')}",
                    remediation="Bake the PBR map for full SurfaceAppearance support",
                ))
        # Texture-size cap (2048x2048 per Roblox spec).
        for slot in ("base_color_texture", "normal_texture", "metallic_roughness_texture"):
            path = getattr(mat, slot)
            over, dims = _texture_too_large(path)
            if over and dims:
                w, h = dims
                out.append(Finding(
                    validator="materials.texture_size",
                    severity="error",
                    message=f"{mat.name}.{slot} is {w}x{h}; exceeds {TEXTURE_RULES.max_texture_dim} cap",
                    remediation=f"Downscale texture to <= {TEXTURE_RULES.max_texture_dim} on both axes",
                ))
    return out
