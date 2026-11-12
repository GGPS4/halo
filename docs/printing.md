# Printing Halo

Every printed part of the Halo Gate is split into pieces that fit a 220 × 220 mm bed (no
piece is longer than 201 mm), and each piece is already laid flat in the way it should print.
The pieces are in Fusion under Halo project › CAD › Halo Gate Print Parts, one design per part,
and as STL files (one per piece) plus DXF cut files for the acrylic. `cad/print_parts.py`
makes all of it.

## What to print

| # | Part | Pieces | Filament | Notes |
|---|---|---|---|---|
| 01 | Plinth tub | 9 (3 × 3 grid) | Matte black PETG | Open side up. Pads under the ring feet are part of the floor. |
| 02 | Top plate | 9 (3 × 3 grid) | Walnut PLA | Printed walnut face down, so the visible face is the smooth bed side. |
| 03 | Gate ring | 4 | Matte black PETG | Flat. The two lower pieces carry the feet. |
| 04 | Display bezel | 1 | Matte black PETG | Pocket side up. |
| 04b | Display trim ring | 1 | Walnut PLA | |
| 05 | Bottle drum | 12 (6 arcs × 2 tiers) | Matte black PETG | Standing upright. The slot in one arc is the level window. |
| 06 | Drum lid | 4 | Walnut PLA | Top face down. |
| 07 | Nozzle arm and hub | 1 | Matte black PETG | Groove for the tubes on top. |
| 08 | Drip tray + grille | 2 | Matte black PETG | |
| 09 | Bottle pad | 1, print 8 | Walnut PLA | |
| 10 | Electronics tray | 1 | Matte black PETG | Holds the Pi 5 off the floor, with a lip so a spill can't reach it. |
| 11 | Tube clip | 1, print 6 | Matte black PETG | Snaps 8 tubes along the drum wall. |

Filament: about 3 kg of black PETG and 2 kg of walnut PLA.

Settings that work: 0.2 mm layers, 4 walls, 20 % gyroid infill (40 % for the gate ring and
the electronics tray feet), no supports except under the tub's side screw holes (paint-on
or "touching build plate" is enough). PETG at about 240 °C, walnut PLA at about 210 °C.

**Laser cut** (3 mm frosted acrylic, DXF files in `Acrylic (laser cut)`): the glass stage
disc (Ø100 mm), 4 gate light strips (30 × 265 mm), and the level window (24 × 240 mm). A local
makerspace or an online cutter (SendCutSend, Ponoko) can cut these.

## Joining pieces

- **Seams** have 1.9 mm holes facing each other. Cut 12 mm lengths of 1.75 mm filament as
  pins, dry-fit the seam, then glue: CA glue for PLA, 5-minute epoxy for PETG (CA holds PETG
  poorly). Clamp or tape the seam flat on a table while it sets.
- **Heat-set inserts** (M3, McMaster 94180A331) go into the 4.1 mm holes with a soldering
  iron at about 220 °C: 4 in the gate ring feet, 12 in the top plate tabs, 2 in the display
  bezel and 2 at the end of the nozzle arm.
- **Screws** are M3 × 10 button heads (McMaster 91290A115):
  - 4 go up through the plinth floor into the ring feet, in counterbores so the base sits flat.
  - 12 go through the tub walls into the top plate's tabs.
  - 2 go through the back of the gate ring into the bezel.
  - 2 hold the nozzle arm to the drum.

## Order of assembly

1. Glue up the tub, the top plate, the ring, the drum tiers and the lid, each on its own.
2. Fit the electronics tray, the Pi and the driver board in the tub, with the scale stage
   mounted on the load cell.
3. Stand the gate ring in the tub: its feet sit on the floor pads, and the 4 floor screws
   pull it down. Run the LED strip in the channel on the inner edge, and the display cable
   in the channel on the back to the hole at the top.
4. Lower the top plate over the ring feet (the slots fit around them) and screw it in
   through the tub walls.
5. Pin the drum to the plate, route the tubes through the clips and up into the arm, and
   screw on the arm. Put on the lid.

The drip tray and the bottle pads sit in place by gravity; nothing holds them.

## Before you print

- **Measure the display.** The bezel pocket is Ø133.5 mm for the 5" round screen on the parts
  list. If yours is a different size, change `display_bezel()` in `cad/print_parts.py` and
  run the script again (it skips parts that already exist, so delete the old "04 Display
  bezel" design first).
- Print one seam of the tub as a test to check that the pins and holes suit your printer.
