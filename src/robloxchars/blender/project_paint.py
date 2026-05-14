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


def _bbox_of_many(objs) -> tuple[Vector, Vector]:
    xs: list[float] = []; ys: list[float] = []; zs: list[float] = []
    for o in objs:
        for c in o.bound_box:
            v = o.matrix_world @ Vector(c)
            xs.append(v.x); ys.append(v.y); zs.append(v.z)
    if not xs:
        return Vector((0, 0, 0)), Vector((0, 0, 0))
    return Vector((min(xs), min(ys), min(zs))), Vector((max(xs), max(ys), max(zs)))


def setup_front_camera(
    objs,
    distance_factor: float = 2.5,
    name: str = "rc_proj_cam",
    view_axis: str = "+Z",
) -> bpy.types.Object:
    """Create + activate an orthographic camera framing the mesh(es).

    `view_axis` is the axis the camera looks DOWN from:
      * '+Z' — camera at +Z, looks straight down (default; accessories
        autoprep'd to Y-up-in-stud-space appear "face-up" from this angle)
      * '-Y' — camera at -Y, looks toward +Y (Blender front view; used for
        Z-up avatars whose faces are on the +Y side)
      * '+Y' / '-Z' / '+X' / '-X' also supported

    `objs` may be a single object or an iterable.
    """
    if isinstance(objs, bpy.types.Object):
        objs = [objs]
    objs = list(objs)
    if not objs:
        raise ValueError("setup_front_camera: empty objs")

    axes = {
        "+X": Vector(( 1.0,  0.0,  0.0)),
        "-X": Vector((-1.0,  0.0,  0.0)),
        "+Y": Vector(( 0.0,  1.0,  0.0)),
        "-Y": Vector(( 0.0, -1.0,  0.0)),
        "+Z": Vector(( 0.0,  0.0,  1.0)),
        "-Z": Vector(( 0.0,  0.0, -1.0)),
    }
    if view_axis not in axes:
        raise ValueError(f"view_axis must be one of {list(axes)}, got {view_axis!r}")
    look_from = axes[view_axis]

    bb_min, bb_max = _bbox_of_many(objs)
    center = (bb_min + bb_max) * 0.5
    extents = bb_max - bb_min
    # Take the two axes perpendicular to look_from for the ortho frame size.
    perp_extents = [extents[i] for i in range(3) if abs(look_from[i]) < 0.5]
    ortho_scale = (max(perp_extents) if perp_extents else max(extents)) * 1.15 or 1.0
    radius = max(extents) / 2 or 1.0

    cam_data = bpy.data.cameras.new(name=name)
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ortho_scale
    cam = bpy.data.objects.new(name, cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = center + look_from * max(radius * distance_factor, 5.0)
    direction = (center - cam.location).normalized()
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
    shared_camera: bpy.types.Object | None = None,
    cleanup_camera: bool = True,
) -> dict:
    """Bake a SD-generated image onto the mesh's primary UV via camera projection.

    Pass `shared_camera` to reuse one camera across multiple meshes (e.g. all
    `_Geo` pieces of an avatar baked from the same front view). When omitted,
    a fresh per-object camera is built and cleaned up at the end.
    """
    own_camera = shared_camera is None
    if own_camera:
        setup_front_camera(obj)
    else:
        bpy.context.scene.camera = shared_camera
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

    # Drop the projection camera (if we built one) and temp material.
    if cleanup_camera and own_camera:
        cam = bpy.data.objects.get("rc_proj_cam")
        if cam is not None:
            bpy.data.objects.remove(cam, do_unlink=True)
    if temp_mat.name in bpy.data.materials:
        bpy.data.materials.remove(temp_mat)

    scene.render.engine = prev_engine
    return {"status": "ok", "out_png": str(out_png), "resolution": resolution}


def _bake_facing_weight(
    obj,
    camera: bpy.types.Object,
    out_png: Path,
    resolution: int = 1024,
    bake_margin: int = 16,
) -> None:
    """Bake a grayscale facing weight (0..1) for the camera onto the primary UV.

    Each surface point's value is max(0, dot(normal, -view_dir)). Surface points
    facing the camera get value 1.0; those facing away get 0.0. Used as a
    blending weight when combining multiple view projections.

    Assumes ProjUV was already created on the object (or will create a fresh
    primary UV via Smart UV Project if missing).
    """
    # Camera forward direction in world space.
    cam_forward_world = camera.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))
    cam_forward_world.normalize()

    # Use primary UV (smart-unwrapped during the projection pass).
    if not obj.data.uv_layers:
        raise RuntimeError("primary UV missing — call project_uv_from_camera first")
    primary_uv = obj.data.uv_layers[0].name if obj.data.uv_layers.active is None else obj.data.uv_layers.active.name

    target_name = f"rc_facing_{obj.name}"
    if target_name in bpy.data.images:
        bpy.data.images.remove(bpy.data.images[target_name])
    target = bpy.data.images.new(name=target_name, width=resolution, height=resolution, alpha=True)
    target.colorspace_settings.name = "Non-Color"
    target.generated_color = (0, 0, 0, 1)

    mat = bpy.data.materials.new(name=f"{obj.name}_FacingMat")
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes): nt.nodes.remove(n)

    out_n = nt.nodes.new("ShaderNodeOutputMaterial"); out_n.location = (400, 0)
    emit = nt.nodes.new("ShaderNodeEmission");         emit.location = (200, 0)
    geom = nt.nodes.new("ShaderNodeNewGeometry");      geom.location = (-400, 200)

    # Constant: -camera_forward (the direction from surface toward camera).
    neg_forward = nt.nodes.new("ShaderNodeCombineXYZ"); neg_forward.location = (-400, -100)
    neg_forward.inputs[0].default_value = -cam_forward_world.x
    neg_forward.inputs[1].default_value = -cam_forward_world.y
    neg_forward.inputs[2].default_value = -cam_forward_world.z

    dot = nt.nodes.new("ShaderNodeVectorMath"); dot.location = (-150, 0)
    dot.operation = "DOT_PRODUCT"
    nt.links.new(geom.outputs["Normal"], dot.inputs[0])
    nt.links.new(neg_forward.outputs["Vector"], dot.inputs[1])

    clamp = nt.nodes.new("ShaderNodeMath"); clamp.location = (50, 0)
    clamp.operation = "MAXIMUM"; clamp.inputs[1].default_value = 0.0
    nt.links.new(dot.outputs["Value"], clamp.inputs[0])

    nt.links.new(clamp.outputs["Value"], emit.inputs["Color"])
    nt.links.new(emit.outputs["Emission"], out_n.inputs["Surface"])

    bake_target_node = nt.nodes.new("ShaderNodeTexImage"); bake_target_node.location = (200, -300)
    bake_target_node.image = target
    bake_target_uv = nt.nodes.new("ShaderNodeUVMap");      bake_target_uv.location = (0, -300)
    bake_target_uv.uv_map = primary_uv
    nt.links.new(bake_target_uv.outputs["UV"], bake_target_node.inputs["Vector"])
    for n in nt.nodes: n.select = False
    bake_target_node.select = True
    nt.nodes.active = bake_target_node

    # Stash existing materials, swap in the facing mat, bake, restore.
    saved_materials = list(obj.data.materials)
    obj.data.materials.clear()
    obj.data.materials.append(mat)

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 4
    scene.render.bake.margin = bake_margin
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.bake(type="EMIT", margin=bake_margin, use_clear=True)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    target.filepath_raw = str(out_png)
    target.file_format = "PNG"
    target.save()

    # Restore.
    obj.data.materials.clear()
    for m in saved_materials:
        obj.data.materials.append(m)
    bpy.data.materials.remove(mat)


def _load_image_pixels(path: Path):
    """Load an image via Blender's API and return (H, W, 4) float32 array."""
    import numpy as np
    img = bpy.data.images.load(str(path), check_existing=False)
    w, h = img.size
    arr = np.array(img.pixels[:], dtype=np.float32).reshape((h, w, 4))
    bpy.data.images.remove(img)
    return arr


def _save_image_pixels(arr, path: Path) -> None:
    """Save a (H, W, 4) float32 array as a PNG via Blender's API."""
    h, w, _ = arr.shape
    name = f"rc_composite_{path.stem}"
    if name in bpy.data.images:
        bpy.data.images.remove(bpy.data.images[name])
    img = bpy.data.images.new(name=name, width=w, height=h, alpha=True)
    img.colorspace_settings.name = "sRGB"
    img.pixels = arr.flatten().tolist()
    path.parent.mkdir(parents=True, exist_ok=True)
    img.filepath_raw = str(path)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def _composite_views_with_facing(
    view_color_paths: dict[str, Path],
    view_facing_paths: dict[str, Path],
    out_png: Path,
) -> dict:
    """Weighted average of N view colors using facing-weight masks.

    For each pixel: out = sum(color_v * facing_v) / sum(facing_v). When the
    sum of facing weights is zero (UV-gutter areas), output is transparent.
    Uses Blender's bundled numpy + Image API — no external PIL needed.
    """
    import numpy as np

    color_arrs: dict[str, "np.ndarray"] = {}
    facing_arrs: dict[str, "np.ndarray"] = {}
    shape = None
    for v, p in view_color_paths.items():
        arr = _load_image_pixels(p)  # (H, W, 4) float32, [0,1]
        color_arrs[v] = arr
        if shape is None:
            shape = arr.shape
    for v, p in view_facing_paths.items():
        arr = _load_image_pixels(p)
        facing_arrs[v] = arr[:, :, 0]  # use red channel (it's grayscale)

    H, W, _ = shape
    sum_color = np.zeros((H, W, 3), dtype=np.float32)
    sum_alpha = np.zeros((H, W),    dtype=np.float32)
    sum_facing = np.zeros((H, W),   dtype=np.float32)
    for v in color_arrs:
        c = color_arrs[v]
        f = facing_arrs[v]
        # Power weighting accentuates whichever view is most aligned.
        w = f ** 1.5
        sum_color += c[:, :, :3] * w[:, :, None]
        sum_alpha = np.maximum(sum_alpha, c[:, :, 3] * (w > 0.01))
        sum_facing += w

    eps = 1e-6
    out_rgb = np.where(sum_facing[:, :, None] > eps,
                       sum_color / np.maximum(sum_facing[:, :, None], eps),
                       0.0)
    out_alpha = np.where(sum_facing > eps, sum_alpha, 0.0)
    out = np.concatenate([out_rgb, out_alpha[:, :, None]], axis=-1)
    _save_image_pixels(out, out_png)
    return {"views": list(view_color_paths), "out_png": str(out_png),
            "coverage_pct": float((sum_facing > eps).mean() * 100)}


def render_views_for_img2img(
    pieces,
    out_dir: Path,
    resolution: int = 768,
    views: tuple[str, ...] = ("front", "back", "left", "right"),
) -> dict[str, Path]:
    """Render the meshes from each view to a PNG file (OpenGL solid shading).

    These rendered views are used as `init_image` for SD img2img: SD will
    stylize the mesh silhouette into a fully-textured character, which we
    then project back. Much better silhouette alignment than free-form
    text-to-image SD.
    """
    pieces = list(pieces)
    if not pieces:
        return {}
    view_axes = {"front": "-Y", "back": "+Y", "left": "-X", "right": "+X"}
    out_dir.mkdir(parents=True, exist_ok=True)

    # Hide everything except the geo meshes so the render is clean.
    hidden = []
    for o in bpy.data.objects:
        if o.type in ("ARMATURE", "EMPTY") and not o.hide_viewport:
            o.hide_viewport = True
            hidden.append(o)

    scene = bpy.context.scene
    prev_xres, prev_yres = scene.render.resolution_x, scene.render.resolution_y
    scene.render.resolution_x = resolution
    scene.render.resolution_y = int(resolution * 4 / 3)  # portrait

    out_paths: dict[str, Path] = {}
    for v in views:
        cam = setup_front_camera(pieces, view_axis=view_axes[v], name=f"rc_render_cam_{v}")
        scene.camera = cam
        out_path = out_dir / f"render_{v}.png"
        scene.render.filepath = str(out_path)
        # Cycles OpenGL render via viewport.
        for area in bpy.context.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "WINDOW":
                        with bpy.context.temp_override(area=area, region=region):
                            bpy.ops.view3d.view_camera()
                            bpy.ops.render.opengl(write_still=True, view_context=False)
                        break
                break
        out_paths[v] = out_path
        bpy.data.objects.remove(cam, do_unlink=True)

    scene.render.resolution_x = prev_xres
    scene.render.resolution_y = prev_yres
    # Restore visibility.
    for o in hidden:
        o.hide_viewport = False
    return out_paths


def bake_pieces_multi_view(
    pieces,
    view_sources: dict[str, Path],
    out_dir: Path,
    resolution: int = 1024,
    bake_margin: int = 8,
) -> dict:
    """Bake N views per piece + facing weights, composite into final basecolor.

    `view_sources` maps view name ('front'/'back'/'left'/'right') to the SD
    image generated for that view. Returns a per-piece log of intermediate
    paths + the final composited basecolor PNG.
    """
    pieces = list(pieces)
    if not pieces:
        return {"status": "nothing_to_bake"}

    # View-axis assignments matching Blender Z-up world: front camera looks
    # along +Y (camera at -Y), back along -Y, left looks along +X, right -X.
    view_axes = {
        "front": "-Y",
        "back":  "+Y",
        "left":  "-X",
        "right": "+X",
    }

    # Build one camera per view.
    cams: dict[str, bpy.types.Object] = {}
    for v in view_sources:
        if v not in view_axes:
            raise ValueError(f"Unsupported view {v!r}; supported: {list(view_axes)}")
        cam = setup_front_camera(pieces, view_axis=view_axes[v], name=f"rc_proj_cam_{v}")
        cams[v] = cam

    out_dir.mkdir(parents=True, exist_ok=True)
    per_view_dir = out_dir / "per_view"
    per_view_dir.mkdir(parents=True, exist_ok=True)

    log: dict = {"views": list(view_sources.keys()), "pieces": {}}

    for piece in pieces:
        piece_log: dict = {"per_view_color": {}, "per_view_facing": {}}
        view_color_paths: dict[str, Path] = {}
        view_facing_paths: dict[str, Path] = {}

        for view_name, src_img in view_sources.items():
            cam = cams[view_name]
            color_out = per_view_dir / f"{piece.name}_{view_name}_color.png"
            facing_out = per_view_dir / f"{piece.name}_{view_name}_facing.png"

            project_and_bake(
                piece,
                source_image_path=src_img,
                out_png=color_out,
                resolution=resolution,
                bake_margin=bake_margin,
                shared_camera=cam,
                cleanup_camera=False,
            )
            view_color_paths[view_name] = color_out

            _bake_facing_weight(piece, cam, facing_out, resolution=resolution,
                                 bake_margin=bake_margin)
            view_facing_paths[view_name] = facing_out

            piece_log["per_view_color"][view_name] = str(color_out)
            piece_log["per_view_facing"][view_name] = str(facing_out)

        # Composite this piece's views into one BaseColor.
        final_out = out_dir / f"{piece.name}_basecolor.png"
        piece_log["composite"] = _composite_views_with_facing(
            view_color_paths, view_facing_paths, final_out,
        )
        piece_log["final"] = str(final_out)
        log["pieces"][piece.name] = piece_log

    # Cleanup cameras.
    for cam in cams.values():
        bpy.data.objects.remove(cam, do_unlink=True)
    return log


def bake_pieces_from_shared_camera(
    pieces,
    source_image_path: Path,
    out_dir: Path,
    resolution: int = 2048,
    bake_margin: int = 16,
    view_axis: str = "-Y",
) -> dict:
    """Run `project_and_bake` on every piece from a single shared camera.

    Used by the auto-rig flow so all 15 `_Geo` pieces of an avatar receive
    consistent texture projection from one front-view SD image. Each piece
    gets its own `<bone>_basecolor.png` in `out_dir`.

    Default `view_axis` is `-Y` (Blender's Front viewport), matching the way
    the autorig template stands the character up Z-up with face toward +Y.
    """
    pieces = list(pieces)
    if not pieces:
        return {"status": "nothing_to_bake", "pieces": []}
    cam = setup_front_camera(pieces, view_axis=view_axis)
    out_dir.mkdir(parents=True, exist_ok=True)
    log: dict = {"camera_name": cam.name, "view_axis": view_axis, "pieces": {}}
    for obj in pieces:
        out_png = out_dir / f"{obj.name}_basecolor.png"
        result = project_and_bake(
            obj,
            source_image_path=source_image_path,
            out_png=out_png,
            resolution=resolution,
            bake_margin=bake_margin,
            shared_camera=cam,
            cleanup_camera=False,
        )
        log["pieces"][obj.name] = result
    bpy.data.objects.remove(cam, do_unlink=True)
    log["status"] = "ok"
    return log
