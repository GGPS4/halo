"""The pour engine: one pump at a time, each stopped by weight, not by a timer.

Features layered on top of the basic weigh-and-stop loop:
  2  strength (light / normal / strong) changes the spirit ratio, see Recipe.plan
  4  every pour is subtracted from the bottle levels and logged to history
  28 the LED ring and the screen show each ingredient's share in its own color
  34 the pump slows down over the last few grams so it stops right on target
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .config import Inventory
from .hardware import hex_rgb
from .recipes import Recipe

TICK = 0.05


class State(str, Enum):
    IDLE = "idle"
    WAITING_FOR_GLASS = "waiting_for_glass"
    POURING = "pouring"
    PAUSED = "paused"            # glass lifted mid-pour
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class Progress:
    state: State = State.IDLE
    drink: str = ""
    size: str = "single"
    strength: str = "normal"
    by: str = ""
    step: str = ""
    step_index: int = -1
    color: str = ""
    fraction: float = 0.0
    poured_g: float = 0.0
    target_g: float = 0.0
    speed: float = 0.0
    message: str = ""
    segments: list[dict] = field(default_factory=list)   # [{bottle, color, target_g}]
    report: list[dict] = field(default_factory=list)
    started: float = 0.0

    def to_dict(self) -> dict:
        return {
            "state": self.state.value, "drink": self.drink, "size": self.size, "strength": self.strength,
            "by": self.by, "step": self.step, "step_index": self.step_index, "color": self.color,
            "fraction": round(self.fraction, 3), "poured_g": round(self.poured_g, 1),
            "target_g": round(self.target_g, 1), "speed": round(self.speed, 2), "message": self.message,
            "segments": self.segments, "report": self.report,
        }


class PourError(Exception):
    pass


class PourEngine:
    def __init__(self, pumps, scale, halo, settings: dict, inventory: Inventory, history=None, prefs=None):
        self.pumps, self.scale, self.halo = pumps, scale, halo
        self.cfg = settings["pour"]
        self.inv = inventory
        self.history = history
        self.prefs = prefs
        self.progress = Progress()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.listeners: list[Callable[[Progress], None]] = []

    # ------------------------------------------------------------ public
    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def size_ml(self, size: str) -> float:
        key = "double_oz" if size == "double" else "single_oz"
        oz = (self.prefs[key] if self.prefs and self.prefs[key] else None) or self.cfg[key]
        return float(oz) * self.cfg["ml_per_oz"]

    def start(self, recipe: Recipe, size: str = "single", strength: str = "normal",
              glass_timeout: float = 60.0, by: str = "") -> None:
        if self.busy:
            raise PourError("Already pouring")
        steps = recipe.plan(self.inv, self.size_ml(size), strength)   # raises if a bottle is missing
        for s in steps:
            b = self.inv.find(s.bottle)
            if b.remaining_ml < s.ml:
                raise PourError(f"{b.name} is too low for this drink ({b.remaining_ml:.0f} ml left)")
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run, args=(recipe, steps, size, strength, glass_timeout, by), daemon=True
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()
        self.pumps.all_off()

    def wait(self, timeout: float | None = None) -> Progress:
        if self._thread:
            self._thread.join(timeout)
        return self.progress

    # ------------------------------------------------------------ internals
    def _emit(self, **changes):
        for k, v in changes.items():
            setattr(self.progress, k, v)
        for fn in self.listeners:
            try:
                fn(self.progress)
            except Exception:
                pass

    def _run(self, recipe, steps, size, strength, glass_timeout, by):
        total = sum(s.grams for s in steps)
        segs = [{"bottle": s.bottle, "color": s.color, "target_g": round(s.grams, 1)} for s in steps]
        self._rgb_segs = [(hex_rgb(s.color), s.grams) for s in steps]
        self.progress = Progress(drink=recipe.name, size=size, strength=strength, by=by,
                                 target_g=total, segments=segs, started=time.time())
        report = []
        try:
            self._wait_for_glass(glass_timeout)
            self.scale.tare()
            done_g = 0.0
            for idx, s in enumerate(steps):
                self._emit(state=State.POURING, step=s.bottle, step_index=idx, color=s.color,
                           message=f"Pouring {s.bottle}")
                got = self._pour_one(s.slot, s.grams, done_g, total)
                ml = got / (self.inv.find(s.bottle).density or 1.0)
                report.append({"bottle": s.bottle, "target_g": round(s.grams, 1),
                               "poured_g": round(got, 1), "ml": round(ml, 1)})
                self.inv.use(s.bottle, ml)
                done_g += got
            self.halo.flash((120, 255, 160))
            self._emit(state=State.DONE, fraction=1.0, poured_g=done_g, report=report,
                       step="", step_index=len(steps), speed=0.0,
                       message=f"Enjoy. {recipe.garnish.capitalize() + ' to finish.' if recipe.garnish else ''}".strip())
        except _Cancelled:
            self._emit(state=State.CANCELLED, message="Cancelled", report=report, speed=0.0)
        except PourError as e:
            self.halo.flash((255, 40, 40))
            self._emit(state=State.ERROR, message=str(e), report=report, speed=0.0)
        finally:
            self.pumps.all_off()
            try:                                   # partial pours still used liquid

                self.inv.save()
            except OSError:
                pass
            if self.history is not None:
                self.history.add({"drink": recipe.name, "size": size, "strength": strength, "by": by,
                                  "state": self.progress.state.value, "report": report})

    def _check_cancel(self):
        if self._cancel.is_set():
            raise _Cancelled()

    def _wait_for_glass(self, timeout):
        self._emit(state=State.WAITING_FOR_GLASS, message="Place a glass on the halo")
        start = time.monotonic()
        while True:
            self._check_cancel()
            g = self.scale.grams()
            if g > self.cfg["max_glass_g"]:
                raise PourError("Something heavy is on the stage — is the glass already full?")
            if g >= self.cfg["glass_min_g"]:
                time.sleep(0.6)                                   # let it settle
                return
            if time.monotonic() - start > timeout:
                raise PourError("No glass detected")
            self.halo.breathe(time.monotonic())
            time.sleep(TICK)

    def speed_for(self, remaining_g: float) -> float:
        """Feature 34: full speed until the slow zone, then ease down to min_speed."""
        zone = self.cfg.get("slow_zone_g", 0) or 0
        if zone <= 0 or remaining_g >= zone:
            return 1.0
        lo = self.cfg.get("min_speed", 0.35)
        return lo + (1.0 - lo) * max(0.0, remaining_g) / zone

    def _pour_one(self, slot, target, done_before, total) -> float:
        cfg = self.cfg
        base = self.scale.grams()
        stop_at = max(0.0, target - cfg["lead_g"])
        start = last_gain_t = time.monotonic()
        best = 0.0
        speed = 1.0
        self.pumps.on(slot, speed)
        try:
            while True:
                self._check_cancel()
                g = self.scale.grams()
                got = g - base
                if g < -cfg["glass_min_g"] * 0.5:                 # glass lifted
                    self.pumps.off(slot)
                    self._paused(base + got)
                    self.pumps.on(slot, speed)
                    last_gain_t = time.monotonic()
                    continue
                if got > best + cfg["stall_min_gain_g"]:
                    best, last_gain_t = got, time.monotonic()
                now = time.monotonic()
                stall_limit = cfg["stall_seconds"] / max(speed, 0.2)   # slow pumps get more time
                if now - last_gain_t > stall_limit:
                    raise PourError(f"Slot {slot} stopped flowing — the bottle may be empty")
                if now - start > cfg["max_seconds_per_ingredient"]:
                    raise PourError(f"Slot {slot} took too long")
                new_speed = self.speed_for(stop_at - got)
                if abs(new_speed - speed) > 0.04:
                    speed = new_speed
                    self.pumps.on(slot, speed)
                poured = done_before + max(got, 0)
                frac = poured / total
                self.halo.segments(self._rgb_segs, poured)
                self._emit(fraction=frac, poured_g=poured, speed=speed)
                if got >= stop_at:
                    break
                time.sleep(TICK)
        finally:
            self.pumps.off(slot)
        time.sleep(0.4)                                            # drips land
        return self.scale.grams() - base

    def _paused(self, expected_g):
        self._emit(state=State.PAUSED, message="Put the glass back to keep pouring")
        while True:
            self._check_cancel()
            g = self.scale.grams()
            if abs(g - expected_g) < self.cfg["glass_min_g"] * 0.5:
                time.sleep(0.6)
                self._emit(state=State.POURING, message="Pouring")
                return
            self.halo.breathe(time.monotonic(), (255, 90, 60))
            time.sleep(TICK)


class _Cancelled(Exception):
    pass
