"""Run Halo:  python -m halo            (touchscreen + web server)
             python -m halo --no-ui    (web server only, e.g. for testing)
Set HALO_MOCK=1 to run without the Pi hardware."""
from __future__ import annotations

import argparse
import os
import threading
from pathlib import Path

from . import hardware
from .bartender import Bartender
from .config import ROOT, Inventory, load_settings
from .pour import PourEngine
from .recipes import RecipeBook


def _load_dotenv(path: Path):
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


class App:
    def __init__(self, settings):
        self.settings = settings
        self.inventory = Inventory.load()
        self.book = RecipeBook()
        self.pumps, self.scale, self.halo = hardware.build(settings)
        self.scale.tare()
        self.engine = PourEngine(self.pumps, self.scale, self.halo, settings, self.inventory)
        self.bartender = Bartender(settings)


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
