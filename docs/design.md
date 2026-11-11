# Halo — design spec

Halo is an AI cocktail maker built around one idea: the glass sits in the middle of a
ring of bottles, under a floating light ring that pours the drink and shows its progress.

## Form

```
                 ┌──── halo ring (Ø190, 8 nozzles converge to a center hub)
                 │        ┌── round touchscreen on the spine head, tilted 20° forward
        ╭────────┴───────╮│
        │   ○  ○  ○      ││  ← spine (hollow, carries the 8 output tubes)
   ▐█▌  ╰──────┬─────────╯│      ▐█▌  ▐█▌  ▐█▌    bottles (8) on a Ø280 pitch circle
   ▐█▌         ▼          ▐█▌    ▐█▌  ▐█▌  ▐█▌
 ══▐█▌══════[ glass ]═════▐█▌════════════════════  base Ø380 × 70
            ═LED halo═
```

| Element | Size | Material / finish |
|---|---|---|
| Base | Ø380 × 70 mm, 6 mm fillet top edge | PETG/ASA print in 4 sectors, matte black |
| Bottle ring | 9 slots at 40° on Ø280; front slot left open as the glass doorway, 8 bottles | Bottle pads: walnut-PLA discs Ø92 × 6 |
| Glass stage | Ø100 × 6 frosted acrylic disc on a 1 kg load cell | Lit from below by a 24-LED ring |
| Spine | 36 × 40 mm section behind the stage (y 55–95), base top to 430 mm, R10 corners | Matte black |
| Halo ring | Ø190 OD / Ø166 ID × 14 mm at 260 mm, 3 spokes to the hub | Black ring with a frosted inner light strip |
| Nozzle hub | Ø36 hub, 8 stainless 3 mm tubes converging to a single point | Stainless + black |
| Display | 5" round 1080×1080 touch panel in a Ø150 bezel, center ~455 mm up, tilted back 20° | Black bezel |

## What is new compared with timed-pump cocktail makers

- **Pours by weight.** A load cell under the glass measures what actually went in. Each
  pump runs until its target grams are reached, with a stall detector (no weight change
  for 2 s means an empty bottle). Timed pours drift as tubing wears; weight does not.
- **Glass detection.** Halo won't pour unless the scale sees a glass, and it pauses if the
  glass is lifted mid-pour.
- **The halo is the progress bar.** The LED ring under the glass and the on-screen ring
  fill together as the drink pours.
- **Claude as the bartender.** It composes drinks from the bottles that are actually
  loaded, from a mood ("smoky", "low ABV", "surprise me"), and explains each one.
- **Levi hook.** `POST /api/voice` takes what Levi heard; `POST /api/ring` lights the ring while it listens.
- **Strength, bottle levels, party menus, pour colors, soft stop, ambient mode** (see README).
- **Drip tray.** Ø150 well in the base top around the stage; a removable black tray (Ø150/Ø106,
  10.5 mm deep) sits on a ledge with 4 × 6×3 mm magnets and has a 24-slot grille flush with the top.

## Electronics

- Raspberry Pi 5 (4 GB) running Raspberry Pi OS Bookworm 64-bit
- Halo Driver Board (custom, 100 × 70 mm 2-layer, stacks on the Pi like a HAT and
  overhangs past the SD-card edge)
  - 8 low-side N-MOSFET pump channels (AO3400A, flyback diode B5819W, 100 Ω gate,
    10 kΩ pull-down) to JST-XH 2-pin pump connectors
  - 12 V screw-terminal input, 470 µF bulk capacitor
  - 74AHCT1G125 3.3 V → 5 V buffer for the LED data line, JST-XH 3-pin LED connector
  - 4-pin header for an HX711 load-cell amplifier module
- 12 V 6 A brick for pumps; 12 V → 5 V 5 A USB-C buck module powers the Pi
- 8 × 12 V peristaltic pumps, food-grade silicone tubing

## GPIO map (BCM)

| Function | GPIO | Header pin |
|---|---|---|
| Pump 1–8 | 27, 22, 23, 24, 25, 5, 6, 13 | 13, 15, 16, 18, 22, 29, 31, 33 |
| HX711 DT / SCK | 4 / 17 | 29 / 31 |
| LED data (SPI0 MOSI) | 10 | 19 |
| 5 V / 3.3 V / GND | — | 2,4 / 1 / 6,9,14,20,25,30,34,39 |

The LED ring is driven over SPI because the PWM-based WS2812 libraries don't run on the
Pi 5's RP1 chip.

The Fusion model (`cad/build_halo.py`) is the source of truth for these dimensions. Run it in
Fusion to rebuild the full assembly; standard hardware (M3 screws 91290A115, heat-set inserts
94180A331) comes from McMaster-Carr.
