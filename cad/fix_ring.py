"""Bring the Halo Gate assembly in line with the print parts: the gate ring stops just under
the top plate (it used to run under the stage, through the load cell) and stands on two feet,
and the 4 M3 screws and heat-set inserts move to where they now go: up through the plinth
floor into the feet. Run in Fusion's Text Commands (Py); it opens what it needs.
"""
import math

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()
tbm = adsk.fusion.TemporaryBRepManager.get()
MM = 0.1
RING_CZ, TOP = 209.0, 69.0
out = []


def P(x, y, z):
    return adsk.core.Point3D.create(x * MM, y * MM, z * MM)


def box(x0, x1, y0, y1, z0, z1):
    obb = adsk.core.OrientedBoundingBox3D.create(P((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2),
                                                 adsk.core.Vector3D.create(1, 0, 0), adsk.core.Vector3D.create(0, 1, 0),
                                                 (x1 - x0) * MM, (y1 - y0) * MM, (z1 - z0) * MM)
    return tbm.createBox(obb)


def find(f, name):
    if f.name == name:
        return f
    for i in range(f.dataFolders.count):
        hit = find(f.dataFolders.item(i), name)
        if hit:
            return hit


folder = find(app.data.activeProject.rootFolder, "Halo Gate")
files = {f.name: f for f in folder.dataFiles}

# 1. the ring part file
doc = app.documents.open(files["Gate ring"])
des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
root = des.rootComponent
old = max((root.bRepBodies.item(i) for i in range(root.bRepBodies.count)), key=lambda b: b.volume)
look, mat = old.appearance, old.material
new = tbm.copy(old)
tbm.booleanOperation(new, box(-300, 300, (TOP - 12) - RING_CZ, 300, -100, 100),
                     adsk.fusion.BooleanTypes.IntersectionBooleanType)
for s in (-1, 1):
    x0, x1 = (97, 143) if s > 0 else (-143, -97)
    tbm.booleanOperation(new, box(x0, x1, 8 - RING_CZ, TOP - RING_CZ + 2, -19, 19),
                         adsk.fusion.BooleanTypes.UnionBooleanType)
root.features.removeFeatures.add(old)
bf = root.features.baseFeatures.add()
bf.startEdit()
body = root.bRepBodies.add(new, bf)
bf.finishEdit()
body = root.bRepBodies.item(root.bRepBodies.count - 1)
body.name = "Gate ring"
body.appearance, body.material = look, mat
doc.save("Ring trimmed above the load cell; stands on two feet screwed to the plinth floor")
doc.close(False)
out.append("ring fixed")

# 2. the linked assembly: pick up the new ring, move the screws and inserts to the feet
asm = app.documents.open(files["Halo Gate Assembly"])
try:
    asm.updateAllReferences()
except Exception:
    pass
ades = adsk.fusion.Design.cast(asm.products.itemByProductType("DesignProductType"))
aroot = ades.rootComponent
spots = [(-120, -10), (120, -10), (-120, 10), (120, 10)]


def place(prefix, z_bottom, flip):
    occs = [o for o in aroot.occurrences if o.component.name.startswith(prefix)]
    for o, (x, y) in zip(occs, spots):
        m = adsk.core.Matrix3D.create()
        if flip:
            m.setToRotation(math.pi, adsk.core.Vector3D.create(1, 0, 0), P(0, 0, 0))
        m.translation = adsk.core.Vector3D.create(x * MM, y * MM, 0)
        o.transform2 = m
        lo = o.boundingBox.minPoint.z
        m.translation = adsk.core.Vector3D.create(x * MM, y * MM, z_bottom * MM - lo)
        o.transform2 = m
    return len(occs)


n_screws = place("91290A115", 0.5, True)       # heads in the floor counterbores, shanks up into the feet
n_inserts = place("94180A331", 8.0, False)     # in the bottom of each foot
if ades.snapshots.hasPendingSnapshot:
    ades.snapshots.add()
asm.save("Ring on two feet; floor screws and inserts moved to the feet")
out.append(f"assembly updated, {n_screws} screws, {n_inserts} inserts moved")
print(" | ".join(out))
