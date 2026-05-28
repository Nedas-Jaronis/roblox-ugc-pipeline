"""Headless Blender: take a face PNG, output a Roblox Face Accessory FBX.

Creates:
- A flat Handle mesh (1 stud × 1.2 stud, facing -Y)
- Material with the PNG as base-color texture
- FaceCenterAttachment empty parented at origin

Usage:
    blender --background --python face_accessory.py -- \
        --in 04_dreamy.png --out 04_dreamy_face.fbx
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import bpy  # type: ignore


def _parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="src", required=True)
    p.add_argument("--out", dest="dst", required=True)
    p.add_argument("--name", default="Face", help="Item name")
    return p.parse_args(argv)


def _clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False, confirm=False)
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)


def main():
    args = _parse()
    src = Path(args.src).resolve()
    dst = Path(args.dst).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)

    _clear_scene()

    # Roblox studs -> meters factor (Blender uses meters; Roblox imports FBX as studs by default)
    # Face plane: 1 stud wide × 1.2 stud tall (3 studs is max bound, smaller looks better up close)
    PLANE_W = 1.0
    PLANE_H = 1.2

    # Create a flat plane facing -Y (toward the camera / face front)
    bpy.ops.mesh.primitive_plane_add(size=1)
    handle = bpy.context.object
    handle.name = "Handle"
    handle.scale = (PLANE_W, 1.0, PLANE_H)
    # Rotate so plane faces -Y (so the texture is visible from front)
    handle.rotation_euler = (math.radians(90), 0, 0)
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    # UV-unwrap (smart project so the plane covers the full UV space)
    bpy.context.view_layer.objects.active = handle
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.unwrap(method='ANGLE_BASED')
    bpy.ops.object.mode_set(mode='OBJECT')

    # Material with image texture
    mat = bpy.data.materials.new(name=f"{args.name}_Mat")
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out_node = nt.nodes.new('ShaderNodeOutputMaterial'); out_node.location = (400, 0)
    bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled');    bsdf.location = (200, 0)
    bsdf.inputs['Roughness'].default_value = 0.6
    tex = nt.nodes.new('ShaderNodeTexImage');           tex.location = (-200, 0)
    tex.image = bpy.data.images.load(str(src))
    nt.links.new(tex.outputs['Color'],  bsdf.inputs['Base Color'])
    nt.links.new(tex.outputs['Alpha'],  bsdf.inputs['Alpha'])
    nt.links.new(bsdf.outputs['BSDF'],  out_node.inputs['Surface'])
    mat.blend_method = 'BLEND'
    handle.data.materials.append(mat)

    # FaceCenterAttachment - empty at origin
    bpy.ops.object.empty_add(type='ARROWS', location=(0, 0, 0))
    att = bpy.context.object
    att.name = "FaceCenterAttachment"
    att.empty_display_size = 0.1
    att.parent = handle

    # Select only handle + attachment for export
    bpy.ops.object.select_all(action='DESELECT')
    handle.select_set(True)
    att.select_set(True)

    # Export FBX with Roblox-friendly axes
    bpy.ops.export_scene.fbx(
        filepath=str(dst),
        use_selection=True,
        axis_forward='-Z',
        axis_up='Y',
        bake_space_transform=True,
        object_types={'MESH', 'EMPTY'},
        path_mode='COPY',
        embed_textures=True,
        add_leaf_bones=False,
    )
    print(f"[face_accessory] wrote {dst}")


if __name__ == "__main__":
    main()
