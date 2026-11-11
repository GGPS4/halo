"""Claude as Halo's bartender.

Claude only sees the bottles that are actually loaded and must answer through a tool
call, so every reply is a recipe Halo can pour without guessing.
"""
from __future__ import annotations

import os

from .config import Inventory
from .recipes import Ingredient, Recipe

SYSTEM = """You are Halo, the bartender inside a home cocktail machine.
You can only use the bottles listed as loaded. Halo pumps liquids only: no ice, no
shaking, no muddling, so design drinks that taste right built straight into a glass
(the guest adds ice and the garnish). Give amounts in parts; the machine scales them to
a single or a double. Keep descriptions to one or two lively sentences. Never suggest
more than 6 ingredients. If the request can't be met with what's loaded, make the
closest good drink and say what you changed in the description."""

RECIPE_TOOL = {
    "name": "make_drink",
    "description": "Return one drink Halo can pour from the loaded bottles.",
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Short, original drink name (max 3 words)"},
            "description": {"type": "string"},
            "ingredients": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "bottle": {"type": "string", "description": "Exact name of a loaded bottle"},
                        "parts": {"type": "number", "minimum": 0.25, "maximum": 8},
                    },
                    "required": ["bottle", "parts"],
                },
            },
            "garnish": {"type": "string"},
            "glass": {"type": "string", "enum": ["rocks", "highball", "coupe", "martini", "shot", "wine"]},
            "color": {"type": "string", "description": "Hex color of the finished drink, e.g. #e8a33c"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["name", "description", "ingredients", "glass", "color"],
    },
}

MENU_TOOL = {
    "name": "party_menu",
    "description": "Return a themed menu of drinks Halo can pour from the loaded bottles.",
    "input_schema": {
        "type": "object",
        "properties": {"drinks": {"type": "array", "minItems": 2, "maxItems": 8,
                                  "items": RECIPE_TOOL["input_schema"]}},
        "required": ["drinks"],
    },
}

ORDER_TOOL = {
    "name": "place_order",
    "description": "Turn a spoken drink order into a structured order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "drink": {"type": "string", "description": "Exact menu drink name, if the guest asked for one"},
            "request": {"type": "string", "description": "What they want, if no menu drink fits"},
            "size": {"type": "string", "enum": ["single", "double"]},
            "strength": {"type": "string", "enum": ["light", "normal", "strong"]},
        },
        "required": ["size", "strength"],
    },
}

MOODS = {
    "surprise": "Surprise me with something I probably haven't had.",
    "refreshing": "Something light and refreshing.",
    "strong": "Spirit-forward and strong.",
    "sweet": "On the sweeter side.",
    "sour": "Bright and sour.",
    "low": "Low-alcohol, easy-drinking.",
    "classic": "A riff on a well-known classic.",
}


class BartenderError(Exception):
    pass


class Bartender:
    def __init__(self, settings: dict, client=None):
        self.cfg = settings["bartender"]
        self._client = client

    @property
    def client(self):
        if self._client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise BartenderError("Set ANTHROPIC_API_KEY in .env to use the bartender")
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def _shelf(self, inv: Inventory) -> str:
        loaded = inv.loaded()
        if not loaded:
            raise BartenderError("No bottles loaded yet")
        return "\n".join(f"- {b.name} ({b.abv:g}% ABV)" for b in loaded)

    def _call(self, tool: dict, content: str) -> dict:
        msg = self.client.messages.create(
            model=self.cfg["model"],
            max_tokens=self.cfg["max_tokens"],
            system=SYSTEM,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": content}],
        )
        block = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            raise BartenderError("The bartender didn't answer")
        return block.input

    def create(self, inv: Inventory, request: str) -> Recipe:
        shelf = self._shelf(inv)
        request = MOODS.get(request, request)
        data = self._call(RECIPE_TOOL, f"Loaded bottles:\n{shelf}\n\nGuest asks: {request}")
        return self._validate(data, inv)

    def party_menu(self, inv: Inventory, theme: str, count: int = 5) -> list[Recipe]:
        """Feature 8: a themed menu of distinct drinks built from what's loaded."""
        shelf = self._shelf(inv)
        count = max(2, min(8, int(count)))
        data = self._call(MENU_TOOL, (
            f"Loaded bottles:\n{shelf}\n\nDesign a party menu of {count} different drinks for this theme: "
            f"{theme}. Vary strength and flavor across the menu and include at least one lighter drink."))
        out, seen = [], set()
        for d in data.get("drinks", [])[:count]:
            try:
                r = self._validate(d, inv)
            except BartenderError:
                continue
            if r.name.lower() not in seen:
                seen.add(r.name.lower())
                r.tags = list(dict.fromkeys(r.tags + ["party"]))[:5]
                out.append(r)
        if not out:
            raise BartenderError("Couldn't build a menu from the loaded bottles")
        return out

    def interpret(self, text: str, drink_names: list[str]) -> dict:
        """Feature 14: turn a spoken order into {drink | request, size, strength}."""
        data = self._call(ORDER_TOOL, (
            f"Drinks on the menu: {', '.join(drink_names) or '(none)'}\n\nSpoken order: \"{text}\"\n"
            "If the order names or clearly means a menu drink, return it as drink. Otherwise put "
            "what they want in request so a new drink can be created."))
        size = data.get("size") if data.get("size") in ("single", "double") else "single"
        strength = data.get("strength") if data.get("strength") in ("light", "normal", "strong") else "normal"
        drink = data.get("drink") if data.get("drink") in drink_names else None
        return {"drink": drink, "request": None if drink else (data.get("request") or text),
                "size": size, "strength": strength}

    @staticmethod
    def _validate(data: dict, inv: Inventory) -> Recipe:
        ingredients = []
        for item in data.get("ingredients", []):
            bottle = inv.find(str(item.get("bottle", "")))
            parts = float(item.get("parts", 0))
            if bottle and parts > 0:
                ingredients.append(Ingredient(bottle=bottle.name, parts=round(parts, 2)))
        if not ingredients:
            raise BartenderError("The suggested drink used bottles that aren't loaded")
        color = str(data.get("color", "#d9a05b"))
        if not (color.startswith("#") and len(color) == 7):
            color = "#d9a05b"
        return Recipe(
            name=str(data.get("name", "House Pour"))[:40],
            description=str(data.get("description", "")),
            ingredients=ingredients,
            garnish=str(data.get("garnish", "")),
            glass=str(data.get("glass", "rocks")),
            color=color,
            created_by="claude",
            tags=[str(t) for t in data.get("tags", [])][:5],
        )
