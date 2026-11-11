"""Run inside Fusion with Halo Assembly open.

1. Gives every body a real physical material (for mass and center of mass) and the
   matching appearance (for renders).
2. Adds the drip tray (feature 11) if it's missing: a Ø152 well with a Ø162 counterbore
   in the base top, and a removable tray whose grille flange rests in the counterbore.
Prints a report of what each part got.
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
REPORT, MISSES = [], []

# ------------------------------------------------------------------ library lookups
_mats, _apps = {}, {}


def _all(kind):
    out = []
    for i in range(app.materialLibraries.count):
        lib = app.materialLibraries.item(i)
        coll = lib.materials if kind == "m" else lib.appearances
        for j in range(coll.count):
            out.append(coll.item(j))
    return out


ALL_M, ALL_A = _all("m"), _all("a")


def material(*names):
    """First exact name that exists, else first name containing every word of an option."""
    key = names
    if key in _mats:
        return _mats[key]
    found = None
    for n in names:
        found = next((m for m in ALL_M if m.name.lower() == n.lower()), None)
        if found:
            break
    if not found:
        for n in names:
            words = n.lower().split()
            found = next((m for m in ALL_M if all(w in m.name.lower() for w in words)), None)
            if found:
                break
    if found:
        found = design.materials.itemByName(found.name) or design.materials.addByCopy(found, found.name)
    else:
        MISSES.append("material " + "/".join(names))
    _mats[key] = found
    return found


def look(*options):
    key = options
    if key in _apps:
        return _apps[key]
    found = None
    for words in options:
        ws = [w.lower() for w in words]
        found = next((a for a in ALL_A if all(w in a.name.lower() for w in ws)), None)
        if found:
            break
    if found:
        found = design.appearances.itemByName(found.name) or design.appearances.addByCopy(found, found.name)
    else:
        MISSES.append("appearance " + "/".join(" ".join(o) for o in options))
    _apps[key] = found
    return found


# ------------------------------------------------------------------ the palette
PETG = lambda: material("PET Plastic", "PETG")                 # printed parts
PLA = lambda: material("PLA", "Plastic")                        # walnut wood-fill PLA
PMMA = lambda: material("PMMA Plastic", "Acrylic")
GLASS = lambda: material("Glass, Clear Glazing", "Glass")
AL = lambda: material("Aluminum 6061", "Aluminum")
SS = lambda: material("Stainless Steel AISI 304", "Stainless Steel")
STEEL = lambda: material("Steel, Non-Alloy", "Steel")
ALLOY = lambda: material("Steel, Alloy", "Steel")
BRASS = lambda: material("Brass")
FR4 = lambda: material("FR4", "Epoxy")
RUBBER = lambda: material("Rubber, Silicone", "Rubber")
PCABS = lambda: material("PC/ABS Plastic", "ABS Plastic", "Plastic")
WATER = lambda: material("Water")

A_BLACK = lambda: look(("plastic", "matte", "black"), ("plastic", "black"), ("black",))
A_GLOSS = lambda: look(("plastic", "glossy", "black"), ("piano", "black"), ("glossy", "black"))
A_WALNUT = lambda: look(("walnut",), ("wood", "dark"))
A_FROST = lambda: look(("plastic", "translucent", "matte", "white"), ("translucent", "white"), ("frosted",))
A_GLOW = lambda: look(("emissive", "warm"), ("led", "warm"), ("emissive",)) or A_FROST()
A_CLEAR = lambda: look(("glass", "clear"), ("glass",))
A_ALU = lambda: look(("aluminum", "satin"), ("aluminum",))
A_SS = lambda: look(("stainless", "satin"), ("steel", "satin"))
A_STEEL = lambda: look(("steel", "satin"), ("steel",))
A_OXIDE = lambda: look(("black", "oxide"), ("steel", "black"), ("black",))
A_BRASS = lambda: look(("brass", "satin"), ("brass",))
A_RUBBER = lambda: look(("rubber", "black"), ("rubber",))
A_PCB = lambda: look(("plastic", "glossy", "green"), ("green",))
A_DRINK = lambda: look(("glass", "amber"), ("amber",), ("orange", "translucent"))


def apply(body, mat, app_):
    try:
        if mat:
            body.material = mat           # sets mass properties (and a default look)
        if app_:
            body.appearance = app_        # then the look we actually want
    except Exception as e:
        MISSES.append(f"{body.name}: {e}")
    return f"{mat.name if mat else '-'} / {app_.name if app_ else '-'}"


# component name fragment -> rule; a rule is (material, appearance) for every body, or a
# function(body_index, body) -> (material, appearance) for multi-body parts
RULES = [
    ("Base", (PETG, A_BLACK)),
    ("Bottle pad", (PLA, A_WALNUT)),
    ("Bottle 750", lambda i, b: (RUBBER, A_RUBBER) if b.name == "Cap" else (GLASS, A_CLEAR)),
    ("Glass stage", (PMMA, A_FROST)),
    ("Load cell", (AL, A_ALU)),
    ("LED ring", (FR4, A_GLOSS)),
    ("Spine", (PETG, A_BLACK)),
    ("Halo light strip", (PMMA, A_GLOW)),
    ("Halo ring", (PETG, A_BLACK)),
    ("Nozzle tip", (SS, A_SS)),
    ("Display head", lambda i, b: (GLASS, A_GLOSS) if b.name == "Screen" else (PETG, A_BLACK)),
    ("Pump", lambda i, b: (STEEL, A_STEEL) if i == 1 else (PCABS, A_BLACK)),
    ("Raspberry Pi", lambda i, b: (AL, A_ALU) if i == 1 else (FR4, A_PCB)),
    ("Driver Board", (FR4, A_GLOSS)),
    ("Rocks glass", lambda i, b: (WATER, A_DRINK) if i == 1 else (GLASS, A_CLEAR)),
    ("Drip tray", (PETG, A_BLACK)),
    ("91290A115", (ALLOY, A_OXIDE)),        # McMaster M3 socket head screw, black-oxide alloy steel
    ("94180A331", (BRASS, A_BRASS)),        # McMaster M3 heat-set insert, brass
]


def rule_for(name):
    for frag, rule in RULES:
        if frag.lower() in name.lower():
            return rule
    return None


def finish_component(comp, done):
    if comp.id in done:
        return
    done.add(comp.id)
    rule = rule_for(comp.name)
    bodies = [comp.bRepBodies.item(i) for i in range(comp.bRepBodies.count)]
    if rule is None:
        if bodies:
            MISSES.append(f"no rule for {comp.name}")
        return
    for i, b in enumerate(bodies):
        m, a = rule(i, b) if callable(rule) else rule
        REPORT.append(f"{comp.name[:34]:34} {b.name[:14]:14} {apply(b, m(), a())}")


# ------------------------------------------------------------------ drip tray
def plane(c, z):
    pi = c.constructionPlanes.createInput()
    pi.setByOffset(c.xYConstructionPlane, V(z))
    p = c.constructionPlanes.add(pi)
    p.isLightBulbOn = False
    return p


def sketch_ring(c, z, ro, ri=None):
    sk = c.sketches.add(plane(c, z))
    sk.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), ro * MM)
    if ri:
        sk.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), ri * MM)
        return max((sk.profiles.item(i) for i in range(sk.profiles.count)), key=lambda p: p.profileLoops.count)
    return sk.profiles.item(0)


def extrude(c, prof, h, op, bodies=None):
    ex = c.features.extrudeFeatures
    inp = ex.createInput(prof, op)
    inp.setDistanceExtent(False, V(h))
    if bodies:
        inp.participantBodies = bodies
    return ex.add(inp)


def add_drip_tray():
    if any("Drip tray" in o.component.name for o in root.occurrences):
        return "drip tray already there"
    base = next(o.component for o in root.occurrences if o.component.name.startswith("Base"))
    bodies = [base.bRepBodies.item(i) for i in range(base.bRepBodies.count)]
    CUT = adsk.fusion.FeatureOperations.CutFeatureOperation
    NEW = adsk.fusion.FeatureOperations.NewBodyFeatureOperation
    extrude(base, sketch_ring(base, 64, 76), 6, CUT, bodies)          # Ø152 well through the top plate
    extrude(base, sketch_ring(base, 68.5, 81), 1.5, CUT, bodies)      # Ø162 counterbore for the flange
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    c = occ.component
    c.name = "Drip tray (removable)"
    extrude(c, sketch_ring(c, 58, 75, 53), 10.5, NEW)                 # channel walls, z 58-68.5
    tray = c.bRepBodies.item(0)
    extrude(c, sketch_ring(c, 60, 73, 55), 8.5, CUT, [tray])          # hollow the channel
    extrude(c, sketch_ring(c, 68.5, 80.5, 53), 1.5, NEW)              # grille flange in the counterbore
    grille = c.bRepBodies.item(1)
    grille.name = "Grille"
    for k in range(24):                                               # 24 drain slots
        a = math.radians(7.5 + 15 * k)
        ux, uy = math.cos(a), math.sin(a)
        nx, ny = -uy * 1.2, ux * 1.2
        pts = [(ux * 57 + nx, uy * 57 + ny), (ux * 72 + nx, uy * 72 + ny),
               (ux * 72 - nx, uy * 72 - ny), (ux * 57 - nx, uy * 57 - ny)]
        sk = c.sketches.add(plane(c, 68.5))
        ln = sk.sketchCurves.sketchLines
        P = lambda p: adsk.core.Point3D.create(p[0] * MM, p[1] * MM, 0)
        for p, q in zip(pts, pts[1:] + pts[:1]):
            ln.addByTwoPoints(P(p), P(q))
        extrude(c, sk.profiles.item(0), 1.5, CUT, [grille])
    return "drip tray added"


# ------------------------------------------------------------------ run
try:
    note = add_drip_tray()
except Exception:
    note = "drip tray FAILED: " + traceback.format_exc(limit=2)
done = set()
for occ in root.allOccurrences:
    finish_component(occ.component, done)

# Bottle tints stay per occurrence so the shelf looks varied
tints = [("glass", "green"), ("glass", "amber"), ("glass", "clear"), ("glass", "blue"),
         ("glass", "brown"), ("glass", "clear"), ("glass", "green"), ("glass", "smoke")]
bottles = [o for o in root.occurrences if o.component.name.startswith("Bottle 750")]
for o, t in zip(bottles, tints):
    a = look(t, ("glass", "clear"))
    if a:
        o.appearance = a
for o in root.occurrences:                                      # clear stray overrides elsewhere
    if not o.component.name.startswith("Bottle 750"):
        o.appearance = None

try:
    mass = root.getPhysicalProperties(adsk.fusion.CalculationAccuracy.LowCalculationAccuracy).mass
except Exception:
    mass = 0
print(note)
print(f"{len(REPORT)} bodies set, assembly mass {mass:.2f} kg (bottles and drink included)")
print("MISSES: " + (" | ".join(MISSES) if MISSES else "none"))
HALO_REPORT = REPORT
