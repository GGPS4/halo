# Halo

An AI cocktail maker that pours by weight. Eight bottles stand in a ring, the glass sits in
the middle on a load cell, and a light ring above it fills up as the drink pours. Claude
invents drinks from whatever bottles are loaded.

- **Pours by weight, not by timer.** Each pump stops when the scale sees its target grams,
  so pours stay accurate as tubing wears. No seconds-per-ounce calibration.
- **Knows when something's wrong.** Won't pour without a glass, pauses if you lift the
  glass, stops and tells you which bottle ran dry.
- **The light is the progress bar.** The LED ring under the glass and the ring on the
  round touchscreen fill together.
- **Claude bartender.** Tap a mood (sour, strong, low-ABV, surprise me) or type a request
  on your phone. Claude only uses the bottles that are loaded.
- **Strength control.** Light, normal or strong changes the spirit-to-mixer ratio without
  changing the size of the drink.
- **Bottle levels.** Every pour is weighed and subtracted from the bottle, so Halo warns you
  before a bottle runs dry and keeps a pour history.
- **Party mode.** Give a theme and Claude writes a short menu from your shelf; the touchscreen
  then shows only those drinks. You can also pick the menu yourself.
- **Pour colors.** The LED ring and the on-screen ring split into each ingredient's share in
  its own color and fill as the drink pours.
- **Soft stop.** Pumps slow down over the last few grams so each ingredient stops on target.
- **Ambient mode.** When idle, the ring and the screen drift through your bottles' colors.
- **Levi voice orders.** `POST /api/voice` with what Levi heard; Halo picks or invents the drink.
- **Drip tray.** A removable tray with a slotted grille surrounds the glass stage.

## The Halo app

Open `http://halo.local:8080` on your phone or laptop (add it to your home screen for an
app icon). From there you can:

- set what's in each of the 8 slots on a top-down map of the machine, mark bottles refilled,
  and swap slots
- browse, search, favorite, add and edit drinks, with a live preview of the ratio and ABV
- pour any drink at single or double size and light, normal or strong
- ask the bartender for a new drink and save the ones you like
- start and end party menus
- see stats: most-poured drinks, usage by bottle, recent pours
- change ring brightness, ambient mode, drink sizes, low-bottle warnings and voice orders
- download and import your drink library

The full build (parts, wiring, printing, assembly) is in the Halo build guide. The design
spec is in [`docs/design.md`](docs/design.md) and the driver board in [`pcb/`](pcb/).

## Hardware

Raspberry Pi 5 · Halo Driver Board (in `pcb/`) · 8 × 12 V peristaltic pumps · 1 kg load
cell + HX711 · 24-LED WS2812B ring · 5" round 1080×1080 HDMI touchscreen · 12 V 6 A supply
· 12 V→5 V 5 A USB-C buck converter.

| Function | BCM GPIO |
|---|---|
| Pumps 1–8 | 27, 22, 23, 24, 25, 5, 6, 13 |
| HX711 DT / SCK | 4 / 17 |
| LED ring data (SPI0 MOSI) | 10 |

## Install (Raspberry Pi OS Bookworm, 64-bit)

```bash
git clone https://github.com/GGPS4/halo.git ~/halo
cd ~/halo
./scripts/install.sh          # packages, SPI on, venv, autostart service
nano .env                     # ANTHROPIC_API_KEY=sk-ant-...
python -m halo.calibrate scale
python -m halo.calibrate prime
sudo systemctl start halo
```

Then open `http://halo.local:8080` on your phone, name the 8 bottles, and pour.

## Develop without the hardware

```bash
pip install -r requirements-dev.txt
HALO_MOCK=1 python -m halo --windowed     # UI in a window, fake pumps and scale
HALO_MOCK=1 python -m pytest
```

## Config

Copy `config.example.toml` to `config.toml` to change pins, pour sizes, the Claude model,
or the LED brightness. `bottles.json` and `recipes.json` are written by the app.

## Ordering a drink from another device (Levi)

```bash
# what Levi heard, Halo does the rest
curl -X POST http://halo.local:8080/api/voice \
     -H 'content-type: application/json' \
     -d '{"text": "a strong double margarita"}'
# light the ring while Levi is listening
curl -X POST http://halo.local:8080/api/ring -d '{"mode": "listening"}' -H 'content-type: application/json'
# or order an exact drink
curl -X POST http://halo.local:8080/api/pour \
     -H 'content-type: application/json' \
     -d '{"drink": "Garden Highball", "size": "single"}'
# or let Claude invent one:
     -d '{"request": "something smoky and not too sweet"}'
```

`GET /api/status` returns the pour state and progress.

## Safety

Food-grade silicone tubing only; flush with `python -m halo.calibrate clean` after each
session. Keep the 12 V brick and electronics in the base, away from the drip tray. Drink
responsibly.

## License

MIT — see [LICENSE](LICENSE).
