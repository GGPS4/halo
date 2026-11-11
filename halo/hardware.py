"""Pumps, scale and LED ring, each with a mock twin for development off the Pi.

Real drivers import their Pi-only libraries lazily so the rest of Halo (tests, the web
app, the UI) runs on any computer with HALO_MOCK=1.
"""
from __future__ import annotations

import math
import threading
import time


def hex_rgb(h: str, fallback=(240, 170, 80)):
    h = (h or "").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


# ---------------------------------------------------------------- pumps
class Pumps:
    """PWM on each MOSFET gate, so pumps can slow down near the target (feature 34)."""

    def __init__(self, pins: list[int]):
        from gpiozero import PWMOutputDevice  # Pi only

        self._outs = [PWMOutputDevice(p, initial_value=0, frequency=200) for p in pins]

    def on(self, slot: int, speed: float = 1.0) -> None:
        self._outs[slot - 1].value = max(0.0, min(1.0, speed))

    def off(self, slot: int) -> None:
        self._outs[slot - 1].value = 0

    def all_off(self) -> None:
        for o in self._outs:
            o.value = 0


class MockPumps:
    """Pretends pumps move liquid onto a MockScale; flow follows the PWM speed."""

    def __init__(self, scale: "MockScale", flow_g_per_s: float = 1.6):
        self.scale = scale
        self.flow = flow_g_per_s
        self.running: dict[int, float] = {}
        self.empty_slots: set[int] = set()
        self.speed_log: list[float] = []

    def _update(self):
        self.scale.flow = sum(self.flow * s for slot, s in self.running.items() if slot not in self.empty_slots)

    def on(self, slot: int, speed: float = 1.0) -> None:
        self.running[slot] = speed
        self.speed_log.append(speed)
        self._update()

    def off(self, slot: int) -> None:
        self.running.pop(slot, None)
        self._update()

    def all_off(self) -> None:
        self.running.clear()
        self._update()


# ---------------------------------------------------------------- scale
class Scale:
    """HX711 read by bit-banging two GPIOs (works on the Pi 5 through lgpio)."""

    def __init__(self, dt: int, sck: int, factor: float):
        import lgpio  # Pi only

        self._lg = lgpio
        self._h = lgpio.gpiochip_open(4 if _is_pi5() else 0)
        self._dt, self._sck = dt, sck
        lgpio.gpio_claim_input(self._h, dt)
        lgpio.gpio_claim_output(self._h, sck, 0)
        self.factor = factor
        self.offset = 0.0
        self._lock = threading.Lock()

    def _read_raw(self) -> int:
        lg, h = self._lg, self._h
        deadline = time.monotonic() + 0.5
        while lg.gpio_read(h, self._dt) == 1:      # wait for data ready
            if time.monotonic() > deadline:
                raise TimeoutError("HX711 not responding — check the load-cell wiring")
            time.sleep(0.001)
        value = 0
        for _ in range(24):
            lg.gpio_write(h, self._sck, 1)
            lg.gpio_write(h, self._sck, 0)
            value = (value << 1) | lg.gpio_read(h, self._dt)
        lg.gpio_write(h, self._sck, 1)              # 25th pulse: channel A, gain 128
        lg.gpio_write(h, self._sck, 0)
        if value & 0x800000:
            value -= 1 << 24
        return value

    def raw(self, samples: int = 3) -> float:
        with self._lock:
            vals = sorted(self._read_raw() for _ in range(samples))
        return vals[len(vals) // 2]                 # median rejects spikes

    def grams(self, samples: int = 3) -> float:
        return (self.raw(samples) - self.offset) / self.factor

    def tare(self) -> None:
        self.offset = sum(self.raw(5) for _ in range(3)) / 3


class MockScale:
    def __init__(self):
        self._g = 0.0
        self.flow = 0.0
        self._t = time.monotonic()

    def _tick(self):
        now = time.monotonic()
        self._g += self.flow * (now - self._t)
        self._t = now

    def grams(self, samples: int = 3) -> float:
        self._tick()
        return self._g

    def tare(self) -> None:
        self._tick()
        self._g = 0.0

    def place(self, grams: float) -> None:
        """Test helper: put something on (or take it off) the stage."""
        self._tick()
        self._g += grams


# ---------------------------------------------------------------- LED halo
WARM = (255, 170, 80)


def _scale(c, k):
    return tuple(int(v * k) for v in c)


class HaloBase:
    """Ring animations shared by the real ring and the mock. Subclasses implement _write()."""

    n = 24
    gate_n = 0      # pixels in the strip inside the gate ring, chained after the 24-LED ring

    def _write(self, colors):
        raise NotImplementedError

    def gate_colors(self, ring: list) -> list:
        """Map the ring's pattern onto the gate strip. The strip runs from the left foot,
        over the top, to the right foot, so both sides climb together: whatever fills the
        ring clockwise rises up the gate from the feet and meets at the top."""
        g, n = self.gate_n, len(ring)
        if g <= 0 or n == 0:
            return []
        out = []
        for j in range(g):
            s = 1.0 - abs(2.0 * j / max(1, g - 1) - 1.0)      # 0 at the feet, 1 at the top
            out.append(ring[min(n - 1, int(s * (n - 1) + 0.5))])
        return out

    def _show(self, colors):
        colors = list(colors)
        self._write(colors + self.gate_colors(colors))

    def set_brightness(self, b: float):
        pass

    def off(self):
        self._show([(0, 0, 0)] * self.n)

    def breathe(self, t: float, color=WARM):
        """Slow breathing glow while waiting for a glass."""
        k = 0.15 + 0.35 * (0.5 + 0.5 * math.sin(t * 1.6))
        self._show([_scale(color, k)] * self.n)

    def progress(self, fraction: float, color=WARM):
        f = max(0.0, min(1.0, fraction)) * self.n
        self._show([_scale(color, 0.08 + 0.92 * (1.0 if i + 1 <= f else max(0.0, f - i))) for i in range(self.n)])

    def segments(self, segs: list[tuple[tuple, float]], done_g: float):
        """Feature 28: each ingredient owns a share of the ring in its own color, and the
        ring fills clockwise as the scale sees liquid land. segs = [(rgb, target_g), ...]"""
        total = sum(g for _, g in segs) or 1.0
        out = []
        for i in range(self.n):
            mid = (i + 0.5) / self.n * total               # grams this pixel represents
            acc, color = 0.0, segs[-1][0] if segs else WARM
            for c, g in segs:
                if mid <= acc + g:
                    color = c
                    break
                acc += g
            lit = max(0.0, min(1.0, (done_g - i / self.n * total) / (total / self.n)))
            out.append(_scale(color, 0.06 + 0.94 * lit))
        self._show(out)

    def ambient(self, t: float, colors: list[tuple]):
        """Feature 15: slow drift of the loaded bottles' colors around the ring when idle."""
        if not colors:
            return self.breathe(t, (90, 70, 50))
        out = []
        for i in range(self.n):
            p = (i / self.n * len(colors) + t * 0.08) % len(colors)
            a, b = colors[int(p)], colors[(int(p) + 1) % len(colors)]
            f = p - int(p)
            c = tuple(int(a[j] + (b[j] - a[j]) * f) for j in range(3))
            k = 0.25 + 0.15 * math.sin(t * 0.7 + i * 0.4)
            out.append(_scale(c, k))
        self._show(out)

    def listening(self, t: float):
        """Feature 14: soft white comet while Levi is taking an order."""
        head = (t * 14) % self.n
        self._show([_scale((210, 225, 255), max(0.05, 1 - ((head - i) % self.n) / 6)) for i in range(self.n)])

    def flash(self, color):
        self._show([color] * self.n)


class Halo(HaloBase):
    def __init__(self, count: int, brightness: float, gate_count: int = 0):
        import board
        import neopixel_spi  # adafruit-circuitpython-neopixel-spi, Pi only

        self.n, self.gate_n = count, gate_count
        self.px = neopixel_spi.NeoPixel_SPI(
            board.SPI(), count + gate_count, brightness=brightness, auto_write=False,
            pixel_order=neopixel_spi.GRB,
        )

    def set_brightness(self, b: float):
        self.px.brightness = max(0.0, min(0.6, b))

    def _write(self, colors):
        for i, c in enumerate(colors):
            self.px[i] = c
        self.px.show()


class MockHalo(HaloBase):
    def __init__(self, count: int = 24, brightness: float = 0.4, gate_count: int = 0):
        self.n, self.gate_n = count, gate_count
        self.brightness = brightness
        self.frame = [(0, 0, 0)] * count          # the ring under the glass
        self.gate = [(0, 0, 0)] * gate_count      # the strip inside the gate ring
        self.last = None

    def set_brightness(self, b: float):
        self.brightness = b

    def _write(self, colors):
        self.frame, self.gate = list(colors[:self.n]), list(colors[self.n:])

    # remember which animation ran last (tests and the web app's mock ring use it)
    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        if name in ("off", "breathe", "progress", "segments", "ambient", "listening", "flash"):
            def wrapped(*a, **k):
                object.__setattr__(self, "last", name)
                return attr(*a, **k)
            return wrapped
        return attr


def _is_pi5() -> bool:
    try:
        with open("/proc/device-tree/model") as f:
            return "Raspberry Pi 5" in f.read()
    except OSError:
        return False


def build(settings: dict):
    """Return (pumps, scale, halo) for this machine."""
    hw = settings["hardware"]
    if hw["mock"]:
        scale = MockScale()
        return MockPumps(scale), scale, MockHalo(hw["led_count"], gate_count=hw.get("gate_led_count", 0))
    scale = Scale(hw["hx711_dt"], hw["hx711_sck"], hw["scale_factor"])
    return Pumps(hw["pump_pins"]), scale, Halo(hw["led_count"], hw["led_brightness"], hw.get("gate_led_count", 0))
