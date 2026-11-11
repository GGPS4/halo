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
    app.engine.cfg.update(single_oz=0.5, stall_seconds=0.6)
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
    assert len(s["bottles"]) == 8 and s["recipes"] and "single" in s["sizes"]
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
    p = client.put("/api/prefs", json={"led_brightness": 5, "ambient": False, "single_oz": 1.5}).json
    assert p["led_brightness"] == 0.6 and p["ambient"] is False
    assert client.halo.engine.size_ml("single") == pytest.approx(1.5 * 29.57)
    client.post("/api/ring", json={"mode": "listening", "seconds": 5})
    client.halo.idle_ring(time.monotonic())
    assert client.halo.halo.last == "listening"


def test_voice_can_be_disabled(client):
    client.put("/api/prefs", json={"voice_enabled": False})
    assert client.post("/api/voice", json={"text": "a margarita"}).status_code == 403
