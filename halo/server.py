"""Halo's web app (phone / laptop) and the JSON API behind it.

The touchscreen, the web app and Levi all talk to the same API:

  GET  /api/state                     everything the web app needs in one call
  GET  /api/status                    live pour progress (polled during a pour)
  PUT  /api/bottles/<slot>            set what's in a slot
  POST /api/bottles/<slot>/refill     mark a slot full again
  POST /api/bottles/swap              {a, b} swap two slots
  GET/POST/DELETE /api/recipes...     drink library
  POST /api/recipes/<name>/favorite
  GET  /api/recipes/export  POST /api/recipes/import
  POST /api/bartender                 {request} -> a drink preview from Claude
  GET/POST/DELETE /api/party          themed party menu (feature 8)
  POST /api/pour  POST /api/cancel
  POST /api/voice                     {text} -> Levi voice orders (feature 14)
  POST /api/ring                      {mode: listening|thinking, seconds}
  GET  /api/stats   GET/PUT /api/prefs
"""
from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .bartender import MOODS, BartenderError
from .config import Bottle
from .pour import PourError
from .recipes import STRENGTH, Recipe

WEB = Path(__file__).parent / "web"


def create_app(halo) -> Flask:
    """`halo` is the App object from __main__ (inventory, book, engine, bartender, ...)."""
    app = Flask(__name__, static_folder=None)

    @app.before_request
    def _activity():
        if request.path.startswith("/api/") and request.method != "GET":
            halo.touch()

    # ------------------------------------------------------------ web app
    @app.get("/")
    def index():
        return send_from_directory(WEB, "index.html")

    @app.get("/<path:name>")
    def static_files(name):
        if name.startswith("api/"):
            return jsonify(error="not found"), 404
        return send_from_directory(WEB, name)

    # ------------------------------------------------------------ helpers
    def recipe_json(r: Recipe):
        inv = halo.inventory
        missing = r.missing(inv)
        d = r.to_dict()
        d["available"] = not missing
        d["missing"] = missing
        d["abv"] = round(r.abv(inv), 1) if not missing else None
        d["colors"] = [(inv.find(i.bottle).color if inv.find(i.bottle) else "#555") for i in r.ingredients]
        d["in_party"] = r.name.lower() in {n.lower() for n in halo.party.data["drinks"]} and halo.party.data["active"]
        return d

    def err(msg, code=400):
        return jsonify(error=msg), code

    # ------------------------------------------------------------ state
    @app.get("/api/state")
    def state():
        low = halo.prefs["low_level_pct"]
        return jsonify(
            bottles=[b.to_dict() for b in halo.inventory.bottles],
            low=[b.slot for b in halo.inventory.low(low)],
            recipes=[recipe_json(r) for r in halo.book.recipes],
            party=halo.party.data,
            prefs=halo.prefs.data,
            status=halo.engine.progress.to_dict(),
            moods=list(MOODS),
            strengths=list(STRENGTH),
            sizes={"single": round(halo.engine.size_ml("single")), "double": round(halo.engine.size_ml("double"))},
            ring=halo.halo.frame if hasattr(halo.halo, "frame") else None,
            mock=bool(halo.settings["hardware"]["mock"]),
        )

    @app.post("/api/dev/glass")
    def dev_glass():
        """Mock mode only: pretend someone put a glass on the stage."""
        if not halo.settings["hardware"]["mock"]:
            return err("only available in mock mode", 404)
        halo.scale.place(250)
        return jsonify(ok=True)

    @app.get("/api/status")
    def status():
        d = halo.engine.progress.to_dict()
        if hasattr(halo.halo, "frame"):
            d["ring"] = halo.halo.frame
        return jsonify(d)

    # ------------------------------------------------------------ bottles
    @app.get("/api/bottles")
    def bottles():
        return jsonify([b.to_dict() for b in halo.inventory.bottles])

    @app.put("/api/bottles/<int:slot>")
    def set_bottle(slot):
        if not 1 <= slot <= 8:
            return err("slot must be 1-8")
        d = request.get_json(force=True)
        old = halo.inventory.bottles[slot - 1]
        name = str(d.get("name", old.name)).strip()
        try:
            cap = float(d.get("capacity_ml", old.capacity_ml) or 750)
            remaining = d.get("remaining_ml")
            if remaining is None:
                remaining = cap if name.lower() != old.name.lower() else old.remaining_ml
            b = Bottle(slot=slot, name=name, abv=float(d.get("abv", old.abv) or 0),
                       density=float(d.get("density", old.density) or 1.0), color=d.get("color", old.color),
                       capacity_ml=cap, remaining_ml=min(float(remaining), cap))
        except (TypeError, ValueError):
            return err("numbers please for ABV, density and sizes")
        halo.inventory.bottles[slot - 1] = b
        halo.inventory.save()
        return jsonify(b.to_dict())

    @app.post("/api/bottles/<int:slot>/refill")
    def refill(slot):
        b = halo.inventory.bottles[slot - 1]
        b.remaining_ml = b.capacity_ml
        halo.inventory.save()
        return jsonify(b.to_dict())

    @app.post("/api/bottles/swap")
    def swap():
        d = request.get_json(force=True)
        a, b = int(d["a"]), int(d["b"])
        if not (1 <= a <= 8 and 1 <= b <= 8) or a == b:
            return err("pick two different slots")
        halo.inventory.swap(a, b)
        halo.inventory.save()
        return jsonify(ok=True)

    # ------------------------------------------------------------ recipes
    @app.get("/api/recipes")
    def recipes():
        return jsonify([recipe_json(r) for r in halo.book.recipes])

    @app.post("/api/recipes")
    def add_recipe():
        d = request.get_json(force=True)
        try:
            r = Recipe.from_dict(d)
        except (KeyError, TypeError, ValueError) as e:
            return err(f"bad recipe: {e}")
        if not r.name.strip() or not r.ingredients:
            return err("a drink needs a name and at least one ingredient")
        halo.book.add(r, replace=d.get("replace"))
        return jsonify(recipe_json(r))

    @app.delete("/api/recipes/<name>")
    def delete_recipe(name):
        halo.book.remove(name)
        return jsonify(ok=True)

    @app.post("/api/recipes/<name>/favorite")
    def favorite(name):
        r = halo.book.get(name)
        if not r:
            return err("unknown drink", 404)
        r.favorite = not r.favorite
        halo.book.save()
        return jsonify(recipe_json(r))

    @app.get("/api/recipes/export")
    def export():
        return jsonify([r.to_dict() for r in halo.book.recipes])

    @app.post("/api/recipes/import")
    def import_():
        items = request.get_json(force=True)
        added = 0
        for d in items if isinstance(items, list) else []:
            try:
                halo.book.add(Recipe.from_dict(d))
                added += 1
            except (KeyError, TypeError, ValueError):
                continue
        return jsonify(added=added)

    # ------------------------------------------------------------ bartender & party
    @app.post("/api/bartender")
    def ask():
        d = request.get_json(force=True)
        try:
            r = halo.bartender.create(halo.inventory, d.get("request") or "surprise")
        except BartenderError as e:
            return err(str(e))
        if d.get("save"):
            halo.book.add(r)
        return jsonify(recipe_json(r))

    @app.get("/api/party")
    def party():
        return jsonify(halo.party.data)

    @app.post("/api/party")
    def start_party():
        d = request.get_json(force=True)
        theme = (d.get("theme") or "").strip()
        if d.get("drinks"):                       # activate a menu the user already reviewed
            halo.party.set(theme, [str(n) for n in d["drinks"]])
            return jsonify(halo.party.data)
        if not theme:
            return err("give the party a theme")
        try:
            menu = halo.bartender.party_menu(halo.inventory, theme, d.get("count", 5))
        except BartenderError as e:
            return err(str(e))
        for r in menu:
            halo.book.add(r)
        halo.party.set(theme, [r.name for r in menu])
        return jsonify(party=halo.party.data, drinks=[recipe_json(r) for r in menu])

    @app.delete("/api/party")
    def end_party():
        halo.party.end()
        return jsonify(halo.party.data)

    # ------------------------------------------------------------ pouring
    def _start(r: Recipe, size: str, strength: str, by: str):
        strength = strength if strength in STRENGTH else halo.prefs["default_strength"]
        halo.engine.start(r, size if size in ("single", "double") else "single", strength, by=by)

    @app.post("/api/pour")
    def pour():
        d = request.get_json(force=True)
        r = halo.book.get(d.get("drink", ""))
        if r is None and d.get("request"):
            try:
                r = halo.bartender.create(halo.inventory, d["request"])
                halo.book.add(r)
            except BartenderError as e:
                return err(str(e))
        if r is None:
            return err("Unknown drink", 404)
        try:
            _start(r, d.get("size", "single"), d.get("strength", ""), d.get("by", "web"))
        except (PourError, ValueError) as e:
            return err(str(e), 409)
        return jsonify(ok=True, drink=r.name)

    @app.post("/api/cancel")
    def cancel():
        halo.engine.cancel()
        return jsonify(ok=True)

    @app.post("/api/voice")
    def voice():
        """Feature 14: Levi sends the words it heard; Halo works out the drink and pours it."""
        if not halo.prefs["voice_enabled"]:
            return err("Voice orders are turned off in Halo's settings", 403)
        text = (request.get_json(force=True).get("text") or "").strip()
        if not text:
            return err("no order heard")
        halo.set_ring("thinking", 10)
        names = [r.name for r in halo.menu()]
        try:
            order = halo.bartender.interpret(text, names)
            if order["drink"]:
                r = halo.book.get(order["drink"])
            else:
                r = halo.bartender.create(halo.inventory, order["request"])
                halo.book.add(r)
            _start(r, order["size"], order["strength"], "levi")
        except (BartenderError, PourError, ValueError) as e:
            halo.set_ring("", 0)
            return err(str(e), 409)
        halo.set_ring("", 0)
        say = f"Making a {order['size']} {r.name}. Put a glass on the halo."
        return jsonify(ok=True, drink=r.name, size=order["size"], strength=order["strength"], say=say)

    @app.post("/api/ring")
    def ring():
        d = request.get_json(force=True)
        halo.set_ring(d.get("mode", ""), float(d.get("seconds", 8)))
        return jsonify(ok=True)

    # ------------------------------------------------------------ stats & prefs
    @app.get("/api/stats")
    def stats():
        return jsonify(halo.history.stats())

    @app.get("/api/prefs")
    def get_prefs():
        return jsonify(halo.prefs.data)

    @app.put("/api/prefs")
    def put_prefs():
        d = request.get_json(force=True)
        if "led_brightness" in d:
            d["led_brightness"] = max(0.05, min(0.6, float(d["led_brightness"])))
            halo.halo.set_brightness(d["led_brightness"])
        for k in ("single_oz", "double_oz"):
            if k in d and d[k] not in (None, ""):
                d[k] = max(0.5, min(12.0, float(d[k])))
        return jsonify(halo.prefs.update(d))

    return app
