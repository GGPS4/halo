"""Drink recipes, stored as parts so the same recipe scales to a single or a double."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import ROOT, Inventory


@dataclass
class Ingredient:
    bottle: str
    parts: float


@dataclass
class Recipe:
    name: str
    ingredients: list[Ingredient]
    description: str = ""
    garnish: str = ""
    glass: str = "rocks"
    color: str = "#d9a05b"
    created_by: str = "you"           # "you" or "claude"
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        d = dict(d)
        d["ingredients"] = [Ingredient(**i) for i in d["ingredients"]]
        return cls(**d)

    def to_dict(self) -> dict:
        return asdict(self)

    def missing(self, inv: Inventory) -> list[str]:
        return [i.bottle for i in self.ingredients if inv.find(i.bottle) is None]

    def plan(self, inv: Inventory, total_ml: float) -> list[tuple[int, str, float]]:
        """Return [(slot, bottle name, target grams)] for a pour of total_ml."""
        missing = self.missing(inv)
        if missing:
            raise ValueError(f"Not loaded: {', '.join(missing)}")
        parts = sum(i.parts for i in self.ingredients)
        if parts <= 0:
            raise ValueError("Recipe has no volume")
        steps = []
        for i in self.ingredients:
            b = inv.find(i.bottle)
            ml = total_ml * i.parts / parts
            steps.append((b.slot, b.name, ml * b.density))
        # Pour the densest first so lighter spirits mix in on top of syrups.
        steps.sort(key=lambda s: -inv.find(s[1]).density)
        return steps

    def abv(self, inv: Inventory) -> float:
        parts = sum(i.parts for i in self.ingredients) or 1
        return sum(inv.find(i.bottle).abv * i.parts for i in self.ingredients if inv.find(i.bottle)) / parts


class RecipeBook:
    def __init__(self, path: Path | None = None):
        self.path = path or ROOT / "recipes.json"
        self.recipes: list[Recipe] = []
        if self.path.exists():
            self.recipes = [Recipe.from_dict(d) for d in json.loads(self.path.read_text())]

    def save(self) -> None:
        self.path.write_text(json.dumps([r.to_dict() for r in self.recipes], indent=2))

    def add(self, recipe: Recipe) -> None:
        self.recipes = [r for r in self.recipes if r.name.lower() != recipe.name.lower()]
        self.recipes.append(recipe)
        self.save()

    def remove(self, name: str) -> None:
        self.recipes = [r for r in self.recipes if r.name.lower() != name.lower()]
        self.save()

    def get(self, name: str) -> Recipe | None:
        return next((r for r in self.recipes if r.name.lower() == name.lower()), None)

    def available(self, inv: Inventory) -> list[Recipe]:
        return [r for r in self.recipes if not r.missing(inv)]
