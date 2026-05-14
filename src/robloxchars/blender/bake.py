"""Bake the mesh's effective BaseColor into a single PNG texture.

Why we need this:
  * `cube3d` outputs untextured meshes (vertex colors at best).
  * Many Sketchfab models import with materials that lack a BaseColor map,
    relying on procedural/vertex-color shading.
  * Roblox `SurfaceAppearance` needs a real PBR map; ImageColor alone is
    permitted but PBR is recommended.

The bake produces one shared 2048x2048 BaseColor PNG for the whole mesh and
rewrites all materials to a clean Principled BSDF wired to that texture.

Phase 1 (this module): BaseColor only, single shared image.
Phase 2 (future): Normal + MetallicRoughness, plus an emission fallback for
vertex-color meshes whose existing shaders don't surface color.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import bpy  # type: ignore


def _ensure_uv(obj: bpy.types.Object, smart_project_if_missing: bool = True) -> str:
    """Return the name of an active UV map; create one if missing."""
    if obj.data.uv_layers and obj.data.uv_layers.active:
        return obj.data.uv_layers.active.name
    if not smart_project_if_missing:
        raise RuntimeError(f"No UV map on '{obj.name}' and smart_project disabled")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=66.0, island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj.data.uv_layers.active.name


def _new_target_image(name: str, size: int) -> bpy.types.Image:
    img = bpy.data.images.get(name)
    if img is not None:
        bpy.data.images.remove(img)
    img = bpy.data.images.new(name=name, width=size, height=size, alpha=True)
    img.colorspace_settings.name = "sRGB"
    img.generated_color = (0.0, 0.0, 0.0, 0.0)
    img.generated_type = "BLANK"
    return img


def _set_active_bake_target(material: bpy.types.Material, image: bpy.types.Image) -> None:
    """Add (or reuse) an Image Texture node pointing at `image` and make it active."""
    if not material.use_nodes:
        material.use_nodes = True
    nt = material.node_tree
    tex_node = None
    for n in nt.nodes:
        if n.type == "TEX_IMAGE" and n.label == "rc_bake_target":
            tex_node = n
            break
    if tex_node is None:
        tex_node = nt.nodes.new(type="ShaderNodeTexImage")
        tex_node.label = "rc_bake_target"
        tex_node.location = (-600, -300)
    tex_node.image = image
    # The node must be selected AND active for Cycles to bake into it.
    for n in nt.nodes:
        n.select = False
    tex_node.select = True
    nt.nodes.active = tex_node


def _replace_material_with_baked(obj: bpy.types.Object, image: bpy.types.Image, mat_name: str) -> None:
    """Replace all material slots on `obj` with a single material that wires
    the baked image into a Principled BSDF's BaseColor."""
    # Build the clean material first.
    mat = bpy.data.materials.new(name=mat_name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new(type="ShaderNodeOutputMaterial")
    out.location = (300, 0)
    bsdf = nt.nodes.new(type="ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    tex = nt.nodes.new(type="ShaderNodeTexImage")
    tex.location = (-400, 0)
    tex.image = image
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    # Drop existing slots and add just this one.
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _has_useful_basecolor(materials: Iterable[bpy.types.Material]) -> bool:
    """Heuristic: does ANY material have a non-default BaseColor source?"""
    for mat in materials:
        if not mat.use_nodes:
            return True  # solid color material — count as useful
        nt = mat.node_tree
        for n in nt.nodes:
            if n.type == "BSDF_PRINCIPLED":
                bc = n.inputs.get("Base Color")
                if bc and bc.is_linked:
                    return True
                if bc and tuple(bc.default_value)[:3] != (0.8, 0.8, 0.8):
                    return True
            if n.type == "TEX_IMAGE" and n.image is not None:
                return True
            if n.type == "ATTRIBUTE":  # vertex colors
                return True
    return False


def _inject_vertex_color_fallback(obj: bpy.types.Object) -> bool:
    """If the mesh has vertex colors but no material reads them, wire them up.

    Returns True if a fallback was injected.
    """
    color_layers = getattr(obj.data, "color_attributes", None) or obj.data.vertex_colors
    if not color_layers:
        return False
    layer_name = color_layers.active.name if color_layers.active else color_layers[0].name
    for mat in obj.data.materials:
        if mat is None:
            continue
        if not mat.use_nodes:
            mat.use_nodes = True
        nt = mat.node_tree
        # Find Principled BSDF.
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        bc = bsdf.inputs.get("Base Color")
        if bc is None or bc.is_linked:
            continue
        # Plug a Vertex Color attribute node into BaseColor.
        attr = nt.nodes.new(type="ShaderNodeAttribute")
        attr.attribute_name = layer_name
        attr.location = (-400, 200)
        nt.links.new(attr.outputs["Color"], bc)
    return True


# ---- main entrypoint --------------------------------------------------------

def bake_basecolor(
    obj: bpy.types.Object,
    out_png: Path,
    resolution: int = 2048,
    bake_margin: int = 16,
) -> dict:
    """Bake all materials' BaseColor into a single PNG.

    Returns a dict log with what happened.
    """
    log: dict = {"resolution": resolution, "out_png": str(out_png)}

    if not obj.data.materials or all(m is None for m in obj.data.materials):
        # Slap a default material on so there's something to bake.
        m = bpy.data.materials.new("rc_default")
        m.use_nodes = True
        obj.data.materials.append(m)
        log["created_default_material"] = True

    if not _has_useful_basecolor([m for m in obj.data.materials if m]):
        injected = _inject_vertex_color_fallback(obj)
        log["vertex_color_fallback"] = injected

    _ensure_uv(obj)

    image = _new_target_image(name=f"rc_bake_{obj.name}", size=resolution)
    for mat in obj.data.materials:
        if mat is None:
            continue
        _set_active_bake_target(mat, image)

    # Switch to Cycles + CPU for predictable headless behavior.
    scene = bpy.context.scene
    prev_engine = scene.render.engine
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.render.bake.margin = bake_margin
    scene.render.bake.use_pass_direct = False
    scene.render.bake.use_pass_indirect = False
    scene.render.bake.use_pass_color = True

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.object.bake(
        type="DIFFUSE",
        pass_filter={"COLOR"},
        margin=bake_margin,
        use_clear=True,
        use_selected_to_active=False,
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    image.filepath_raw = str(out_png)
    image.file_format = "PNG"
    image.save()

    _replace_material_with_baked(obj, image, mat_name=f"{obj.name}_Mat")

    scene.render.engine = prev_engine
    log["status"] = "ok"
    return log
