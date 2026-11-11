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
- **Phone setup page and API.** `http://halo.local:8080` for bottles and drinks;
  `POST /api/pour` so a voice assistant can order a drink.

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
