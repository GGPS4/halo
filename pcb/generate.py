"""Generates the Halo Driver Board: Gerbers, drill files, JLCPCB BOM/CPL, a KiCad 8
board file and preview images, all from the placement and routing below.

    python pcb/generate.py      ->  pcb/out/...

Coordinates are in mm, origin at the top-left corner, y pointing down (KiCad style).
Gerbers flip y so the origin is the bottom-left corner.
"""
from __future__ import annotations

import csv
import math
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

OUT = Path(__file__).parent / "out"
W, H = 100.0, 70.0
CORNER_R = 3.0
POUR_CLEAR = 0.3          # copper pour to anything of another net
EDGE_CLEAR = 0.5
MASK_EXP = 0.05
VIA_D, VIA_DRILL = 0.6, 0.3

# --------------------------------------------------------------------------- model
@dataclass
class Pad:
    ref: str
    num: str
    x: float
    y: float
    shape: str          # rect | circle
    w: float
    h: float
    net: str
    drill: float = 0.0  # 0 = SMD (top only)

    @property
    def smd(self):
        return self.drill == 0


@dataclass
class Track:
    x1: float
    y1: float
    x2: float
    y2: float
    w: float
    layer: str          # F | B
    net: str


@dataclass
class Via:
    x: float
    y: float
    net: str


@dataclass
class Part:
    ref: str
    value: str
    footprint: str
    x: float
    y: float
    rot: int
    lcsc: str = ""
    smt: bool = True     # assembled by JLCPCB
    pads: list = field(default_factory=list)


parts: list[Part] = []
tracks: list[Track] = []
vias: list[Via] = []
npth: list[tuple[float, float, float]] = []   # x, y, diameter
silk_lines: list[tuple] = []                   # x1,y1,x2,y2,w
silk_texts: list[tuple] = []                   # text,x,y,size,anchor

# --------------------------------------------------------------------------- footprints
# Each returns [(num, dx, dy, shape, w, h, drill)] at rotation 0 (KiCad library geometry).
def fp_r0603():
    return [("1", -0.825, 0, "rect", 0.8, 0.95, 0), ("2", 0.825, 0, "rect", 0.8, 0.95, 0)]


def fp_c0805():
    return [("1", -0.95, 0, "rect", 1.0, 1.45, 0), ("2", 0.95, 0, "rect", 1.0, 1.45, 0)]


def fp_sot23():
    return [("1", -1.1375, -0.95, "rect", 1.325, 0.6, 0), ("2", -1.1375, 0.95, "rect", 1.325, 0.6, 0),
            ("3", 1.1375, 0, "rect", 1.325, 0.6, 0)]


def fp_sot23_5():
    return [("1", -1.1375, -0.95, "rect", 1.325, 0.6, 0), ("2", -1.1375, 0, "rect", 1.325, 0.6, 0),
            ("3", -1.1375, 0.95, "rect", 1.325, 0.6, 0), ("4", 1.1375, 0.95, "rect", 1.325, 0.6, 0),
            ("5", 1.1375, -0.95, "rect", 1.325, 0.6, 0)]


def fp_sod123():
    return [("1", -1.635, 0, "rect", 0.91, 1.22, 0), ("2", 1.635, 0, "rect", 0.91, 1.22, 0)]


def fp_row(n, pitch, drill, pad, first_rect=True):
    return [(str(i + 1), i * pitch, 0, "rect" if (i == 0 and first_rect) else "circle", pad, pad, drill)
            for i in range(n)]


def rot(dx, dy, r):
    r %= 360
    if r == 0:
        return dx, dy
    if r == 90:
        return -dy, dx
    if r == 180:
        return -dx, -dy
    return dy, -dx


def place(ref, value, footprint, pads, x, y, r, nets: dict, lcsc="", smt=True):
    p = Part(ref, value, footprint, x, y, r, lcsc, smt)
    for num, dx, dy, shape, w, h, drill in pads:
        ox, oy = rot(dx, dy, r)
        if r % 180 == 90:
            w, h = h, w
        p.pads.append(Pad(ref, num, round(x + ox, 4), round(y + oy, 4), shape, w, h, nets[num], drill))
    parts.append(p)
    return p


def trace(net, pts, w=0.25, layer="F"):
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        tracks.append(Track(x1, y1, x2, y2, w, layer, net))


def via(net, x, y):
    vias.append(Via(x, y, net))


# --------------------------------------------------------------------------- placement
X0 = 35.0                         # Raspberry Pi footprint starts here (HAT-compatible)
for hx, hy in [(X0 + 3.5, 3.5), (X0 + 61.5, 3.5), (X0 + 3.5, 52.5), (X0 + 61.5, 52.5)]:
    npth.append((hx, hy, 2.75))

# J1: 2x20 header. Odd pins on the inner row (y 4.77), even pins on the edge row (y 2.23).
GPIO_HDR = {}
def hdr_xy(pin):
    k = (pin - 1) // 2
    return (round(X0 + 8.37 + k * 2.54, 3), 4.77 if pin % 2 else 2.23)

PIN_NET = {1: "+3V3", 2: "+5V",
           6: "GND", 9: "GND", 14: "GND", 20: "GND", 25: "GND", 30: "GND", 34: "GND", 39: "GND",
           7: "HX_DT", 11: "HX_SCK", 19: "LED_DIN"}
GATE_PINS = [13, 15, 16, 18, 22, 29, 31, 33]        # channel 1..8, left to right
GATE_GPIO = [27, 22, 23, 24, 25, 5, 6, 13]
for i, pin in enumerate(GATE_PINS):
    PIN_NET[pin] = f"GPIO_P{i + 1}"
j1 = Part("J1", "2x20 female header", "PinSocket_2x20_P2.54", hdr_xy(1)[0], 3.5, 0, smt=False)
for pin in range(1, 41):
    x, y = hdr_xy(pin)
    j1.pads.append(Pad("J1", str(pin), x, y, "rect" if pin == 1 else "circle", 1.7, 1.7,
                       PIN_NET.get(pin, f"NC_J1_{pin}"), 1.0))
parts.append(j1)

# J2: 12 V input screw terminal, bottom-left.
place("J2", "12V IN KF301-2P", "TerminalBlock_5.08_1x02", fp_row(2, 5.08, 1.3, 2.4), 4.5, 64.58, 270,
      {"1": "+12V", "2": "GND"}, smt=False)
# C1: bulk electrolytic 470 uF 25 V
place("C1", "470uF 25V", "CP_Radial_D8.0mm_P3.50mm", fp_row(2, 3.5, 1.0, 1.8), 6.0, 51.75, 270,
      {"1": "+12V", "2": "GND"}, smt=False)
trace("+12V", [(6.0, 51.75), (8.5, 54.25), (8.5, 62.6)], w=1.5, layer="B")

# Pump channels
YP = 65.5                                   # connector pin line
CX = [14.0 + 9.5 * i for i in range(8)]
trace("+12V", [(4.5, 63.0), (CX[-1] + 5.1, 63.0)], w=2.0, layer="B")   # 12 V bus
for i, cx in enumerate(CX):
    n = i + 1
    g, d, gpio = f"GATE{n}", f"PUMP{n}-", f"GPIO_P{n}"
    place(f"J{n + 2}", f"PUMP {n} JST-XH", "JST_XH_B2B-XH-A", fp_row(2, 2.5, 0.95, 1.7), cx - 1.25, YP, 0,
          {"1": "+12V", "2": d}, smt=False)
    place(f"Q{n}", "AO3400A", "SOT-23", fp_sot23(), cx + 1.5, YP - 8.5, 0,
          {"1": g, "2": "GND", "3": d}, lcsc="C20917")
    place(f"D{n}", "B5819W", "D_SOD-123", fp_sod123(), cx + 4.6, YP - 5.5, 270,
          {"1": "+12V", "2": d}, lcsc="C8598")
    place(f"R{n}", "100", "R_0603", fp_r0603(), cx + 0.36, YP - 12.0, 90,
          {"1": gpio, "2": g}, lcsc="C22775")
    place(f"R{n + 8}", "10k", "R_0603", fp_r0603(), cx - 1.9, YP - 9.45, 0,
          {"1": "GND", "2": g}, lcsc="C25804")
    # drain: connector pin 2 -> FET drain, tap to diode anode
    trace(d, [(cx + 1.25, YP), (cx + 2.64, YP - 1.4), (cx + 2.64, YP - 8.5)], w=0.8)
    trace(d, [(cx + 2.64, YP - 7.135), (cx + 4.6, YP - 7.135)], w=0.6)
    # diode cathode -> via -> 12 V bus
    trace("+12V", [(cx + 4.6, YP - 3.865), (cx + 4.6, YP - 2.5)], w=0.6)
    via("+12V", cx + 4.6, YP - 2.5)
    trace("+12V", [(cx - 1.25, YP - 2.5), (cx - 1.25, YP)], w=1.0, layer="B")
    # gate network
    trace(g, [(cx + 0.36, YP - 11.175), (cx + 0.36, YP - 9.45)], w=0.3)
    trace(g, [(cx - 1.075, YP - 9.45), (cx + 0.36, YP - 9.45)], w=0.3)
    # grounds to the bottom plane
    trace("GND", [(cx + 0.3625, YP - 7.55), (cx - 0.9, YP - 6.9)], w=0.5)
    via("GND", cx - 0.9, YP - 6.9)
    trace("GND", [(cx - 2.725, YP - 9.45), (cx - 2.725, YP - 7.9)], w=0.4)
    via("GND", cx - 2.725, YP - 7.9)
    silk_texts.append((str(n), cx, YP + 3.9, 1.6, "c"))

# Gate routing from the header: monotone staircase, leftmost channel on the highest level.
for i, pin in enumerate(GATE_PINS):
    n = i + 1
    px, py = hdr_xy(pin)
    level = 26.0 + 2.0 * i
    rx = CX[i] + 0.36
    if py < 3:                                  # edge-row pin: slip out between inner pins
        sx = px + 1.27
        pts = [(px, py), (sx, 3.5), (sx, level)]
    else:
        pts = [(px, py), (px, level)]
    pts += [(rx, level), (rx, YP - 12.825)]
    trace(f"GPIO_P{n}", pts, w=0.25)

# J11: HX711 module header on the top edge (GND, SCK, DT, VCC)
place("J11", "HX711 1x4", "PinHeader_1x04_P2.54", fp_row(4, 2.54, 1.0, 1.7), 24.0, 3.5, 0,
      {"1": "GND", "2": "HX_SCK", "3": "HX_DT", "4": "+3V3"}, smt=False)
trace("+3V3", [hdr_xy(1), (43.37, 7.5), (31.62, 7.5), (31.62, 3.5)], w=0.4)
trace("HX_DT", [hdr_xy(7), (50.99, 9.0), (29.08, 9.0), (29.08, 3.5)])
trace("HX_SCK", [hdr_xy(11), (56.07, 10.5), (26.54, 10.5), (26.54, 3.5)])

# LED ring: JST-XH 3 pin (+5V, DIN, GND), 74AHCT1G125 level shifter, 330R, caps
place("J12", "LED RING JST-XH 3P", "JST_XH_B3B-XH-A", fp_row(3, 2.5, 0.95, 1.7), 6.0, 3.6, 0,
      {"1": "+5V", "2": "LED_DOUT", "3": "GND"}, smt=False)
place("U1", "74AHCT1G125", "SOT-23-5", fp_sot23_5(), 9.5, 12.0, 180,
      {"1": "GND", "2": "LED_DIN", "3": "GND", "4": "LED_Y", "5": "+5V"}, lcsc="C7484")
place("R17", "330", "R_0603", fp_r0603(), 8.5, 8.8, 90, {"1": "LED_DOUT", "2": "LED_Y"}, lcsc="C23138")
place("C3", "10uF 25V", "C_0805", fp_c0805(), 2.5, 8.0, 90, {"1": "+5V", "2": "GND"}, lcsc="C15850")
place("C2", "100nF", "C_0603", fp_r0603(), 6.5, 15.2, 0, {"1": "+5V", "2": "GND"}, lcsc="C14663")
trace("LED_Y", [(8.3625, 11.05), (8.5, 10.6), (8.5, 9.625)], w=0.3)
trace("LED_DOUT", [(8.5, 7.975), (8.5, 3.6)], w=0.3)
trace("+5V", [(5.0, 13.5), (7.0, 12.95), (8.3625, 12.95)], w=0.5)
trace("+5V", [(5.0, 13.5), (5.0, 5.0), (6.0, 3.6)], w=0.6)
trace("+5V", [(2.5, 7.05), (5.0, 7.05)], w=0.5)
trace("+5V", [(5.675, 15.2), (5.0, 14.3), (5.0, 13.5)], w=0.4)
via("+5V", 5.0, 13.5)
trace("GND", [(2.5, 8.95), (2.5, 10.4)], w=0.5)
via("GND", 2.5, 10.4)
trace("GND", [(7.325, 15.2), (8.6, 15.9)], w=0.4)
via("GND", 8.6, 15.9)
trace("GND", [(10.6375, 11.05), (12.2, 8.9)], w=0.3)
via("GND", 12.2, 8.9)
trace("GND", [(10.6375, 12.95), (11.7, 13.9)], w=0.3)
via("GND", 11.7, 13.9)
trace("LED_DIN", [(14.0, 12.0), (10.6375, 12.0)], w=0.25)
via("LED_DIN", 14.0, 12.0)
# Bottom-layer runs (they cross the top-layer signal fan-out)
trace("+5V", [hdr_xy(2), (41.9, 3.7), (41.9, 10.0), (5.0, 10.0), (5.0, 13.5)], w=0.6, layer="B")
trace("LED_DIN", [hdr_xy(19), (66.23, 16.0), (14.0, 16.0), (14.0, 12.0)], w=0.25, layer="B")

# --------------------------------------------------------------------------- silkscreen
silk_texts += [
    ("HALO DRIVER v1.0", 22.0, 30.0, 2.2, "c"),
    ("8-CH PUMP  /  LOAD CELL  /  LED HALO", 22.0, 33.5, 1.1, "c"),
    ("github.com/GGPS4/halo", 22.0, 36.6, 1.1, "c"),
    ("12V", 4.5, 57.2, 1.2, "c"), ("+", 1.4, 64.6, 1.4, "c"), ("-", 1.4, 59.5, 1.4, "c"),
    ("HX711", 27.8, 7.0 - 0.6, 1.0, "c"),
    ("G CK DT V", 27.8, 0.9, 0.75, "c"),
    ("LED", 14.5, 3.6, 1.1, "c"),
    ("+  D  G", 8.5, 0.95, 0.8, "c"),
    ("RASPBERRY PI 5", 67.0, 48.0, 1.3, "c"),
]
for cx in CX:                                  # "+" beside pin 1 (+12 V) of each pump connector
    silk_texts.append(("+", cx - 3.3, YP, 1.1, "c"))
# Pi outline under the board, as a placement guide
silk_lines += [(X0, 0.5, X0, 56.5, 0.12), (X0, 56.5, 99.5, 56.5, 0.12)]

# --------------------------------------------------------------------------- geometry helpers
def all_pads():
    return [p for part in parts for p in part.pads]


def pad_layers(p):
    return ["F", "B"] if p.drill else ["F"]


def rect_poly(cx, cy, w, h):
    return [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2), (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]


def seg_dist(a, b, c, d):
    def pt_seg(p, a, b):
        ax, ay = a; bx, by = b; px, py = p
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
        return math.hypot(px - ax - t * dx, py - ay - t * dy)

    def inter(a, b, c, d):
        def o(p, q, r):
            return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        d1, d2, d3, d4 = o(c, d, a), o(c, d, b), o(a, b, c), o(a, b, d)
        return d1 * d2 < 0 and d3 * d4 < 0

    if inter(a, b, c, d):
        return 0.0
    return min(pt_seg(a, c, d), pt_seg(b, c, d), pt_seg(c, a, b), pt_seg(d, a, b))


def shapes(layer):
    """Every copper object on a layer as (net, kind, data) for clearance checks."""
    out = []
    for p in all_pads():
        if layer in pad_layers(p):
            if p.shape == "circle":
                out.append((p.net, "cap", ((p.x, p.y), (p.x, p.y), p.w / 2), p))
            else:
                out.append((p.net, "poly", rect_poly(p.x, p.y, p.w, p.h), p))
    for t in tracks:
        if t.layer == layer:
            out.append((t.net, "cap", ((t.x1, t.y1), (t.x2, t.y2), t.w / 2), t))
    for v in vias:
        out.append((v.net, "cap", ((v.x, v.y), (v.x, v.y), VIA_D / 2), v))
    return out


def poly_segs(poly):
    return list(zip(poly, poly[1:] + poly[:1]))


def inside(pt, poly):
    x, y = pt
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    return min(xs) <= x <= max(xs) and min(ys) <= y <= max(ys)


def dist(s1, s2):
    k1, d1 = s1[1], s1[2]
    k2, d2 = s2[1], s2[2]
    if k1 == "cap" and k2 == "cap":
        return seg_dist(d1[0], d1[1], d2[0], d2[1]) - d1[2] - d2[2]
    if k1 == "poly" and k2 == "poly":
        if any(inside(p, d2) for p in d1) or any(inside(p, d1) for p in d2):
            return 0.0
        return min(seg_dist(a, b, c, d) for a, b in poly_segs(d1) for c, d in poly_segs(d2))
    cap, poly = (d1, d2) if k1 == "cap" else (d2, d1)
    if inside(cap[0], poly) or inside(cap[1], poly):
        return 0.0
    return min(seg_dist(cap[0], cap[1], c, d) for c, d in poly_segs(poly)) - cap[2]


def clearance_report(min_clear=0.2):
    problems = []
    for layer in ("F", "B"):
        sh = shapes(layer)
        for i in range(len(sh)):
            for j in range(i + 1, len(sh)):
                if sh[i][0] == sh[j][0]:
                    continue
                d = dist(sh[i], sh[j])
                if d < min_clear:
                    problems.append((layer, round(d, 3), sh[i][0], sh[j][0], _where(sh[i][3]), _where(sh[j][3])))
    # holes keep-out
    for hx, hy, hd in npth:
        for layer in ("F", "B"):
            for s in shapes(layer):
                d = dist(s, ("", "cap", ((hx, hy), (hx, hy), 3.0)))
                if d < 0:
                    problems.append((layer, round(d, 3), s[0], "MOUNT", _where(s[3]), f"hole {hx},{hy}"))
    # board edge
    for layer in ("F", "B"):
        for s in shapes(layer):
            for edge in [((0, 0), (W, 0)), ((W, 0), (W, H)), ((W, H), (0, H)), ((0, H), (0, 0))]:
                d = dist(s, ("", "cap", (edge[0], edge[1], 0.0)))
                if d < EDGE_CLEAR - 0.15:
                    problems.append((layer, round(d, 3), s[0], "EDGE", _where(s[3]), ""))
    return problems


def _where(o):
    if isinstance(o, Pad):
        return f"{o.ref}.{o.num}@({o.x:.2f},{o.y:.2f})"
    if isinstance(o, Track):
        return f"trk({o.x1:.2f},{o.y1:.2f})-({o.x2:.2f},{o.y2:.2f})"
    return f"via({o.x:.2f},{o.y:.2f})"


def connectivity_report():
    """Union-find over same-net copper that touches; pours are treated as connecting all
    GND objects on each layer. Returns nets that end up in more than one piece."""
    objs = []
    for layer in ("F", "B"):
        for s in shapes(layer):
            objs.append((layer, s))
    parent = list(range(len(objs)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    ident = {}
    for i, (layer, s) in enumerate(objs):
        key = id(s[3])
        if key in ident:            # THT pads and vias exist on both layers
            union(i, ident[key])
        ident[key] = i
    for i in range(len(objs)):
        for j in range(i + 1, len(objs)):
            (l1, s1), (l2, s2) = objs[i], objs[j]
            if l1 == l2 and s1[0] == s2[0] and dist(s1, s2) <= 0.001:
                union(i, j)
    gnd = [i for i, (l, s) in enumerate(objs) if s[0] == "GND"]
    for i in gnd[1:]:
        union(i, gnd[0])
    pieces = {}
    for i, (l, s) in enumerate(objs):
        pieces.setdefault(s[0], set()).add(find(i))
    return {net: len(p) for net, p in pieces.items() if len(p) > 1 and not net.startswith("NC_")}


# --------------------------------------------------------------------------- stitching vias
def add_stitching(pitch=5.0):
    added = 0
    y = 3.0
    while y < 60.5:
        x = 3.0
        while x < W - 3:
            cand = ("GND", "cap", ((x, y), (x, y), VIA_D / 2 + 0.35), None)
            ok = all(dist(cand, s) > 0 for layer in ("F", "B") for s in shapes(layer))
            ok = ok and all(math.hypot(x - hx, y - hy) > 3.6 for hx, hy, _ in npth)
            if ok:
                via("GND", round(x, 2), round(y, 2))
                added += 1
            x += pitch
        y += pitch
    return added


# --------------------------------------------------------------------------- gerber writer
def gnum(v):
    return str(int(round(v * 1e6)))


class Gerber:
    def __init__(self, function):
        self.ap = {}
        self.body = []
        self.head = ["%FSLAX46Y46*%", "%MOMM*%", f"%TF.FileFunction,{function}*%",
                     "%TF.GenerationSoftware,Halo,generate.py,1.0*%", "%LPD*%"]

    def aperture(self, kind, a, b=None):
        key = (kind, round(a, 4), None if b is None else round(b, 4))
        if key not in self.ap:
            code = 10 + len(self.ap)
            self.ap[key] = code
            spec = f"C,{a:.4f}" if kind == "C" else f"R,{a:.4f}X{b:.4f}"
            self.head.append(f"%ADD{code}{spec}*%")
        return self.ap[key]

    def xy(self, x, y):
        return f"X{gnum(x)}Y{gnum(H - y)}"

    def polarity(self, dark):
        self.body.append("%LPD*%" if dark else "%LPC*%")

    def flash_pad(self, p, grow=0.0):
        if p.shape == "circle":
            code = self.aperture("C", p.w + 2 * grow)
        else:
            code = self.aperture("R", p.w + 2 * grow, p.h + 2 * grow)
        self.body += [f"D{code}*", self.xy(p.x, p.y) + "D03*"]

    def circle(self, x, y, d):
        code = self.aperture("C", d)
        self.body += [f"D{code}*", self.xy(x, y) + "D03*"]

    def line(self, x1, y1, x2, y2, w):
        code = self.aperture("C", w)
        self.body += [f"D{code}*", self.xy(x1, y1) + "D02*", self.xy(x2, y2) + "D01*"]

    def region(self, pts):
        self.body += ["G36*", self.xy(*pts[0]) + "D02*"]
        self.body += [self.xy(x, y) + "D01*" for x, y in pts[1:]]
        self.body += [self.xy(*pts[0]) + "D01*", "G37*"]

    def save(self, path):
        path.write_text("\n".join(self.head + ["G01*"] + self.body + ["M02*"]) + "\n")


def rounded_rect(x0, y0, x1, y1, r, steps=8):
    pts = []
    for (cx, cy, a0) in [(x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)]:
        for k in range(steps + 1):
            a = math.radians(a0 + 90 * k / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def copper_layer(layer):
    g = Gerber("Copper,L1,Top" if layer == "F" else "Copper,L2,Bot")
    # 1) pour
    e = EDGE_CLEAR
    if layer == "F":
        g.region(rounded_rect(e, e, W - e, H - e, CORNER_R - e))
    else:
        g.region(rounded_rect(e, e, W - e, YP - 4.3, CORNER_R - e))
    # 2) knock out everything that isn't GND, plus the mounting-hole keep-outs
    g.polarity(False)
    for p in all_pads():
        if layer in pad_layers(p) and p.net != "GND":
            g.flash_pad(p, POUR_CLEAR)
    for t in tracks:
        if t.layer == layer and t.net != "GND":
            g.line(t.x1, t.y1, t.x2, t.y2, t.w + 2 * POUR_CLEAR)
    for v in vias:
        if v.net != "GND":
            g.circle(v.x, v.y, VIA_D + 2 * POUR_CLEAR)
    for hx, hy, _ in npth:
        g.circle(hx, hy, 6.4)
    # 3) all copper features
    g.polarity(True)
    for p in all_pads():
        if layer in pad_layers(p):
            g.flash_pad(p)
    for t in tracks:
        if t.layer == layer:
            g.line(t.x1, t.y1, t.x2, t.y2, t.w)
    for v in vias:
        g.circle(v.x, v.y, VIA_D)
    return g


def mask_layer(layer):
    g = Gerber("Soldermask,Top" if layer == "F" else "Soldermask,Bot")
    for p in all_pads():
        if layer in pad_layers(p):
            g.flash_pad(p, MASK_EXP)
    for hx, hy, hd in npth:
        g.circle(hx, hy, hd + 0.2)
    return g


def paste_layer():
    g = Gerber("Paste,Top")
    for p in all_pads():
        if p.smd:
            g.flash_pad(p)
    return g


def text_polys(text, x, y, size, anchor="c"):
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath

    tp = TextPath((0, 0), text, size=size * 1.38, prop=FontProperties(family="DejaVu Sans", weight="bold"))
    ext = tp.get_extents()
    ox = x - (ext.x0 + ext.width / 2) if anchor == "c" else x - ext.x0
    oy = y + (ext.y0 + ext.height / 2)
    polys = []
    for poly in tp.to_polygons():
        pts = [(ox + px, oy - py) for px, py in poly]
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1])) / 2
        polys.append((pts, area))
    return polys


def silk_layer():
    g = Gerber("Legend,Top")
    for x1, y1, x2, y2, w in silk_lines:
        g.line(x1, y1, x2, y2, w)
    for part in parts:                       # simple body outlines
        _outline(g, part)
    for text, x, y, size, anchor in silk_texts:
        polys = text_polys(text, x, y, size, anchor)
        if not polys:
            continue
        sign = 1 if max(polys, key=lambda p: abs(p[1]))[1] > 0 else -1
        for pts, a in polys:
            if a * sign > 0:
                g.region(pts)
        holes = [pts for pts, a in polys if a * sign < 0]
        if holes:
            g.polarity(False)
            for pts in holes:
                g.region(pts)
            g.polarity(True)
    return g


def _outline(g, part):
    w = 0.15
    if part.ref.startswith("Q") or part.ref == "U1":
        x, y = part.x, part.y
        g.line(x - 0.7, y - 1.5, x - 0.7, y + 1.5, w) if False else None
    if part.ref.startswith("J") and part.ref not in ("J1",):
        xs = [p.x for p in part.pads]; ys = [p.y for p in part.pads]
        pad = max(p.w for p in part.pads) / 2 + 0.6
        x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
        for a, b in [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]:
            g.line(a[0], a[1], b[0], b[1], w)
    if part.ref == "J1":
        x0, x1 = hdr_xy(1)[0] - 1.6, hdr_xy(40)[0] + 1.6
        for a, b in [((x0, 0.75), (x1, 0.75)), ((x1, 0.75), (x1, 6.25)), ((x1, 6.25), (x0, 6.25)), ((x0, 6.25), (x0, 0.75))]:
            g.line(a[0], a[1], b[0], b[1], w)
    if part.ref == "C1":
        cx, cy = 6.0, 50.0
        pts = [(cx + 4.2 * math.cos(t / 24 * math.tau), cy + 4.2 * math.sin(t / 24 * math.tau)) for t in range(25)]
        for a, b in zip(pts, pts[1:]):
            g.line(a[0], a[1], b[0], b[1], w)


def edge_layer():
    g = Gerber("Profile,NP")
    pts = rounded_rect(0, 0, W, H, CORNER_R)
    for a, b in zip(pts, pts[1:] + pts[:1]):
        g.line(a[0], a[1], b[0], b[1], 0.1)
    return g


def drill_file(path, holes, plated):
    tools = {}
    for x, y, d in holes:
        tools.setdefault(round(d, 3), []).append((x, y))
    lines = ["M48", "; Halo Driver Board", f"; {'PLATED' if plated else 'NON-PLATED'} holes", "FMAT,2", "METRIC,TZ"]
    for i, d in enumerate(sorted(tools), 1):
        lines.append(f"T{i}C{d:.3f}")
    lines += ["%", "G90", "G05"]
    for i, d in enumerate(sorted(tools), 1):
        lines.append(f"T{i}")
        lines += [f"X{x:.3f}Y{H - y:.3f}" for x, y in tools[d]]
    lines += ["T0", "M30"]
    path.write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- JLCPCB BOM / CPL
JLC_ROT = {"SOT-23": 180, "SOT-23-5": 180, "D_SOD-123": 180}    # KiCad->JLC offsets; review in the JLC preview


def bom_cpl():
    smt = [p for p in parts if p.smt]
    groups = {}
    for p in smt:
        groups.setdefault((p.value, p.footprint, p.lcsc), []).append(p.ref)
    with open(OUT / "halo-driver-BOM.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (val, fp, lcsc), refs in groups.items():
            w.writerow([val, ",".join(sorted(refs, key=_refkey)), fp, lcsc])
    with open(OUT / "halo-driver-CPL.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for p in sorted(smt, key=lambda p: _refkey(p.ref)):
            r = (360 - p.rot + JLC_ROT.get(p.footprint, 0)) % 360
            w.writerow([p.ref, f"{p.x:.3f}mm", f"{H - p.y:.3f}mm", "Top", r])
    with open(OUT / "halo-driver-hand-solder.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Ref", "Part", "Footprint"])
        for p in parts:
            if not p.smt:
                w.writerow([p.ref, p.value, p.footprint])


def _refkey(r):
    import re
    m = re.match(r"([A-Z]+)(\d+)", r)
    return (m.group(1), int(m.group(2)))


# --------------------------------------------------------------------------- KiCad board
def kicad_pcb(path):
    nets = sorted({p.net for p in all_pads()} | {t.net for t in tracks} | {v.net for v in vias})
    nid = {n: i + 1 for i, n in enumerate(nets)}
    u = lambda: str(uuid.uuid4())
    L = ['(kicad_pcb (version 20240108) (generator "halo_generate") (generator_version "1.0")',
         '  (general (thickness 1.6) (legacy_teardrops no))', '  (paper "A4")',
         '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (36 "B.SilkS" user "B.Silkscreen")',
         '    (37 "F.SilkS" user "F.Silkscreen") (38 "B.Mask" user) (39 "F.Mask" user)',
         '    (34 "B.Paste" user) (35 "F.Paste" user) (44 "Edge.Cuts" user) (46 "B.CrtYd" user "B.Courtyard")',
         '    (47 "F.CrtYd" user "F.Courtyard") (48 "B.Fab" user) (49 "F.Fab" user))',
         '  (setup (pad_to_mask_clearance 0.05) (pcbplotparams (layerselection 0x00010fc_ffffffff) (outputdirectory "gerbers/")))',
         '  (net 0 "")']
    L += [f'  (net {nid[n]} "{n}")' for n in nets]
    for part in parts:
        L.append(f'  (footprint "Halo:{part.footprint}" (layer "F.Cu") (uuid "{u()}") (at {part.x} {part.y})')
        L.append(f'    (property "Reference" "{part.ref}" (at 0 -2) (layer "F.SilkS") (hide yes) (uuid "{u()}") (effects (font (size 0.8 0.8) (thickness 0.12))))')
        L.append(f'    (property "Value" "{part.value}" (at 0 2) (layer "F.Fab") (hide yes) (uuid "{u()}") (effects (font (size 0.8 0.8) (thickness 0.12))))')
        if part.lcsc:
            L.append(f'    (property "LCSC" "{part.lcsc}" (at 0 0) (layer "F.Fab") (hide yes) (uuid "{u()}") (effects (font (size 0.8 0.8))))')
        if not part.smt:
            L.append("    (attr through_hole)")
        else:
            L.append("    (attr smd)")
        for p in part.pads:
            dx, dy = round(p.x - part.x, 4), round(p.y - part.y, 4)
            shape = "rect" if p.shape == "rect" else "circle"
            if p.smd:
                L.append(f'    (pad "{p.num}" smd {shape} (at {dx} {dy}) (size {p.w} {p.h}) (layers "F.Cu" "F.Paste" "F.Mask") (net {nid[p.net]} "{p.net}") (uuid "{u()}"))')
            else:
                L.append(f'    (pad "{p.num}" thru_hole {shape} (at {dx} {dy}) (size {p.w} {p.h}) (drill {p.drill}) (layers "*.Cu" "*.Mask") (net {nid[p.net]} "{p.net}") (uuid "{u()}"))')
        L.append("  )")
    for i, (hx, hy, hd) in enumerate(npth):
        L.append(f'  (footprint "Halo:MountingHole_2.7mm" (layer "F.Cu") (uuid "{u()}") (at {hx} {hy})')
        L.append(f'    (property "Reference" "H{i + 1}" (at 0 -3) (layer "F.SilkS") (hide yes) (uuid "{u()}") (effects (font (size 0.8 0.8))))')
        L.append(f'    (pad "" np_thru_hole circle (at 0 0) (size {hd} {hd}) (drill {hd}) (layers "*.Cu" "*.Mask") (uuid "{u()}"))')
        L.append(f'    (fp_circle (center 0 0) (end 3 0) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd") (uuid "{u()}"))')
        L.append("  )")
    pts = rounded_rect(0, 0, W, H, CORNER_R)
    for a, b in zip(pts, pts[1:] + pts[:1]):
        L.append(f'  (gr_line (start {a[0]:.4f} {a[1]:.4f}) (end {b[0]:.4f} {b[1]:.4f}) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts") (uuid "{u()}"))')
    for text, x, y, size, anchor in silk_texts:
        L.append(f'  (gr_text "{text}" (at {x} {y}) (layer "F.SilkS") (uuid "{u()}") (effects (font (size {size} {size}) (thickness {size * 0.15:.2f}) bold)))')
    for x1, y1, x2, y2, w in silk_lines:
        L.append(f'  (gr_line (start {x1} {y1}) (end {x2} {y2}) (stroke (width {w}) (type solid)) (layer "F.SilkS") (uuid "{u()}"))')
    for t in tracks:
        layer = "F.Cu" if t.layer == "F" else "B.Cu"
        L.append(f'  (segment (start {t.x1} {t.y1}) (end {t.x2} {t.y2}) (width {t.w}) (layer "{layer}") (net {nid[t.net]}) (uuid "{u()}"))')
    for v in vias:
        L.append(f'  (via (at {v.x} {v.y}) (size {VIA_D}) (drill {VIA_DRILL}) (layers "F.Cu" "B.Cu") (net {nid[v.net]}) (uuid "{u()}"))')
    for layer, bottom in (("F.Cu", W), ("B.Cu", YP - 4.3)):
        y1 = H - EDGE_CLEAR if layer == "F.Cu" else bottom
        L.append(f'  (zone (net {nid["GND"]}) (net_name "GND") (layer "{layer}") (uuid "{u()}") (hatch edge 0.5)')
        L.append(f'    (connect_pads yes (clearance {POUR_CLEAR})) (min_thickness 0.25) (filled_areas_thickness no)')
        L.append('    (keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour allowed) (footprints allowed))' if False else
                 '    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))')
        e = EDGE_CLEAR
        L.append(f'    (polygon (pts (xy {e} {e}) (xy {W - e} {e}) (xy {W - e} {y1}) (xy {e} {y1}))))')
    L.append(")")
    path.write_text("\n".join(L) + "\n")


# --------------------------------------------------------------------------- previews
def previews():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Polygon, Rectangle

    colors = {"F": "#c8402c", "B": "#2c6fc8"}
    for layer in ("F", "B", "both"):
        fig, ax = plt.subplots(figsize=(12, 8.4), dpi=130)
        ax.set_facecolor("#14351f")
        ax.add_patch(Polygon(rounded_rect(0, 0, W, H, CORNER_R), closed=True, fc="#1d4a2b", ec="#e8e0b0", lw=1))
        layers = ["B", "F"] if layer == "both" else [layer]
        for L in layers:
            c = colors[L]
            for t in tracks:
                if t.layer == L:
                    ax.plot([t.x1, t.x2], [t.y1, t.y2], color=c, lw=t.w * 4.6, solid_capstyle="round", alpha=0.9)
        for p in all_pads():
            if any(L in pad_layers(p) for L in layers):
                fc = "#d8b25a"
                if p.shape == "circle":
                    ax.add_patch(Circle((p.x, p.y), p.w / 2, fc=fc, ec="none"))
                else:
                    ax.add_patch(Rectangle((p.x - p.w / 2, p.y - p.h / 2), p.w, p.h, fc=fc, ec="none"))
                if p.drill:
                    ax.add_patch(Circle((p.x, p.y), p.drill / 2, fc="#111", ec="none"))
        for v in vias:
            ax.add_patch(Circle((v.x, v.y), VIA_D / 2, fc="#d8b25a", ec="none"))
            ax.add_patch(Circle((v.x, v.y), VIA_DRILL / 2, fc="#111", ec="none"))
        for hx, hy, hd in npth:
            ax.add_patch(Circle((hx, hy), hd / 2, fc="#111", ec="#e8e0b0"))
        if layer != "B":
            for text, x, y, size, anchor in silk_texts:
                for pts, a in text_polys(text, x, y, size, anchor):
                    ax.add_patch(Polygon(pts, closed=True, fc="none", ec="white", lw=0.4))
            for part in parts:
                ax.text(part.x, part.y, part.ref, color="white", fontsize=5, ha="center", va="center", alpha=0.75)
        ax.set_xlim(-2, W + 2); ax.set_ylim(H + 2, -2); ax.set_aspect("equal"); ax.axis("off")
        name = {"F": "top", "B": "bottom", "both": "both"}[layer]
        fig.savefig(OUT / f"preview-{name}.png", bbox_inches="tight", facecolor="#0e0e10")
        plt.close(fig)


# --------------------------------------------------------------------------- main
def main():
    OUT.mkdir(exist_ok=True)
    problems = clearance_report()
    if problems:
        print(f"{len(problems)} clearance problems:")
        for p in problems:
            print("  ", p)
        raise SystemExit(1)
    stitched = add_stitching()
    problems = clearance_report()
    assert not problems, problems
    broken = connectivity_report()
    gdir = OUT / "gerbers"
    gdir.mkdir(exist_ok=True)
    copper_layer("F").save(gdir / "halo-driver-F_Cu.gtl")
    copper_layer("B").save(gdir / "halo-driver-B_Cu.gbl")
    mask_layer("F").save(gdir / "halo-driver-F_Mask.gts")
    mask_layer("B").save(gdir / "halo-driver-B_Mask.gbs")
    paste_layer().save(gdir / "halo-driver-F_Paste.gtp")
    silk_layer().save(gdir / "halo-driver-F_Silkscreen.gto")
    edge_layer().save(gdir / "halo-driver-Edge_Cuts.gko")
    pth = [(p.x, p.y, p.drill) for p in all_pads() if p.drill] + [(v.x, v.y, VIA_DRILL) for v in vias]
    drill_file(gdir / "halo-driver-PTH.drl", pth, True)
    drill_file(gdir / "halo-driver-NPTH.drl", npth, False)
    with zipfile.ZipFile(OUT / "halo-driver-gerbers.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(gdir.iterdir()):
            z.write(f, f.name)
    bom_cpl()
    kicad_pcb(OUT / "halo-driver.kicad_pcb")
    previews()
    print(f"OK: {len(parts)} parts, {len(tracks)} track segments, {len(vias)} vias ({stitched} stitching)")
    print("Nets in more than one piece (should be empty):", broken or "none")


if __name__ == "__main__":
    main()
