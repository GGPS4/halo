"""The pour engine: one pump at a time, each stopped by weight, not by a timer."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .config import Inventory
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
    step: str = ""
    fraction: float = 0.0
    poured_g: float = 0.0
    target_g: float = 0.0
    message: str = ""
    report: list[dict] = field(default_factory=list)


class PourError(Exception):
    pass


class PourEngine:
    def __init__(self, pumps, scale, halo, settings: dict, inventory: Inventory):
        self.pumps, self.scale, self.halo = pumps, scale, halo
        self.cfg = settings["pour"]
        self.inv = inventory
        self.progress = Progress()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.listeners: list[Callable[[Progress], None]] = []

    # ------------------------------------------------------------ public
    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def size_ml(self, size: str) -> float:
        oz = self.cfg["double_oz"] if size == "double" else self.cfg["single_oz"]
        return oz * self.cfg["ml_per_oz"]

    def start(self, recipe: Recipe, size: str = "single", glass_timeout: float = 60.0) -> None:
        if self.busy:
            raise PourError("Already pouring")
        steps = recipe.plan(self.inv, self.size_ml(size))      # raises if a bottle is missing
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run, args=(recipe, steps, glass_timeout), daemon=True
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

    def _run(self, recipe, steps, glass_timeout):
        total = sum(g for _, _, g in steps)
        self.progress = Progress(drink=recipe.name, target_g=total)
        try:
            self._wait_for_glass(glass_timeout)
            self.scale.tare()
            done_g = 0.0
            report = []
            for slot, name, target in steps:
                self._emit(state=State.POURING, step=name, message=f"Pouring {name}")
                got = self._pour_one(slot, target, done_g, total)
                report.append({"bottle": name, "target_g": round(target, 1), "poured_g": round(got, 1)})
                done_g += got
            self.halo.flash((120, 255, 160))
            self._emit(state=State.DONE, fraction=1.0, poured_g=done_g, report=report,
                       message="Enjoy.")
        except _Cancelled:
            self._emit(state=State.CANCELLED, message="Cancelled")
        except PourError as e:
            self.halo.flash((255, 40, 40))
            self._emit(state=State.ERROR, message=str(e))
        finally:
            self.pumps.all_off()

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

    def _pour_one(self, slot, target, done_before, total) -> float:
        cfg = self.cfg
        base = self.scale.grams()
        stop_at = max(0.0, target - cfg["lead_g"])
        start = last_gain_t = time.monotonic()
        best = 0.0
        self.pumps.on(slot)
        try:
            while True:
                self._check_cancel()
                g = self.scale.grams()
                got = g - base
                if g < -cfg["glass_min_g"] * 0.5:                 # glass lifted
                    self.pumps.off(slot)
                    self._paused(base + got)
                    self.pumps.on(slot)
                    last_gain_t = time.monotonic()
                    continue
                if got > best + cfg["stall_min_gain_g"]:
                    best, last_gain_t = got, time.monotonic()
                now = time.monotonic()
                if now - last_gain_t > cfg["stall_seconds"]:
                    raise PourError(f"Slot {slot} stopped flowing — the bottle may be empty")
                if now - start > cfg["max_seconds_per_ingredient"]:
                    raise PourError(f"Slot {slot} took too long")
                frac = (done_before + max(got, 0)) / total
                self.halo.progress(frac)
                self._emit(fraction=frac, poured_g=done_before + max(got, 0))
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
