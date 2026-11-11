"""Draw docs/img/wiring.svg (and .png with rsvg-convert): every connection in Halo.

    python scripts/wiring_diagram.py
"""
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "img"
W, H = 1800, 1210
BG, CARD, LINE, INK, MUTE = "#12241d", "#1b3329", "#2c4a3d", "#eaf2ee", "#9fb5ab"
V12, V5, GND, DATA, VIDEO = "#e5534b", "#f2a33a", "#8a9a93", "#5aa9e6", "#b48ead"

parts = []


def box(x, y, w, h, title, lines=(), accent=None):
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{CARD}" stroke="{accent or LINE}" stroke-width="2"/>')
    parts.append(f'<text x="{x + 18}" y="{y + 34}" font-size="22" font-weight="600" fill="{INK}">{title}</text>')
    for i, t in enumerate(lines):
        parts.append(f'<text x="{x + 18}" y="{y + 62 + i * 24}" font-size="16" fill="{MUTE}">{t}</text>')


def wire(points, color, label=None, at=None, dash=False, width=4):
    d = "M " + " L ".join(f"{x},{y}" for x, y in points)
    extra = ' stroke-dasharray="10 7"' if dash else ""
    parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"{extra}/>')
    if label:
        lx, ly = at or points[len(points) // 2]
        tw = 8.4 * len(label) + 16
        parts.append(f'<rect x="{lx - tw / 2}" y="{ly - 15}" width="{tw}" height="26" rx="13" fill="{BG}" stroke="{color}" stroke-width="1.5"/>')
        parts.append(f'<text x="{lx}" y="{ly + 4}" font-size="14" fill="{INK}" text-anchor="middle">{label}</text>')


def dot(x, y, color):
    parts.append(f'<circle cx="{x}" cy="{y}" r="6" fill="{color}"/>')


# ----------------------------------------------------------------- title and legend
parts.append(f'<text x="40" y="58" font-size="34" font-weight="600" fill="{INK}" font-family="DejaVu Serif, serif">Halo wiring</text>')
parts.append(f'<text x="40" y="88" font-size="17" fill="{MUTE}">Everything lives in the plinth except the display (on the gate ring), the gate strip (inside the ring) and the LED ring and load cell (under the stage).</text>')
for i, (c, t) in enumerate([(V12, "12 V"), (V5, "5 V (with its ground)"), (DATA, "Data"), (VIDEO, "HDMI / USB")]):
    x = 1090 + [0, 100, 330, 430][i]
    parts.append(f'<line x1="{x}" y1="52" x2="{x + 30}" y2="52" stroke="{c}" stroke-width="5" stroke-linecap="round"/>')
    parts.append(f'<text x="{x + 38}" y="58" font-size="16" fill="{INK}">{t}</text>')

# ----------------------------------------------------------------- power column
box(40, 140, 300, 110, "12 V 6 A power brick", ["5.5 x 2.1 mm barrel plug"], V12)
box(40, 300, 300, 110, "Barrel inlet", ["Back wall of the plinth", "screw-terminal adapter"], V12)
box(40, 470, 300, 130, "5 V converter (Pi)", ["12 V to 5 V 5 A, USB-C out", "Powers the Pi"], V5)
box(40, 660, 300, 130, "5 V converter (LEDs)", ["DROK 12 V to 5 V 3 A,", "wire leads", "Powers the gate strip"], V5)

wire([(190, 250), (190, 300)], V12)
# 12 V from the inlet to J2 and to both converters
wire([(340, 340), (430, 340), (430, 205), (560, 205)], V12, "+12 V and GND to J2", (450, 272))
wire([(190, 410), (190, 470)], V12)
wire([(340, 380), (390, 380), (390, 720), (340, 720)], V12)
dot(390, 380, V12)

# ----------------------------------------------------------------- Pi and board
box(560, 140, 460, 250, "Halo Driver Board", [
    "Stacks on the Pi through J1 (2 x 20 header)",
    "J2   12 V in (screw terminal)",
    "J3 to J10   pumps 1 to 8 (12 V, switched)",
    "J11   HX711 (G, CK, DT, V)",
    "J12   LED ring (+5V, D, G)",
    "GPIO 27 22 23 24 25 5 6 13 drive the pumps",
    "HX711 DT = GPIO 4, SCK = GPIO 17; LEDs = GPIO 10",
], DATA)
box(560, 470, 460, 200, "Raspberry Pi 5", [
    "Driver board plugged on top (J1)",
    "Active cooler fitted",
    "micro-HDMI 0 to the display",
    "USB-A to the display (touch and power)",
], V5)
wire([(340, 535), (560, 535)], V5, "USB-C 5 V", (450, 535))
wire([(790, 390), (790, 470)], DATA, "J1 stacked", (860, 430))

# display
box(1080, 470, 330, 200, "5 in round display", ["1080 x 1080 touch", "On top of the gate ring", "Cables run up between", "the ring and the drum"], VIDEO)
wire([(1020, 560), (1080, 560)], VIDEO)
wire([(1020, 620), (1080, 620)], VIDEO)

# ----------------------------------------------------------------- pumps
px, py = 1080, 140
box(px, py, 680, 250, "Pumps 1 to 8", ["Kamoer KPHM100, 12 V. Wire + to the pin marked +.",
                                       "If one runs backwards, swap its two wires."], V12)
for i in range(8):
    x = px + 24 + i * 82
    parts.append(f'<rect x="{x}" y="{py + 120}" width="70" height="96" rx="10" fill="{BG}" stroke="{LINE}" stroke-width="2"/>')
    parts.append(f'<text x="{x + 35}" y="{py + 160}" font-size="22" font-weight="600" fill="{INK}" text-anchor="middle">{i + 1}</text>')
    parts.append(f'<text x="{x + 35}" y="{py + 186}" font-size="13" fill="{MUTE}" text-anchor="middle">J{i + 3}</text>')
wire([(1020, 265), (1080, 265)], V12, None)
parts.append(f'<text x="1050" y="250" font-size="13" fill="{MUTE}" text-anchor="middle">x8</text>')

# ----------------------------------------------------------------- scale
box(560, 760, 300, 150, "HX711 amplifier", ["G - GND", "CK - SCK", "DT - DT", "V - VCC"], DATA)
box(560, 960, 300, 150, "1 kg load cell", ["Under the stage", "red E+, black E-", "white A-, green A+"], DATA)
wire([(700, 390), (700, 430), (530, 430), (530, 835), (560, 835)], DATA, "J11  G CK DT V", (530, 760))
wire([(710, 910), (710, 960)], DATA, "4 wires", (790, 935))

# ----------------------------------------------------------------- lights
box(1080, 760, 330, 150, "24-LED ring", ["Under the frosted stage", "DIN from J12, +5V, GND", "DOUT continues to the strip"], DATA)
box(1440, 760, 320, 150, "Gate strip, 52 LEDs", ["Inside the gate ring", "DIN from the ring's DOUT", "5 V and GND from the", "LED converter"], DATA)
wire([(1000, 390), (1000, 430), (1050, 430), (1050, 830), (1080, 830)], DATA, "J12  +5V D G", (1050, 720))
wire([(1410, 835), (1440, 835)], DATA)
parts.append(f'<text x="1425" y="748" font-size="13" fill="{MUTE}" text-anchor="middle">ring DOUT to strip DIN</text>')
wire([(340, 760), (380, 760), (380, 1140), (1600, 1140), (1600, 910)], V5, "5 V and GND to the gate strip", (1000, 1140))

parts.append(f'<text x="40" y="1190" font-size="15" fill="{MUTE}">All grounds are common: the board, Pi, both converters, the LED ring and the gate strip share GND. '
             f'The board has no reverse-polarity protection, so check + and - at J2 before powering up.</text>')

svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
       f'font-family="Inter, DejaVu Sans, sans-serif">'
       f'<rect width="{W}" height="{H}" fill="{BG}"/>' + "".join(parts) + "</svg>")
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "wiring.svg").write_text(svg)
subprocess.run(["rsvg-convert", "-w", str(W), "-o", str(OUT / "wiring.png"), str(OUT / "wiring.svg")], check=True)
print("wrote", OUT / "wiring.svg", OUT / "wiring.png")
