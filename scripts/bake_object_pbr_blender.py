"""Bake a reconstructed coloured surface into a portable GLB with PBR maps.

Run with Blender --background --factory-startup --python THIS -- --input ...
Geometry/appearance are captured derivatives. Roughness and metallic are
explicit estimates, not measured reflectance. Outputs require visual review.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import bpy
import numpy as np
from mathutils import Vector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--resolution", type=int, default=4096)
    parser.add_argument("--faces", type=int, default=100000)
    parser.add_argument("--roughness", type=float, default=.65)
    parser.add_argument("--up", nargs=3, type=float, default=[0,-1,0],
                        help="Observed capture-up vector; retained in provenance")
    parser.add_argument("--camera-position", nargs=3, type=float,
                        help="Recorded source camera position, in original capture coordinates")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    if args.output.exists():
        raise ValueError("Choose a new output folder; previous bakes are preserved")
    args.output.mkdir(parents=True)
    started = time.time()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.wm.ply_import(filepath=str(args.input.resolve()))
    high = bpy.context.object
    high.name = args.label + "_captured_surface"
    up = Vector(args.up).normalized()
    capture_rotation = up.rotation_difference(Vector((0,0,1)))
    high.rotation_mode = "QUATERNION"
    high.rotation_quaternion = capture_rotation
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    if not high.data.color_attributes:
        raise ValueError("Input has no captured vertex colours to bake")
    for polygon in high.data.polygons:
        polygon.use_smooth = True
    colour_name = high.data.color_attributes[0].name
    material = bpy.data.materials.new("Captured appearance")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    colour = nodes.new("ShaderNodeVertexColor")
    colour.layer_name = colour_name
    links.new(colour.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs[0], out.inputs["Surface"])
    high.data.materials.clear()
    high.data.materials.append(material)
    low = high.copy()
    low.data = high.data.copy()
    bpy.context.collection.objects.link(low)
    low.name = args.label
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    if len(low.data.polygons) > args.faces:
        modifier = low.modifiers.new("Surface simplification", "DECIMATE")
        modifier.ratio = args.faces / len(low.data.polygons)
        bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.151917, island_margin=.01)
    bpy.ops.object.mode_set(mode="OBJECT")
    for attribute in list(low.data.color_attributes):
        low.data.color_attributes.remove(attribute)
    pbr = bpy.data.materials.new(args.label + " PBR")
    pbr.use_nodes = True
    low.data.materials.clear()
    low.data.materials.append(pbr)
    nodes, links = pbr.node_tree.nodes, pbr.node_tree.links
    principled = nodes.get("Principled BSDF")
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 32
    scene.cycles.device = "CPU"
    preferences = bpy.context.preferences.addons['cycles'].preferences
    try:
        preferences.compute_device_type = 'OPTIX'
        preferences.refresh_devices()
        devices = [device for device in preferences.devices if device.type == 'OPTIX']
        if devices:
            for device in preferences.devices:
                device.use = device in devices
            scene.cycles.device = 'GPU'
            print('Using OptiX:', [device.name for device in devices], flush=True)
    except (TypeError, RuntimeError):
        print('OptiX unavailable; using CPU Cycles', flush=True)
    scene.render.threads_mode = "FIXED"
    scene.render.threads = 8
    scene.render.bake.margin = 16
    extent = max(low.dimensions)
    paths = {}

    def bake(kind, name, selected, noncolour=False):
        print("BAKE", name, flush=True)
        image = bpy.data.images.new(name, width=args.resolution, height=args.resolution, alpha=False)
        image.colorspace_settings.name = "Non-Color" if noncolour else "sRGB"
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image
        nodes.active = tex
        bpy.ops.object.select_all(action="DESELECT")
        high.hide_render = not selected
        high.hide_set(not selected)
        if selected:
            high.select_set(True)
        low.select_set(True)
        bpy.context.view_layer.objects.active = low
        bpy.ops.object.bake(type=kind, use_selected_to_active=selected,
                            cage_extrusion=extent * .005, max_ray_distance=extent * .02,
                            margin=16, use_clear=True)
        path = args.output / (name + ".png")
        image.filepath_raw = str(path.resolve())
        image.file_format = "PNG"
        image.save()
        paths[name] = path
        return image, tex

    base, base_node = bake("EMIT", "basecolor", True)
    normal, normal_node = bake("NORMAL", "normal", True, True)
    ao, ao_node = bake("AO", "occlusion", False, True)
    links.new(base_node.outputs["Color"], principled.inputs["Base Color"])
    normal_map = nodes.new("ShaderNodeNormalMap")
    links.new(normal_node.outputs["Color"], normal_map.inputs["Color"])
    links.new(normal_map.outputs["Normal"], principled.inputs["Normal"])
    pixels = np.empty(args.resolution * args.resolution * 4, dtype=np.float32)
    ao.pixels.foreach_get(pixels)
    orm_pixels = pixels.reshape(-1, 4)
    # glTF: R=occlusion, G=roughness, B=metallic. Values are explicit,
    # conservative non-metal material estimates, with no invented scratches.
    orm_pixels[:, 1] = args.roughness
    orm_pixels[:, 2] = 0
    orm_pixels[:, 3] = 1
    orm = bpy.data.images.new("occlusion-roughness-metallic", width=args.resolution,
                              height=args.resolution, alpha=False)
    orm.colorspace_settings.name = "Non-Color"
    orm.pixels.foreach_set(pixels)
    orm.filepath_raw = str((args.output / "orm.png").resolve())
    orm.file_format = "PNG"
    orm.save()
    paths["orm"] = Path(orm.filepath_raw)
    orm_node = nodes.new("ShaderNodeTexImage")
    orm_node.image = orm
    separate = nodes.new("ShaderNodeSeparateColor")
    links.new(orm_node.outputs["Color"], separate.inputs["Color"])
    links.new(separate.outputs["Green"], principled.inputs["Roughness"])
    links.new(separate.outputs["Blue"], principled.inputs["Metallic"])
    settings = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
    settings.interface.new_socket(name="Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
    settings_node = nodes.new("ShaderNodeGroup")
    settings_node.node_tree = settings
    links.new(separate.outputs["Red"], settings_node.inputs["Occlusion"])
    # High surface stays in the editable source file but only the finished
    # object is included in the standalone GLB.
    high.hide_render = True
    high.hide_set(True)
    bpy.ops.object.select_all(action="DESELECT")
    low.select_set(True)
    bpy.context.view_layer.objects.active = low
    low["capture_provenance"] = "Observed surface and baked appearance; estimated dielectric material."
    low["roughness_estimate"] = args.roughness
    low["metallic_estimate"] = 0.0
    center = sum((low.matrix_world @ Vector(corner) for corner in low.bound_box), Vector()) / 8
    direction = ((capture_rotation @ Vector(args.camera_position))-center).normalized() if args.camera_position else Vector((1.4,-1.8,1.1)).normalized()
    initial_position = center + direction * extent * 2.5
    low['inspection_camera_position_gltf'] = [initial_position.x,initial_position.z,-initial_position.y]
    low['inspection_camera_target_gltf'] = [center.x,center.z,-center.y]
    for image in (base, normal, ao, orm):
        image.pack()
    glb = args.output / "model.glb"
    bpy.ops.export_scene.gltf(filepath=str(glb.resolve()), export_format="GLB", use_selection=True,
                              export_apply=True, export_extras=True, export_yup=True)
    # Save a reusable three-quarter inspection view and an editable source.
    bpy.ops.object.camera_add(location=center + direction * extent * 2.5)
    camera = bpy.context.object
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = extent * 1.7
    scene.camera = camera
    world = bpy.data.worlds.new("Inspection studio")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (.35, .35, .35, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = .7
    scene.world = world
    for location, energy, size in (((1,-2,3),900,3),((-2,-1,1),500,2),((1,2,2),700,2)):
        bpy.ops.object.light_add(type="AREA", location=center + Vector(location) * extent)
        lamp = bpy.context.object
        lamp.data.energy = energy * extent * extent
        lamp.data.shape = "DISK"
        lamp.data.size = size * extent
        lamp.rotation_euler = (center-lamp.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.resolution_x = 1400
    scene.render.resolution_y = 1400
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.filepath = str((args.output / "inspection.png").resolve())
    bpy.ops.wm.save_as_mainfile(filepath=str((args.output / "editable.blend").resolve()))
    bpy.ops.render.render(write_still=True)
    record = dict(schema="vitrine/pbr-bake/1", label=args.label,
                  input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                  glb_sha256=hashlib.sha256(glb.read_bytes()).hexdigest(),
                  triangles=len(low.data.polygons), texture_resolution=args.resolution,
                  seconds=round(time.time()-started,2), blender=bpy.app.version_string,
                  render_device=scene.cycles.device,
                  capture_up=list(up), export_up="glTF +Y, Blender +Z",
                  inspection_camera=dict(position=[camera.location.x,camera.location.z,-camera.location.y],
                                         lookAt=[center.x,center.z,-center.y],up=[0,1,0]),
                  maps={name:dict(file=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                        for name,path in paths.items()},
                  captured=["surface geometry", "baked vertex-colour appearance"],
                  derived=["normal map from reconstructed surface", "geometry ambient occlusion"],
                  estimated=dict(roughness=args.roughness,metallic=0.0),
                  limitations=["Base colour retains capture illumination; it is not measured diffuse albedo.",
                               "Unobserved regions and Poisson bridges require visual review.",
                               "Scale remains in arbitrary reconstruction units."],
                  review_status="requires_visual_review")
    (args.output/"pbr-provenance.json").write_text(json.dumps(record,indent=2),encoding="utf-8")
    print(json.dumps(record,indent=2),flush=True)

if __name__ == "__main__":
    main()
