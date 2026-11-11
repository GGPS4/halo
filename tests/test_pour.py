import os
import threading
import time

os.environ["HALO_MOCK"] = "1"

import pytest

from halo.bartender import Bartender, BartenderError
from halo.config import Bottle, History, Inventory, load_settings
from halo.hardware import MockHalo, MockPumps, MockScale
from halo.pour import PourEngine, State
from halo.recipes import Ingredient, Recipe


@pytest.fixture
def rig(tmp_path):
    settings = load_settings(tmp_path / "none.toml")
    settings["pour"].update(stall_seconds=0.6, single_oz=0.5, double_oz=1.0)
    inv = Inventory(bottles=[Bottle(slot=i) for i in range(1, 9)], path=tmp_path / "b.json")
    inv.bottles[0] = Bottle(1, "Gin", 40, 0.95, "#cfe3dc")
    inv.bottles[1] = Bottle(2, "Tonic", 0, 1.03, "#d9eef7")
    inv.bottles[2] = Bottle(3, "Simple Syrup", 0, 1.3, "#f4ecd2")
    scale = MockScale()
    pumps = MockPumps(scale, flow_g_per_s=20)
    history = History(tmp_path / "h.json")
    engine = PourEngine(pumps, scale, MockHalo(), settings, inv, history=history)
    return engine, scale, pumps, inv


def gt(**kw):
    return Recipe(name="G&T", ingredients=[Ingredient("Gin", 1), Ingredient("Tonic", 3)], **kw)


def place_glass_soon(scale, grams=250, delay=0.2):
    threading.Timer(delay, scale.place, args=(grams,)).start()


def test_plan_scales_and_orders_by_density(rig):
    engine, *_, inv = rig
    r = Recipe("Sweet", [Ingredient("Gin", 2), Ingredient("Simple Syrup", 1)])
    steps = r.plan(inv, 30)
    assert [s.bottle for s in steps] == ["Simple Syrup", "Gin"]          # densest first
    assert steps[0].grams == pytest.approx(10 * 1.3)
    assert steps[1].grams == pytest.approx(20 * 0.95)
    assert steps[1].color == "#cfe3dc"


def test_strength_changes_ratio_not_volume(rig):
    *_, inv = rig
    r = gt()
    ml = lambda strength: {s.bottle: s.ml for s in r.plan(inv, 100, strength)}
    light, normal, strong = ml("light"), ml("normal"), ml("strong")
    assert sum(light.values()) == pytest.approx(100) == pytest.approx(sum(strong.values()))
    assert light["Gin"] < normal["Gin"] < strong["Gin"]
    assert r.abv(inv, "strong") > r.abv(inv, "light")


def test_missing_bottle_refused(rig):
    engine, *_ = rig
    with pytest.raises(ValueError):
        engine.start(Recipe("X", [Ingredient("Rum", 1)]))


def test_full_pour_hits_target_and_updates_levels(rig):
    engine, scale, pumps, inv = rig
    gin_before = inv.find("Gin").remaining_ml
    place_glass_soon(scale)
    engine.start(gt(), "single")
    p = engine.wait(15)
    assert p.state == State.DONE, p.message
    target = sum(s.grams for s in gt().plan(inv, engine.size_ml("single")))
    assert p.poured_g == pytest.approx(target, abs=6)
    assert pumps.running == {}
    assert inv.find("Gin").remaining_ml < gin_before                  # feature 4
    assert engine.history.items[-1]["state"] == "done"
    assert [s["bottle"] for s in p.segments] == ["Tonic", "Gin"]       # feature 28


def test_pump_slows_down_near_target(rig):
    engine, scale, pumps, _ = rig
    assert engine.speed_for(100) == 1.0
    assert engine.speed_for(0) == pytest.approx(engine.cfg["min_speed"])
    assert 0.35 < engine.speed_for(4) < 1.0
    place_glass_soon(scale)
    engine.start(gt(), "double")
    assert engine.wait(20).state == State.DONE
    assert min(pumps.speed_log) < 1.0                                 # feature 34 actually used


def test_too_low_bottle_refused(rig):
    engine, _, _, inv = rig
    inv.find("Gin").remaining_ml = 1
    with pytest.raises(Exception, match="too low"):
        engine.start(gt())


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
    assert pumps.running == {}


def test_cancel_stops_pumps(rig):
    engine, scale, pumps, _ = rig
    place_glass_soon(scale)
    engine.start(gt(), "double")
    time.sleep(1.0)
    engine.cancel()
    assert engine.wait(5).state == State.CANCELLED
    assert pumps.running == {}


def test_bartender_validation_drops_unloaded_bottles(rig):
    *_, inv = rig
    r = Bartender._validate(
        {"name": "Test", "description": "d", "glass": "rocks", "color": "nope",
         "ingredients": [{"bottle": "gin", "parts": 2}, {"bottle": "Mezcal", "parts": 1}]}, inv)
    assert [i.bottle for i in r.ingredients] == ["Gin"] and r.color == "#d9a05b"
    with pytest.raises(BartenderError):
        Bartender._validate({"ingredients": [{"bottle": "Mezcal", "parts": 1}]}, inv)


class FakeClient:
    def __init__(self, data):
        self.data = data
        outer = self

        class messages:
            @staticmethod
            def create(**kw):
                outer.last = kw
                block = type("B", (), {"type": "tool_use", "input": outer.data})()
                return type("M", (), {"content": [block]})
        self.messages = messages


def test_bartender_uses_tool_call(rig):
    *_, inv = rig
    fake = FakeClient({"name": "Juniper Fizz", "description": "Bright.", "glass": "highball", "color": "#e0f0d0",
                       "ingredients": [{"bottle": "Gin", "parts": 1}, {"bottle": "Tonic", "parts": 3}]})
    r = Bartender(load_settings(), client=fake).create(inv, "refreshing")
    assert r.name == "Juniper Fizz" and r.created_by == "claude"
    assert fake.last["tool_choice"]["name"] == "make_drink"


def test_party_menu_dedupes_and_tags(rig):
    *_, inv = rig
    d = {"name": "A", "description": "", "glass": "rocks", "color": "#aabbcc",
         "ingredients": [{"bottle": "Gin", "parts": 1}]}
    fake = FakeClient({"drinks": [d, dict(d), dict(d, name="B"), dict(d, name="C", ingredients=[{"bottle": "Rum", "parts": 1}])]})
    menu = Bartender(load_settings(), client=fake).party_menu(inv, "garden party", 4)
    assert [r.name for r in menu] == ["A", "B"]
    assert all("party" in r.tags for r in menu)


def test_voice_interpret_matches_menu(rig):
    fake = FakeClient({"drink": "G&T", "size": "double", "strength": "strong"})
    order = Bartender(load_settings(), client=fake).interpret("a strong double gin and tonic", ["G&T"])
    assert order == {"drink": "G&T", "request": None, "size": "double", "strength": "strong"}
    fake.data = {"drink": "Nope", "request": "something smoky", "size": "big", "strength": "x"}
    order = Bartender(load_settings(), client=fake).interpret("something smoky", ["G&T"])
    assert order["drink"] is None and order["request"] == "something smoky" and order["size"] == "single"


def test_led_segments_and_ambient(rig):
    halo = MockHalo()
    halo.segments([((255, 0, 0), 10), ((0, 0, 255), 30)], done_g=40)
    assert halo.frame[0][0] > 200 and halo.frame[-1][2] > 200           # red first quarter, blue after
    halo.segments([((255, 0, 0), 10), ((0, 0, 255), 30)], done_g=0)
    assert max(max(c) for c in halo.frame) < 30                          # nothing lit yet
    halo.ambient(1.0, [(255, 0, 0), (0, 255, 0)])
    assert halo.last == "ambient" and any(sum(c) > 0 for c in halo.frame)


def test_gate_strip_climbs_from_both_feet():
    halo = MockHalo(gate_count=52)
    halo.progress(0.5, (255, 0, 0))
    lit = [c[0] > 128 for c in halo.gate]
    assert len(halo.frame) == 24 and len(halo.gate) == 52
    assert lit[0] and lit[-1]                     # both feet lit at half way
    assert not lit[25] and not lit[26]            # the top is still dark
    assert lit == lit[::-1]                       # left and right sides match
    halo.progress(1.0, (255, 0, 0))
    assert all(c[0] > 200 for c in halo.gate)
