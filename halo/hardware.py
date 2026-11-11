"""Pumps, scale and LED ring, each with a mock twin for development off the Pi.

Real drivers import their Pi-only libraries lazily so the rest of Halo (tests, the web
page, the UI) runs on any computer with HALO_MOCK=1.
"""
from __future__ import annotations

import math
import threading
import time


# ---------------------------------------------------------------- pumps
class Pumps:
    def __init__(self, pins: list[int]):
        from gpiozero import DigitalOutputDevice  # Pi only

        self._outs = [DigitalOutputDevice(p, initial_value=False) for p in pins]

    def on(self, slot: int) -> None:
        self._outs[slot - 1].on()

    def off(self, slot: int) -> None:
        self._outs[slot - 1].off()

    def all_off(self) -> None:
        for o in self._outs:
            o.off()


class MockPumps:
    """Pretends pumps move liquid onto a MockScale."""

    def __init__(self, scale: "MockScale", flow_g_per_s: float = 1.6):
        self.scale = scale
        self.flow = flow_g_per_s
        self.running: set[int] = set()
        self.empty_slots: set[int] = set()

    def on(self, slot: int) -> None:
        self.running.add(slot)
        if slot not in self.empty_slots:
            self.scale.flow = self.flow

    def off(self, slot: int) -> None:
        self.running.discard(slot)
        if not self.running:
            self.scale.flow = 0.0

    def all_off(self) -> None:
        self.running.clear()
        self.scale.flow = 0.0


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


class Halo:
    def __init__(self, count: int, brightness: float):
        import board
        import neopixel_spi  # adafruit-circuitpython-neopixel-spi, Pi only

        self.n = count
        self.px = neopixel_spi.NeoPixel_SPI(
            board.SPI(), count, brightness=brightness, auto_write=False, pixel_order=neopixel_spi.GRB
        )

    def _show(self, colors):
        for i, c in enumerate(colors):
            self.px[i] = c
        self.px.show()

    def off(self):
        self._show([(0, 0, 0)] * self.n)

    def breathe(self, t: float, color=WARM):
        """Slow breathing glow while waiting for a glass."""
        k = 0.15 + 0.35 * (0.5 + 0.5 * math.sin(t * 1.6))
        self._show([tuple(int(v * k) for v in color)] * self.n)

    def progress(self, fraction: float, color=WARM):
        """Fill the ring clockwise from the front; the leading pixel fades in smoothly."""
        f = max(0.0, min(1.0, fraction)) * self.n
        out = []
        for i in range(self.n):
            k = 1.0 if i + 1 <= f else max(0.0, f - i)
            out.append(tuple(int(v * (0.08 + 0.92 * k)) for v in color))
        self._show(out)

    def flash(self, color):
        self._show([color] * self.n)


class MockHalo:
    def __init__(self, count: int = 24, brightness: float = 0.4):
        self.n = count
        self.last = None

    def off(self):
        self.last = ("off",)

    def breathe(self, t, color=WARM):
        self.last = ("breathe",)

    def progress(self, fraction, color=WARM):
        self.last = ("progress", round(fraction, 3))

    def flash(self, color):
        self.last = ("flash", color)


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
        return MockPumps(scale), scale, MockHalo(hw["led_count"])
    scale = Scale(hw["hx711_dt"], hw["hx711_sck"], hw["scale_factor"])
    return Pumps(hw["pump_pins"]), scale, Halo(hw["led_count"], hw["led_brightness"])
