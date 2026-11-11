"""Save every unique top-level component of the open assembly as its own Fusion design,
in a "Halo Gate Components" folder beside the assembly. Run in Fusion's Text Commands (Py)."""
import traceback

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()
doc = app.activeDocument
design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
root = design.rootComponent
FOLDER = "Halo Gate Components"
OUT = []

parent = doc.dataFile.parentFolder
folder = parent.dataFolders.itemByName(FOLDER) or parent.dataFolders.add(FOLDER)
existing = {f.name for f in folder.dataFiles}


def copy_bodies(comp, name):
    """Fallback for linked parts: copy the solid bodies into a fresh design and save it."""
    tbm = adsk.fusion.TemporaryBRepManager.get()
    bodies = []
    for occ_body in comp.bRepBodies:
        bodies.append(occ_body)
    for o in comp.allOccurrences:
        for b in o.bRepBodies:
            bodies.append(b)
    nd = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    ndes = adsk.fusion.Design.cast(nd.products.itemByProductType("DesignProductType"))
    nroot = ndes.rootComponent
    bf = nroot.features.baseFeatures.add()
    bf.startEdit()
    for b in bodies:
        nb = nroot.bRepBodies.add(tbm.copy(b), bf)
        nb.name = b.name
        try:
            nb.material = ndes.materials.itemByName(b.material.name) or ndes.materials.addByCopy(b.material, b.material.name)
            nb.appearance = ndes.appearances.itemByName(b.appearance.name) or ndes.appearances.addByCopy(b.appearance, b.appearance.name)
        except Exception:
            pass
    bf.finishEdit()
    nd.saveAs(name, folder, "Halo Gate part", "")
    nd.close(False)
    doc.activate()


seen = set()
for occ in root.occurrences:
    comp = occ.component
    if comp.id in seen:
        continue
    seen.add(comp.id)
    name = comp.name
    if name in existing:
        OUT.append("skip " + name)
        continue
    try:
        comp.saveCopyAs(name, folder, "Halo Gate part", "")
        OUT.append("ok   " + name)
    except Exception:
        try:
            copy_bodies(comp, name)
            OUT.append("copy " + name)
        except Exception:
            OUT.append("FAIL " + name + ": " + traceback.format_exc(limit=1).splitlines()[-1])

print(f"{len(seen)} parts -> {FOLDER}: " + " | ".join(OUT))
