import os
import threading
import time

os.environ["HALO_MOCK"] = "1"

import pytest

from halo.bartender import Bartender, BartenderError
from halo.config import Bottle, Inventory, load_settings
from halo.hardware import MockHalo, MockPumps, MockScale
from halo.pour import PourEngine, State
from halo.recipes import Ingredient, Recipe


@pytest.fixture
def rig(tmp_path):
    settings = load_settings(tmp_path / "none.toml")
    settings["pour"].update(stall_seconds=0.6, single_oz=0.5, double_oz=1.0)
    inv = Inventory(bottles=[Bottle(slot=i) for i in range(1, 9)], path=tmp_path / "b.json")
    inv.bottles[0] = Bottle(1, "Gin", 40, 0.95)
    inv.bottles[1] = Bottle(2, "Tonic", 0, 1.03)
    inv.bottles[2] = Bottle(3, "Simple Syrup", 0, 1.3)
    scale = MockScale()
    pumps = MockPumps(scale, flow_g_per_s=20)
    engine = PourEngine(pumps, scale, MockHalo(), settings, inv)
    return engine, scale, pumps, inv


def gt(**kw):
    return Recipe(name="G&T", ingredients=[Ingredient("Gin", 1), Ingredient("Tonic", 3)], **kw)


def place_glass_soon(scale, grams=250, delay=0.2):
    threading.Timer(delay, scale.place, args=(grams,)).start()


def test_plan_scales_and_orders_by_density(rig):
    engine, *_ , inv = rig
    r = Recipe("Sweet", [Ingredient("Gin", 2), Ingredient("Simple Syrup", 1)])
    steps = r.plan(inv, 30)
    assert [s[1] for s in steps] == ["Simple Syrup", "Gin"]          # densest first
    assert steps[0][2] == pytest.approx(10 * 1.3)
    assert steps[1][2] == pytest.approx(20 * 0.95)


def test_missing_bottle_refused(rig):
    engine, *_ = rig
    with pytest.raises(ValueError):
        engine.start(Recipe("X", [Ingredient("Rum", 1)]))


def test_full_pour_hits_target_weight(rig):
    engine, scale, pumps, inv = rig
    place_glass_soon(scale)
    engine.start(gt(), "single")
    p = engine.wait(15)
    assert p.state == State.DONE, p.message
    target = sum(s[2] for s in gt().plan(inv, engine.size_ml("single")))
    assert p.poured_g == pytest.approx(target, abs=6)
    assert pumps.running == set()


def test_no_glass_times_out(rig):
    engine, *_ = rig
    engine.start(gt(), glass_timeout=0.3)
    assert engine.wait(5).state == State.ERROR


def test_empty_bottle_detected(rig):
    engine, scale, pumps, _ = rig
    pumps.empty_slots.add(1)
    place_glass_soon(scale)
    engine.start(gt())
    p = engine.wait(10)
    assert p.state == State.ERROR and "empty" in p.message
    assert pumps.running == set()


def test_cancel_stops_pumps(rig):
    engine, scale, pumps, _ = rig
    place_glass_soon(scale)
    engine.start(gt(), "double")
    time.sleep(1.0)
    engine.cancel()
    assert engine.wait(5).state == State.CANCELLED
    assert pumps.running == set()


def test_bartender_validation_drops_unloaded_bottles(rig):
    *_, inv = rig
    r = Bartender._validate(
        {"name": "Test", "description": "d", "glass": "rocks", "color": "nope",
         "ingredients": [{"bottle": "gin", "parts": 2}, {"bottle": "Mezcal", "parts": 1}]}, inv)
    assert [i.bottle for i in r.ingredients] == ["Gin"] and r.color == "#d9a05b"
    with pytest.raises(BartenderError):
        Bartender._validate({"ingredients": [{"bottle": "Mezcal", "parts": 1}]}, inv)


def test_bartender_uses_tool_call(rig):
    *_, inv = rig

    class Block:
        type = "tool_use"
        input = {"name": "Juniper Fizz", "description": "Bright.", "glass": "highball",
                 "color": "#e0f0d0", "ingredients": [{"bottle": "Gin", "parts": 1}, {"bottle": "Tonic", "parts": 3}]}

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw):
                assert kw["tool_choice"]["name"] == "make_drink"
                assert "Gin" in kw["messages"][0]["content"]
                return type("M", (), {"content": [Block()]})

    r = Bartender(load_settings(), client=FakeClient()).create(inv, "refreshing")
    assert r.name == "Juniper Fizz" and r.created_by == "claude"
