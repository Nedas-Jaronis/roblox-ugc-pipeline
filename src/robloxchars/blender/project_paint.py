"""Project a 2D image onto a mesh from a camera, then bake to a UV texture.

Strategy:
  1. Set up a camera looking at the mesh from a chosen direction (default: +Z).
  2. Create a new UV layer 'ProjUV' on the mesh.
  3. Use `uv.project_from_view` so 'ProjUV' maps each vertex to the camera-space
     normalized coordinates of the rendered view.
  4. Build a temporary material that samples the source image via 'ProjUV' and
     emits the result, so the bake captures color regardless of lighting.
  5. Bake to a NEW image using the mesh's primary UV layer (DiffuseUV) so the
     resulting PNG is a standard UV texture sheet.
  6. Replace the mesh's material with a clean Principled BSDF wired to the
     baked texture.

Single-view limitation: surfaces hidden from the camera get the back-projection
of whatever the camera saw straight behind them (stretched). For accessories
with a clear "front" (hats, glasses, capes) this is usually acceptable; for
fully-360 props you want multi-view.
"""

from __future__ import annotations

import math
from pathlib import Path

import bpy  # type: ignore
from mathutils import Vector  # type: ignore


def _world_bbox_center_and_radius(obj) -> tuple[Vector, float]:
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    xs = [v.x for v in corners]; ys = [v.y for v in corners]; zs = [v.z for v in corners]
    center = Vector(((min(xs)+max(xs))/2, (min(ys)+max(ys))/2, (min(zs)+max(zs))/2))
    radius = max((max(xs)-min(xs))/2, (max(ys)-min(ys))/2, (max(zs)-min(zs))/2)
    return center, radius


def setup_front_camera(obj, distance_factor: float = 2.5) -> bpy.types.Object:
    """Create + activate an orthographic camera looking at the mesh from +Z."""
    center, radius = _world_bbox_center_and_radius(obj)
    cam_data = bpy.data.cameras.new(name="rc_proj_cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = radius * 2.4  # a bit of margin
    cam = bpy.data.objects.new("rc_proj_cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    # Position the camera in front of the mesh (positive Z), looking toward origin.
    cam.location = center + Vector((0.0, 0.0, radius * distance_factor))
    # Camera looks along -Z by default; aim at mesh center.
    direction = (center - cam.location).normalized()
    # Convert direction into a rotation by aligning -Z to direction.
    rot_quat = direction.to_track_quat("-Z", "Y")
    cam.rotation_euler = rot_quat.to_euler()
    bpy.context.scene.camera = cam
    return cam


def project_uv_from_camera(obj) -> str:
    """Create a 'ProjUV' UV layer mapping vertices to camera-view normalized coords.

    We compute the projection manually (camera-space NDC) rather than relying
    on bpy.ops.uv.project_from_view, which depends on Blender's viewport state
    (the viewport must be in CAMERA view; merely setting scene.camera isn't
    enough). The manual path is deterministic and works whether Blender is
    headless or has an open 3D viewport.
    """
    if not obj.data.uv_layers:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=66.0, island_margin=0.02)
        bpy.ops.object.mode_set(mode="OBJECT")
    primary = obj.data.uv_layers.active.name

    if "ProjUV" in obj.data.uv_layers:
        obj.data.uv_layers.remove(obj.data.uv_layers["ProjUV"])
    proj = obj.data.uv_layers.new(name="ProjUV")
    obj.data.uv_layers.active = proj

    _manual_project_from_camera(obj)

    obj.data.uv_layers.active = obj.data.uv_layers[primary]
    return primary


def _manual_project_from_camera(obj) -> None:
    """Compute the ProjUV manually when no 3D viewport is available.

    Maps each loop's vertex to camera NDC then to (u, v) in [0, 1].
    """
    scene = bpy.context.scene
    cam = scene.camera
    if cam is None:
        raise RuntimeError("No active camera for manual projection")
    proj_layer = obj.data.uv_layers["ProjUV"]
    mat_world = obj.matrix_world
    cam_mat_inv = cam.matrix_world.inverted()
    # Compute orthographic projection extents.
    ortho_scale = cam.data.ortho_scale
    # Half-extent in camera local x/y.
    half = ortho_scale / 2

    for poly in obj.data.polygons:
        for loop_index in poly.loop_indices:
            vi = obj.data.loops[loop_index].vertex_index
            world_co = mat_world @ obj.data.vertices[vi].co
            cam_co = cam_mat_inv @ world_co
            u = (cam_co.x + half) / (2 * half)
            v = (cam_co.y + half) / (2 * half)
            proj_layer.data[loop_index].uv = (u, v)


def project_and_bake(
    obj,
    source_image_path: Path,
    out_png: Path,
    resolution: int = 2048,
    bake_margin: int = 16,
) -> dict:
    """Bake a SD-generated image onto the mesh's primary UV via camera projection."""
    setup_front_camera(obj)
    primary_uv = project_uv_from_camera(obj)

    # Load the SD source image.
    source_image = bpy.data.images.load(str(source_image_path), check_existing=True)
    source_image.colorspace_settings.name = "sRGB"

    # Target image (the eventual BaseColor).
    target_name = f"rc_proj_baked_{obj.name}"
    if target_name in bpy.data.images:
        bpy.data.images.remove(bpy.data.images[target_name])
    target = bpy.data.images.new(name=target_name, width=resolution, height=resolution, alpha=True)
    target.colorspace_settings.name = "sRGB"
    target.generated_color = (0, 0, 0, 0)

    # Build a temp material:
    #   ProjUV node -> ImageTexture(source, ProjUV) -> Emission -> Output
    # Bake EMIT into target using the primary UV layer.
    temp_mat = bpy.data.materials.new(name=f"{obj.name}_TempProj")
    temp_mat.use_nodes = True
    nt = temp_mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)

    out_node = nt.nodes.new("ShaderNodeOutputMaterial");  out_node.location = (400, 0)
    emit = nt.nodes.new("ShaderNodeEmission");            emit.location = (200, 0)
    src_tex = nt.nodes.new("ShaderNodeTexImage");         src_tex.location = (-200, 0)
    src_tex.image = source_image
    uv_proj = nt.nodes.new("ShaderNodeUVMap");            uv_proj.location = (-400, 0)
    uv_proj.uv_map = "ProjUV"
    # Target bake image node (must be selected+active).
    bake_target_node = nt.nodes.new("ShaderNodeTexImage"); bake_target_node.location = (-200, -300)
    bake_target_node.image = target
    bake_target_uv = nt.nodes.new("ShaderNodeUVMap");      bake_target_uv.location = (-400, -300)
    bake_target_uv.uv_map = primary_uv

    nt.links.new(uv_proj.outputs["UV"], src_tex.inputs["Vector"])
    nt.links.new(src_tex.outputs["Color"], emit.inputs["Color"])
    nt.links.new(emit.outputs["Emission"], out_node.inputs["Surface"])
    nt.links.new(bake_target_uv.outputs["UV"], bake_target_node.inputs["Vector"])

    for n in nt.nodes:
        n.select = False
    bake_target_node.select = True
    nt.nodes.active = bake_target_node

    # Replace the object's materials with the temp one so the bake sees it.
    obj.data.materials.clear()
    obj.data.materials.append(temp_mat)

    # Bake.
    scene = bpy.context.scene
    prev_engine = scene.render.engine
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 8
    scene.render.bake.margin = bake_margin
    scene.render.bake.use_pass_direct = False
    scene.render.bake.use_pass_indirect = False
    scene.render.bake.use_pass_color = True

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.bake(type="EMIT", margin=bake_margin, use_clear=True)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    target.filepath_raw = str(out_png)
    target.file_format = "PNG"
    target.save()

    # Replace with a clean Principled BSDF using the baked texture.
    final_mat = bpy.data.materials.new(name=f"{obj.name}_Mat")
    final_mat.use_nodes = True
    fnt = final_mat.node_tree
    for n in list(fnt.nodes):
        fnt.nodes.remove(n)
    fo = fnt.nodes.new("ShaderNodeOutputMaterial"); fo.location = (300, 0)
    fb = fnt.nodes.new("ShaderNodeBsdfPrincipled"); fb.location = (0, 0)
    ft = fnt.nodes.new("ShaderNodeTexImage");       ft.location = (-300, 0)
    ft.image = target
    fnt.links.new(ft.outputs["Color"], fb.inputs["Base Color"])
    fnt.links.new(fb.outputs["BSDF"], fo.inputs["Surface"])
    obj.data.materials.clear()
    obj.data.materials.append(final_mat)

    # Drop the projection camera and temp material so the export is clean.
    cam = bpy.data.objects.get("rc_proj_cam")
    if cam is not None:
        bpy.data.objects.remove(cam, do_unlink=True)
    if temp_mat.name in bpy.data.materials:
        bpy.data.materials.remove(temp_mat)

    scene.render.engine = prev_engine
    return {"status": "ok", "out_png": str(out_png), "resolution": resolution}
