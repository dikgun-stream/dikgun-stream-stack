"""Render the DIKGUN letter-drop stingers in Blender.

One letter per kick, replacing what would have been a black flash. Each letter
falls, hits a ground plane, and squashes on impact - a hard, readable cut accent
rather than a silent frame.

Usage (from this directory):

    blender --background --python blender_letters.py -- --out stingers
    blender --background --python blender_letters.py -- --out stingers --letter D

Design notes
------------
* Opaque, full-frame renders. The stinger IS a shot, not an overlay, so alpha
  would only add compositing risk for no gain.
* EEVEE rather than Cycles: these are 30-frame clips and there are six of them,
  so render time matters more than photorealism.
* The fall is keyframed rather than simulated. A rigid-body sim is another source
  of nondeterminism for a 0.5s effect that needs to land on an exact beat.
* Squash on impact uses scale keyframes (x,y up / z down) rather than a
  deform modifier, which keeps the mesh cheap and the timing exact.
"""

import math
import os
import sys

import bpy

VERSION = bpy.app.version_string

# brand palette
INK = (0.086, 0.078, 0.071, 1.0)      # #161412
RED = (0.784, 0.086, 0.114, 1.0)      # #C8161D
CREAM = (0.925, 0.894, 0.824, 1.0)    # #ECE4D2

FPS = 60
FALL_END = 18          # frame the letter hits the ground
TOTAL = 34             # ~0.57s at 60fps
LETTERS = list("DIKGUN")


def clear():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def setup_render(out_dir, res=(1920, 1080)):
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE_NEXT" if "EEVEE_NEXT" in [
        e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
    ] else "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.fps = FPS
    sc.frame_start = 1
    sc.frame_end = TOTAL
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = "PNG"
    sc.render.filepath = os.path.join(out_dir, "")
    # EEVEE settings differ across versions; set what exists
    try:
        sc.eevee.taa_render_samples = 16
    except AttributeError:
        pass


def make_ground():
    bpy.ops.mesh.primitive_plane_add(size=40, location=(0, 0, 0))
    g = bpy.context.object
    g.name = "Ground"
    m = bpy.data.materials.new("GroundMat")
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = INK
        bsdf.inputs["Roughness"].default_value = 0.65
    g.data.materials.append(m)
    return g


def make_letter(ch, colour):
    bpy.ops.object.text_add(location=(0, 0, 0))
    ob = bpy.context.object
    ob.name = f"Letter_{ch}"
    ob.data.body = ch
    ob.data.size = 4.6
    ob.data.extrude = 0.42
    ob.data.bevel_depth = 0.055
    ob.data.bevel_resolution = 3
    ob.data.align_x = "CENTER"
    ob.data.align_y = "CENTER"
    # stand the text upright and face the camera
    ob.rotation_euler = (math.radians(90), 0, 0)
    bpy.ops.object.convert(target="MESH")

    m = bpy.data.materials.new(f"Mat_{ch}")
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = colour
        bsdf.inputs["Roughness"].default_value = 0.32
        bsdf.inputs["Metallic"].default_value = 0.15
    ob.data.materials.append(m)
    return ob



def _iter_fcurves(action):
    """Yield fcurves across Blender's action API generations.

    Blender 4.4 introduced slotted actions; `action.fcurves` was removed and the
    curves now hang off layers -> strips -> channelbags. 5.x raises AttributeError
    on the old attribute, which is what broke the first run. Support both so the
    script is not pinned to one Blender release.
    """
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


def _set_interpolation(ob, before, after):
    ad = ob.animation_data
    if not ad or not ad.action:
        return
    curves = list(_iter_fcurves(ad.action))
    if not curves:
        print("[blender] warning: no fcurves found; using default interpolation")
        return
    # Blender 5 moved easing into its own property; the interpolation enum no
    # longer accepts EASE_IN/EASE_OUT. Set both, defensively.
    for fc in curves:
        for kp in fc.keyframe_points:
            if kp.co[0] < before[1]:
                kp.interpolation = before[0]
                if before[2]:
                    try:
                        kp.easing = before[2]
                    except (AttributeError, TypeError):
                        pass
            else:
                kp.interpolation = after[0]


def animate_fall(ob):
    """Accelerating fall, one-frame squash, small settle."""
    top = 13.0
    # start high
    ob.location = (0, 0, top)
    ob.keyframe_insert("location", frame=1)
    # accelerate: few keys, ease-out interpolation gives the gravity feel
    mid_f = int(FALL_END * 0.72)
    ob.location = (0, 0, top * 0.18)
    ob.keyframe_insert("location", frame=mid_f)
    ob.location = (0, 0, 0.0)
    ob.keyframe_insert("location", frame=FALL_END)

    _set_interpolation(ob, before=("QUAD", FALL_END, "EASE_IN"),
                       after=("LINEAR", None, None))

    # squash on impact, recover over the next few frames
    ob.scale = (1, 1, 1)
    ob.keyframe_insert("scale", frame=FALL_END)
    ob.scale = (1.32, 1.32, 0.55)
    ob.keyframe_insert("scale", frame=FALL_END + 2)
    ob.scale = (0.94, 0.94, 1.08)
    ob.keyframe_insert("scale", frame=FALL_END + 7)
    ob.scale = (1, 1, 1)
    ob.keyframe_insert("scale", frame=FALL_END + 14)


def add_camera():
    cam_data = bpy.data.cameras.new("Cam")
    cam_data.lens = 48
    cam = bpy.data.objects.new("Cam", cam_data)
    bpy.context.collection.objects.link(cam)
    cam.location = (0, -7.4, 2.05)
    cam.rotation_euler = (math.radians(78), 0, 0)
    bpy.context.scene.camera = cam
    return cam


def add_lights():
    key = bpy.data.lights.new("Key", type="AREA")
    key.energy = 1400
    key.size = 9
    ko = bpy.data.objects.new("Key", key)
    bpy.context.collection.objects.link(ko)
    ko.location = (4.5, -6.5, 8.5)
    ko.rotation_euler = (math.radians(52), 0, math.radians(34))

    rim = bpy.data.lights.new("Rim", type="AREA")
    rim.energy = 900
    rim.size = 6
    rim.color = RED[:3]
    ro = bpy.data.objects.new("Rim", rim)
    bpy.context.collection.objects.link(ro)
    ro.location = (-5.5, 3.0, 4.2)
    ro.rotation_euler = (math.radians(68), 0, math.radians(-140))

    bpy.context.scene.world = bpy.data.worlds.new("W")
    bpy.context.scene.world.use_nodes = True
    bg = bpy.context.scene.world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.012, 0.012, 0.014, 1)
        bg.inputs["Strength"].default_value = 0.35


def render_letter(ch, out_dir, colour):
    clear()
    setup_render(out_dir)
    make_ground()
    ob = make_letter(ch, colour)
    animate_fall(ob)
    add_camera()
    add_lights()
    path = os.path.join(out_dir, ch)
    os.makedirs(path, exist_ok=True)
    bpy.context.scene.render.filepath = os.path.join(path, "f_")
    print(f"[blender] rendering {ch} -> {path}  (Blender {VERSION})")
    bpy.ops.render.render(animation=True)
    return path


def main():
    argv = sys.argv
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    out_dir = "stingers"
    only = None
    for i, a in enumerate(args):
        if a == "--out" and i + 1 < len(args):
            out_dir = args[i + 1]
        if a == "--letter" and i + 1 < len(args):
            only = args[i + 1].upper()

    # Blender's CWD in --background is NOT the script directory (it is the drive
    # root here), so a relative --out silently wrote to C:\stingers. Anchor it.
    base = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isabs(out_dir):
        out_dir = os.path.join(base, out_dir)
    os.makedirs(out_dir, exist_ok=True)
    print(f"[blender] output root: {out_dir}")
    letters = [only] if only else LETTERS
    for i, ch in enumerate(letters):
        # alternate cream and red so consecutive stingers do not read as a loop
        colour = CREAM if LETTERS.index(ch) % 2 == 0 else RED
        render_letter(ch, out_dir, colour)
    print("[blender] done")


if __name__ == "__main__":
    main()
