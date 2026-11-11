import os
import shutil
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HALO_MOCK", "1")
    monkeypatch.setenv("HALO_HOME", str(tmp_path))
    shutil.copy(ROOT / "scripts/sample_bottles.json", tmp_path / "bottles.json")
    shutil.copy(ROOT / "scripts/sample_recipes.json", tmp_path / "recipes.json")
    import importlib

    import halo.config
    importlib.reload(halo.config)
    import halo.recipes, halo.pour, halo.server, halo.__main__
    for m in (halo.recipes, halo.pour, halo.server, halo.__main__):
        importlib.reload(m)
    app = halo.__main__.App(halo.config.load_settings())
    app.engine.cfg.update(pour_oz=0.5, cup_ml=60, stall_seconds=0.6, settle_s=0.2, surge_g_s=80)
    app.pumps.flow = 25
    web = halo.server.create_app(app)
    c = web.test_client()
    c.halo = app
    return c


def test_web_app_served(client):
    r = client.get("/")
    assert r.status_code == 200 and b"<title>Halo</title>" in r.data
    assert client.get("/manifest.webmanifest").status_code == 200


def test_state_has_everything(client):
    s = client.get("/api/state").json
    assert len(s["bottles"]) == 8 and s["recipes"] and s["sizes"]["cup_ml"] == 60
    gh = next(r for r in s["recipes"] if r["name"] == "Garden Highball")
    assert gh["sizes"]["light"]["single"] <= gh["sizes"]["normal"]["single"] <= gh["sizes"]["normal"]["double"] <= 60
    assert s["service"] == []
    assert all("available" in r for r in s["recipes"])


def test_bottle_edit_refill_swap(client):
    r = client.put("/api/bottles/8", json={"name": "Mezcal", "abv": 40, "density": 0.95, "color": "#c8b089",
                                            "capacity_ml": 700})
    assert r.json["remaining_ml"] == 700
    client.put("/api/bottles/8", json={"remaining_ml": 100})
    assert client.get("/api/state").json["low"] == [8]
    client.post("/api/bottles/8/refill")
    assert client.get("/api/bottles").json[7]["level"] == 1.0
    client.post("/api/bottles/swap", json={"a": 1, "b": 8})
    b = client.get("/api/bottles").json
    assert b[0]["name"] == "Mezcal" and b[0]["slot"] == 1 and b[7]["name"] == "Gin"


def test_recipe_crud_favorite_import_export(client):
    d = {"name": "Test Fizz", "ingredients": [{"bottle": "Gin", "parts": 1}, {"bottle": "Soda Water", "parts": 2}]}
    assert client.post("/api/recipes", json=d).json["available"]
    assert client.post("/api/recipes/Test Fizz/favorite").json["favorite"] is True
    client.post("/api/recipes", json={**d, "name": "Renamed Fizz", "replace": "Test Fizz"})
    names = [r["name"] for r in client.get("/api/recipes").json]
    assert "Renamed Fizz" in names and "Test Fizz" not in names
    exported = client.get("/api/recipes/export").json
    client.delete("/api/recipes/Renamed Fizz")
    assert client.post("/api/recipes/import", json=exported).json["added"] == len(exported)
    assert client.post("/api/recipes", json={"name": "", "ingredients": []}).status_code == 400


def test_pour_with_strength_and_stats(client):
    client.halo.scale.place(250)
    r = client.post("/api/pour", json={"drink": "Daiquiri", "size": "single", "strength": "strong"})
    assert r.status_code == 200
    st = client.halo.engine.wait(20)
    assert st.state.value == "done" and st.strength == "strong"
    stats = client.get("/api/stats").json
    assert stats["pours"] == 1 and stats["top_drinks"][0][0] == "Daiquiri"
    assert client.get("/api/bottles").json[1]["level"] < 1.0


def test_party_activate_and_end(client):
    r = client.post("/api/party", json={"theme": "Brunch", "drinks": ["Garden Highball", "Rum Fizz"]})
    assert r.json["active"]
    assert [d.name for d in client.halo.menu()] == ["Garden Highball", "Rum Fizz"]
    client.delete("/api/party")
    assert len(client.halo.menu()) > 2


def test_prefs_and_ring(client):
    p = client.put("/api/prefs", json={"led_brightness": 5, "ambient": False, "pour_oz": 9}).json
    assert p["led_brightness"] == 0.6 and p["ambient"] is False
    assert client.halo.engine._setting("pour_oz") == pytest.approx(4.0)      # clamped
    client.put("/api/prefs", json={"pour_oz": None})
    client.post("/api/ring", json={"mode": "listening", "seconds": 5})
    client.halo.idle_ring(time.monotonic())
    assert client.halo.halo.last == "listening"


def test_voice_can_be_disabled(client):
    client.put("/api/prefs", json={"voice_enabled": False})
    assert client.post("/api/voice", json={"text": "a margarita"}).status_code == 403


def test_pumps_report_and_reset(client):
    client.halo.learning.record(1, "Gin", 1.6, 2.5, 0.4)
    rows = client.get("/api/pumps").json
    assert rows[0]["bottle"] == "Gin" and rows[0]["pours"] == 1 and rows[0]["ml_per_min"] > 90
    assert client.post("/api/pumps/1/reset").json["ok"]
    assert client.get("/api/pumps").json[0]["pours"] == 0


def _drive(client, answers, timeout=30):
    """Answer a maintenance job's prompts by title until it finishes; return the titles seen."""
    m, seen = client.halo.maintenance, []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = m.to_dict()
        if st["state"] == "waiting":
            title = st["prompt"]["title"]
            seen.append(title)
            action, data, before = answers[title]
            if before:
                before()
            client.post("/api/maintenance/answer", json={"action": action, "data": data})
            while m.to_dict()["state"] == "waiting" and m.to_dict()["prompt"]["title"] == title:
                time.sleep(0.02)
        elif not st["active"]:
            return seen
        time.sleep(0.02)
    raise AssertionError(f"job didn't finish; stuck at {m.to_dict()}")


def _fast(m):
    m.cfg.update(soap_g=4, rinse_g=4, rinses=1, soak_s=0.2, purge_s=0.05, prime_g=2, prime_max_s=1.5,
                 flow_s=0.3, catch_max_g=40)


def test_self_clean_runs_every_line_and_locks_pouring(client):
    app = client.halo
    _fast(app.maintenance)
    scale = app.scale
    assert client.post("/api/maintenance/start", json={"kind": "clean"}).status_code == 200
    r = client.post("/api/pour", json={"drink": "Daiquiri"})
    assert r.status_code == 409 and "cleaning" in r.json["error"]          # locked while cleaning
    seen = _drive(client, {
        "Set down a catch cup": ("next", {}, lambda: scale.place(300)),
        "Soapy water": ("next", {}, None),
        "Empty the catch cup": ("next", {}, None),
        "Clean water": ("next", {}, None),
        "Dry the lines": ("next", {}, None),
        "Put the bottles back": ("prime", {}, None),
    })
    st = app.maintenance.to_dict()
    assert st["state"] == "done", st
    assert "Empty the catch cup" in seen                                   # catch cup never overflows
    assert {r["label"] for r in st["results"]} >= {"Soap pass", "Rinse", "Lines dried"}
    assert all(r["ok"] for r in st["results"])
    assert app.prefs["last_clean"] and not app.engine.locked_by


def test_setup_check_scale_lights_and_pumps(client):
    app = client.halo
    _fast(app.maintenance)
    scale = app.scale
    client.post("/api/maintenance/start", json={"kind": "setup"})
    _drive(client, {
        "Clear the stage": ("next", {}, None),
        "Check the scale": ("weigh", {"known_g": 383}, lambda: scale.place(383)),
        "Lights": ("yes", {}, lambda: scale.place(-383)),
        "Pumps": ("next", {}, lambda: scale.place(200)),
        "Empty the catch cup": ("next", {}, None),
    })
    st = app.maintenance.to_dict()
    assert st["state"] == "done" and st["message"] == "All good.", st
    pumps = [r for r in st["results"] if r["label"].startswith("Pump ")]
    assert len(pumps) == 8 and all("ml/min" in r["detail"] for r in pumps)


def test_cancel_cleaning(client):
    client.post("/api/maintenance/start", json={"kind": "clean"})
    time.sleep(0.1)
    client.post("/api/maintenance/cancel")
    st = client.halo.maintenance.wait(5)
    assert st["state"] == "cancelled" and client.halo.pumps.running == {}
    assert not client.halo.engine.locked_by
