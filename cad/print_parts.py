"""Print-ready Halo Gate parts. Run in Fusion's Text Commands (Py) with Halo Gate Assembly open.

Makes CAD › Halo Gate Print Parts with one design per printed part. Every part is split into
pieces that fit a 220 x 220 mm bed (largest piece 210 mm), each piece laid flat in its print
orientation. Seams get 1.9 mm holes for 1.75 mm filament pins (glue with CA or epoxy), and
fasteners are the M3 screws and heat-set inserts on the parts list (inserts: 4.1 mm holes).
STL files for every piece and DXF cut files for the acrylic go to Documents/Halo print files.

Design fixes made here (and in the assembly by fix_ring.py):
- The gate ring no longer runs under the stage, where it hit the load cell. It's trimmed just
  below the top plate and stands on two feet that pass through the plate and are screwed to
  pads on the plinth floor.
- The nozzle arm is as tall as the hub (28 mm) so it prints flat, with a groove on top for
  the 8 tubes, which leave the drum through a slot just above it.
- Feature 2: a cable channel runs up the back of the gate ring from the left foot to the
  display, plus tube clips for the drum wall.
- Feature 7: a raised electronics tray with a lip, standoffs for the Pi 5 and zip-tie slots
  for both 5 V converters.
"""
import math
import os
import traceback

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()
tbm = adsk.fusion.TemporaryBRepManager.get()
MM = 0.1
U = adsk.fusion.BooleanTypes.UnionBooleanType
D = adsk.fusion.BooleanTypes.DifferenceBooleanType
I = adsk.fusion.BooleanTypes.IntersectionBooleanType
BED = 210.0
OUT = os.path.join(os.path.expanduser("~"), "Documents", "Halo print files")
LOG = []

# ------------------------------------------------------------------ dimensions (match build_gate.py)
TOP, PLATE = 69.0, 8.0
BX0, BX1, BY0, BY1 = -215.0, 215.0, -115.0, 385.0
RING_CZ, RING_RO, RING_RI, RING_T = 209.0, 200.0, 170.0, 40.0
DRUM_CY, DRUM_R, DRUM_TOP, WALL = 190.0, 170.0, 400.0, 4.0
BOTTLE_ANGLES = [22.5 + 45 * k for k in range(8)]
PIN_R, INSERT_R, CLEAR_R, HEAD_R = 0.95, 2.05, 1.7, 3.25


# ------------------------------------------------------------------ solid helpers (mm)
def P(x, y, z):
    return adsk.core.Point3D.create(x * MM, y * MM, z * MM)


def norm(v):
    m = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / m for c in v)


def cyl(p1, p2, r):
    return tbm.createCylinderOrCone(P(*p1), r * MM, P(*p2), r * MM)


def zcyl(x, y, z0, z1, r):
    return cyl((x, y, z0), (x, y, z1), r)


def obox(c, size, ldir=(1, 0, 0), wdir=(0, 1, 0)):
    l, w = norm(ldir), norm(wdir)
    obb = adsk.core.OrientedBoundingBox3D.create(P(*c), adsk.core.Vector3D.create(*l),
                                                 adsk.core.Vector3D.create(*w),
                                                 size[0] * MM, size[1] * MM, size[2] * MM)
    return tbm.createBox(obb)


def box(x0, x1, y0, y1, z0, z1):
    return obox(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2), (x1 - x0, y1 - y0, z1 - z0))


def op(a, b, kind):
    tbm.booleanOperation(a, b, kind)
    return a


def union(a, *bs):
    for b in bs:
        op(a, b, U)
    return a


def cut(a, *bs):
    for b in bs:
        op(a, b, D)
    return a


def keep(a, b):
    return op(a, b, I)


def copy(b):
    return tbm.copy(b)


def rrect(x0, y0, x1, y1, r, z0, z1):
    b = box(x0 + r, x1 - r, y0, y1, z0, z1)
    union(b, box(x0, x1, y0 + r, y1 - r, z0, z1))
    for cx in (x0 + r, x1 - r):
        for cy in (y0 + r, y1 - r):
            union(b, zcyl(cx, cy, z0, z1, r))
    return b


def annulus(x, y, z0, z1, ro, ri):
    return cut(zcyl(x, y, z0, z1, ro), zcyl(x, y, z0 - 1, z1 + 1, ri))


def pin(c, axis, length=12.0, r=PIN_R):
    a, h = norm(axis), length / 2
    return cyl(tuple(c[i] - a[i] * h for i in range(3)), tuple(c[i] + a[i] * h for i in range(3)), r)


def half(origin, n, size=3000.0):
    """Half-space where (p - origin) . n >= 0."""
    n = norm(n)
    w = norm((n[1], -n[0], 0.0)) if abs(n[2]) < 0.9 else (1.0, 0.0, 0.0)
    c = tuple(origin[i] + n[i] * size / 2 for i in range(3))
    return obox(c, (size, size, size), n, w)


def solid(b) -> bool:
    try:
        return b is not None and b.volume > 1e-6
    except Exception:
        return b is not None and b.faces.count > 0


def grid(body, xs, ys, name):
    """Split by planes x in xs and y in ys (cuts are lists of edges incl. outer bounds)."""
    out = []
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            p = keep(copy(body), box(xs[i], xs[i + 1], ys[j], ys[j + 1], -2000, 2000))
            if solid(p):
                out.append((f"{name} {chr(65 + j)}{i + 1}", p))
    return out


def rot(body, angle_deg, axis, origin=(0, 0, 0)):
    m = adsk.core.Matrix3D.create()
    m.setToRotation(math.radians(angle_deg), adsk.core.Vector3D.create(*axis), P(*origin))
    tbm.transform(body, m)
    return body


# ------------------------------------------------------------------ parts
def plinth_tub():
    tub = rrect(BX0, BY0, BX1, BY1, 50, 0, TOP - PLATE)
    cut(tub, rrect(BX0 + 5, BY0 + 5, BX1 - 5, BY1 - 5, 45, 4, TOP))
    for s in (-1, 1):                                       # pads under the ring feet
        union(tub, box(s * 95 if s > 0 else -145, 145 if s > 0 else -95, -21, 21, 3, 8))
        for y in (-10, 10):                                 # M3 screws up into the feet
            cut(tub, zcyl(s * 120, y, -1, 9, CLEAR_R), zcyl(s * 120, y, -1, 3.5, HEAD_R))
    for x in (-150, 0, 150):                                # screws into the top plate's tabs
        cut(tub, cyl((x, BY0 - 1, 55), (x, BY0 + 6, 55), CLEAR_R), cyl((x, BY1 + 1, 55), (x, BY1 - 6, 55), CLEAR_R))
    for y in (-15, 135, 285):
        cut(tub, cyl((BX0 - 1, y, 55), (BX0 + 6, y, 55), CLEAR_R), cyl((BX1 + 1, y, 55), (BX1 - 6, y, 55), CLEAR_R))
    xs, ys = [BX0, -71.67, 71.67, BX1], [BY0, 51.67, 218.33, BY1]
    for x in xs[1:-1]:                                      # seam pins: walls and floor
        for y in (BY0 + 2.5, BY1 - 2.5):
            for z in (20, 45):
                cut(tub, pin((x, y, z), (1, 0, 0)))
        for y in (-40, 130, 300):
            cut(tub, pin((x, y, 2), (1, 0, 0)))
    for y in ys[1:-1]:
        for x in (BX0 + 2.5, BX1 - 2.5):
            for z in (20, 45):
                cut(tub, pin((x, y, z), (0, 1, 0)))
        for x in (-150, 0, 150):
            cut(tub, pin((x, y, 2), (0, 1, 0)))
    return grid(tub, xs, ys, "Tub"), "black"


def top_plate():
    z0 = TOP - PLATE
    plate = rrect(BX0, BY0, BX1, BY1, 50, z0, TOP)
    cut(plate, zcyl(0, 0, z0 - 1, TOP + 1, 76), zcyl(0, 0, TOP - 1.5, TOP + 1, 81))
    for s in (-1, 1):
        cut(plate, box(95 if s > 0 else -145, 145 if s > 0 else -95, -20.5, 20.5, z0 - 1, TOP + 1))
    for a in BOTTLE_ANGLES:
        r = math.radians(a)
        cut(plate, zcyl(75 * math.cos(r), DRUM_CY + 75 * math.sin(r), z0 - 1, TOP + 1, 5))
    for k in range(6):                                      # pins locating the drum
        r = math.radians(30 + 60 * k)
        cut(plate, zcyl(168 * math.cos(r), DRUM_CY + 168 * math.sin(r), TOP - 5, TOP + 1, PIN_R))
    tabs = []                                               # tabs hanging inside the tub walls
    for x in (-150, 0, 150):
        tabs += [(box(x - 6, x + 6, BY0 + 5.3, BY0 + 15.3, 49, z0 + 0.5), cyl((x, BY0 + 5.2, 55), (x, BY0 + 10.3, 55), INSERT_R)),
                 (box(x - 6, x + 6, BY1 - 15.3, BY1 - 5.3, 49, z0 + 0.5), cyl((x, BY1 - 5.2, 55), (x, BY1 - 10.3, 55), INSERT_R))]
    for y in (-15, 135, 285):
        tabs += [(box(BX0 + 5.3, BX0 + 15.3, y - 6, y + 6, 49, z0 + 0.5), cyl((BX0 + 5.2, y, 55), (BX0 + 10.3, y, 55), INSERT_R)),
                 (box(BX1 - 15.3, BX1 - 5.3, y - 6, y + 6, 49, z0 + 0.5), cyl((BX1 - 5.2, y, 55), (BX1 - 10.3, y, 55), INSERT_R))]
    for t, hole in tabs:
        union(plate, t)
        cut(plate, hole)
    xs, ys = [BX0, -50, 50, BX1], [BY0, 80, 270, BY1]
    zc = z0 + PLATE / 2
    for x in (-50, 50):
        for y in (-95, 110, 180, 330):
            cut(plate, pin((x, y, zc), (1, 0, 0)))
    for y in (80, 270):
        for x in (-170, -100, 0, 100, 170):
            cut(plate, pin((x, y, zc), (0, 1, 0)))
    pieces = grid(plate, xs, ys, "Top plate")
    for _, p in pieces:                                     # walnut face down on the bed
        rot(p, 180, (1, 0, 0))
    return pieces, "walnut"


def gate_ring():
    """Built in the ring's own flat frame: x = world x, y = world z - 209, z = -world y."""
    ring = annulus(0, 0, -RING_T / 2, RING_T / 2, RING_RO, RING_RI)
    keep(ring, box(-300, 300, (TOP - 12) - RING_CZ, 300, -100, 100))          # trimmed just under the plate
    foot_y0 = 8 - RING_CZ
    for s in (-1, 1):
        union(ring, box(97 if s > 0 else -143, 143 if s > 0 else -97, foot_y0, TOP - RING_CZ + 2, -19, 19))
        for z in (-10, 10):                                  # inserts for the floor screws
            cut(ring, cyl((s * 120, foot_y0 - 0.1, z), (s * 120, foot_y0 + 5, z), INSERT_R))
    cut(ring, annulus(0, 0, -6, 6, RING_RI + 4, RING_RI - 1))                    # LED strip channel
    # feature 2: cable channel up the back (z = -20 face) from the left foot to the display
    groove = annulus(0, 0, -21, -12, 192, 178)
    upper = keep(copy(groove), box(-300, 0, 0, 300, -50, 50))
    lower = keep(keep(copy(groove), box(-300, 0, -300, 0, -50, 50)),
                 half((0, 0, 0), (math.sin(math.radians(229)), -math.cos(math.radians(229)), 0)))
    cut(ring, upper, lower, box(-126, -112, foot_y0 - 1, -138, -21, -11))
    cut(ring, box(-12, 12, 176, 190, -30, 30))                                 # cable through to the display
    for x, y in ((40, 186), (55, 172)):                                        # display screws from behind
        cut(ring, cyl((x, y, -21), (x, y, 21), CLEAR_R), cyl((x, y, -21), (x, y, -17), HEAD_R))
    for z in (-4, 10):                                                         # seam pins
        cut(ring, pin((0, 195, z), (1, 0, 0)))
        for x in (-185, 185):
            cut(ring, pin((x, 0, z), (0, 1, 0)))
    pieces = grid(ring, [-300, 0, 300], [-300, 0, 300], "Gate ring")
    return pieces, "black"


def display_bezel():
    bezel = zcyl(0, 0, 0, 16, 78)
    cut(bezel, zcyl(0, 0, 6, 17, 66.75))                     # pocket for the 5 in round display
    cut(bezel, box(-12, 12, -10, 4, -1, 7))                  # cable from the ring
    for x, y in ((40, 0), (55, -14)):
        cut(bezel, zcyl(x, y, -0.1, 5, INSERT_R))
    trim = annulus(0, 0, 0, 2, 78, 70)
    return [("Display bezel", bezel)], [("Display trim ring", trim)]


def drum():
    shell = annulus(0, DRUM_CY, TOP, DRUM_TOP, DRUM_R, DRUM_R - WALL)
    a = math.radians(205)                                    # level window slot
    rc = DRUM_R - WALL / 2
    cut(shell, obox((rc * math.cos(a), DRUM_CY + rc * math.sin(a), 249), (12, 20, 232),
                    (math.cos(a), math.sin(a), 0), (-math.sin(a), math.cos(a), 0)))
    front = DRUM_CY - DRUM_R                                 # nozzle arm: screws and the tube slot
    for x in (-5, 5):
        cut(shell, cyl((x, front - 2, 266), (x, front + 6, 266), CLEAR_R))
    cut(shell, box(-8, 8, front - 2, front + 6, 281, 293))
    for k in range(6):                                       # vertical seams every 60 deg
        t = math.radians(60 * k)
        for z in (110, 190, 280, 360):
            cut(shell, pin((rc * math.cos(t), DRUM_CY + rc * math.sin(t), z), (-math.sin(t), math.cos(t), 0)))
        t2 = math.radians(30 + 60 * k)                       # tier seam and plate pins
        for z in (234.5, TOP):
            cut(shell, pin((rc * math.cos(t2), DRUM_CY + rc * math.sin(t2), z), (0, 0, 1)))
    pieces = []
    for tier, (z0, z1) in enumerate(((TOP, 234.5), (234.5, DRUM_TOP))):
        for k in range(6):
            a0, a1 = math.radians(60 * k), math.radians(60 * k + 60)
            p = keep(copy(shell), box(-300, 300, -300, 600, z0, z1))
            keep(p, half((0, DRUM_CY, 0), (-math.sin(a0), math.cos(a0), 0)))
            keep(p, half((0, DRUM_CY, 0), (math.sin(a1), -math.cos(a1), 0)))
            if solid(p):
                pieces.append((f"Drum {'lower' if tier == 0 else 'upper'} {k + 1}", p))
    return pieces, "black"


def drum_lid():
    lid = zcyl(0, DRUM_CY, DRUM_TOP, DRUM_TOP + 12, DRUM_R + 2)
    union(lid, annulus(0, DRUM_CY, DRUM_TOP - 6, DRUM_TOP + 0.5, DRUM_R - WALL - 0.5, DRUM_R - WALL - 4.5))
    for d in (60, 130):
        for s in (-1, 1):
            cut(lid, pin((0, DRUM_CY + s * d, DRUM_TOP + 6), (1, 0, 0)), pin((s * d, DRUM_CY, DRUM_TOP + 6), (0, 1, 0)))
    pieces = grid(lid, [-300, 0, 300], [DRUM_CY - 300, DRUM_CY, DRUM_CY + 300], "Drum lid")
    for _, p in pieces:
        rot(p, 180, (1, 0, 0))                              # top face down
    return pieces, "walnut"


def nozzle_arm():
    front = DRUM_CY - DRUM_R
    arm = box(-10, 10, 0, front, 252, 280)
    union(arm, zcyl(0, 0, 252, 280, 18))
    for k in range(8):                                       # the 8 stainless tips
        a = math.radians(22.5 + 45 * k)
        cut(arm, zcyl(7.5 * math.cos(a), 7.5 * math.sin(a), 250, 282, 1.6))
    cut(arm, box(-7, 7, 2, front + 1, 274, 281))             # tube groove on top
    for x in (-5, 5):
        cut(arm, cyl((x, front + 0.1, 266), (x, front - 5, 266), INSERT_R))
    return [("Nozzle arm and hub", arm)], "black"


def drip_tray():
    z0 = TOP - 12
    tray = annulus(0, 0, z0, z0 + 10.5, 75, 53)
    cut(tray, annulus(0, 0, z0 + 2, z0 + 11, 73, 55))
    grille = annulus(0, 0, TOP - 1.5, TOP, 80.5, 53)
    for k in range(24):
        a = math.radians(7.5 + 15 * k)
        cut(grille, obox((64.5 * math.cos(a), 64.5 * math.sin(a), TOP - 0.75), (15, 2.4, 3),
                         (math.cos(a), math.sin(a), 0), (-math.sin(a), math.cos(a), 0)))
    return [("Drip tray", tray), ("Drip tray grille", grille)], "black"


def bottle_pad():
    return [("Bottle pad (print 8)", zcyl(0, 0, 0, 4, 42))], "walnut"


def electronics_tray():
    """Feature 7: raised 6 mm off the floor on feet, with a lip, Pi 5 standoffs and zip-tie slots."""
    t = box(-80, 80, -55, 55, 6, 9)
    cut(t, box(-78, 78, -53, 53, 9, 30))
    union(t, cut(box(-80, 80, -55, 55, 6, 14), box(-78, 78, -53, 53, 5, 20)))   # 2 mm lip, 8 mm tall
    for x in (-74, 74):
        for y in (-49, 49):
            union(t, box(x - 5, x + 5, y - 5, y + 5, 0, 6))  # feet
    ox, oy = -72, -28                                        # Pi 5: 85 x 56, M2.5 holes 58 x 49
    for dx in (3.5, 61.5):
        for dy in (3.5, 52.5):
            union(t, zcyl(ox + dx, oy + dy, 9, 15, 3))
            cut(t, zcyl(ox + dx, oy + dy, 5, 16, 1.25))
    for x0 in (28, 52):                                      # zip-tie slots for the two converters
        for y in (-35, -15, 15, 35):
            cut(t, box(x0, x0 + 4, y - 1, y + 1, 5, 10))
    for x in (-60, -20, 20, 60):                             # drain holes: spills fall through, not pool
        cut(t, zcyl(x, 0, 5, 10, 2.5))
    return [("Electronics tray", t)], "black"


def tube_clips():
    """Feature 2: holds the 8 tubes in a neat row up the drum wall (print 6, stick on with VHB)."""
    clip = box(0, 72, 0, 10, 0, 9)
    for k in range(8):
        x = 4.5 + k * 9
        cut(clip, cyl((x, -1, 5), (x, 11, 5), 2.65), box(x - 2.1, x + 2.1, -1, 11, 7, 10))
    return [("Tube clip (print 6)", clip)], "black"


# ------------------------------------------------------------------ output
def appearance(design, words):
    for i in range(app.materialLibraries.count):
        lib = app.materialLibraries.item(i)
        for j in range(lib.appearances.count):
            a = lib.appearances.item(j)
            if all(w in a.name.lower() for w in words):
                return design.appearances.itemByName(a.name) or design.appearances.addByCopy(a, a.name)
    return None


def layout(pieces):
    """Drop every piece onto z = 0 and lay them out in rows like a print plate."""
    x = y = row_h = 0.0
    for _, b in pieces:
        bb = b.boundingBox
        w, h = (bb.maxPoint.x - bb.minPoint.x) / MM, (bb.maxPoint.y - bb.minPoint.y) / MM
        if x + w > 700:
            x, y, row_h = 0.0, y + row_h + 15, 0.0
        m = adsk.core.Matrix3D.create()
        m.translation = adsk.core.Vector3D.create(x * MM - bb.minPoint.x, y * MM - bb.minPoint.y, -bb.minPoint.z)
        tbm.transform(b, m)
        x += w + 15
        row_h = max(row_h, h)


def save_part(folder, title, pieces, look, note=""):
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    root = des.rootComponent
    layout(pieces)
    worst = 0.0
    bf = root.features.baseFeatures.add()
    bf.startEdit()
    for name, b in pieces:
        bb = b.boundingBox
        dims = sorted([(bb.maxPoint.x - bb.minPoint.x) / MM, (bb.maxPoint.y - bb.minPoint.y) / MM,
                       (bb.maxPoint.z - bb.minPoint.z) / MM])
        worst = max(worst, dims[2])                           # every side must fit a 220 mm printer
        root.bRepBodies.add(b, bf)
    bf.finishEdit()
    ap = appearance(des, ("walnut",)) if look == "walnut" else appearance(des, ("plastic", "matte", "black"))
    os.makedirs(os.path.join(OUT, title), exist_ok=True)
    em = des.exportManager
    for i in range(root.bRepBodies.count):
        body = root.bRepBodies.item(i)
        body.name = pieces[i][0]
        if ap:
            body.appearance = ap
        opts = em.createSTLExportOptions(body, os.path.join(OUT, title, pieces[i][0] + ".stl"))
        opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementMedium
        em.execute(opts)
    app.activeViewport.fit()
    doc.saveAs(title, folder, note or f"{len(pieces)} pieces, {look}", "")
    doc.close(False)
    LOG.append(f"{title}: {len(pieces)} pcs, largest {worst:.0f} mm{' TOO BIG' if worst > BED else ''}")


def acrylic(folder):
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    root = des.rootComponent
    os.makedirs(os.path.join(OUT, "Acrylic (laser cut)"), exist_ok=True)

    def sk(name, draw):
        s = root.sketches.add(root.xYConstructionPlane)
        s.name = name
        draw(s)
        s.saveAsDXF(os.path.join(OUT, "Acrylic (laser cut)", name + ".dxf"))

    sk("Glass stage disc 100 mm", lambda s: s.sketchCurves.sketchCircles.addByCenterRadius(P(0, 0, 0), 50 * MM))
    sk("Gate strip 30 x 265 mm (cut 4)", lambda s: s.sketchCurves.sketchLines.addTwoPointRectangle(P(0, 120, 0), P(265, 150, 0)))
    sk("Level window 24 x 240 mm", lambda s: s.sketchCurves.sketchLines.addTwoPointRectangle(P(0, 200, 0), P(24, 440, 0)))
    doc.saveAs("Acrylic cut files", folder, "3 mm frosted acrylic: stage disc, 4 gate strips, level window", "")
    doc.close(False)
    LOG.append("Acrylic cut files: 3 DXF")


def main():
    src = app.activeDocument
    cad = src.dataFile.parentFolder
    if cad.name == "Halo Gate":
        cad = cad.parentFolder
    folder = cad.dataFolders.itemByName("Halo Gate Print Parts") or cad.dataFolders.add("Halo Gate Print Parts")
    have = {f.name for f in folder.dataFiles}
    jobs = [
        ("01 Plinth tub", plinth_tub), ("02 Top plate", top_plate), ("03 Gate ring", gate_ring),
        ("05 Bottle drum", drum), ("06 Drum lid", drum_lid), ("07 Nozzle arm and hub", nozzle_arm),
        ("08 Drip tray", drip_tray), ("09 Bottle pad", bottle_pad), ("10 Electronics tray", electronics_tray),
        ("11 Tube clip", tube_clips),
    ]
    for title, fn in jobs:
        if title in have:
            LOG.append(f"{title}: already there")
            continue
        try:
            pieces, look = fn()
            save_part(folder, title, pieces, look)
        except Exception:
            LOG.append(f"FAIL {title}: {traceback.format_exc(limit=3).splitlines()[-1]}")
    for title, which in (("04 Display bezel", 0), ("04b Display trim ring", 1)):
        if title in have:
            continue
        try:
            save_part(folder, title, display_bezel()[which], "black" if which == 0 else "walnut")
        except Exception:
            LOG.append(f"FAIL {title}: {traceback.format_exc(limit=3).splitlines()[-1]}")
    if "Acrylic cut files" not in have:
        try:
            acrylic(folder)
        except Exception:
            LOG.append(f"FAIL acrylic: {traceback.format_exc(limit=3).splitlines()[-1]}")
    src.activate()
    print(" | ".join(LOG) + f" | files in {OUT}")


main()
