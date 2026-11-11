"""The pour engine: one pump at a time, each stopped by weight, not by a timer.

Features layered on top of the basic weigh-and-stop loop:
  2  strength (light / normal / strong) changes the spirit ratio, see Recipe.plan
  4  every pour is subtracted from the bottle levels and logged to history
  28 the LED ring and the screen show each ingredient's share in its own color
  34 the pump slows down over the last few grams so it stops right on target
  1  each pump learns its flow and how much lands after it stops (config.PumpLearning)
  3  a finished drink keeps the ring lit in its color until the cup is lifted, and the next
     pour won't start into a drink that's still sitting there
  13 (thick liquids) syrups and slow pumps start slowing earlier and finish gentler; see profile()
  13 a weight guard stops the pump if weight jumps faster than any pump could pour, if the
     cup gets lighter mid-pour (tipped or spilled), or if a pump runs well past its target

Drinks are poured over a cup of ice and served as they are: a single is one standard drink
of alcohol, the mixers are sized around it, and nothing overflows the cup (see size_ml).
"""
from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from .config import Inventory
from .hardware import hex_rgb
from .recipes import STRENGTH, Recipe

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
    ready: bool = False          # finished drink still on the stage, waiting to be picked up
    segments: list[dict] = field(default_factory=list)   # [{bottle, color, target_g}]
    report: list[dict] = field(default_factory=list)
    started: float = 0.0

    def to_dict(self) -> dict:
        return {
            "state": self.state.value, "drink": self.drink, "size": self.size, "strength": self.strength,
            "by": self.by, "step": self.step, "step_index": self.step_index, "color": self.color,
            "fraction": round(self.fraction, 3), "poured_g": round(self.poured_g, 1), "ready": self.ready,
            "target_g": round(self.target_g, 1), "speed": round(self.speed, 2), "message": self.message,
            "segments": self.segments, "report": self.report,
        }


class PourError(Exception):
    pass


class PourEngine:
    def __init__(self, pumps, scale, halo, settings: dict, inventory: Inventory, history=None, prefs=None,
                 learning=None):
        self.pumps, self.scale, self.halo = pumps, scale, halo
        self.learning = learning
        self.cfg = settings["pour"]
        self.inv = inventory
        self.history = history
        self.prefs = prefs
        self.progress = Progress()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.listeners: list[Callable[[Progress], None]] = []
        self.ready_color: tuple | None = None        # set while a finished drink waits on the stage
        self.locked_by: str = ""                     # set while cleaning or the setup check runs
        self._needs_clear = False                    # a cup was left on the stage after a fault
        self._ready_lock = threading.Lock()

    # ------------------------------------------------------------ public
    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _setting(self, key):
        v = self.prefs[key] if self.prefs and key in self.prefs.data else None
        return float(v if v not in (None, "") else self.cfg[key])

    def size_ml(self, recipe: Recipe, size: str = "single", strength: str = "normal") -> float:
        """Total ml to pour. A single carries the alcohol of pour_oz of a 40% spirit (a double
        twice that), scaled by strength, and is capped at what fits in a cup of ice."""
        ethanol = self._setting("pour_oz") * self.cfg["ml_per_oz"] * 0.40
        ethanol *= (2.0 if size == "double" else 1.0) * STRENGTH.get(strength, 1.0)
        return recipe.total_ml(self.inv, ethanol, strength, self._setting("cup_ml"))

    def start(self, recipe: Recipe, size: str = "single", strength: str = "normal",
              glass_timeout: float = 60.0, by: str = "") -> None:
        if self.busy:
            raise PourError("Already pouring")
        if self.locked_by:
            raise PourError(f"Halo is busy with {self.locked_by}. Finish or cancel it first.")
        recipe.plan(self.inv, 1, strength)                             # raises if a bottle is missing
        steps = recipe.plan(self.inv, self.size_ml(recipe, size, strength), strength)
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
                got = self._pour_one(s.slot, s.grams, done_g, total, s.bottle)
                ml = got / (self.inv.find(s.bottle).density or 1.0)
                report.append({"bottle": s.bottle, "target_g": round(s.grams, 1),
                               "poured_g": round(got, 1), "ml": round(ml, 1)})
                self.inv.use(s.bottle, ml)
                done_g += got
            self.halo.flash((120, 255, 160))
            self._emit(state=State.DONE, fraction=1.0, poured_g=done_g, report=report,
                       step="", step_index=len(steps), speed=0.0, ready=True,
                       message=f"Ready. {recipe.garnish.capitalize() + ' if you like.' if recipe.garnish else ''}".strip())
            self._await_pickup(hex_rgb(recipe.color or (steps[-1].color if steps else "")))
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

    # ------------------------------------------------------------ feature 3: ready until picked up
    def _stage_g(self) -> float:
        """Weight on the stage relative to the last tare: the cup of the last drink, after a pour."""
        return self.scale.grams()

    def _await_pickup(self, color):
        """Keep the finished drink's glow on (the ring loop draws it) until the cup is lifted."""
        with self._ready_lock:
            self.ready_color = color
        threading.Thread(target=self._watch_pickup, daemon=True).start()

    def _watch_pickup(self):
        # after the pour the scale was tared with the cup on it, so lifting it reads strongly negative
        samples = collections.deque()                  # (time, grams) over the last 3 s
        while True:
            with self._ready_lock:
                if self.ready_color is None:          # a new pour took over
                    return
            try:
                g = self._stage_g()
            except Exception:
                time.sleep(0.15)
                continue
            if g < -self.cfg["glass_min_g"] * 0.5:
                break
            now = time.monotonic()
            samples.append((now, g))
            while samples and now - samples[0][0] > 3.0:
                samples.popleft()
            if self._still_rising(samples):              # feature 13: a pump is stuck on
                self.pumps.all_off()
                with self._ready_lock:
                    self.ready_color = None
                self._needs_clear = True
                self.halo.flash((255, 40, 40))
                self._emit(state=State.ERROR, ready=False, speed=0.0,
                           message="Liquid kept coming after the drink finished. Unplug Halo and check the "
                                   "driver board: a pump channel may be stuck on.")
                return
            time.sleep(0.15)
        time.sleep(0.4)
        try:
            self.scale.tare()                               # stage is empty: weigh from zero again
        except Exception:
            pass
        with self._ready_lock:
            if self.ready_color is None:
                return
            self.ready_color = None
        if self.progress.state == State.DONE:
            self._emit(ready=False, state=State.IDLE, message="")

    @staticmethod
    def _still_rising(samples) -> bool:
        """A steady climb over 3 s, every second of it, is a pump that won't stop. A garnish
        or a straw dropped in is a single step and doesn't count."""
        if len(samples) < 6 or samples[-1][0] - samples[0][0] < 2.8:
            return False
        t0 = samples[0][0]
        at = lambda k: min(samples, key=lambda s: abs(s[0] - (t0 + k)))[1]
        steps = [at(k + 1) - at(k) for k in range(3)]
        return sum(steps) > 3.0 and all(0.4 < d < 6.0 for d in steps)

    @property
    def waiting_pickup(self) -> bool:
        with self._ready_lock:
            return self.ready_color is not None

    def _wait_for_glass(self, timeout):
        if self.waiting_pickup:                         # the last drink is still on the stage
            self._emit(state=State.WAITING_FOR_GLASS, message="Take the finished drink off the halo first")
            start = time.monotonic()
            while self.waiting_pickup:
                self._check_cancel()
                if time.monotonic() - start > timeout:
                    raise PourError("The last drink is still on the halo")
                time.sleep(TICK)
        if self._needs_clear:                           # nobody is watching the stage; check it here
            self._emit(state=State.WAITING_FOR_GLASS, message="Take the cup off the halo first")
            start = time.monotonic()
            while self.scale.grams() > -self.cfg["glass_min_g"] * 0.5:
                self._check_cancel()
                if time.monotonic() - start > timeout:
                    raise PourError("The last cup is still on the halo")
                time.sleep(TICK)
            time.sleep(0.4)
            self.scale.tare()
            self._needs_clear = False
        self._emit(state=State.WAITING_FOR_GLASS, message="Place a cup of ice on the halo")
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

    def speed_for(self, remaining_g: float, profile: tuple[float, float] | None = None) -> float:
        """Feature 34: full speed until the slow zone, then ease down to min_speed."""
        zone, lo = profile or self.profile()
        if zone <= 0 or remaining_g >= zone:
            return 1.0
        return lo + (1.0 - lo) * max(0.0, remaining_g) / zone

    def profile(self, slot: int = 0, bottle: str = "") -> tuple[float, float]:
        """Feature 13 (thick liquids): (slow zone in grams, slowest speed) for this pump.

        Syrups and heavy liqueurs keep moving after the pump slows and drip longer, so they
        start slowing earlier and finish gentler. "Thick" comes from the bottle's density
        (1.15 g/ml and up) and, once the pump has poured a few times, from what it learned:
        a pump that runs much slower than its full-speed baseline for thin liquids, or that
        keeps missing its target, gets a longer, gentler finish too.
        """
        zone = float(self.cfg.get("slow_zone_g", 0) or 0)
        lo = float(self.cfg.get("min_speed", 0.35))
        b = self.inv.find(bottle) if bottle else None
        thick = 0.0                                          # 0 = water-like .. 1 = syrup
        if b and b.density >= 1.15:
            thick = min(1.0, (b.density - 1.15) / 0.15 + 0.5)
        e = self.learning.entry(slot, bottle) if (self.learning and bottle) else None
        if e and e.get("n", 0) >= 2:
            if e.get("flow_gps") and e["flow_gps"] < 0.8 * self.cfg.get("thin_flow_gps", 1.6):
                thick = max(thick, 0.6)
            if e.get("avg_error_g", 0) > 1.0:                # still landing off target: finish slower
                zone *= min(1.6, 1.0 + (e["avg_error_g"] - 1.0) * 0.4)
        zone *= 1.0 + 0.75 * thick
        lo = max(0.2, lo * (1.0 - 0.3 * thick))
        return zone, lo

    def _pour_one(self, slot, target, done_before, total, bottle="") -> float:
        cfg = self.cfg
        base = self.scale.grams()
        lead = self.learning.lead(slot, bottle, cfg["lead_g"]) if self.learning else cfg["lead_g"]
        stop_at = max(0.0, target - lead)
        start = last_gain_t = time.monotonic()
        best = 0.0
        speed = 1.0
        flow_from = flow_to = None          # (time, grams) while running flat out, for learning
        paused = False
        # feature 13: the fastest this pump could plausibly fill the cup
        learned = self.learning.entry(slot, bottle) if self.learning else None
        surge = cfg.get("surge_g_s", 8.0)
        if learned and learned.get("flow_gps"):
            surge = max(surge, 4.0 * learned["flow_gps"])
        window = collections.deque()        # (time, grams) over the last ~0.4 s
        profile = self.profile(slot, bottle)
        got = 0.0
        self.pumps.on(slot, speed)
        try:
            while True:
                self._check_cancel()
                g = self.scale.grams()
                got = g - base
                if g < -cfg["glass_min_g"] * 0.5:                 # glass lifted
                    self.pumps.off(slot)
                    self._paused(base + got)
                    paused = True
                    self.pumps.on(slot, speed)
                    last_gain_t = time.monotonic()
                    continue
                if got > best + cfg["stall_min_gain_g"]:
                    best, last_gain_t = got, time.monotonic()
                now = time.monotonic()
                self._guard(slot, now, got, best, target, surge, window)
                if speed >= 0.999 and got > 2.0:                   # liquid is landing; tube is full
                    if flow_from is None:
                        flow_from = (now, got)
                    else:
                        flow_to = (now, got)
                stall_limit = cfg["stall_seconds"] / max(speed, 0.2)   # slow pumps get more time
                if now - last_gain_t > stall_limit:
                    raise PourError(f"Slot {slot} stopped flowing — the bottle may be empty")
                if now - start > cfg["max_seconds_per_ingredient"]:
                    raise PourError(f"Slot {slot} took too long")
                new_speed = self.speed_for(stop_at - got, profile)
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
        got_at_stop = got
        time.sleep(cfg.get("settle_s", 0.6))                      # tube runs dry, drips land
        final = self.scale.grams() - base
        time.sleep(0.25)
        still = self.scale.grams() - base - final
        if still > 3.0:                                             # feature 13: pump didn't stop
            self.pumps.all_off()
            raise PourError(f"Slot {slot} is still pouring after Halo switched it off. Unplug Halo "
                            "and check that pump's channel on the driver board.")
        if self.learning and not paused and bottle:
            flow = None
            if flow_from and flow_to and flow_to[0] - flow_from[0] >= 0.5:
                flow = (flow_to[1] - flow_from[1]) / (flow_to[0] - flow_from[0])
            self.learning.record(slot, bottle, flow, final - got_at_stop, final - target)
        return final

    def _guard(self, slot, now, got, best, target, surge, window):
        """Feature 13: stop on weight the pump can't explain."""
        window.append((now, got))
        while window and now - window[0][0] > 0.4:
            window.popleft()
        if len(window) >= 3 and now - window[0][0] >= 0.25:
            rate = (got - window[0][1]) / (now - window[0][0])
            if rate > surge:
                raise PourError("Weight jumped faster than Halo can pour, so it stopped. "
                                "Is something pressing on the cup?")
        drop = best - got
        if best > 5 and drop > max(12.0, 0.3 * best):
            raise PourError("The cup got lighter mid-pour; it may have tipped or spilled. Stopped.")

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
