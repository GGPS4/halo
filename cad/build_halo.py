"""Builds the Halo cocktail maker as one multi-component Fusion design.

Run inside Autodesk Fusion (Utilities > Scripts and Add-Ins, or the Text Commands window
with exec(open(path).read())). All sizes are mm; Fusion works in cm internally.
World frame: Z up, front of the machine faces -Y, glass at the origin.
"""
import math
import traceback

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()
MM = 0.1
LOG = []

doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
design = adsk.fusion.Design.cast(app.activeProduct)
design.designType = adsk.fusion.DesignTypes.ParametricDesignType
root = design.rootComponent


V = lambda mm: adsk.core.ValueInput.createByReal(mm * MM)
NEW = adsk.fusion.FeatureOperations.NewBodyFeatureOperation
JOIN = adsk.fusion.FeatureOperations.JoinFeatureOperation
CUT = adsk.fusion.FeatureOperations.CutFeatureOperation


def P(x, y, z=0.0):
    return adsk.core.Point3D.create(x * MM, y * MM, z * MM)


# ------------------------------------------------------------------ placement
def mat(x=0, y=0, z=0, rotz=0, rotx=0):
    m = adsk.core.Matrix3D.create()
    o = adsk.core.Point3D.create(0, 0, 0)
    if rotx:
        r = adsk.core.Matrix3D.create()
        r.setToRotation(math.radians(rotx), adsk.core.Vector3D.create(1, 0, 0), o)
        m.transformBy(r)
    if rotz:
        r = adsk.core.Matrix3D.create()
        r.setToRotation(math.radians(rotz), adsk.core.Vector3D.create(0, 0, 1), o)
        m.transformBy(r)
    m.translation = adsk.core.Vector3D.create(x * MM, y * MM, z * MM)
    return m


def new_part(name, m=None, parent=None):
    parent = parent or root
    occ = parent.occurrences.addNewComponent(m or adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ, occ.component


def copy_part(comp, m, parent=None):
    return (parent or root).occurrences.addExistingComponent(comp, m)


# ------------------------------------------------------------------ geometry
def plane(c, z):
    if abs(z) < 1e-9:
        return c.xYConstructionPlane
    pi = c.constructionPlanes.createInput()
    pi.setByOffset(c.xYConstructionPlane, V(z))
    pl = c.constructionPlanes.add(pi)
    pl.isLightBulbOn = False
    return pl


def _extrude(c, prof, h, op, taper_deg=None):
    ex = c.features.extrudeFeatures
    if taper_deg is None:
        return ex.addSimple(prof, V(h), op)
    inp = ex.createInput(prof, op)
    inp.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(V(h)),
                         adsk.fusion.ExtentDirections.PositiveExtentDirection,
                         adsk.core.ValueInput.createByString(f"{taper_deg} deg"))
    return ex.add(inp)


def cyl(c, r, h, z0=0, x=0, y=0, op=NEW, taper_deg=None):
    sk = c.sketches.add(plane(c, z0))
    sk.sketchCurves.sketchCircles.addByCenterRadius(P(x, y), r * MM)
    return _extrude(c, sk.profiles.item(0), h, op, taper_deg)


def ring(c, ro, ri, h, z0=0, op=NEW):
    sk = c.sketches.add(plane(c, z0))
    sk.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0), ro * MM)
    sk.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0), ri * MM)
    prof = max((sk.profiles.item(i) for i in range(sk.profiles.count)),
               key=lambda p: p.profileLoops.count)
    return _extrude(c, prof, h, op)


def poly(c, pts, h, z0=0, op=NEW):
    sk = c.sketches.add(plane(c, z0))
    lines = sk.sketchCurves.sketchLines
    for a, b in zip(pts, pts[1:] + pts[:1]):
        lines.addByTwoPoints(P(*a), P(*b))
    return _extrude(c, sk.profiles.item(0), h, op)


def box(c, x0, y0, x1, y1, h, z0=0, op=NEW):
    return poly(c, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], h, z0, op)


def bar(c, a_deg, r0, r1, w, h, z0, op=NEW):
    """Radial bar from radius r0 to r1 along angle a."""
    a = math.radians(a_deg)
    ux, uy = math.cos(a), math.sin(a)
    nx, ny = -uy * w / 2, ux * w / 2
    pts = [(ux * r0 + nx, uy * r0 + ny), (ux * r1 + nx, uy * r1 + ny),
           (ux * r1 - nx, uy * r1 - ny), (ux * r0 - nx, uy * r0 - ny)]
    return poly(c, pts, h, z0, op)


def fillet(c, edges, r):
    if not edges:
        return
    coll = adsk.core.ObjectCollection.create()
    for e in edges:
        coll.add(e)
    f = c.features.filletFeatures
    inp = f.createInput()
    try:
        inp.edgeSetInputs.addConstantRadiusEdgeSet(coll, V(r), True)
    except Exception:
        inp.addConstantRadiusEdgeSet(coll, V(r), True)
    f.add(inp)


def circle_edges(body, radius, z):
    out = []
    for e in body.edges:
        g = e.geometry
        if g.objectType == adsk.core.Circle3D.classType():
            if abs(g.radius - radius * MM) < 1e-4 and abs(g.center.z - z * MM) < 1e-4:
                out.append(e)
    return out


def vertical_edges(body):
    out = []
    for e in body.edges:
        g = e.geometry
        if g.objectType == adsk.core.Line3D.classType():
            s, t = g.startPoint, g.endPoint
            if abs(s.x - t.x) < 1e-6 and abs(s.y - t.y) < 1e-6:
                out.append(e)
    return out


# ------------------------------------------------------------------ appearances
_LIB = None
_CACHE = {}


def _appearances():
    global _LIB
    if _LIB is None:
        _LIB = []
        for i in range(app.materialLibraries.count):
            lib = app.materialLibraries.item(i)
            if "appearance" in lib.name.lower():
                for j in range(lib.appearances.count):
                    _LIB.append(lib.appearances.item(j))
    return _LIB


def look(*options):
    """First library appearance whose name contains every word of an option."""
    for words in options:
        key = tuple(words)
        if key in _CACHE:
            return _CACHE[key]
        for a in _appearances():
            n = a.name.lower()
            if all(w in n for w in words):
                local = design.appearances.itemByName(a.name) or design.appearances.addByCopy(a, a.name)
                _CACHE[key] = local
                return local
    return None


def paint(c, appearance):
    if appearance is None:
        return
    for b in c.bRepBodies:
        b.appearance = appearance


BLACK = lambda: look(("plastic", "matte", "black"), ("abs", "black"), ("black",))
WALNUT = lambda: look(("walnut",), ("wood", "oak"))
FROST = lambda: look(("translucent", "white"), ("frosted",), ("acrylic",))
STEEL = lambda: look(("stainless", "satin"), ("steel", "satin"), ("steel",))
GLASS = lambda: look(("glass", "clear"), ("glass",))
GLOSS = lambda: look(("glossy", "black"), ("piano", "black"), ("black",))
PCB_G = lambda: look(("plastic", "glossy", "green"), ("green",))
ALU = lambda: look(("aluminum", "satin"), ("aluminum",))


def step(name, fn):
    try:
        fn()
        LOG.append("ok   " + name)
    except Exception:
        LOG.append("FAIL " + name + "\n" + traceback.format_exc(limit=2))


# ================================================================== the parts
BASE_R, BASE_H, WALL, TOP = 190.0, 70.0, 6.0, 6.0
PITCH_R = 140.0
SLOTS = [-90 + 40 * k for k in range(1, 9)]        # 8 bottle angles; -90 (front) stays open
SPINE = (-18, 55, 18, 95)                          # x0, y0, x1, y1
SPINE_TOP = 430.0
RING_Z, RING_OD, RING_ID, RING_T = 260.0, 190.0, 166.0, 14.0
STAGE_Z = 69.0


def make_base():
    occ, c = new_part("Base (4 printed sectors)")
    cyl(c, BASE_R, BASE_H)
    body = c.bRepBodies.item(0)
    cyl(c, BASE_R - WALL, BASE_H - TOP - 4, z0=4, op=CUT)          # hollow, 4 mm floor, 6 mm top
    cyl(c, 52, TOP, z0=BASE_H - TOP, op=CUT)                        # opening for the glass stage
    for a in SLOTS:                                                 # tube grommet holes
        r = PITCH_R - 52
        cyl(c, 4, TOP, z0=BASE_H - TOP, x=r * math.cos(math.radians(a)),
            y=r * math.sin(math.radians(a)), op=CUT)
    body = c.bRepBodies.item(0)
    fillet(c, circle_edges(body, BASE_R, BASE_H), 8)
    body = c.bRepBodies.item(0)
    sp = c.features.splitBodyFeatures
    sp.add(sp.createInput(body, c.xZConstructionPlane, True))
    coll = adsk.core.ObjectCollection.create()
    for b in c.bRepBodies:
        coll.add(b)
    sp.add(sp.createInput(coll, c.yZConstructionPlane, True))
    for i, b in enumerate(c.bRepBodies):
        b.name = f"Base sector {i + 1}"
    paint(c, BLACK())


def make_pads_and_bottles():
    occ, pad = new_part("Bottle pad (walnut PLA)", mat(PITCH_R * math.cos(math.radians(SLOTS[0])),
                                                         PITCH_R * math.sin(math.radians(SLOTS[0])), BASE_H))
    cyl(pad, 46, 6)
    cyl(pad, 41, 2, z0=4, op=CUT)                                   # locating recess for the bottle
    paint(pad, WALNUT())
    occ_b, bottle = new_part("Bottle 750 ml (reference)", mat(PITCH_R * math.cos(math.radians(SLOTS[0])),
                                                                PITCH_R * math.sin(math.radians(SLOTS[0])), BASE_H + 4))
    cyl(bottle, 40, 200)
    cyl(bottle, 40, 45, z0=200, op=JOIN, taper_deg=-30)
    cyl(bottle, 14, 50, z0=245, op=JOIN)
    cyl(bottle, 15.5, 12, z0=290, op=NEW)                           # pour cap with dip tube
    bottle.bRepBodies.item(1).name = "Cap"
    bottle.bRepBodies.item(0).appearance = GLASS()
    bottle.bRepBodies.item(1).appearance = BLACK()
    tints = [("glass", "green"), ("glass", "amber"), ("glass", "clear"), ("glass", "blue"),
             ("glass", "brown"), ("glass", "clear"), ("glass", "green"), ("glass", "smoke")]
    for i, a in enumerate(SLOTS):
        x, y = PITCH_R * math.cos(math.radians(a)), PITCH_R * math.sin(math.radians(a))
        if i:
            copy_part(pad, mat(x, y, BASE_H))
            ob = copy_part(bottle, mat(x, y, BASE_H + 4))
        else:
            ob = occ_b
        tint = look(tints[i], ("glass",))
        if tint:
            ob.appearance = tint


def make_stage():
    occ, c = new_part("Glass stage (frosted acrylic)")
    cyl(c, 50, 6, z0=STAGE_Z)
    paint(c, FROST())
    occ, lc = new_part("Load cell 1 kg (purchased)")
    box(lc, -40, -6.35, 40, 6.35, 12.7, z0=40)
    box(lc, 28, -6.35, 40, 6.35, 36, z0=4)                           # fixed-end pedestal
    box(lc, -40, -6, -35, 6, STAGE_Z - 52.7, z0=52.7)                # post to the stage
    paint(lc, ALU())
    occ, led = new_part("LED ring 24x WS2812B (purchased)")
    ring(led, 33, 26, 1.6, z0=60)
    paint(led, PCB_G())


def make_spine_and_halo():
    occ, c = new_part("Spine")
    x0, y0, x1, y1 = SPINE
    box(c, x0, y0, x1, y1, SPINE_TOP - BASE_H, z0=BASE_H)
    fillet(c, vertical_edges(c.bRepBodies.item(0)), 10)
    paint(c, BLACK())

    occ, h = new_part("Halo ring")
    ring(h, RING_OD / 2, RING_ID / 2, RING_T, z0=RING_Z)
    for a in (90, 210, 330):
        bar(h, a, 16, RING_ID / 2 + 1, 6, 6, z0=RING_Z + 4, op=JOIN)
    cyl(h, 18, 28, z0=RING_Z - 8, op=JOIN)                           # nozzle hub
    paint(h, BLACK())
    occ, strip = new_part("Halo light strip (frosted acrylic)")
    ring(strip, RING_ID / 2, RING_ID / 2 - 2, 8, z0=RING_Z + 3)
    paint(strip, FROST())
    occ, tip = new_part("Nozzle tip 3 mm stainless (purchased)", mat(8, 0, RING_Z - 23))
    cyl(tip, 1.5, 15)
    cyl(tip, 1.0, 15, op=CUT)
    paint(tip, STEEL())
    for k in range(1, 8):
        a = math.radians(45 * k)
        copy_part(tip, mat(8 * math.cos(a), 8 * math.sin(a), RING_Z - 23))


def make_display():
    occ, c = new_part("Display head (5 in round)", mat(0, 70, 455, rotx=70))
    cyl(c, 75, 14)
    paint(c, BLACK())
    cyl(c, 64, 1, z0=14, op=NEW)
    c.bRepBodies.item(1).name = "Screen"
    g = GLOSS()
    if g:
        c.bRepBodies.item(1).appearance = g


def make_pumps():
    def m_for(a):
        return mat(PITCH_R * math.cos(math.radians(a)), PITCH_R * math.sin(math.radians(a)), 35,
                   rotz=a, rotx=90)

    occ, c = new_part("Pump Kamoer KPHM100 (purchased)", m_for(SLOTS[0]))
    box(c, -16.5, -16.5, 16.5, 16.5, 28)
    cyl(c, 12, 45, z0=28, op=NEW)
    for x in (-7, 7):
        cyl(c, 2.5, 8, z0=-8, x=x, y=9, op=NEW)
    c.bRepBodies.item(0).appearance = BLACK()
    c.bRepBodies.item(1).appearance = ALU()
    for a in SLOTS[1:]:
        copy_part(c, m_for(a))


def make_electronics():
    occ, pi = new_part("Raspberry Pi 5 (purchased)", mat(0, 80, 10, rotz=0))
    box(pi, -42.5, -28, 42.5, 28, 1.6)
    box(pi, -20, -20, 20, 20, 9, z0=1.6, op=NEW)                    # active cooler
    pi.bRepBodies.item(0).appearance = PCB_G()
    pi.bRepBodies.item(1).appearance = ALU()
    occ, drv = new_part("Halo Driver Board v1 (JLCPCB)", mat(0, 80, 26))
    box(drv, -77.5, -28.25, 22.5, 41.75, 1.6)
    paint(drv, BLACK())
    occ, glass = new_part("Rocks glass + drink (reference)", mat(0, 0, STAGE_Z + 6))
    cyl(glass, 40, 90)
    cyl(glass, 37, 85, z0=5, op=CUT)
    cyl(glass, 36.5, 45, z0=5, op=NEW)
    glass.bRepBodies.item(0).appearance = GLASS()
    drink = look(("glass", "amber"), ("amber",), ("orange",))
    if drink:
        glass.bRepBodies.item(1).appearance = drink


for name, fn in [("base", make_base), ("pads+bottles", make_pads_and_bottles), ("stage", make_stage),
                 ("spine+halo", make_spine_and_halo), ("display", make_display), ("pumps", make_pumps),
                 ("electronics", make_electronics)]:
    step(name, fn)

try:
    if design.snapshots.hasPendingSnapshot:
        design.snapshots.add()
except Exception:
    pass
app.activeViewport.fit()
print("\n".join(LOG))
