/* Halo recipe library: classic drinks from TheCocktailDB (thecocktaildb.com, free public API)
 * that the loaded bottles can make. Runs in the browser, so it works the same on Halo and in
 * the demo. Each drink is matched to your bottles by ingredient name, its measures are turned
 * into parts, and garnishes are kept as a note. Drinks that need shaking with cream, milk or
 * egg are left out, because Halo pours straight into a cup of ice.
 */
(() => {
  const API = "https://www.thecocktaildb.com/api/json/v1/1/";
  const WEEK = 7 * 86400e3;

  // your bottle (lowercase) -> TheCocktailDB ingredient names it can stand in for
  const SYN = {
    "gin": ["gin", "london dry gin"], "vodka": ["vodka"], "white rum": ["light rum", "white rum", "rum"],
    "light rum": ["light rum", "white rum", "rum"], "rum": ["rum", "light rum", "white rum"], "dark rum": ["dark rum", "rum"],
    "spiced rum": ["spiced rum"], "bourbon": ["bourbon", "whiskey", "whisky", "blended whiskey"],
    "whiskey": ["whiskey", "whisky", "bourbon", "blended whiskey"], "rye": ["rye whiskey", "whiskey"],
    "scotch": ["scotch", "blended scotch"], "tequila": ["tequila"], "tequila blanco": ["tequila"], "mezcal": ["mezcal"],
    "orange liqueur": ["triple sec", "cointreau", "grand marnier", "orange liqueur", "orange curacao"],
    "triple sec": ["triple sec", "cointreau", "orange liqueur"], "cointreau": ["cointreau", "triple sec"],
    "lime juice": ["lime juice", "lime"], "lemon juice": ["lemon juice", "lemon"],
    "simple syrup": ["sugar syrup", "simple syrup", "syrup", "sugar"], "sugar syrup": ["sugar syrup", "simple syrup", "sugar"],
    "soda water": ["soda water", "club soda", "carbonated water"], "club soda": ["club soda", "soda water", "carbonated water"],
    "tonic": ["tonic water"], "tonic water": ["tonic water"], "ginger beer": ["ginger beer"], "ginger ale": ["ginger ale"],
    "cola": ["coca-cola", "cola"], "cranberry juice": ["cranberry juice"], "orange juice": ["orange juice"],
    "pineapple juice": ["pineapple juice"], "grapefruit juice": ["grapefruit juice"], "grenadine": ["grenadine"],
    "sweet vermouth": ["sweet vermouth", "vermouth"], "dry vermouth": ["dry vermouth", "vermouth"],
    "campari": ["campari"], "aperol": ["aperol"], "prosecco": ["prosecco", "champagne", "sparkling wine"],
    "amaretto": ["amaretto"], "coffee liqueur": ["kahlua", "coffee liqueur"], "peach schnapps": ["peach schnapps"],
  };
  const GARNISH = /^(ice|crushed ice|mint|mint leaves?|salt|cherry|maraschino cherry|olive|cucumber|nutmeg|cinnamon|orange peel|lemon peel|lime peel|orange|lemon|lime|celery|pepper|nutmeg|sugar cubes?|strawberries|raspberries|blackberries|blueberries)$/;
  const BITTERS = /bitters/;
  const NEEDS_SHAKING = /(cream|milk|egg|yoghurt|yogurt|coconut cream|ice cream)/;

  const norm = s => String(s || "").toLowerCase().replace(/\s+/g, " ").trim();
  const cache = {
    get(k) { try { const v = JSON.parse(localStorage.getItem("halo-lib:" + k)); return v && Date.now() - v.t < WEEK ? v.d : null; } catch (e) { return null; } },
    set(k, d) { try { localStorage.setItem("halo-lib:" + k, JSON.stringify({t: Date.now(), d})); } catch (e) {} },
  };
  async function getJSON(path) {
    const hit = cache.get(path); if (hit) return hit;
    const r = await fetch(API + path); if (!r.ok) throw new Error(`TheCocktailDB answered ${r.status}`);
    const d = await r.json(); cache.set(path, d); return d;
  }

  // which of your bottles stands in for a TheCocktailDB ingredient (or null)
  function namesFor(bottle) {
    const n = norm(bottle);
    if (SYN[n]) return SYN[n];
    const key = Object.keys(SYN).sort((a, b) => b.length - a.length).find(k => new RegExp(`\\b${k}\\b`).test(n));
    return key ? SYN[key] : [n];
  }
  function bottleFor(ing, measure, bottles) {
    const n = norm(ing);
    if ((n === "lime" || n === "lemon") && !/juice|oz|cl|ml|part|shot|tsp|tbsp/.test(norm(measure))) return null;
    let best = null, rank = 99;
    for (const b of bottles) {
      const i = namesFor(b.name).indexOf(n);
      if (i >= 0 && i < rank) { best = b; rank = i; }
    }
    return best;
  }

  // a TheCocktailDB measure in ounces; null when unknown
  function toOz(measure, ing) {
    const m = norm(measure);
    if (!m) return null;
    if (/top|fill|to top/.test(m)) return 3;
    if (/splash/.test(m)) return 0.25;
    if (/juice of/.test(m)) return 1 * (num(m) || 1);
    const q = num(m); if (q == null) return null;
    if (/\bcl\b|cl$/.test(m)) return q / 2.957;
    if (/\bml\b|ml$/.test(m)) return q / 29.57;
    if (/shot|jigger/.test(m)) return q * 1.5;
    if (/tsp|teaspoon/.test(m)) return q / 6;
    if (/tblsp|tbsp|tablespoon/.test(m)) return q / 2;
    if (/cube/.test(m)) return q / 6;            // a sugar cube is about a teaspoon
    if (/cup/.test(m)) return q * 8;
    if (/dash/.test(m)) return q / 32;
    return q;                                   // oz, parts, or a bare number
  }
  function num(m) {
    const range = m.match(/(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)/);
    if (range) return (+range[1] + +range[2]) / 2;
    const mixed = m.match(/(\d+)\s+(\d+)\/(\d+)/);
    if (mixed) return +mixed[1] + mixed[2] / mixed[3];
    const frac = m.match(/(\d+)\/(\d+)/);
    if (frac) return frac[1] / frac[2];
    const n = m.match(/\d+(?:\.\d+)?/);
    return n ? +n[0] : null;
  }

  // one TheCocktailDB drink -> what Halo would pour, or null if it isn't a fit
  function match(d, bottles) {
    const items = [];
    for (let i = 1; i <= 15; i++) {
      const ing = d["strIngredient" + i]; if (!ing || !ing.trim()) continue;
      items.push({ing: ing.trim(), measure: (d["strMeasure" + i] || "").trim()});
    }
    if (items.some(x => NEEDS_SHAKING.test(norm(x.ing)))) return null;
    const pour = [], missing = [], skipped = [], garnish = [];
    for (const x of items) {
      let n = norm(x.ing);
      if (/sugar$/.test(n) && !/syrup/.test(n)) n = "sugar";              // powdered, superfine...
      const b = bottleFor(n, x.measure, bottles);
      if (b) {
        let oz = toOz(x.measure, n);
        if (oz == null && /soda|tonic|ginger|cola|carbonated|lemonade|prosecco|champagne|sparkling/.test(n)) oz = 3;  // "top up"
        if (n === "sugar") oz = oz != null ? oz * 1.5 : 0.25;                 // granulated -> syrup
        const prev = pour.find(p => p.bottle === b.name);
        if (prev) prev.oz += oz ?? 1; else pour.push({bottle: b.name, oz: oz ?? 1, from: x.ing});
      } else if (BITTERS.test(n)) skipped.push(`${x.measure ? x.measure + " " : ""}${x.ing}`.trim());
      else if (GARNISH.test(n)) garnish.push(x.ing.toLowerCase());
      else if (n === "sugar") skipped.push("sugar");
      else if (n === "water") skipped.push("water");
      else missing.push(x.ing);
    }
    if (!pour.length || missing.length > 1) return null;
    const total = pour.reduce((a, p) => a + p.oz, 0);
    if (total <= 0) return null;
    const round = v => Math.max(0.25, Math.round(v * 4) / 4);
    return {
      id: d.idDrink, name: d.strDrink, thumb: d.strDrinkThumb ? d.strDrinkThumb + "/preview" : "",
      category: d.strCategory || "", glass: d.strGlass || "", alcoholic: d.strAlcoholic || "",
      canPour: missing.length === 0, missing, skipped, garnish,
      ingredients: pour.map(p => ({bottle: p.bottle, parts: round(p.oz)})),
      source: `https://www.thecocktaildb.com/drink/${d.idDrink}`,
    };
  }

  async function load(bottles, onProgress = () => {}) {
    bottles = bottles.filter(b => b.name);
    if (!bottles.length) return [];
    // search by each spirit or liqueur you have; drinks that use several of your bottles come first
    const base = bottles.filter(b => b.abv >= 15).map(b => namesFor(b.name)[0]);
    const seeds = [...new Set(base.length ? base : bottles.map(b => namesFor(b.name)[0]))];
    const score = new Map();
    for (const s of seeds) {
      const r = await getJSON(`filter.php?i=${encodeURIComponent(s)}`).catch(() => ({drinks: null}));
      for (const d of (Array.isArray(r.drinks) ? r.drinks : [])) score.set(d.idDrink, (score.get(d.idDrink) || 0) + 1);
    }
    const ids = [...score.entries()].sort((a, b) => b[1] - a[1]).slice(0, 90).map(e => e[0]);
    const out = [];
    let done = 0;
    const worker = async () => {
      while (ids.length) {
        const id = ids.shift();
        try {
          const r = await getJSON(`lookup.php?i=${id}`);
          const m = r.drinks && r.drinks[0] && match(r.drinks[0], bottles);
          if (m) out.push(m);
        } catch (e) {}
        onProgress(++done, done + ids.length);
      }
    };
    await Promise.all(Array.from({length: 6}, worker));
    return out.sort((a, b) => (b.canPour - a.canPour) || a.name.localeCompare(b.name));
  }

  window.HaloLibrary = {load, match, toOz};
})();
