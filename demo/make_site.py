"""Render reports/forecast_now.csv as a static demo page (no server needed).

    python demo/make_site.py reports/forecast_now.csv site

Writes site/index.html (tile map + table, all data inlined) and
site/forecast.json. Uses only the standard library and pandas.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

UK = {
    "Volynska oblast": "Волинська", "Rivnenska oblast": "Рівненська", "Zhytomyrska oblast": "Житомирська",
    "Kyivska oblast": "Київська", "Chernihivska oblast": "Чернігівська", "Sumska oblast": "Сумська",
    "Lvivska oblast": "Львівська", "Ternopilska oblast": "Тернопільська", "Khmelnytska oblast": "Хмельницька",
    "Kyiv City": "м. Київ", "Cherkaska oblast": "Черкаська", "Poltavska oblast": "Полтавська",
    "Kharkivska oblast": "Харківська", "Zakarpatska oblast": "Закарпатська",
    "Ivano-Frankivska oblast": "Івано-Франківська", "Chernivetska oblast": "Чернівецька",
    "Vinnytska oblast": "Вінницька", "Kirovohradska oblast": "Кіровоградська",
    "Dnipropetrovska oblast": "Дніпропетровська", "Donetska oblast": "Донецька", "Odeska oblast": "Одеська",
    "Mykolaivska oblast": "Миколаївська", "Zaporizka oblast": "Запорізька", "Khersonska oblast": "Херсонська",
}
# (column, row) on a schematic tile map of Ukraine.
TILES = {
    "Volynska oblast": (1, 0), "Rivnenska oblast": (2, 0), "Zhytomyrska oblast": (3, 0),
    "Kyivska oblast": (4, 0), "Chernihivska oblast": (5, 0), "Sumska oblast": (6, 0),
    "Lvivska oblast": (0, 1), "Ternopilska oblast": (1, 1), "Khmelnytska oblast": (2, 1),
    "Vinnytska oblast": (3, 1), "Kyiv City": (4, 1), "Poltavska oblast": (5, 1), "Kharkivska oblast": (6, 1),
    "Zakarpatska oblast": (0, 2), "Ivano-Frankivska oblast": (1, 2), "Chernivetska oblast": (2, 2),
    "Kirovohradska oblast": (3, 2), "Cherkaska oblast": (4, 2), "Dnipropetrovska oblast": (5, 2),
    "Donetska oblast": (6, 2), "Odeska oblast": (2, 3), "Mykolaivska oblast": (3, 3),
    "Khersonska oblast": (4, 3), "Zaporizka oblast": (5, 3),
}

PAGE = """<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Прогноз тривог</title>
<style>
:root {
  --bg: #fbfaf8; --surface: #ffffff; --ink: #1d1d1b; --ink2: #5c5b57; --line: #e4e2dc;
  --s0: #cde2fb; --s1: #0d366b; --focus: #256abf;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #1b1b19; --surface: #242422; --ink: #f2f1ee; --ink2: #b0aea8; --line: #3a3936;
          --s0: #2a3442; --s1: #86b6ef; --focus: #86b6ef; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
       font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--ink2); margin: 0 0 18px; }
.warn { border-left: 3px solid var(--line); padding: 6px 12px; color: var(--ink2); margin: 0 0 18px; }
.controls { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-bottom: 14px; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.seg button { background: var(--surface); color: var(--ink); border: 0; padding: 6px 12px; cursor: pointer;
              font: inherit; border-right: 1px solid var(--line); }
.seg button:last-child { border-right: 0; }
.seg button[aria-pressed="true"] { background: var(--ink); color: var(--bg); }
.map { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; margin-bottom: 8px; }
.tile { aspect-ratio: 1.15; border-radius: 4px; padding: 5px 6px; display: flex; flex-direction: column;
        justify-content: space-between; min-width: 0; outline-offset: 2px; }
.tile:focus-visible { outline: 2px solid var(--focus); }
.tile .n { font-size: 11px; line-height: 1.15; overflow: hidden; text-overflow: ellipsis; }
.tile .v { font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; }
.legend { display: flex; align-items: center; gap: 8px; color: var(--ink2); font-size: 13px; margin-bottom: 24px; }
.legend .bar { width: 160px; height: 10px; border-radius: 2px; background: linear-gradient(90deg, var(--s0), var(--s1)); }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 5px 6px; border-bottom: 1px solid var(--line); }
th:first-child, td:first-child { text-align: left; }
th { color: var(--ink2); font-weight: 500; font-size: 13px; }
.tablewrap { overflow-x: auto; }
footer { color: var(--ink2); font-size: 13px; margin-top: 24px; }
@media (max-width: 560px) { .tile .n { font-size: 9px; } .tile .v { font-size: 13px; } .tile { padding: 3px; } }
</style>
</head>
<body>
<main>
  <h1>Прогноз повітряних тривог по областях</h1>
  <p class="sub">Станом на __ORIGIN__ (Київ). Оновлено __BUILT__ UTC.</p>
  <p class="warn">Навчальний статистичний прогноз. Не замінює офіційні сигнали тривоги: завжди дійте за сиреною та застосунком «Повітряна тривога».</p>
  <div class="controls">
    <div class="seg" role="group" aria-label="Що показати">
      <button data-t="active" aria-pressed="true">Тривога триває</button>
      <button data-t="onset" aria-pressed="false">Почнеться нова</button>
    </div>
    <div class="seg" role="group" aria-label="Горизонт" id="hz"></div>
  </div>
  <p class="sub" id="desc"></p>
  <div class="map" id="map" role="img" aria-label="Схематична карта ймовірностей по областях"></div>
  <div class="legend"><span>0%</span><span class="bar"></span><span>100%</span></div>
  <div class="tablewrap"><table id="tbl"></table></div>
  <footer>Модель: LightGBM на історії тривог (волонтерський датасет eTryvoga). Луганська область і Крим не прогнозуються.</footer>
</main>
<script>
const DATA = __DATA__;
const H = DATA.horizons;
let t = "active", h = H[0];
const css = getComputedStyle(document.documentElement);
function hex(c) { c = c.trim().replace("#", ""); return [0, 2, 4].map(i => parseInt(c.slice(i, i + 2), 16)); }
function color(p) {
  const a = hex(css.getPropertyValue("--s0")), b = hex(css.getPropertyValue("--s1"));
  const m = a.map((x, i) => Math.round(x + (b[i] - x) * p));
  const lum = (0.2126 * m[0] + 0.7152 * m[1] + 0.0722 * m[2]) / 255;
  return [`rgb(${m.join(",")})`, lum > 0.55 ? "#1d1d1b" : "#ffffff"];
}
const pct = p => Math.round(p * 100) + "%";
const desc = () => t === "active"
  ? `Ймовірність, що в області буде тривога протягом години №${h} від моменту прогнозу.`
  : `Ймовірність, що нова тривога почнеться протягом найближчих ${h} год.`;
function render() {
  document.getElementById("desc").textContent = desc();
  const map = document.getElementById("map");
  map.innerHTML = "";
  for (const r of DATA.rows) {
    const p = r[`${t}_${h}h`];
    const [bg, fg] = color(p);
    const d = document.createElement("div");
    d.className = "tile"; d.tabIndex = 0;
    d.style.gridColumn = r.col + 1; d.style.gridRow = r.row + 1;
    d.style.background = bg; d.style.color = fg;
    d.title = `${r.name}: ${pct(p)}`;
    d.innerHTML = `<span class="n">${r.name}</span><span class="v">${pct(p)}</span>`;
    map.appendChild(d);
  }
  const rows = [...DATA.rows].sort((a, b) => b[`${t}_${h}h`] - a[`${t}_${h}h`]);
  const head = `<tr><th>Область</th>${H.map(x => `<th>${x} год</th>`).join("")}</tr>`;
  document.getElementById("tbl").innerHTML = head + rows.map(r =>
    `<tr><td>${r.name}</td>${H.map(x => `<td>${pct(r[`${t}_${x}h`])}</td>`).join("")}</tr>`).join("");
}
const hz = document.getElementById("hz");
for (const x of H) {
  const b = document.createElement("button");
  b.dataset.h = x; b.textContent = `${x} год`; b.setAttribute("aria-pressed", x === h);
  hz.appendChild(b);
}
document.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
  if (b.dataset.t) t = b.dataset.t; else h = +b.dataset.h;
  document.querySelectorAll("[data-t]").forEach(x => x.setAttribute("aria-pressed", x.dataset.t === t));
  document.querySelectorAll("[data-h]").forEach(x => x.setAttribute("aria-pressed", +x.dataset.h === h));
  render();
}));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
render();
</script>
</body>
</html>
"""


def main(csv_path: str, out_dir: str) -> None:
    df = pd.read_csv(csv_path)
    horizons = sorted({int(c.split("_")[1][:-1]) for c in df.columns if c.startswith("active_")})
    rows = []
    for rec in df.to_dict("records"):
        col, row = TILES.get(rec["region"], (6, 4))
        rows.append({**rec, "name": UK.get(rec["region"], rec["region"]), "col": col, "row": row})
    meta_path = Path(csv_path).with_name("forecast_meta.json")
    origin = json.loads(meta_path.read_text())["origin_kyiv"] if meta_path.exists() else "останню годину даних"
    built = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    payload = {"horizons": horizons, "rows": rows, "built_utc": built, "origin_kyiv": origin}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "forecast.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    html = (PAGE.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
            .replace("__ORIGIN__", origin).replace("__BUILT__", built))
    (out / "index.html").write_text(html, encoding="utf-8")
    print(f"wrote {out / 'index.html'} ({len(rows)} regions, horizons {horizons})")


if __name__ == "__main__":
    main(*sys.argv[1:3])
