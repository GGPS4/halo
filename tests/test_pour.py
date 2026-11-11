import os
import threading
import time

os.environ["HALO_MOCK"] = "1"

import pytest

from halo.bartender import Bartender, BartenderError
from halo.config import Bottle, History, Inventory, PumpLearning, load_settings
from halo.hardware import MockHalo, MockPumps, MockScale
from halo.pour import PourEngine, State
from halo.recipes import Ingredient, Recipe


@pytest.fixture
def rig(tmp_path):
    settings = load_settings(tmp_path / "none.toml")
    settings["pour"].update(stall_seconds=0.6, pour_oz=0.5, cup_ml=80, settle_s=0.2, surge_g_s=60)
    inv = Inventory(bottles=[Bottle(slot=i) for i in range(1, 9)], path=tmp_path / "b.json")
    inv.bottles[0] = Bottle(1, "Gin", 40, 0.95, "#cfe3dc")
    inv.bottles[1] = Bottle(2, "Tonic", 0, 1.03, "#d9eef7")
    inv.bottles[2] = Bottle(3, "Simple Syrup", 0, 1.3, "#f4ecd2")
    scale = MockScale()
    pumps = MockPumps(scale, flow_g_per_s=20)
    history = History(tmp_path / "h.json")
    engine = PourEngine(pumps, scale, MockHalo(), settings, inv, history=history,
                        learning=PumpLearning(tmp_path / "pumps.json"))
    return engine, scale, pumps, inv


def gt(**kw):
    return Recipe(name="G&T", ingredients=[Ingredient("Gin", 1), Ingredient("Tonic", 3)], **kw)


def place_glass_soon(scale, grams=250, delay=0.2):
    threading.Timer(delay, scale.place, args=(grams,)).start()


def lift_cup(scale, engine):
    """Take the finished drink off the stage and wait for Halo to notice (feature 3)."""
    scale.place(-1000)
    for _ in range(50):
        if not engine.waiting_pickup:
            return
        time.sleep(0.05)
    raise AssertionError("Halo didn't notice the cup was lifted")


def test_plan_scales_and_orders_lightest_first(rig):
    engine, *_, inv = rig
    r = Recipe("Sweet", [Ingredient("Simple Syrup", 1), Ingredient("Gin", 2)])
    steps = r.plan(inv, 30)
    assert [s.bottle for s in steps] == ["Gin", "Simple Syrup"]          # syrup falls through last
    assert steps[0].grams == pytest.approx(20 * 0.95)
    assert steps[1].grams == pytest.approx(10 * 1.3)
    assert steps[0].color == "#cfe3dc"


def test_size_is_one_standard_drink_capped_by_the_cup(rig):
    engine, *_, inv = rig
    engine.cfg.update(pour_oz=1.5, cup_ml=240)
    ethanol = 1.5 * 29.57 * 0.4
    highball = gt()                                                     # 10% ABV
    assert engine.size_ml(highball) == pytest.approx(ethanol / 0.10)    # ~177 ml, long
    neat = Recipe("Neat", [Ingredient("Gin", 1)])
    assert engine.size_ml(neat) == pytest.approx(1.5 * 29.57)          # 1.5 oz, short
    assert engine.size_ml(highball, "double") == 240                    # would be 355: cup is full
    soda = Recipe("Soda", [Ingredient("Tonic", 1)])
    assert engine.size_ml(soda) == 240                                  # no alcohol: fill the cup
    gin = lambda st: {s.bottle: s.ml for s in highball.plan(inv, engine.size_ml(highball, "single", st), st)}["Gin"]
    assert gin("light") < gin("normal") < gin("strong")


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
    target = sum(s.grams for s in gt().plan(inv, engine.size_ml(gt(), "single")))
    assert p.poured_g == pytest.approx(target, abs=6)
    assert pumps.running == {}
    assert inv.find("Gin").remaining_ml < gin_before                  # feature 4
    assert engine.history.items[-1]["state"] == "done"
    assert [s["bottle"] for s in p.segments] == ["Gin", "Tonic"]       # feature 28, lightest first


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


def test_pumps_learn_to_stop_early(rig):
    """Feature 1: 4 g keeps landing after each pump stops. The first pour overshoots; after
    a few pours each pump has learned to stop early and lands on target."""
    engine, scale, pumps, inv = rig
    pumps.drip_g = 4.0
    errors = []
    for _ in range(4):
        place_glass_soon(scale, delay=0.05)
        engine.start(gt(), "single")
        p = engine.wait(15)
        assert p.state == State.DONE, p.message
        errors.append(max(abs(r["poured_g"] - r["target_g"]) for r in p.report))
        lift_cup(scale, engine)
    assert errors[0] > 2.0                       # 1.5 g default lead, 4 g lands: overshoot
    assert errors[-1] < 1.0                      # learned
    gin = engine.learning.entry(1, "Gin")
    assert gin["after_g"] == pytest.approx(4.0, abs=0.6) and gin["n"] == 4
    assert engine.learning.entry(2, "Tonic")["flow_gps"] == pytest.approx(20, rel=0.25)   # the long pour


def test_learning_resets_for_a_new_bottle_and_flags_worn_tubing(tmp_path):
    L = PumpLearning(tmp_path / "p.json")
    for _ in range(5):
        L.record(1, "Gin", 2.0, 3.0, 0.2)
    assert L.lead(1, "Gin", 1.5) == pytest.approx(3.0)
    assert L.lead(1, "Vodka", 1.5) == 1.5            # different bottle: start over
    for _ in range(12):
        L.record(1, "Gin", 1.2, 3.0, 0.2)            # tubing wearing: 60% of the starting flow
    inv = Inventory(bottles=[Bottle(slot=i) for i in range(1, 9)], path=tmp_path / "b.json")
    inv.bottles[0] = Bottle(1, "Gin", 40, 0.95)
    assert L.service(inv) and "tubing" in L.service(inv)[0]["message"]
    assert PumpLearning(tmp_path / "p.json").entry(1, "Gin")["n"] == 17   # saved to disk


def test_ready_glow_until_lifted_and_no_pour_into_a_finished_drink(rig):
    """Feature 3."""
    engine, scale, pumps, inv = rig
    place_glass_soon(scale)
    engine.start(gt())
    p = engine.wait(15)
    assert p.state == State.DONE and p.ready and engine.waiting_pickup
    scale.place(-1000); scale.place(1000)             # a bump that isn't a pickup... still waiting
    engine.start(gt(), glass_timeout=0.5)            # finished drink still on the stage
    p = engine.wait(5)
    assert p.state == State.ERROR and "still on the halo" in p.message
    assert engine.waiting_pickup                      # still waiting; nothing was poured into it
    lift_cup(scale, engine)
    assert not engine.waiting_pickup
    assert abs(scale.grams()) < 1                     # stage re-zeroed for the next cup
    place_glass_soon(scale)
    engine.start(gt())
    assert engine.wait(15).state == State.DONE


def test_guard_stops_on_a_weight_surge(rig):
    """Feature 13: something pressing on the cup."""
    engine, scale, pumps, _ = rig
    place_glass_soon(scale)
    engine.start(gt(), "double")
    while engine.progress.state != State.POURING:
        time.sleep(0.02)
    time.sleep(0.3)
    scale.place(150)
    p = engine.wait(5)
    assert p.state == State.ERROR and "faster than Halo can pour" in p.message
    assert pumps.running == {}


def test_guard_stops_when_the_cup_tips(rig):
    engine, scale, pumps, _ = rig
    place_glass_soon(scale)
    engine.start(gt(), "double")
    while engine.progress.poured_g < 12:
        time.sleep(0.02)
    scale.place(-20)
    p = engine.wait(5)
    assert p.state == State.ERROR and "tipped" in p.message
    assert pumps.running == {}


def test_guard_catches_a_pump_that_wont_stop(rig):
    engine, scale, pumps, _ = rig
    pumps.stuck.add(2)
    place_glass_soon(scale)
    engine.start(gt())
    p = engine.wait(10)
    pumps.stuck.clear()
    assert p.state == State.ERROR and "still pouring" in p.message
    assert pumps.running == {}


def test_lifting_a_finished_drink_returns_to_idle(rig):
    engine, scale, *_ = rig
    place_glass_soon(scale)
    engine.start(gt())
    assert engine.wait(15).ready
    lift_cup(scale, engine)
    assert engine.progress.state == State.IDLE and not engine.progress.ready


def test_guard_catches_a_slow_leak_after_the_drink(rig):
    """A pump stuck on at real-world speed (1.6 g/s) only shows once the drink is done."""
    engine, scale, pumps, _ = rig
    place_glass_soon(scale)
    engine.start(gt())
    assert engine.wait(15).state == State.DONE
    scale.flow = 1.6                                   # something keeps dribbling in
    for _ in range(80):
        if engine.progress.state == State.ERROR:
            break
        time.sleep(0.1)
    scale.flow = 0.0
    assert engine.progress.state == State.ERROR and "kept coming" in engine.progress.message
    assert not engine.waiting_pickup


def test_pumps_start_softly_and_stop_at_once():
    """Feature 12: a pump eases up to speed; off() is immediate and cancels the ramp."""
    scale = MockScale()
    pumps = MockPumps(scale, flow_g_per_s=10)
    pumps.on(1, 1.0)
    assert pumps.running[1] == pytest.approx(0.3)
    time.sleep(0.6)
    assert pumps.running[1] == pytest.approx(1.0)
    ramp = pumps.speed_log
    assert ramp == sorted(ramp) and len(ramp) >= 6                    # climbed in steps
    pumps.on(1, 0.5)                                                  # slow zone: applies at once
    assert pumps.running[1] == pytest.approx(0.5)
    pumps.off(1)
    pumps.on(2, 1.0)
    pumps.off(2)                                                      # off mid-ramp
    time.sleep(0.5)
    assert pumps.running == {}


def test_thick_liquids_slow_earlier_and_gentler(rig):
    """Feature 13 (thick liquids)."""
    engine, scale, pumps, inv = rig
    thin_zone, thin_lo = engine.profile(1, "Gin")
    syrup_zone, syrup_lo = engine.profile(3, "Simple Syrup")          # density 1.3
    assert syrup_zone > thin_zone * 1.5 and syrup_lo < thin_lo
    for _ in range(3):                                                # a pump that keeps missing
        engine.learning.record(1, "Gin", 1.6, 2.0, 2.5)
    assert engine.profile(1, "Gin")[0] > thin_zone
    assert engine.speed_for(syrup_zone + 1, (syrup_zone, syrup_lo)) == 1.0
    assert engine.speed_for(0, (syrup_zone, syrup_lo)) == pytest.approx(syrup_lo)
