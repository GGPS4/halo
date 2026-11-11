"""Turn CAD › Halo Gate Components into a "Halo Gate" folder holding every part file plus an
assembly built from them. Run in Fusion's Text Commands (Py) with Halo Gate Assembly open.

Each part in the new assembly is a linked reference to its own file in the folder, placed
exactly where it sits in Halo Gate Assembly, so editing a part file updates the assembly.
"""
import adsk.core
import adsk.fusion

app = adsk.core.Application.get()
src = app.activeDocument
if not src.name.startswith("Halo Gate Assembly"):
    raise RuntimeError("Open Halo Gate Assembly first")
sdes = adsk.fusion.Design.cast(src.products.itemByProductType("DesignProductType"))
cad = src.dataFile.parentFolder
folder = cad.dataFolders.itemByName("Halo Gate") or cad.dataFolders.itemByName("Halo Gate Components")
note = ""
if folder.name != "Halo Gate":
    try:
        folder.name = "Halo Gate"
    except Exception as e:  # noqa: BLE001
        note = f"rename failed: {e}"
files = {f.name: f for f in folder.dataFiles}
places = [(o.component.name, o.transform2) for o in sdes.rootComponent.occurrences]

doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
des = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
root = des.rootComponent
placed, missing = 0, []
for name, m in places:
    df = files.get(name)
    if df is None:
        missing.append(name)
        continue
    root.occurrences.addByInsert(df, m, True)
    placed += 1
if des.snapshots.hasPendingSnapshot:
    des.snapshots.add()
app.activeViewport.fit()
doc.saveAs("Halo Gate Assembly", folder, "Assembly built from the linked part files in this folder", "")
print(f"folder={folder.name} parts={len(files)} placed={placed} missing={missing} {note}")
