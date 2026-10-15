# SPDX-License-Identifier: GPL-3.0-or-later
"""Vehicle kit: an arcade car, kart or pickup truck whose wheels are separate child objects.

The body is the root (origin on the ground under its centre); the wheels <name>_Wheel_FL, _FR,
_RL and _RR have their origins on the axle centres, so game code can spin them about their local
X axis and steer the front ones about Z. The driver faces -Y, so the left wheels are at +X.
"""

import math
from types import SimpleNamespace

from mathutils import Matrix

from .base import Part, boolean, choice, color, kit, number

# style: (usual length, width, wheel radius) in metres
STYLES = {"car": (3.8, 1.85, 0.36), "kart": (1.9, 1.25, 0.18), "truck": (5.0, 2.2, 0.5)}
PAINT = ("#E8382F", "#2F7DE8", "#F2B705", "#2FBF71", "#F28C28", "#8E5CD6", "#22B8CF", "#F2F2F2")
GLASS = "#1F2A36"
WHEELS = (("FL", 1, -1), ("FR", -1, -1), ("RL", 1, 1), ("RR", -1, 1))  # (suffix, side x, end y)


def layout(p) -> SimpleNamespace:
    """Sizes and wheel placement of a vehicle, in metres."""
    style = p["style"]
    l0, w0, r0 = STYLES[style]
    length = max(p["length"] or l0, 1.0)
    width = p["width"] or w0 * length / l0
    width = min(max(width, 0.35 * length), 0.9 * length)
    radius = p["wheel_radius"] or r0 * length / l0
    radius = min(max(radius, 0.05 * length), 0.16 * length, (0.28 if style == "kart" else 0.42) * width)
    if style == "kart":
        tyres = (0.85 * radius, 1.15 * radius)
        front, rear = max(0.16 * length, 1.15 * radius), max(0.16 * length, 1.15 * radius)
    elif style == "truck":
        tyres = (0.75 * radius, 0.75 * radius)
        front, rear = max(0.18 * length, 1.35 * radius), max(0.2 * length, 1.35 * radius)
    else:
        tyres = (0.7 * radius, 0.7 * radius)
        front, rear = max(0.19 * length, 1.35 * radius), max(0.18 * length, 1.35 * radius)
    axles = (-length / 2 + front, length / 2 - rear)
    wheels = []
    for suffix, side, end in WHEELS:
        tyre = tyres[0] if end < 0 else tyres[1]
        y = axles[0] if end < 0 else axles[1]
        wheels.append((suffix, (side * (width / 2 - tyre / 2), y, radius), tyre))
    return SimpleNamespace(style=style, L=length, W=width, R=radius, tyres=tyres, axles=axles, wheels=wheels)


def _parts(p):
    d = layout(p)
    clips = (("spin", {"axis": "X", "speed": 1.0}, "spin"),) if p["spin_clip"] else ()
    return [Part(f"Wheel_{suffix}", location,
                 build=lambda m, p, rng, tyre=tyre: _wheel(m, layout(p), tyre), clips=clips)
            for suffix, location, tyre in d.wheels]


@kit("vehicle", label="Vehicle",
     description=("An arcade vehicle: a car, a go-kart or a pickup truck. The body is the root, with its origin on "
                  "the ground under its centre; the four wheels are child objects <name>_Wheel_FL, _Wheel_FR, "
                  "_Wheel_RL and _Wheel_RR (F = front at -Y, L = the driver's left at +X) with their origins on "
                  "the axle centres: roll them about their local X axis and steer the front ones about Z from "
                  "code. Collider: put a box Collider (and a kinematic RigidBody) on the body: an under-tray takes "
                  "its mesh down to the ground, so the box spans ground to roof; the wheels are separate entities "
                  "and stay out of it. The optional `spin` clip (one turn per second, forward) is for show: it "
                  "overrides steering while it plays."),
     params={
         "style": choice("car", tuple(STYLES), "car: a chunky coupe; kart: an open go-kart; truck: a pickup"),
         "length": number(0.0, 0.0, 12.0, "Length along Y in metres; 0 = the style's usual length "
                                          "(car 3.8, kart 1.9, truck 5.0)"),
         "width": number(0.0, 0.0, 6.0, "Width across the wheels in metres; 0 = in proportion to the length "
                                        "(kept between 35% and 90% of it)"),
         "wheel_radius": number(0.0, 0.0, 1.5, "Wheel radius in metres; 0 = in proportion to the length "
                                               "(at most 16% of it)"),
         "color": color("auto", "Paint colour as #RRGGBB, or auto to pick one from the seed"),
         "spin_clip": boolean(False, "Add a looping 'spin' clip to every wheel (play it with an Animator on the body)"),
     }, max_tris=2600, parts=_parts)
def vehicle(m, p, rng):
    d = layout(p)
    paint = m.slot("plastic", color=p["color"] if p["color"] != "auto" else PAINT[p["seed"] % len(PAINT)],
                   roughness=0.35)
    s = SimpleNamespace(paint=paint, dark=m.slot("rubber"), glass=m.slot("plastic", color=GLASS, roughness=0.1),
                        head=m.slot("emissive", color="#FFF3BF", emission_strength=2.0),
                        tail=m.slot("emissive", color="#FF3B30", emission_strength=2.0), metal=m.slot("metal"))
    {"car": _car, "kart": _kart, "truck": _truck}[d.style](m, d, s, rng)


# ------------------------------------------------------------------ wheels


def _wheel(m, d, width):
    """A tyre with a recessed metal hub, its axle along X through the origin."""
    r, hw = d.R, width / 2
    tyre, hub = m.slot("rubber"), m.slot("metal")
    profile = [(0.0, -0.78 * hw), (0.25 * r, -0.74 * hw), (0.5 * r, -0.62 * hw), (0.6 * r, -hw), (0.86 * r, -hw),
               (r, -0.55 * hw), (r, 0.55 * hw), (0.86 * r, hw), (0.6 * r, hw), (0.5 * r, 0.62 * hw),
               (0.25 * r, 0.74 * hw), (0.0, 0.78 * hw)]
    slots = [hub, hub, hub, tyre, tyre, tyre, tyre, tyre, hub, hub, hub]
    smooth = [False, False, False, False, True, True, True, False, False, False, False]
    segments = 12 if d.style == "kart" else 16
    m.lathe(profile, segments, tyre, band_slots=slots, band_smooth=smooth,
            matrix=Matrix.Rotation(math.pi / 2, 4, "Y"))
    m.smooth_angle = math.radians(50)


# ------------------------------------------------------------------ bodies


def _taper(faces, z0, z1, top, bottom=1.0):
    """Narrow a shell's X extent linearly with height: `bottom` x scale at z0, `top` at z1. Keeps
    the sides and the X-facing caps of a prism along X planar."""
    for v in {v for f in faces for v in f.verts}:
        t = min(1.0, max(0.0, (v.co.z - z0) / (z1 - z0)))
        v.co.x *= bottom + (top - bottom) * t


def _arch(y, r, z0, arch, steps=7):
    """Points of a wheel arch over a wheel at (y, r), from where it meets z0 in front, over the top."""
    a0 = math.asin(max(-1.0, (z0 - r) / arch))
    out = []
    for i in range(steps):
        a = (math.pi - a0) + (2 * a0 - math.pi) * i / (steps - 1)
        out.append((y + arch * math.cos(a), r + arch * math.sin(a)))
    return out


def _lower_body(m, d, slot, half, z0, zb, nose=(0.25, 0.62, 0.85), rear=0.3):
    """The body below the windows: a slab along X with wheel arches cut into its bottom edge.
    `nose` is (bumper, nose top, hood front) heights as fractions from z0 to zb."""
    L, R = d.L, d.R
    span = zb - z0
    arch = 1.14 * R
    pts = [(-L / 2 + 0.02 * L, z0)]
    pts += _arch(d.axles[0], R, z0, arch) + _arch(d.axles[1], R, z0, arch)
    pts += [(L / 2 - 0.02 * L, z0), (L / 2, z0 + rear * span), (L / 2, zb - 0.025 * L), (L / 2 - 0.03 * L, zb),
            (-L / 2 + 0.07 * L, z0 + nose[2] * span), (-L / 2, z0 + nose[1] * span), (-L / 2, z0 + nose[0] * span)]
    faces = m.prism(pts, "X", -half, half, slot)
    _taper(faces, z0, zb, 1.0, 0.92)
    return faces


def _cabin(m, slot, half, base, top, rows, taper=0.8, sink=0.0):
    """Window glass between the belt line `base` and the roof `top`: rows are (y at base, y at top)
    for the front and back. Narrower at the top; it reaches `sink` below the belt line, into the
    body, so a sloping hood never leaves a gap under the windscreen."""
    (f0, f1), (b0, b1) = rows
    k = sink / (top - base)
    pts = [(f0 - (f1 - f0) * k, base - sink), (b0 - (b1 - b0) * k, base - sink), (b1, top), (f1, top)]
    faces = m.prism(pts, "X", -half, half, slot)
    _taper(faces, base, top, taper)


def _roof(m, slot, half, top, y0, y1, thick):
    m.prism([(y0, top - 0.002), (y1, top - 0.002), (y1 - thick, top + thick), (y0 + thick, top + thick)],
            "X", -half, half, slot)


def _lights(m, d, s, zf, zr, half_f, half_r, size):
    L, W = d.L, d.W
    for side in (-1, 1):
        x0, x1 = sorted((side * (half_f - 0.04 * W), side * (half_f - 0.04 * W - size[0])))
        m.box((x0, -L / 2 - 0.006 * L, zf), (x1, -L / 2 + 0.02 * L, zf + size[1]), s.head)
        x0, x1 = sorted((side * (half_r - 0.03 * W), side * (half_r - 0.03 * W - size[0])))
        m.box((x0, L / 2 - 0.02 * L, zr), (x1, L / 2 + 0.006 * L, zr + 0.8 * size[1]), s.tail)


def _car(m, d, s, rng):
    L, W, R = d.L, d.W, d.R
    half = W / 2 - 0.22 * d.tyres[0]
    z0, zb = 0.42 * R, 2.15 * R + 0.045 * L
    span = zb - z0
    zr = zb + 0.125 * L
    _lower_body(m, d, s.paint, half, z0, zb)
    # the under-tray between the wheels fills the arches and reaches the ground, so a box Collider on
    # the body spans ground to roof
    inner = W / 2 - d.tyres[0] - 0.02 * W
    m.box((-inner, d.axles[0] - 1.2 * R, 0.0), (inner, d.axles[1] + 1.2 * R, z0 + 0.4 * span), s.dark)
    cab = half - 0.05 * W
    _cabin(m, s.glass, cab, zb, zr, ((-0.13 * L, 0.02 * L), (0.4 * L, 0.27 * L)), sink=0.15 * span)
    _roof(m, s.paint, 0.8 * cab + 0.01 * W, zr, 0.012 * L, 0.282 * L, 0.012 * L)
    # bumpers, grille, lights, mirrors, exhaust
    for y0, y1 in ((-L / 2 - 0.012 * L, -L / 2 + 0.04 * L), (L / 2 - 0.04 * L, L / 2 + 0.012 * L)):
        m.box((-0.95 * half, y0, z0 + 0.02 * span), (0.95 * half, y1, z0 + 0.2 * span), s.dark)
    m.box((-0.14 * W, -L / 2 - 0.004 * L, z0 + 0.3 * span), (0.14 * W, -L / 2 + 0.02 * L, z0 + 0.5 * span), s.dark)
    _lights(m, d, s, z0 + 0.38 * span, z0 + 0.62 * span, half, half, (0.16 * W, 0.13 * span))
    for side in (-1, 1):
        x0, x1 = sorted((side * cab * 0.95, side * (half + 0.025 * W)))
        m.box((x0, -0.105 * L, zb + 0.008 * L), (x1, -0.085 * L, zb + 0.026 * L), s.paint)
    m.lathe([(0.025 * W, 0.0), (0.025 * W, 0.08 * L)], 8, s.metal, smooth=False,
            matrix=Matrix.Translation((0.25 * W, L / 2 - 0.04 * L, z0 + 0.1 * span)) @ Matrix.Rotation(-math.pi / 2, 4, "X"))
    if rng.random() < 0.6:  # a rear spoiler
        top = zb + 0.07 * L
        for x in (-0.3 * W, 0.3 * W):
            m.box((x - 0.02 * W, 0.4 * L, zb - 0.01), (x + 0.02 * W, 0.44 * L, top), s.dark)
        m.prism([(0.37 * L, top), (0.49 * L, top), (0.5 * L, top + 0.02 * L), (0.39 * L, top + 0.025 * L)],
                "X", -0.97 * half, 0.97 * half, s.paint)


def _truck(m, d, s, rng):
    L, W, R = d.L, d.W, d.R
    half = W / 2 - 0.2 * d.tyres[0]
    z0, zb = 0.45 * R, 2.15 * R + 0.035 * L
    span = zb - z0
    zr = zb + 0.17 * L
    _lower_body(m, d, s.paint, half, z0, zb, nose=(0.2, 0.8, 0.96), rear=0.15)
    inner = W / 2 - d.tyres[0] - 0.02 * W
    m.box((-inner, d.axles[0] - 1.2 * R, 0.0), (inner, d.axles[1] + 1.2 * R, z0 + 0.4 * span), s.dark)
    cab = half - 0.04 * W
    _cabin(m, s.glass, cab, zb, zr, ((-0.2 * L, -0.13 * L), (0.1 * L, 0.1 * L)), taper=0.88, sink=0.1 * span)
    _roof(m, s.paint, 0.88 * cab + 0.01 * W, zr, -0.135 * L, 0.105 * L, 0.01 * L)
    m.box((-cab, 0.095 * L, zb - 0.01), (cab, 0.12 * L, zr), s.paint)  # the cab's back wall
    # the bed: side walls and a tailgate on the body's rear deck
    wall, rim = 0.035 * W, zb + 0.09 * L
    for side in (-1, 1):
        x0, x1 = sorted((side * half * 0.97, side * (half * 0.97 - wall)))
        m.box((x0, 0.13 * L, zb - 0.01), (x1, L / 2 - 0.03 * L, rim), s.paint)
    m.box((-half * 0.97, 0.13 * L, zb - 0.01), (half * 0.97, 0.13 * L + wall, rim), s.paint)
    m.box((-half * 0.97, L / 2 - 0.03 * L - wall, zb - 0.01), (half * 0.97, L / 2 - 0.03 * L, rim), s.paint)
    m.box((-half * 0.9, 0.13 * L + wall, zb - 0.01), (half * 0.9, L / 2 - 0.03 * L - wall, zb + 0.004 * L), s.dark)
    # chrome bumpers, a big grille, lights
    for y0, y1 in ((-L / 2 - 0.025 * L, -L / 2 + 0.03 * L), (L / 2 - 0.035 * L, L / 2 + 0.02 * L)):
        m.box((-0.98 * half, y0, z0), (0.98 * half, y1, z0 + 0.24 * span), s.metal)
    m.box((-0.24 * W, -L / 2 - 0.006 * L, z0 + 0.32 * span), (0.24 * W, -L / 2 + 0.02 * L, z0 + 0.74 * span), s.dark)
    _lights(m, d, s, z0 + 0.52 * span, z0 + 0.55 * span, half, half, (0.12 * W, 0.18 * span))
    if rng.random() < 0.5:  # roof lights
        for i in range(4):
            x = (-0.3 + 0.2 * i) * W
            m.box((x - 0.05 * W, -0.12 * L, zr + 0.008 * L), (x + 0.05 * W, -0.1 * L, zr + 0.025 * L), s.head)


def _kart(m, d, s, rng):
    L, W, R = d.L, d.W, d.R
    yf, yr = d.axles
    rear_inner = W / 2 - d.tyres[1]
    z0 = 0.35 * R
    # the frame: a low tray between the wheels down to the ground, bumper bars front and back
    tray = rear_inner - 0.03 * W
    m.box((-tray, yf - 0.6 * R, 0.0), (tray, yr + 0.3 * R, z0 + 0.25 * R), s.dark)
    m.box((-W / 2 + 0.06 * W, -L / 2 - 0.01 * L, 0.75 * R), (W / 2 - 0.06 * W, -L / 2 + 0.025 * L, 0.98 * R),
          s.dark)
    m.box((-W / 2 + 0.06 * W, L / 2 - 0.035 * L, 0.8 * R), (W / 2 - 0.06 * W, L / 2, 1.05 * R), s.dark)
    # a pointed nose and side pods between the wheels
    nose = min(0.3 * W, W / 2 - d.tyres[0] - 0.03 * W)
    top = z0 + 1.6 * R
    m.prism([(-L / 2 + 0.03 * L, z0 + 0.25 * R), (yf + 0.9 * R, z0 + 0.25 * R), (yf + 1.3 * R, top),
             (yf - 0.2 * R, top - 0.15 * R), (-L / 2 + 0.03 * L, z0 + 0.9 * R)], "X", -nose, nose, s.paint)
    pod_out = W / 2 - 0.15 * d.tyres[0]
    pod_in = tray - 0.12 * W
    y0, y1 = yf + 1.3 * R, yr - 1.3 * R
    if y1 - y0 > 0.1 * L:
        for side in (-1, 1):
            x0, x1 = sorted((side * pod_in, side * pod_out))
            m.prism([(y0, z0 + 0.25 * R), (y1, z0 + 0.25 * R), (y1, z0 + 1.4 * R), (y0 + 0.5 * R, z0 + 1.4 * R),
                     (y0, z0 + 0.9 * R)], "X", x0, x1, s.paint)
    # seat, steering column and wheel
    sy = yr - 1.6 * R
    sw = min(0.18 * W, pod_in)
    m.box((-sw, sy - 0.12 * L, z0 + 0.2 * R), (sw, sy, z0 + 0.7 * R), s.dark)
    m.prism([(sy - 0.04 * L, z0 + 0.2 * R), (sy + 0.02 * L, z0 + 0.2 * R), (sy + 0.06 * L, z0 + 2.4 * R),
             (sy + 0.0 * L, z0 + 2.4 * R)], "X", -sw, sw, s.dark)
    wheel_at = (0.0, yf + 1.9 * R, z0 + 2.9 * R)
    tilt = Matrix.Translation(wheel_at) @ Matrix.Rotation(math.radians(-60), 4, "X")
    m.lathe([(0.012 * L, -0.27 * L), (0.012 * L, 0.0)], 6, s.metal, smooth=False, matrix=tilt)
    m.torus(0.065 * L, 0.012 * L, 12, 4, s.dark, matrix=tilt)
    # the engine and exhaust beside the seat
    ex = min(tray, 0.22 * W)
    m.box((ex - 0.12 * W, yr - 0.6 * R, z0 + 0.25 * R), (ex, yr + 0.4 * R, z0 + 1.6 * R), s.metal)
    m.lathe([(0.02 * L, 0.0), (0.02 * L, 0.14 * L)], 8, s.dark, smooth=False,
            matrix=Matrix.Translation((ex - 0.06 * W, yr + 0.3 * R, z0 + 1.1 * R)) @ Matrix.Rotation(-math.pi / 2, 4, "X"))
