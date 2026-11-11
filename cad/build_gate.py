"""Halo "Gate" redesign. Run in Fusion with Halo Assembly open; saves Halo Gate Assembly.

Sealed form: a walnut-topped plinth, a vertical light ring (the gate) the glass stands in,
and a closed drum behind it that hides the 8 bottles (pumps and electronics in the plinth).
The round screen sits on the top of the ring. Purchased parts and McMaster hardware from
the original assembly are reused and moved; custom parts are rebuilt.
World frame: Z up, front faces -Y, glass at the origin. Units mm (Fusion uses cm).
"""
import math
import traceback

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()
design = adsk.fusion.Design.cast(app.activeProduct)
root = design.rootComponent
MM = 0.1
V = lambda mm: adsk.core.ValueInput.createByReal(mm * MM)
NEW = adsk.fusion.FeatureOperations.NewBodyFeatureOperation
JOIN = adsk.fusion.FeatureOperations.JoinFeatureOperation
CUT = adsk.fusion.FeatureOperations.CutFeatureOperation
LOG = []

# ------------------------------------------------------------------ key dimensions
TOP = 69.0                      # plinth top (walnut), same height the stage and glass already use
PLATE = 8.0                     # walnut top plate thickness
BX0, BX1, BY0, BY1 = -215.0, 215.0, -115.0, 385.0     # plinth footprint
RING_CZ, RING_RO, RING_RI, RING_T = 209.0, 200.0, 170.0, 40.0
DRUM_CY, DRUM_R, DRUM_TOP = 190.0, 170.0, 400.0
BOTTLE_R = 120.0
BOTTLE_ANGLES = [22.5 + 45 * k for k in range(8)]


def P(x, y, z=0.0):
    return adsk.core.Point3D.create(x * MM, y * MM, z * MM)


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


def new_part(name, m=None):
    occ = root.occurrences.addNewComponent(m or adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ, occ.component


def plane(c, z):
    if abs(z) < 1e-9:
        return c.xYConstructionPlane
    pi = c.constructionPlanes.createInput()
    pi.setByOffset(c.xYConstructionPlane, V(z))
    p = c.constructionPlanes.add(pi)
    p.isLightBulbOn = False
    return p


def _ext(c, prof, h, op, bodies=None):
    ex = c.features.extrudeFeatures
    inp = ex.createInput(prof, op)
    inp.setDistanceExtent(False, V(h))
    if bodies:
        inp.participantBodies = bodies
    return ex.add(inp)


def cyl(c, r, h, z0=0, x=0, y=0, op=NEW, bodies=None):
    sk = c.sketches.add(plane(c, z0))
    sk.sketchCurves.sketchCircles.addByCenterRadius(P(x, y), r * MM)
    return _ext(c, sk.profiles.item(0), h, op, bodies)


def ring(c, ro, ri, h, z0=0, x=0, y=0, op=NEW, bodies=None):
    sk = c.sketches.add(plane(c, z0))
    sk.sketchCurves.sketchCircles.addByCenterRadius(P(x, y), ro * MM)
    sk.sketchCurves.sketchCircles.addByCenterRadius(P(x, y), ri * MM)
    prof = max((sk.profiles.item(i) for i in range(sk.profiles.count)), key=lambda p: p.profileLoops.count)
    return _ext(c, prof, h, op, bodies)


def poly(c, pts, h, z0=0, op=NEW, bodies=None):
    sk = c.sketches.add(plane(c, z0))
    ln = sk.sketchCurves.sketchLines
    for a, b in zip(pts, pts[1:] + pts[:1]):
        ln.addByTwoPoints(P(*a), P(*b))
    return _ext(c, sk.profiles.item(0), h, op, bodies)


def box(c, x0, y0, x1, y1, h, z0=0, op=NEW, bodies=None):
    return poly(c, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], h, z0, op, bodies)


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


def vertical_edges(body):
    out = []
    for e in body.edges:
        g = e.geometry
        if g.objectType == adsk.core.Line3D.classType():
            s, t = g.startPoint, g.endPoint
            if abs(s.x - t.x) < 1e-6 and abs(s.y - t.y) < 1e-6:
                out.append(e)
    return out


def circle_edges(body, radius, z):
    out = []
    for e in body.edges:
        g = e.geometry
        if g.objectType == adsk.core.Circle3D.classType():
            if abs(g.radius - radius * MM) < 1e-4 and abs(g.center.z - z * MM) < 1e-4:
                out.append(e)
    return out


def step(name, fn):
    try:
        fn()
        LOG.append("ok   " + name)
    except Exception:
        LOG.append("FAIL " + name + ": " + traceback.format_exc(limit=2).splitlines()[-1])


def occs(prefix):
    return [o for o in root.occurrences if o.component.name.startswith(prefix)]


# ------------------------------------------------------------------ 1. clear the old shell
OLD_SHELL = ["Base", "Bottle pad", "Spine", "Halo ring", "Halo light strip", "Display head",
             "Glass stage", "Drip tray"]


def clear_old():
    for o in list(root.occurrences):
        if any(o.component.name.startswith(p) for p in OLD_SHELL):
            o.deleteMe()


# ------------------------------------------------------------------ 2. new custom parts
def make_plinth():
    occ, c = new_part("Plinth tub")
    box(c, BX0, BY0, BX1, BY1, TOP - PLATE)
    body = c.bRepBodies.item(0)
    fillet(c, vertical_edges(body), 50)
    body = c.bRepBodies.item(0)
    box(c, BX0 + 5, BY0 + 5, BX1 - 5, BY1 - 5, TOP - PLATE - 4, z0=4, op=CUT, bodies=[body])
    occ, p = new_part("Top plate (walnut)")
    box(p, BX0, BY0, BX1, BY1, PLATE, z0=TOP - PLATE)
    plate = p.bRepBodies.item(0)
    fillet(p, vertical_edges(plate), 50)
    plate = p.bRepBodies.item(0)
    cyl(p, 76, PLATE, z0=TOP - PLATE, op=CUT, bodies=[plate])                 # drip-tray well
    cyl(p, 81, 1.5, z0=TOP - 1.5, op=CUT, bodies=[plate])                       # tray flange seat
    for s in (-1, 1):                                                            # slots for the ring's feet
        box(p, s * 95, -20.5, s * 145, 20.5, PLATE, z0=TOP - PLATE, op=CUT, bodies=[plate])
    for a in BOTTLE_ANGLES:                                                      # tube pass-throughs to the pumps
        x, y = 75 * math.cos(math.radians(a)), DRUM_CY + 75 * math.sin(math.radians(a))
        cyl(p, 5, PLATE, z0=TOP - PLATE, x=x, y=y, op=CUT, bodies=[plate])


def make_gate():
    m = mat(0, 0, RING_CZ, rotx=90)                       # built flat, stood up: thickness along Y
    occ, c = new_part("Gate ring", m)
    ring(c, RING_RO, RING_RI, RING_T, z0=-RING_T / 2)
    # 12 x 4 mm channel in the inner face for the WS2812B strip, covered by the frosted strip
    ring(c, RING_RI + 4, RING_RI - 1, 12, z0=-6, op=CUT, bodies=[c.bRepBodies.item(0)])
    occ, s = new_part("Gate light strip (frosted acrylic)", m)
    ring(s, RING_RI, RING_RI - 3, 30, z0=-15)


def make_display():
    # disc built along +Z, tipped forward so its face points at the viewer (-Y), on the ring top
    m = mat(0, -RING_T / 2, RING_CZ + 186, rotx=90)
    occ, c = new_part("Display head (5 in round)", m)
    cyl(c, 78, 16)
    cyl(c, 66, 1, z0=16, op=NEW)
    c.bRepBodies.item(1).name = "Screen"
    ring(c, 78, 70, 2, z0=16, op=NEW)
    c.bRepBodies.item(2).name = "Walnut trim"


def make_drum():
    occ, c = new_part("Bottle drum")
    cyl(c, DRUM_R, DRUM_TOP - TOP, z0=TOP, y=DRUM_CY)
    body = c.bRepBodies.item(0)
    cyl(c, DRUM_R - 4, DRUM_TOP - TOP, z0=TOP, y=DRUM_CY, op=CUT, bodies=[body])
    occ, lid = new_part("Drum lid (walnut)")
    cyl(lid, DRUM_R + 2, 12, z0=DRUM_TOP, y=DRUM_CY)
    lb = lid.bRepBodies.item(0)
    try:
        fillet(lid, circle_edges(lb, DRUM_R + 2, DRUM_TOP + 12), 4)
    except Exception:
        pass
    occ, w = new_part("Level window (frosted acrylic)")
    # a narrow frosted window on the front-left of the drum to see bottle levels
    a = math.radians(205)
    cx, cy = (DRUM_R + 0.5) * math.cos(a), DRUM_CY + (DRUM_R + 0.5) * math.sin(a)
    tx, ty = -math.sin(a), math.cos(a)
    hw, hd = 12, 1.5
    nx, ny = math.cos(a), math.sin(a)
    pts = [(cx + tx * hw + nx * hd, cy + ty * hw + ny * hd), (cx - tx * hw + nx * hd, cy - ty * hw + ny * hd),
           (cx - tx * hw - nx * hd, cy - ty * hw - ny * hd), (cx + tx * hw - nx * hd, cy + ty * hw - ny * hd)]
    poly(w, pts, 240, z0=TOP + 60)


def make_arm():
    occ, c = new_part("Nozzle arm and hub")
    box(c, -10, 0, 10, DRUM_CY - DRUM_R + 6, 14, z0=259)        # bridges the drum to the hub
    cyl(c, 18, 28, z0=252, op=JOIN)
    body = c.bRepBodies.item(0)
    try:
        fillet(c, circle_edges(body, 18, 252), 3)
    except Exception:
        pass


def make_stage_and_tray():
    occ, c = new_part("Glass stage (frosted acrylic)")
    cyl(c, 50, 6, z0=TOP)
    occ, t = new_part("Drip tray (removable)")
    z0 = TOP - 12
    ring(t, 75, 53, 10.5, z0=z0)
    tray = t.bRepBodies.item(0)
    ring(t, 73, 55, 8.5, z0=z0 + 2, op=CUT, bodies=[tray])
    ring(t, 80.5, 53, 1.5, z0=TOP - 1.5, op=NEW)
    grille = t.bRepBodies.item(1)
    grille.name = "Grille"
    for k in range(24):
        a = math.radians(7.5 + 15 * k)
        ux, uy = math.cos(a), math.sin(a)
        nx, ny = -uy * 1.2, ux * 1.2
        pts = [(ux * 57 + nx, uy * 57 + ny), (ux * 72 + nx, uy * 72 + ny),
               (ux * 72 - nx, uy * 72 - ny), (ux * 57 - nx, uy * 57 - ny)]
        poly(t, pts, 1.5, z0=TOP - 1.5, op=CUT, bodies=[grille])


def make_pads():
    first = None
    for a in BOTTLE_ANGLES:
        x, y = BOTTLE_R * math.cos(math.radians(a)), DRUM_CY + BOTTLE_R * math.sin(math.radians(a))
        if first is None:
            occ, first = new_part("Bottle pad (walnut PLA)", mat(x, y, TOP))
            cyl(first, 42, 4)
        else:
            root.occurrences.addExistingComponent(first, mat(x, y, TOP))


# ------------------------------------------------------------------ 3. move the reused parts
def move_parts():
    bottles = occs("Bottle 750")
    for o, a in zip(bottles, BOTTLE_ANGLES):
        o.transform2 = mat(BOTTLE_R * math.cos(math.radians(a)),
                           DRUM_CY + BOTTLE_R * math.sin(math.radians(a)), TOP + 4)
    for o, a in zip(occs("Pump"), BOTTLE_ANGLES):
        o.transform2 = mat(BOTTLE_R * math.cos(math.radians(a)),
                           DRUM_CY + BOTTLE_R * math.sin(math.radians(a)), 35, rotz=a, rotx=90)
    for o in occs("Raspberry Pi"):
        o.transform2 = mat(0, DRUM_CY, 10)
    for o in occs("Halo Driver Board"):
        o.transform2 = mat(20, DRUM_CY, 26)            # offset +20 in X to clear the 202.5° pump
    # M3 screws and heat-set inserts now fix the ring's feet to the plinth
    pos = [(-120, -10), (120, -10), (-120, 10), (120, 10)]
    flip = mat(rotx=180)
    for o, (x, y) in zip(occs("91290A115"), pos):
        m = mat(rotx=180)
        m.translation = adsk.core.Vector3D.create(x * MM, y * MM, (TOP - PLATE + 3.3) * MM)
        o.transform2 = m
    for o, (x, y) in zip(occs("94180A331"), pos):
        o.transform2 = mat(x, y, TOP + 2.04)
    if design.snapshots.hasPendingSnapshot:
        design.snapshots.add()


# ------------------------------------------------------------------ 4. materials and colors
_m, _a = {}, {}
ALL_M = [app.materialLibraries.item(i).materials.item(j) for i in range(app.materialLibraries.count)
         for j in range(app.materialLibraries.item(i).materials.count)]
ALL_A = [app.materialLibraries.item(i).appearances.item(j) for i in range(app.materialLibraries.count)
         for j in range(app.materialLibraries.item(i).appearances.count)]


def material(*names):
    if names in _m:
        return _m[names]
    f = None
    for n in names:
        f = next((m for m in ALL_M if m.name.lower() == n.lower()), None)
        if f:
            break
    if f:
        f = design.materials.itemByName(f.name) or design.materials.addByCopy(f, f.name)
    _m[names] = f
    return f


def look(*options):
    if options in _a:
        return _a[options]
    f = None
    for words in options:
        f = next((a for a in ALL_A if all(w in a.name.lower() for w in words)), None)
        if f:
            break
    if f:
        f = design.appearances.itemByName(f.name) or design.appearances.addByCopy(f, f.name)
    _a[options] = f
    return f


PETG = lambda: material("PET Plastic")
PLA = lambda: material("PLA", "Plastic")
PMMA = lambda: material("PMMA Plastic")
GLASS = lambda: material("Glass, Clear Glazing", "Glass")
AL = lambda: material("Aluminum 6061")
SS = lambda: material("Stainless Steel AISI 304")
STEEL = lambda: material("Steel, Non-Alloy")
ALLOY = lambda: material("Steel, Alloy")
BRASS = lambda: material("Brass")
FR4 = lambda: material("FR4")
RUBBER = lambda: material("Rubber, Silicone")
PCABS = lambda: material("PC/ABS Plastic")
WATER = lambda: material("Water")
A_BLACK = lambda: look(("plastic", "matte", "black"), ("black",))
A_GLOSS = lambda: look(("plastic", "glossy", "black"), ("glossy", "black"))
A_WALNUT = lambda: look(("walnut",), ("wood",))
A_FROST = lambda: look(("plastic", "translucent", "matte", "white"), ("translucent", "white"))
A_CLEAR = lambda: look(("glass (clear)",), ("glass", "clear"))
A_ALU = lambda: look(("aluminum", "satin"),)
A_SS = lambda: look(("stainless", "satin"), ("steel", "satin"))
A_STEEL = lambda: look(("steel", "satin"),)
A_OXIDE = lambda: look(("black", "oxide"), ("black",))
A_BRASS = lambda: look(("brass", "satin"), ("brass",))
A_RUBBER = lambda: look(("rubber", "black"), ("rubber",))
A_PCB = lambda: look(("plastic", "glossy", "green"), ("green",))
A_DRINK = lambda: look(("amber",),)

RULES = [
    ("Plinth tub", (PETG, A_BLACK)),
    ("Top plate", (PLA, A_WALNUT)),
    ("Gate light strip", (PMMA, A_FROST)),
    ("Gate ring", (PETG, A_BLACK)),
    ("Display head", lambda i, b: {"Screen": (GLASS, A_GLOSS), "Walnut trim": (PLA, A_WALNUT)}.get(b.name, (PETG, A_BLACK))),
    ("Bottle drum", (PETG, A_BLACK)),
    ("Drum lid", (PLA, A_WALNUT)),
    ("Level window", (PMMA, A_FROST)),
    ("Nozzle arm", (PETG, A_BLACK)),
    ("Glass stage", (PMMA, A_FROST)),
    ("Drip tray", (PETG, A_BLACK)),
    ("Bottle pad", (PLA, A_WALNUT)),
    ("Bottle 750", lambda i, b: (RUBBER, A_RUBBER) if b.name == "Cap" else (GLASS, A_CLEAR)),
    ("Load cell", (AL, A_ALU)),
    ("LED ring", (FR4, A_GLOSS)),
    ("Nozzle tip", (SS, A_SS)),
    ("Pump", lambda i, b: (STEEL, A_STEEL) if i == 1 else (PCABS, A_BLACK)),
    ("Raspberry Pi", lambda i, b: (AL, A_ALU) if i == 1 else (FR4, A_PCB)),
    ("Halo Driver Board", (FR4, A_GLOSS)),
    ("Rocks glass", lambda i, b: (WATER, A_DRINK) if i == 1 else (GLASS, A_CLEAR)),
    ("91290A115", (ALLOY, A_OXIDE)),
    ("94180A331", (BRASS, A_BRASS)),
]


def paint_all():
    done, count, misses = set(), 0, []
    for occ in root.allOccurrences:
        c = occ.component
        if c.id in done:
            continue
        done.add(c.id)
        rule = next((r for f, r in RULES if c.name.lower().startswith(f.lower()) or f.lower() in c.name.lower()), None)
        if rule is None:
            if c.bRepBodies.count:
                misses.append(c.name)
            continue
        for i in range(c.bRepBodies.count):
            b = c.bRepBodies.item(i)
            m, a = rule(i, b) if callable(rule) else rule
            mm, aa = m(), a()
            if mm:
                b.material = mm
            if aa:
                b.appearance = aa
            count += 1
    LOG.append(f"materials on {count} bodies; unmatched: {misses or 'none'}")


# ------------------------------------------------------------------ run
for name, fn in [("clear old shell", clear_old), ("plinth", make_plinth), ("gate ring", make_gate),
                 ("display", make_display), ("drum", make_drum), ("nozzle arm", make_arm),
                 ("stage + tray", make_stage_and_tray), ("bottle pads", make_pads),
                 ("move parts", move_parts), ("materials", paint_all)]:
    step(name, fn)

v = app.activeViewport
cam = v.camera
cam.target = adsk.core.Point3D.create(0, 9, 20)
cam.eye = adsk.core.Point3D.create(80, -130, 70)
cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
cam.isFitView = True
v.camera = cam
v.refresh()
print("\n".join(LOG))
