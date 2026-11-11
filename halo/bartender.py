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

    def create(self, inv: Inventory, request: str) -> Recipe:
        loaded = inv.loaded()
        if not loaded:
            raise BartenderError("No bottles loaded yet")
        shelf = "\n".join(f"- {b.name} ({b.abv:g}% ABV)" for b in loaded)
        request = MOODS.get(request, request)
        msg = self.client.messages.create(
            model=self.cfg["model"],
            max_tokens=self.cfg["max_tokens"],
            system=SYSTEM,
            tools=[RECIPE_TOOL],
            tool_choice={"type": "tool", "name": "make_drink"},
            messages=[{"role": "user", "content": f"Loaded bottles:\n{shelf}\n\nGuest asks: {request}"}],
        )
        block = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            raise BartenderError("The bartender didn't return a recipe")
        return self._validate(block.input, inv)

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
