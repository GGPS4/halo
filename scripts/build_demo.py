"""Build halo/web/demo.js (the in-browser demo backend) from the sample bottles and drinks.

    python scripts/build_demo.py

Run it again after changing scripts/demo_template.js or the sample data.
"""
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(ROOT))
from halo.bartender import MOODS  # noqa: E402
from halo.config import PREF_DEFAULTS  # noqa: E402
from halo.recipes import STRENGTH, Recipe  # noqa: E402

# A half-used shelf looks more like a real one, and Lime Juice shows the low-bottle warning.
LEVELS = {"Gin": 0.64, "White Rum": 0.81, "Bourbon": 0.47, "Tequila Blanco": 0.9,
          "Orange Liqueur": 0.72, "Lime Juice": 0.11, "Simple Syrup": 0.55, "Soda Water": 0.38}

EXTRA = [
    {"name": "Ember Old Fashioned", "created_by": "claude", "favorite": True,
     "ingredients": [{"bottle": "Bourbon", "parts": 2}, {"bottle": "Simple Syrup", "parts": 0.25},
                     {"bottle": "Orange Liqueur", "parts": 0.25}],
     "description": "Bourbon softened with a little orange liqueur in place of bitters. Warm, round and slow.",
     "garnish": "orange peel", "glass": "rocks", "color": "#c47a2c", "tags": ["strong", "classic"]},
    {"name": "Paloma Spritz", "created_by": "claude",
     "ingredients": [{"bottle": "Tequila Blanco", "parts": 1.5}, {"bottle": "Lime Juice", "parts": 0.5},
                     {"bottle": "Simple Syrup", "parts": 0.4}, {"bottle": "Soda Water", "parts": 3},
                     {"bottle": "Grapefruit Soda", "parts": 1}],
     "description": "Tequila and lime lengthened with soda. Load grapefruit soda to make it.",
     "garnish": "salt rim", "glass": "highball", "color": "#f4c7b0", "tags": ["refreshing"]},
]
HISTORY = [("Daiquiri", 3), ("Margarita", 5), ("Garden Highball", 2), ("Ember Old Fashioned", 4),
           ("Daiquiri", 1.2), ("Rum Fizz", 6), ("Margarita", 0.4)]


def main():
    bottles = []
    for d in json.loads((ROOT / "scripts/sample_bottles.json").read_text()):
        cap = d.get("capacity_ml", 750.0)
        lvl = LEVELS.get(d["name"], 1.0)
        bottles.append({"abv": d.get("abv", 0), "capacity_ml": cap, "color": d.get("color", "#c9a46a"),
                        "density": d.get("density", 1.0), "level": lvl, "name": d["name"],
                        "remaining_ml": round(cap * lvl), "slot": d["slot"]})
    recipes = [Recipe.from_dict(d).to_dict() for d in json.loads((ROOT / "scripts/sample_recipes.json").read_text())]
    for r in recipes:
        r["favorite"] = r["name"] == "Daiquiri"
    recipes += [Recipe.from_dict(d).to_dict() for d in EXTRA]

    dens = {b["name"]: b["density"] for b in bottles}
    by_name = {r["name"]: r for r in recipes}
    now = int(time.time())
    history = []
    for name, days in HISTORY:
        r = by_name[name]
        parts = sum(i["parts"] for i in r["ingredients"])
        rep = []
        for i in r["ingredients"]:
            ml = 59 * i["parts"] / parts
            g = round(ml * dens[i["bottle"]], 1)
            rep.append({"bottle": i["bottle"], "ml": round(ml, 1), "target_g": g, "poured_g": g})
        history.append({"time": now - int(days * 86400), "drink": name, "size": "single", "strength": "normal",
                        "by": "phone", "state": "done", "report": rep})
    history.sort(key=lambda h: h["time"])

    seed = {"bottles": bottles, "recipes": recipes, "history": history, "prefs": dict(PREF_DEFAULTS),
            "moods": list(MOODS), "strengths": list(STRENGTH)}
    js = (ROOT / "scripts/demo_template.js").read_text().replace("__SEED__", json.dumps(seed, separators=(",", ":")))
    (ROOT / "halo/web/demo.js").write_text(js)
    print(f"wrote halo/web/demo.js ({len(js) // 1024} KB)")


if __name__ == "__main__":
    main()
