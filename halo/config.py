"""Settings, preferences, bottle inventory and pour history.

- config.toml   hardware and pour settings you set once (read-only at runtime)
- prefs.json    things you change from the web app (brightness, ambient mode, sizes...)
- bottles.json  what is in each of the 8 slots and how full it is
- history.json  every pour, for bottle levels and the stats page
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import tomllib
from dataclasses import asdict, dataclass, field, fields
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
        "lead_g": 1.5,            # stop this early; liquid in the tube still lands
        "slow_zone_g": 8.0,       # start slowing the pump this far from the target
        "min_speed": 0.35,        # slowest pump speed in the slow zone (0..1)
        "max_seconds_per_ingredient": 60.0,
        "ml_per_oz": 29.57,
        "single_oz": 2.0,
        "double_oz": 4.0,
    },
    "bartender": {
        "model": "claude-haiku-5-5",
        "max_tokens": 1500,
    },
    "server": {"host": "0.0.0.0", "port": 8080},
    "ui": {"fullscreen": True, "size": 1080, "ambient_after_s": 90},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_settings(path: Path | None = None) -> dict:
    path = path or ROOT / "config.toml"
    settings = copy.deepcopy(DEFAULTS)
    if path.exists():
        with open(path, "rb") as f:
            settings = _merge(DEFAULTS, tomllib.load(f))
    if os.environ.get("HALO_MOCK") == "1":
        settings["hardware"]["mock"] = True
    return settings


def _known(cls, data: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


# --------------------------------------------------------------------------- prefs
PREF_DEFAULTS = {
    "led_brightness": 0.4,
    "ambient": True,             # idle glow in the loaded bottles' colors (feature 15)
    "default_strength": "normal",
    "single_oz": None,           # None = use config.toml
    "double_oz": None,
    "low_level_pct": 15,         # warn when a bottle drops below this (feature 4)
    "voice_enabled": True,       # accept orders from Levi (feature 14)
}


class Prefs:
    def __init__(self, path: Path | None = None):
        self.path = path or ROOT / "prefs.json"
        self.data = dict(PREF_DEFAULTS)
        if self.path.exists():
            try:
                self.data.update({k: v for k, v in json.loads(self.path.read_text()).items() if k in PREF_DEFAULTS})
            except json.JSONDecodeError:
                pass

    def __getitem__(self, key):
        return self.data[key]

    def update(self, changes: dict) -> dict:
        for k, v in changes.items():
            if k in PREF_DEFAULTS:
                self.data[k] = v
        self.path.write_text(json.dumps(self.data, indent=2))
        return self.data


# --------------------------------------------------------------------------- bottles
@dataclass
class Bottle:
    slot: int                   # 1..8, matches the pump number
    name: str = ""              # "" = empty slot
    abv: float = 0.0            # percent
    density: float = 1.0        # g/ml; spirits ~0.95, syrups ~1.3
    color: str = "#c9a46a"      # used for ingredient arcs on screen and on the LED ring
    capacity_ml: float = 750.0
    remaining_ml: float = 750.0

    @property
    def loaded(self) -> bool:
        return bool(self.name.strip())

    @property
    def level(self) -> float:
        return max(0.0, min(1.0, self.remaining_ml / self.capacity_ml)) if self.capacity_ml else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["level"] = round(self.level, 3)
        return d


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
                b = Bottle(**_known(Bottle, item))
                if 1 <= b.slot <= 8:
                    bottles[b.slot - 1] = b
        return cls(bottles=bottles, path=path)

    def save(self) -> None:
        self.path.write_text(json.dumps([asdict(b) for b in self.bottles], indent=2))

    def loaded(self) -> list[Bottle]:
        return [b for b in self.bottles if b.loaded]

    def find(self, name: str) -> Bottle | None:
        key = name.strip().lower()
        for b in self.loaded():
            if b.name.lower() == key:
                return b
        return None

    def low(self, pct: float) -> list[Bottle]:
        return [b for b in self.loaded() if b.level * 100 < pct]

    def use(self, name: str, ml: float) -> None:
        b = self.find(name)
        if b:
            b.remaining_ml = max(0.0, b.remaining_ml - ml)

    def swap(self, a: int, b: int) -> None:
        ba, bb = self.bottles[a - 1], self.bottles[b - 1]
        ba.slot, bb.slot = b, a
        self.bottles[a - 1], self.bottles[b - 1] = bb, ba


# --------------------------------------------------------------------------- history
class History:
    def __init__(self, path: Path | None = None):
        self.path = path or ROOT / "history.json"
        self._lock = threading.Lock()
        self.items: list[dict] = []
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                self.items = []

    def add(self, entry: dict) -> None:
        with self._lock:
            self.items.append({"time": time.time(), **entry})
            self.items = self.items[-2000:]
            self.path.write_text(json.dumps(self.items))

    def stats(self) -> dict:
        drinks: dict[str, int] = {}
        bottles: dict[str, float] = {}
        total_ml = 0.0
        for h in self.items:
            if h.get("state") != "done":
                continue
            drinks[h["drink"]] = drinks.get(h["drink"], 0) + 1
            for r in h.get("report", []):
                bottles[r["bottle"]] = bottles.get(r["bottle"], 0) + r.get("ml", 0)
                total_ml += r.get("ml", 0)
        week = time.time() - 7 * 86400
        return {
            "pours": sum(drinks.values()),
            "this_week": sum(1 for h in self.items if h.get("state") == "done" and h["time"] > week),
            "total_ml": round(total_ml),
            "top_drinks": sorted(drinks.items(), key=lambda kv: -kv[1])[:8],
            "by_bottle": sorted(((k, round(v)) for k, v in bottles.items()), key=lambda kv: -kv[1]),
            "recent": list(reversed(self.items[-12:])),
        }
