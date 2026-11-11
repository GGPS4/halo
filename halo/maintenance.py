"""Step-by-step maintenance jobs run from the web app: self-clean and the setup check.

Each job runs in its own thread and talks to the person through prompts: it sets a prompt
(a title, some text, buttons and maybe an input), waits for an answer from the web app,
then runs the pumps and the scale. Pouring is locked while a job runs, and the LED ring
shows the job's progress.

Self-clean
  The pumps each draw from their own bottle, so cleaning works on the dip tubes: all 8 go
  into a container of warm soapy water, then into clean water, then out into the air. Every
  line, pump, tube and the nozzle tips are flushed into a catch cup on the stage, and Halo
  asks for the cup to be emptied before it can overflow the scale. It finishes by priming
  the lines again once the bottles are back.

Setup check (feature 10)
  Empty-stage zero, a scale check against something of known weight (with a one-tap fix),
  a light test, then each loaded pump is primed until liquid reaches the cup and timed to
  measure its flow, with a plain-language fix for anything that fails.
"""
from __future__ import annotations

import threading
import time

TICK = 0.05
BLUE = (110, 190, 255)


class MaintenanceError(Exception):
    pass


class _Cancelled(Exception):
    pass


class Maintenance:
    TITLES = {"clean": "Cleaning", "setup": "Setup check"}

    def __init__(self, app):
        self.app = app
        cfg = app.settings.get("maintenance", {})
        self.cfg = {
            "catch_max_g": 650.0,      # ask for the catch cup to be emptied past this
            "soap_g": 30.0,            # per line
            "rinse_g": 40.0,           # per line, per rinse
            "rinses": 2,
            "soak_s": 60.0,
            "purge_s": 6.0,
            "prime_g": 3.0,            # liquid at the nozzle
            "prime_max_s": 20.0,
            "flow_s": 3.0,
            **cfg,
        }
        self._lock = threading.Lock()
        self._go = threading.Event()
        self._cancel = threading.Event()
        self._answer: tuple[str, dict] = ("", {})
        self._thread: threading.Thread | None = None
        self.ring_color: tuple | None = None
        self.status = self._idle()

    # ------------------------------------------------------------ public
    @staticmethod
    def _idle() -> dict:
        return {"kind": "", "title": "", "state": "idle", "prompt": None, "step": "",
                "fraction": 0.0, "results": [], "message": ""}

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, kind: str) -> dict:
        if kind not in self.TITLES:
            raise MaintenanceError("Unknown job")
        if self.active:
            raise MaintenanceError(f"{self.status['title']} is already running")
        engine = self.app.engine
        if engine.busy:
            raise MaintenanceError("Halo is pouring. Wait for the drink to finish.")
        if engine.waiting_pickup:
            raise MaintenanceError("Take the finished drink off the halo first.")
        self._cancel.clear()
        self._go.clear()
        self.status = {**self._idle(), "kind": kind, "title": self.TITLES[kind], "state": "running"}
        engine.locked_by = self.TITLES[kind].lower()
        target = self._clean if kind == "clean" else self._setup
        self._thread = threading.Thread(target=self._run, args=(target,), daemon=True)
        self._thread.start()
        return self.to_dict()

    def answer(self, action: str, data: dict | None = None) -> dict:
        if not self.active or self.status["state"] != "waiting":
            raise MaintenanceError("Nothing is waiting for an answer")
        self._answer = (action, data or {})
        self._go.set()
        return self.to_dict()

    def cancel(self) -> dict:
        self._cancel.set()
        self._go.set()
        self.app.pumps.all_off()
        return self.to_dict()

    def wait(self, timeout: float | None = None) -> dict:
        if self._thread:
            self._thread.join(timeout)
        return self.to_dict()

    def to_dict(self) -> dict:
        with self._lock:
            return {**self.status, "results": list(self.status["results"]), "active": self.active}

    def draw_ring(self, t: float):
        """Called by the ring loop while a job runs."""
        halo = self.app.halo
        if self.ring_color is not None:
            return halo.flash(self.ring_color)
        if self.status["state"] == "waiting":
            return halo.breathe(t, BLUE)
        return halo.progress(self.status["fraction"], BLUE)

    # ------------------------------------------------------------ plumbing
    def _set(self, **kw):
        with self._lock:
            self.status.update(kw)

    def _result(self, label: str, ok: bool, detail: str = ""):
        with self._lock:
            self.status["results"].append({"label": label, "ok": ok, "detail": detail})

    def _check(self):
        if self._cancel.is_set():
            raise _Cancelled()

    def _ask(self, title: str, text: str, actions=(("next", "Next"),), fields=None) -> tuple[str, dict]:
        self._go.clear()
        self._set(state="waiting", prompt={"title": title, "text": text,
                                           "actions": [{"id": a, "label": l} for a, l in actions],
                                           "fields": fields or []})
        while not self._go.wait(0.2):
            self._check()
        self._check()
        self._set(state="running", prompt=None)
        return self._answer

    def _sleep(self, seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._check()
            time.sleep(TICK)

    def _grams(self, samples: int = 3) -> float:
        return self.app.scale.grams(samples)

    def _need_cup(self, title: str, text: str):
        """Ask for a cup on the stage until the scale sees one, then zero on it."""
        while True:
            self._ask(title, text)
            if self._grams() >= 15:
                self._sleep(0.4)
                self.app.scale.tare()
                return
            text = "Halo doesn't see anything on the stage yet. Set the cup down, then tap Next."

    def _room_in_catch(self, need_g: float):
        if self._grams() + need_g <= self.cfg["catch_max_g"]:
            return
        self.app.pumps.all_off()
        self._ask("Empty the catch cup",
                  "The catch cup is getting full. Pour it out, put it back on the halo, then tap Next.")
        self._sleep(0.4)
        self.app.scale.tare()

    def _pump(self, slot: int, grams: float | None, max_s: float, speed: float = 1.0) -> float:
        """Run one pump until `grams` land in the cup (or for max_s if grams is None)."""
        pumps = self.app.pumps
        self._room_in_catch(grams or 25.0)
        base = self._grams()
        start = time.monotonic()
        pumps.on(slot, speed)
        try:
            while time.monotonic() - start < max_s:
                self._check()
                if grams is not None and self._grams() - base >= grams:
                    break
                time.sleep(TICK)
        finally:
            pumps.off(slot)
        time.sleep(0.3)
        return self._grams() - base

    def _run(self, job):
        engine = self.app.engine
        try:
            job()
        except _Cancelled:
            self.app.pumps.all_off()
            self._set(state="cancelled", prompt=None, message="Stopped. Lift the dip tubes back into "
                      "their bottles before pouring." if self.status["kind"] == "clean" else "Stopped.")
        except Exception as e:  # noqa: BLE001 - shown to the person, never crashes Halo
            self.app.pumps.all_off()
            self._set(state="error", prompt=None, message=str(e))
        finally:
            self.ring_color = None
            engine.locked_by = ""
            engine._needs_clear = True       # a catch cup may still be there; next pour waits for a clear stage

    # ------------------------------------------------------------ self-clean
    def _clean(self):
        c = self.cfg
        slots = list(range(1, 9))
        self._need_cup("Set down a catch cup",
                       "Put a large cup or bowl (about a litre) on the halo to catch the cleaning water. "
                       "Halo will ask you to empty it when it fills.")
        self._ask("Soapy water",
                  "Take the bottles out of the drum. Fill a container with warm water and a few drops of "
                  "dish soap, and put all 8 dip tubes in it.", (("next", "Start cleaning"),))
        total = len(slots) * (2 + c["rinses"]) + 1
        done = 0

        def tick(step):
            nonlocal done
            done += 1
            self._set(step=step, fraction=done / total)

        weak = []
        for s in slots:
            self._set(step=f"Soap through line {s}")
            moved = self._pump(s, c["soap_g"], 30.0)
            if moved < 5:
                weak.append(s)
            tick(f"Soap through line {s}")
        self._result("Soap pass", not weak,
                     f"Lines {', '.join(map(str, weak))} barely moved any water. Check their dip tubes are "
                     "in the soapy water; if they are, the line may be clogged." if weak else "All 8 lines flowed.")
        self._set(step="Letting the soap sit")
        end = time.monotonic() + c["soak_s"]
        while time.monotonic() < end:
            self._check()
            self._set(message=f"Letting the soap work: {int(end - time.monotonic())} s")
            time.sleep(0.25)
        self._set(message="")
        tick("Soak")

        self._ask("Clean water", "Move all 8 dip tubes into a container of clean warm water.",
                  (("next", "Rinse"),))
        for r in range(c["rinses"]):
            for s in slots:
                self._set(step=f"Rinse {r + 1} of {c['rinses']}: line {s}")
                self._pump(s, c["rinse_g"], 30.0)
                tick(f"Rinse line {s}")
        self._result("Rinse", True, f"Each line rinsed {c['rinses']} times.")

        self._ask("Dry the lines", "Lift the dip tubes out of the water so they hang in the air.",
                  (("next", "Dry"),))
        for s in slots:
            self._set(step=f"Drying line {s}")
            self._pump(s, None, c["purge_s"])
            tick(f"Dry line {s}")
        self._result("Lines dried", True, "Air pushed the water out of every line.")
        self.app.prefs.update({"last_clean": time.time()})

        action, _ = self._ask("Put the bottles back",
                              "Empty the catch cup and set it back down. Put the bottles back in the drum with "
                              "their dip tubes, then tap Prime so each line fills up to the nozzle.",
                              (("prime", "Prime the lines"), ("skip", "Skip")))
        if action == "prime":
            self.app.scale.tare()
            self._prime_all()
        self._set(state="done", step="", fraction=1.0, message="Halo is clean.")

    # ------------------------------------------------------------ setup check
    def _setup(self):
        self._ask("Clear the stage", "Take everything off the halo so it can zero the scale.")
        self._sleep(0.5)
        self.app.scale.tare()
        self._result("Scale zeroed", True)
        self._set(fraction=0.1)

        # scale accuracy against something of known weight
        action, data = self._ask(
            "Check the scale",
            "Put something with a known weight on the halo, like an unopened 12 oz can of soda "
            "(about 383 g for regular, 368 g for diet), type its weight, then tap Weigh.",
            (("weigh", "Weigh"), ("skip", "Skip")),
            [{"id": "known_g", "label": "Weight (g)", "value": 383}])
        if action == "weigh":
            try:
                known = float(data.get("known_g") or 0)
            except (TypeError, ValueError):
                known = 0.0
            self._sleep(0.8)
            read = sum(self._grams(5) for _ in range(3)) / 3
            err = (read - known) / known * 100 if known > 0 else 0.0
            if known <= 0:
                self._result("Scale", False, "No weight entered, so the scale wasn't checked.")
            elif abs(err) <= 2.0:
                self._result("Scale", True, f"Read {read:.0f} g for {known:.0f} g ({err:+.1f}%).")
            else:
                fix, _ = self._ask("Scale is off",
                                   f"Halo read {read:.0f} g for something that weighs {known:.0f} g ({err:+.1f}%). "
                                   "Fix it so it reads right?", (("fix", "Fix the scale"), ("skip", "Leave it")))
                scale = self.app.scale
                if fix == "fix" and hasattr(scale, "factor") and read > 0:
                    scale.factor = scale.factor * read / known
                    self.app.prefs.update({"scale_factor": scale.factor})
                    self._result("Scale", True, f"Was {err:+.1f}% off; corrected.")
                else:
                    self._result("Scale", False, f"Reads {err:+.1f}% off. Run the check again to fix it.")
        self._set(fraction=0.3)

        # lights
        self._set(step="Light test")
        for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)):
            self.ring_color = color
            self._sleep(0.8)
        self.ring_color = None
        action, _ = self._ask("Lights", "Did the ring under the cup and the light inside the gate both show red, "
                              "green, blue, then white?", (("yes", "Looks right"), ("no", "Something was off")))
        self._result("Lights", action == "yes", "" if action == "yes" else
                     "Check the LED ring's plug on J12, then the strip's DIN wire from the ring's DOUT. "
                     "If colors are swapped, the strip may be a different color order.")
        self._set(fraction=0.45)

        # pumps
        loaded = self.app.inventory.loaded()
        if not loaded:
            self._result("Pumps", False, "No bottles are set up yet. Name them on the Bottles page, then run "
                                         "the check again.")
        else:
            self._need_cup("Pumps", "Put a cup on the halo. Halo will run each loaded bottle until it reaches "
                                    "the cup, then time it.")
            self._prime_all(measure=True)
        self._set(state="done", step="", fraction=1.0,
                  message="All good." if all(r["ok"] for r in self.status["results"]) else
                  "Some checks need attention.")

    def _prime_all(self, measure: bool = False):
        c = self.cfg
        loaded = self.app.inventory.loaded()
        start_frac = self.status["fraction"]
        for i, b in enumerate(loaded):
            self._set(step=f"Priming {b.name} (pump {b.slot})")
            t0 = time.monotonic()
            got = self._pump(b.slot, c["prime_g"], c["prime_max_s"])
            if got < c["prime_g"] * 0.5:
                self._result(f"Pump {b.slot} ({b.name})", False,
                             f"Nothing reached the cup in {c['prime_max_s']:.0f} s. Check the dip tube reaches the "
                             "bottom of the bottle and the tubing is on the pump. If the pump runs but nothing "
                             "comes out, swap its two wires on the board so it turns the other way.")
                continue
            used = got
            if measure:
                self._set(step=f"Timing {b.name}")
                moved = self._pump(b.slot, None, c["flow_s"])
                used += moved
                ml_min = moved / (b.density or 1.0) / c["flow_s"] * 60
                ok = ml_min >= 40
                self._result(f"Pump {b.slot} ({b.name})", ok,
                             f"Primed in {time.monotonic() - t0:.0f} s, pours {ml_min:.0f} ml/min." +
                             ("" if ok else " That's slow: check the tubing for kinks, or it may be worn."))
            else:
                self._result(f"Primed {b.name}", True)
            self.app.inventory.use(b.name, used / (b.density or 1.0))
            self._set(fraction=start_frac + (1 - start_frac) * (i + 1) / len(loaded))
        try:
            self.app.inventory.save()
        except OSError:
            pass
