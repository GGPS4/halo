"""Drink recipes, stored as parts so the same recipe scales to any size and strength."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .config import ROOT, Inventory

# Feature 2: strength changes the spirit-to-mixer ratio, not the size of the drink.
STRENGTH = {"light": 0.7, "normal": 1.0, "strong": 1.35}
SPIRIT_ABV = 15.0          # anything at or above this counts as a spirit for the strength slider


@dataclass
class Ingredient:
    bottle: str
    parts: float


@dataclass
class Step:
    slot: int
    bottle: str
    grams: float
    ml: float
    color: str


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
    favorite: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        names = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in names}
        d["ingredients"] = [Ingredient(bottle=str(i["bottle"]), parts=float(i["parts"])) for i in d["ingredients"]]
        return cls(**d)

    def to_dict(self) -> dict:
        return asdict(self)

    def missing(self, inv: Inventory) -> list[str]:
        return [i.bottle for i in self.ingredients if inv.find(i.bottle) is None]

    def weighted_parts(self, inv: Inventory, strength: str = "normal") -> list[tuple[Ingredient, float]]:
        k = STRENGTH.get(strength, 1.0)
        out = []
        for i in self.ingredients:
            b = inv.find(i.bottle)
            spirit = b is not None and b.abv >= SPIRIT_ABV
            out.append((i, i.parts * (k if spirit else 1.0)))
        return out

    def plan(self, inv: Inventory, total_ml: float, strength: str = "normal") -> list[Step]:
        """Pour order and target grams for a drink of total_ml at the given strength."""
        missing = self.missing(inv)
        if missing:
            raise ValueError(f"Not loaded: {', '.join(missing)}")
        weighted = self.weighted_parts(inv, strength)
        parts = sum(w for _, w in weighted)
        if parts <= 0:
            raise ValueError("Recipe has no volume")
        steps = []
        for ing, w in weighted:
            b = inv.find(ing.bottle)
            ml = total_ml * w / parts
            steps.append(Step(b.slot, b.name, ml * b.density, ml, b.color))
        # Lightest first, densest last. Drinks are poured over ice and served without shaking,
        # so each heavier liquid should fall through what's already in the cup and mix on the
        # way down; poured the other way round, spirits float on a layer of syrup.
        steps.sort(key=lambda s: inv.find(s.bottle).density)
        return steps

    def total_ml(self, inv: Inventory, ethanol_ml: float, strength: str = "normal", cup_ml: float = 240.0) -> float:
        """How much to pour so the drink holds ethanol_ml of alcohol, never more than fits in
        the cup. A highball comes out long and a spirit-forward drink short, each with the same
        alcohol; drinks with no alcohol fill the cup."""
        abv = self.abv(inv, strength)
        if abv < 0.5:
            return cup_ml
        return min(cup_ml, ethanol_ml / (abv / 100.0))

    def abv(self, inv: Inventory, strength: str = "normal") -> float:
        weighted = self.weighted_parts(inv, strength)
        parts = sum(w for _, w in weighted) or 1
        return sum((inv.find(i.bottle).abv if inv.find(i.bottle) else 0) * w for i, w in weighted) / parts


class RecipeBook:
    def __init__(self, path: Path | None = None):
        self.path = path or ROOT / "recipes.json"
        self.recipes: list[Recipe] = []
        if self.path.exists():
            self.recipes = [Recipe.from_dict(d) for d in json.loads(self.path.read_text())]

    def save(self) -> None:
        self.path.write_text(json.dumps([r.to_dict() for r in self.recipes], indent=2))

    def add(self, recipe: Recipe, replace: str | None = None) -> None:
        drop = {recipe.name.lower(), (replace or "").lower()}
        self.recipes = [r for r in self.recipes if r.name.lower() not in drop]
        self.recipes.append(recipe)
        self.save()

    def remove(self, name: str) -> None:
        self.recipes = [r for r in self.recipes if r.name.lower() != name.lower()]
        self.save()

    def get(self, name: str) -> Recipe | None:
        return next((r for r in self.recipes if r.name.lower() == name.lower()), None)

    def available(self, inv: Inventory) -> list[Recipe]:
        return [r for r in self.recipes if not r.missing(inv)]
