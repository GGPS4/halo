"""Phone-friendly setup page plus a small JSON API (used by the touchscreen and by Levi)."""
from __future__ import annotations

from flask import Flask, jsonify, render_template_string, request

from .bartender import MOODS, BartenderError
from .config import Bottle
from .pour import PourError
from .recipes import Ingredient, Recipe


def create_app(halo) -> Flask:
    """`halo` is the App object from main.py (inventory, book, engine, bartender)."""
    app = Flask(__name__)

    @app.get("/")
    def index():
        return render_template_string(PAGE, bottles=halo.inventory.bottles,
                                      recipes=halo.book.recipes, moods=MOODS)

    # ---- bottles
    @app.get("/api/bottles")
    def bottles():
        return jsonify([b.__dict__ for b in halo.inventory.bottles])

    @app.put("/api/bottles/<int:slot>")
    def set_bottle(slot):
        if not 1 <= slot <= 8:
            return jsonify(error="slot must be 1-8"), 400
        d = request.get_json(force=True)
        halo.inventory.bottles[slot - 1] = Bottle(
            slot=slot, name=d.get("name", "").strip(), abv=float(d.get("abv", 0) or 0),
            density=float(d.get("density", 1.0) or 1.0), color=d.get("color", "#c9a46a"),
        )
        halo.inventory.save()
        return jsonify(ok=True)

    # ---- recipes
    @app.get("/api/recipes")
    def recipes():
        inv = halo.inventory
        return jsonify([{**r.to_dict(), "available": not r.missing(inv)} for r in halo.book.recipes])

    @app.post("/api/recipes")
    def add_recipe():
        d = request.get_json(force=True)
        try:
            r = Recipe.from_dict({**d, "ingredients": [dict(i) for i in d["ingredients"]]})
        except (KeyError, TypeError) as e:
            return jsonify(error=f"bad recipe: {e}"), 400
        halo.book.add(r)
        return jsonify(ok=True)

    @app.delete("/api/recipes/<name>")
    def delete_recipe(name):
        halo.book.remove(name)
        return jsonify(ok=True)

    @app.post("/api/bartender")
    def ask():
        d = request.get_json(force=True)
        try:
            r = halo.bartender.create(halo.inventory, d.get("request", "surprise"))
        except BartenderError as e:
            return jsonify(error=str(e)), 400
        if d.get("save", True):
            halo.book.add(r)
        return jsonify(r.to_dict())

    # ---- pouring
    @app.post("/api/pour")
    def pour():
        d = request.get_json(force=True)
        r = halo.book.get(d.get("drink", ""))
        if r is None and d.get("request"):
            try:
                r = halo.bartender.create(halo.inventory, d["request"])
                halo.book.add(r)
            except BartenderError as e:
                return jsonify(error=str(e)), 400
        if r is None:
            return jsonify(error="Unknown drink"), 404
        try:
            halo.engine.start(r, d.get("size", "single"))
        except (PourError, ValueError) as e:
            return jsonify(error=str(e)), 409
        return jsonify(ok=True, drink=r.name)

    @app.post("/api/cancel")
    def cancel():
        halo.engine.cancel()
        return jsonify(ok=True)

    @app.get("/api/status")
    def status():
        p = halo.engine.progress
        return jsonify(state=p.state.value, drink=p.drink, step=p.step, fraction=round(p.fraction, 3),
                       poured_g=round(p.poured_g, 1), message=p.message, report=p.report)

    return app


PAGE = """<!doctype html><html><head><meta name=viewport content="width=device-width,initial-scale=1">
<title>Halo</title><style>
:root{--bg:#0d0d0f;--card:#17171b;--line:#2a2a30;--fg:#f1ede6;--mute:#8d8a85;--accent:#f0aa50}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:720px;margin:auto;padding:24px}h1{font-weight:300;letter-spacing:.3em;font-size:28px;margin:8px 0 24px}
h2{font-size:13px;letter-spacing:.15em;text-transform:uppercase;color:var(--mute);margin:32px 0 12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin-bottom:10px}
.row{display:grid;grid-template-columns:28px 1fr 70px 70px 36px;gap:8px;align-items:center}
input{background:#0f0f12;border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px;width:100%}
input[type=color]{padding:2px;height:36px}button{background:var(--accent);color:#1a1206;border:0;border-radius:999px;padding:9px 16px;font-weight:600;cursor:pointer}
button.ghost{background:transparent;color:var(--fg);border:1px solid var(--line)}.chips button{margin:0 6px 8px 0}
.mute{color:var(--mute)}#status{position:sticky;top:0;background:var(--bg);padding:8px 0;color:var(--accent)}
</style></head><body><main>
<h1>HALO</h1><div id=status></div>
<h2>Bottles</h2>
{% for b in bottles %}<div class="card row">
<b>{{b.slot}}</b><input id=n{{b.slot}} value="{{b.name}}" placeholder="empty">
<input id=a{{b.slot}} value="{{b.abv}}" title="ABV %"><input id=d{{b.slot}} value="{{b.density}}" title="g/ml">
<input type=color id=c{{b.slot}} value="{{b.color}}"></div>{% endfor %}
<button onclick=saveBottles()>Save bottles</button>
<h2>Ask the bartender</h2><div class=chips>{% for k in moods %}<button class=ghost onclick="ask('{{k}}')">{{k}}</button>{% endfor %}</div>
<div class=card><input id=req placeholder="or describe it: smoky, a little sweet, not too strong"><br><br>
<button onclick="ask(document.getElementById('req').value)">Create drink</button></div>
<h2>Drinks</h2><div id=drinks></div>
</main><script>
const j=(u,m='GET',b)=>fetch(u,{method:m,headers:{'content-type':'application/json'},body:b&&JSON.stringify(b)}).then(r=>r.json());
async function saveBottles(){for(let s=1;s<=8;s++){await j('/api/bottles/'+s,'PUT',{name:n(s).value,abv:a(s).value,density:d(s).value,color:c(s).value})}load()}
const n=s=>document.getElementById('n'+s),a=s=>document.getElementById('a'+s),d=s=>document.getElementById('d'+s),c=s=>document.getElementById('c'+s);
async function ask(r){st('Thinking up a drink…');const x=await j('/api/bartender','POST',{request:r});st(x.error||('New drink: '+x.name));load()}
async function pour(name,size){const x=await j('/api/pour','POST',{drink:name,size});st(x.error||'Place a glass on the halo')}
function st(t){document.getElementById('status').textContent=t}
async function load(){const rs=await j('/api/recipes');document.getElementById('drinks').innerHTML=rs.map(r=>`<div class=card>
<b style="color:${r.color}">●</b> <b>${r.name}</b> <span class=mute>${r.created_by=='claude'?'· by Claude':''}</span><br>
<span class=mute>${r.ingredients.map(i=>i.parts+' '+i.bottle).join(' · ')}</span><br>${r.description}<br><br>
${r.available?`<button onclick="pour('${r.name.replace(/'/g,"\\\\'")}','single')">Single</button> <button class=ghost onclick="pour('${r.name.replace(/'/g,"\\\\'")}','double')">Double</button>`:'<span class=mute>Missing a bottle</span>'}</div>`).join('')}
setInterval(async()=>{const s=await j('/api/status');if(s.state!='idle')st(s.message+(s.state=='pouring'?' '+Math.round(s.fraction*100)+'%':''))},1000);load();
</script></body></html>"""
