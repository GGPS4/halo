"""Run Halo:  python -m halo            (touchscreen + web app)
             python -m halo --no-ui    (web app only, e.g. for testing)
Set HALO_MOCK=1 to run without the Pi hardware."""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

from . import hardware
from .bartender import Bartender
from .config import ROOT, History, Inventory, Prefs, PumpLearning, load_settings
from .pour import PourEngine
from .recipes import RecipeBook


def _load_dotenv(path: Path):
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


class Party:
    """Feature 8: an active themed menu narrows the drinks shown on the touchscreen."""

    def __init__(self, path: Path | None = None):
        self.path = path or ROOT / "party.json"
        self.data = {"active": False, "theme": "", "drinks": []}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except json.JSONDecodeError:
                pass

    def set(self, theme: str, drinks: list[str]):
        self.data = {"active": True, "theme": theme, "drinks": drinks}
        self.path.write_text(json.dumps(self.data))

    def end(self):
        self.data["active"] = False
        self.path.write_text(json.dumps(self.data))


class App:
    def __init__(self, settings):
        self.settings = settings
        self.prefs = Prefs()
        self.inventory = Inventory.load()
        self.book = RecipeBook()
        self.history = History()
        self.learning = PumpLearning()
        self.party = Party()
        self.pumps, self.scale, self.halo = hardware.build(settings)
        self.halo.set_brightness(self.prefs["led_brightness"])
        self.scale.tare()
        self.engine = PourEngine(self.pumps, self.scale, self.halo, settings, self.inventory,
                                 history=self.history, prefs=self.prefs, learning=self.learning)
        self.bartender = Bartender(settings)
        self.ring_mode = ("", 0.0)          # Levi can ask for "listening" / "thinking" for a few seconds
        self.last_activity = time.monotonic()

    def touch(self):
        self.last_activity = time.monotonic()

    def set_ring(self, mode: str, seconds: float = 8.0):
        self.ring_mode = (mode, time.monotonic() + seconds)
        self.touch()

    def idle_ring(self, t: float):
        """What the LED ring does when no drink is pouring."""
        mode, until = self.ring_mode
        if mode and time.monotonic() < until:
            if mode == "listening":
                return self.halo.listening(t)
            return self.halo.breathe(t, (160, 190, 255))
        if self.prefs["ambient"] and time.monotonic() - self.last_activity > 20:
            cols = [hardware.hex_rgb(b.color) for b in self.inventory.loaded()]
            return self.halo.ambient(t, cols)
        return self.halo.breathe(t, (90, 70, 50))

    def menu(self):
        """Drinks the touchscreen should offer: the party menu if one is on, else everything pourable."""
        drinks = self.book.available(self.inventory)
        if self.party.data["active"]:
            names = {n.lower() for n in self.party.data["drinks"]}
            picked = [r for r in drinks if r.name.lower() in names]
            if picked:
                return picked
        return sorted(drinks, key=lambda r: (not r.favorite, r.name.lower()))


def ring_loop(app: App, stop: threading.Event):
    """Drives idle animations when there's no touchscreen UI (e.g. --no-ui)."""
    while not stop.is_set():
        if not app.engine.busy:
            app.idle_ring(time.monotonic())
        time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser(prog="halo")
    ap.add_argument("--no-ui", action="store_true")
    ap.add_argument("--windowed", action="store_true")
    args = ap.parse_args()

    _load_dotenv(ROOT / ".env")
    settings = load_settings()
    app = App(settings)

    from .server import create_app

    web = create_app(app)
    srv = settings["server"]
    kwargs = dict(host=srv["host"], port=srv["port"], threaded=True, use_reloader=False)
    if args.no_ui:
        stop = threading.Event()
        threading.Thread(target=ring_loop, args=(app, stop), daemon=True).start()
        web.run(**kwargs)
        return
    threading.Thread(target=web.run, kwargs=kwargs, daemon=True).start()

    from .ui import UI

    try:
        UI(app, settings["ui"]["size"], settings["ui"]["fullscreen"] and not args.windowed).run()
    finally:
        app.pumps.all_off()
        app.halo.off()


if __name__ == "__main__":
    main()
