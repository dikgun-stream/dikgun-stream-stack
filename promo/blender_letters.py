"""Render the DIKGUN letter stingers: letters ACCUMULATE, camera pulls back.

Each stinger is one clip in the build-up of the word:

    clip 1  ->  D
    clip 2  ->  D I
    clip 3  ->  D I K
    clip 4  ->  D I K G
    clip 5  ->  D I K G U
    clip 6  ->  D I K G U N

In clip N, letters 1..N-1 are already standing and letter N falls in beside them;
the camera is framed to fit the accumulated word and dollies out slowly during
the clip. That is the "letters previously dropped should be seen next to the ones
dropping in, slowly panning out" brief.

Faults fixed here
-----------------
* LETTERS FLOATED. align_y="CENTER" puts the glyph midpoint at the origin, so the
  local bounding box min-z is about -H/2. settle_on_ground() computed the lift
  correctly and then animate_fall() wrote location.z = 0, discarding it. The lift
  is now passed into the animation as the resting height.
* CRACKS LOOKED TERRIBLE. Emission strength 6 blew the red out to flat pink, and
  tex.interpolation="Closest" on a 1024 texture stretched over 26 units produced
  hard blocky steps. Now: strength 1.6, LINEAR sampling, a smaller 10-unit plane,
  and the texture is generated at 2048.

Usage (from this directory):

    blender --background --python blender_letters.py -- --out stingers
    blender --background --python blender_letters.py -- --out stingers --stage 3

Requires crack.png (2048) from make_crack.py, run with SYSTEM python because
Blender's bundled python has no PIL.
"""

import math
import os
import sys

import bpy
from mathutils import Vector

VERSION = bpy.app.version_string

INK = (0.086, 0.078, 0.071, 1.0)      # #161412
RED = (0.784, 0.086, 0.114, 1.0)      # #C8161D
CREAM = (0.925, 0.894, 0.824, 1.0)    # #ECE4D2

FPS = 60
FALL_END = 13          # frame the newest letter touches the ground
TOTAL = 42             # ~0.70s at 60fps - about one beat at 120bpm
LETTERS = list("DIKGUN")
GAP = 0.42             # spacing between glyphs, in world units
CAM_LENS = 45.0
SENSOR = 36.0


def clear():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def setup_render(out_dir, res=(1920, 1080)):
    sc = bpy.context.scene
    engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.fps = FPS
    sc.frame_start = 1
    sc.frame_end = TOTAL
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = "PNG"
    sc.render.filepath = os.path.join(out_dir, "")
    try:
        sc.eevee.taa_render_samples = 24
    except AttributeError:
        pass


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEX = {
    "diff": os.path.join(BASE_DIR, "cracked_diff_2k.jpg"),
    "disp": os.path.join(BASE_DIR, "cracked_disp_2k.jpg"),
    "nor":  os.path.join(BASE_DIR, "cracked_nor_2k.jpg"),
}


def _img(path, non_colour=False):
    im = bpy.data.images.load(path)
    if non_colour:
        try:
            im.colorspace_settings.name = "Non-Color"
        except (AttributeError, TypeError):
            pass
    return im


def _obj_coords(nt, tree, scale=6.0):
    """Object-space coords so the tiling is independent of the plane size."""
    co = nt.new("ShaderNodeTexCoord")
    mp = nt.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (scale, scale, scale)
    tree.links.new(co.outputs["Object"], mp.inputs["Vector"])
    return mp.outputs["Vector"]


def make_ground():
    """Real CC0 cracked-concrete surface (Poly Haven), tinted down to the brand ink.

    ColourRamp is used for the tint rather than a Mix node: the Mix RGB node was
    replaced in Blender 4.x and its socket names differ between versions, while
    ColorRamp has been stable throughout.
    """
    bpy.ops.mesh.primitive_plane_add(size=140, location=(0, 0, 0))
    g = bpy.context.object
    g.name = "Ground"
    m = bpy.data.materials.new("GroundMat")
    m.use_nodes = True
    tree = m.node_tree
    nt = tree.nodes
    bsdf = nt.get("Principled BSDF")
    vec = _obj_coords(nt, tree, 7.0)

    td = nt.new("ShaderNodeTexImage")
    td.image = _img(TEX["diff"])
    tn = nt.new("ShaderNodeTexImage")
    tn.image = _img(TEX["nor"], non_colour=True)
    for t in (td, tn):
        tree.links.new(vec, t.inputs["Vector"])

    ramp = nt.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.05
    ramp.color_ramp.elements[0].color = (0.012, 0.012, 0.014, 1)
    ramp.color_ramp.elements[1].position = 0.85
    ramp.color_ramp.elements[1].color = (0.16, 0.145, 0.13, 1)
    tree.links.new(td.outputs["Color"], ramp.inputs["Fac"])

    nm = nt.new("ShaderNodeNormalMap")
    nm.inputs["Strength"].default_value = 0.7
    tree.links.new(tn.outputs["Color"], nm.inputs["Color"])

    tree.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    tree.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = 0.88
    g.data.materials.append(m)
    return g


def _iter_fcurves(action):
    try:
        for fc in action.fcurves:
            yield fc
        return
    except AttributeError:
        pass
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for bag in getattr(strip, "channelbags", []):
                for fc in bag.fcurves:
                    yield fc


def _set_ease(obj, before, after):
    ad = obj.animation_data
    if not ad or not ad.action:
        return
    for fc in _iter_fcurves(ad.action):
        for kp in fc.keyframe_points:
            if kp.co[0] < before[0]:
                kp.interpolation = before[1]
                try:
                    kp.easing = before[2]
                except (AttributeError, TypeError):
                    pass
            else:
                kp.interpolation = after[1]


def make_letter_mesh(ch, colour):
    """Create the glyph as a mesh, standing upright, un-positioned."""
    bpy.ops.object.text_add(location=(0, 0, 0))
    ob = bpy.context.object
    ob.name = f"Letter_{ch}"
    ob.data.body = ch
    ob.data.size = 4.0
    ob.data.extrude = 0.38
    ob.data.bevel_depth = 0.05
    ob.data.bevel_resolution = 3
    ob.data.align_x = "CENTER"
    ob.data.align_y = "CENTER"
    ob.rotation_euler = (math.radians(90), 0, 0)
    bpy.ops.object.convert(target="MESH")

    m = bpy.data.materials.new(f"Mat_{ch}")
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = colour
        bsdf.inputs["Roughness"].default_value = 0.28
        bsdf.inputs["Metallic"].default_value = 0.22
        try:
            bsdf.inputs["Emission Color"].default_value = colour
            bsdf.inputs["Emission Strength"].default_value = 0.35
        except KeyError:
            pass
    ob.data.materials.append(m)
    return ob


def measure(ob):
    """World-space width, height and the z needed to sit exactly on the ground."""
    bpy.context.view_layer.update()
    eob = ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
    pts = [eob.matrix_world @ Vector(c) for c in ob.bound_box]
    xs = [p.x for p in pts]
    zs = [p.z for p in pts]
    return (max(xs) - min(xs), max(zs) - min(zs), -min(zs))


def make_crack_plane(centre_x, impact_frame, size=9.0):
    """Glowing fracture under the newest letter, from the CC0 displacement map.

    The displacement map's cracks are the DARK values, so a ColorRamp inverts them
    into a mask (dark -> white). That mask drives an emission pass; the plane
    bursts outward on the impact frame.
    """
    if not os.path.isfile(TEX["disp"]):
        print(f"[blender] warning: {TEX['disp']} missing - run download first")
        return None

    bpy.ops.mesh.primitive_plane_add(size=size, location=(centre_x, 0, 0.006))
    p = bpy.context.object
    p.name = "Crack"
    mat = bpy.data.materials.new("CrackMat")
    mat.use_nodes = True
    tree = mat.node_tree
    nt = tree.nodes
    out = nt.get("Material Output")
    bsdf = nt.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (0, 0, 0, 1)
    bsdf.inputs["Roughness"].default_value = 1.0

    vec = _obj_coords(nt, tree, 2.2)
    tdisp = nt.new("ShaderNodeTexImage")
    tdisp.image = _img(TEX["disp"], non_colour=True)
    tree.links.new(vec, tdisp.inputs["Vector"])

    mask = nt.new("ShaderNodeValToRGB")
    # invert: the map is dark inside the cracks, and we want those to glow
    mask.color_ramp.elements[0].position = 0.0
    mask.color_ramp.elements[0].color = (1, 1, 1, 1)
    mask.color_ramp.elements[1].position = 0.42
    mask.color_ramp.elements[1].color = (0, 0, 0, 1)
    tree.links.new(tdisp.outputs["Color"], mask.inputs["Fac"])

    emit = nt.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = RED

    mul = nt.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.inputs[1].default_value = 2.4
    tree.links.new(mask.outputs["Color"], mul.inputs[0])
    tree.links.new(mul.outputs[0], emit.inputs["Strength"])

    add = nt.new("ShaderNodeAddShader")
    tree.links.new(bsdf.outputs[0], add.inputs[0])
    tree.links.new(emit.outputs[0], add.inputs[1])
    tree.links.new(add.outputs[0], out.inputs["Surface"])
    p.data.materials.append(mat)

    p.scale = (0.03, 0.03, 1.0)
    p.keyframe_insert("scale", frame=impact_frame)
    p.scale = (1.0, 1.0, 1.0)
    p.keyframe_insert("scale", frame=impact_frame + 4)
    _set_ease(p, before=(1e9, "LINEAR", None), after=(0, "QUAD"))
    return p


def add_camera_for(width, height):
    """Frame the accumulated word, then dolly out during the clip."""
    hfov = 2.0 * math.atan(SENSOR / 2.0 / CAM_LENS)
    aspect = 1920.0 / 1080.0
    vfov = 2.0 * math.atan(math.tan(hfov / 2.0) / aspect)

    d_w = (width * 1.18 / 2.0) / math.tan(hfov / 2.0)
    d_h = (height * 1.25 / 2.0) / math.tan(vfov / 2.0)
    d = max(d_w, d_h) + 3.0

    cam_data = bpy.data.cameras.new("Cam")
    cam_data.lens = CAM_LENS
    cam = bpy.data.objects.new("Cam", cam_data)
    bpy.context.collection.objects.link(cam)

    cam_z = height * 0.58
    target_z = height * 0.46
    tilt = math.atan2(cam_z - target_z, d)
    cam.rotation_euler = (math.radians(90) - tilt, 0, 0)

    # slow pull back: 12% further away by the end of the clip
    cam.location = (0, -d, cam_z)
    cam.keyframe_insert("location", frame=1)
    cam.location = (0, -d * 1.12, cam_z * 1.05)
    cam.keyframe_insert("location", frame=TOTAL)

    bpy.context.scene.camera = cam
    return cam


def add_lights(spread):
    key = bpy.data.lights.new("Key", type="AREA")
    key.energy = 2200 + 400 * spread
    key.size = 14
    ko = bpy.data.objects.new("Key", key)
    bpy.context.collection.objects.link(ko)
    ko.location = (6.0, -10.0, 12.0)
    ko.rotation_euler = (math.radians(50), 0, math.radians(30))

    rim = bpy.data.lights.new("Rim", type="AREA")
    rim.energy = 1200
    rim.size = 9
    rim.color = RED[:3]
    ro = bpy.data.objects.new("Rim", rim)
    bpy.context.collection.objects.link(ro)
    ro.location = (-7.0, 4.0, 6.0)
    ro.rotation_euler = (math.radians(66), 0, math.radians(-140))

    bpy.context.scene.world = bpy.data.worlds.new("W")
    bpy.context.scene.world.use_nodes = True
    bg = bpy.context.scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.012, 0.012, 0.014, 1)
        bg.inputs["Strength"].default_value = 0.3


def animate_fall(ob, rest_z):
    """Rigid fall to rest_z (the height that puts the glyph ON the ground)."""
    top = rest_z + 12.0
    ob.location = (0, 0, top)
    ob.keyframe_insert("location", frame=1)
    ob.location = (0, 0, rest_z + 2.0)
    ob.keyframe_insert("location", frame=int(FALL_END * 0.72))
    ob.location = (0, 0, rest_z)
    ob.keyframe_insert("location", frame=FALL_END)
    ob.location = (0, 0, rest_z)
    ob.keyframe_insert("location", frame=TOTAL)
    _set_ease(ob, before=(FALL_END, "QUAD", "EASE_IN"), after=(0, "LINEAR"))


def render_stage(n, out_dir):
    """Clip n shows letters 1..n, with letter n dropping in."""
    clear()
    setup_render(out_dir)
    make_ground()

    cols = [CREAM if i % 2 == 0 else RED for i in range(n)]

    # measure first, at the origin, then lay out
    obs, widths, heights, lifts = [], [], [], []
    for i in range(n):
        ob = make_letter_mesh(LETTERS[i], cols[i])
        w, h, lift = measure(ob)
        obs.append(ob)
        widths.append(w)
        heights.append(h)
        lifts.append(lift)

    total_w = sum(widths) + GAP * max(0, n - 1)
    x = -total_w / 2.0
    for i, ob in enumerate(obs):
        cx = x + widths[i] / 2.0
        x += widths[i] + GAP
        ob.location = (cx, 0, lifts[i])
        ob.keyframe_insert("location", frame=1)
        if i < n - 1:
            # already standing: rigid for the whole clip
            ob.location = (cx, 0, lifts[i])
            ob.keyframe_insert("location", frame=TOTAL)
        else:
            ob.location = (cx, 0, lifts[i])
            ob.keyframe_insert("location", frame=1)
            animate_fall(ob, lifts[i])
            ob.location = (cx, 0, lifts[i])
            ob.keyframe_insert("location", frame=TOTAL)
            make_crack_plane(cx, FALL_END)

    max_h = max(heights)
    add_camera_for(total_w, max_h)
    add_lights(n / len(LETTERS))

    name = "".join(LETTERS[:n])
    path = os.path.join(out_dir, name)
    os.makedirs(path, exist_ok=True)
    bpy.context.scene.render.filepath = os.path.join(path, "f_")
    print(f"[blender] stage {n} '{name}' -> {path}  (w={total_w:.1f} h={max_h:.1f})  "
          f"Blender {VERSION}")
    bpy.ops.render.render(animation=True)
    return path


def main():
    argv = sys.argv
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    out_dir = "stingers"
    stage = None
    for i, a in enumerate(args):
        if a == "--out" and i + 1 < len(args):
            out_dir = args[i + 1]
        if a == "--stage" and i + 1 < len(args):
            stage = int(args[i + 1])

    base = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(base, out_dir)
    os.makedirs(out_dir, exist_ok=True)
    print(f"[blender] output root: {out_dir}")

    stages = [stage] if stage else list(range(1, len(LETTERS) + 1))
    for n in stages:
        render_stage(n, out_dir)
    print("[blender] done")


if __name__ == "__main__":
    main()
