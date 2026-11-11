"""Settings and bottle inventory.

Settings live in config.toml (read-only at runtime). The bottle inventory lives in
bottles.json because it changes whenever you swap a bottle, and the web page writes it.
"""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.environ.get("HALO_HOME", Path(__file__).resolve().parent.parent))

DEFAULTS = {
    "hardware": {
        "mock": False,
        "pump_pins": [27, 22, 23, 24, 25, 5, 6, 13],   # Halo Driver Board v1
        "hx711_dt": 4,
        "hx711_sck": 17,
        "scale_factor": 420.0,   # raw counts per gram, set by `python -m halo.calibrate`
        "led_count": 24,
        "led_brightness": 0.4,
    },
    "pour": {
        "glass_min_g": 60.0,      # anything lighter than this isn't a glass
        "max_glass_g": 900.0,
        "stall_seconds": 2.0,     # no weight gain for this long = empty bottle
        "stall_min_gain_g": 1.0,
        "lead_g": 2.5,            # stop this early; liquid in the tube still lands
        "max_seconds_per_ingredient": 60.0,
        "ml_per_oz": 29.57,
        "single_oz": 2.0,         # total liquor for a "single"
        "double_oz": 4.0,
    },
    "bartender": {
        "model": "claude-haiku-5-5",
        "max_tokens": 1200,
    },
    "server": {"host": "0.0.0.0", "port": 8080},
    "ui": {"fullscreen": True, "size": 1080},
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_settings(path: Path | None = None) -> dict:
    path = path or ROOT / "config.toml"
    settings = DEFAULTS
    if path.exists():
        with open(path, "rb") as f:
            settings = _merge(DEFAULTS, tomllib.load(f))
    if os.environ.get("HALO_MOCK") == "1":
        settings["hardware"]["mock"] = True
    return settings


@dataclass
class Bottle:
    slot: int                 # 1..8, matches the pump number
    name: str = ""            # "" = empty slot
    abv: float = 0.0          # percent
    density: float = 1.0      # g/ml; spirits ~0.95, syrups ~1.3
    color: str = "#c9a46a"    # used for the ingredient arcs on screen

    @property
    def loaded(self) -> bool:
        return bool(self.name.strip())


@dataclass
class Inventory:
    bottles: list[Bottle] = field(default_factory=list)
    path: Path = ROOT / "bottles.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "Inventory":
        path = path or ROOT / "bottles.json"
        bottles = [Bottle(slot=i) for i in range(1, 9)]
        if path.exists():
            for item in json.loads(path.read_text()):
                b = Bottle(**item)
                if 1 <= b.slot <= 8:
                    bottles[b.slot - 1] = b
        return cls(bottles=bottles, path=path)

    def save(self) -> None:
        self.path.write_text(json.dumps([b.__dict__ for b in self.bottles], indent=2))

    def loaded(self) -> list[Bottle]:
        return [b for b in self.bottles if b.loaded]

    def find(self, name: str) -> Bottle | None:
        key = name.strip().lower()
        for b in self.loaded():
            if b.name.lower() == key:
                return b
        return None
