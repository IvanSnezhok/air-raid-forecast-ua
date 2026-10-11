"use strict";

// ---------------------------------------------------------------- helpers
const $ = (sel, el = document) => el.querySelector(sel);
const SVGNS = "http://www.w3.org/2000/svg";
const KYIV_TZ = "Europe/Kyiv";
const fmtTime = new Intl.DateTimeFormat("uk-UA", { timeZone: KYIV_TZ, hour: "2-digit", minute: "2-digit" });
const fmtDay = new Intl.DateTimeFormat("uk-UA", { timeZone: KYIV_TZ, day: "numeric", month: "short" });
const fmtFull = new Intl.DateTimeFormat("uk-UA", { timeZone: KYIV_TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const kyivHour = (d) => Number(new Intl.DateTimeFormat("en-GB", { timeZone: KYIV_TZ, hour: "2-digit", hour12: false }).format(d)) % 24;
const pct = (p) => (p == null ? "–" : `${Math.round(p * 100)}%`);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const slug = (name) => name.replace(/ oblast$/, "").toLowerCase().replace(/ /g, "-");
const fullName = (r) => (r.name === "Kyiv City" ? "м. Київ" : `${r.name_uk} область`);
const LEVELS = [[0.15, "низька"], [0.4, "помірна"], [0.7, "висока"], [1.01, "дуже висока"]];
const levelIdx = (p) => LEVELS.findIndex(([x]) => p < x);
const level = (p) => (p == null ? "" : `<span class="lvl l${levelIdx(p)}">${LEVELS[levelIdx(p)][1]}</span>`);
const listUk = (names) => (names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} і ${names[names.length - 1]}`);
const num = (v, d = 3) => (v == null ? "–" : Number(v).toFixed(d));

function duration(fromIso, toDate = new Date()) {
  const m = Math.max(0, Math.round((toDate - new Date(fromIso)) / 60000));
  const h = Math.floor(m / 60);
  return h ? `${h} год ${m % 60} хв` : `${m} хв`;
}

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

// Static build (GitHub Pages): no server, /api/x?q is served from precomputed api/x.json.
const STATIC_SITE = !!document.querySelector('meta[name="airraid-static"]');
const apiUrl = (url) => (STATIC_SITE && url.startsWith("/api/") ? `${url.slice(1).split("?")[0]}.json` : url);

async function getJSON(url) {
  url = apiUrl(url);
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) {
    const err = new Error(`${url}: ${r.status}`);
    err.status = r.status;
    try { err.body = await r.json(); } catch (_) { /* ignore */ }
    throw err;
  }
  return r.json();
}

// Sequential ramp read from CSS custom properties, so it follows light/dark theme.
let RAMP = [];
function readRamp() {
  const cs = getComputedStyle(document.documentElement);
  RAMP = [0, 1, 2, 3, 4, 5].map((i) => {
    const hex = cs.getPropertyValue(`--r${i}`).trim().replace("#", "");
    return [0, 2, 4].map((j) => parseInt(hex.slice(j, j + 2), 16));
  });
}
function rampColor(p) {
  const t = Math.min(1, Math.max(0, p)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(t));
  const f = t - i;
  const c = RAMP[i].map((v, k) => Math.round(v + (RAMP[i + 1][k] - v) * f));
  return `rgb(${c.join(",")})`;
}
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const darkMode = () => window.matchMedia("(prefers-color-scheme: dark)").matches;
const invText = (p) => (darkMode() ? p > 0.6 : p > 0.55);

// ---------------------------------------------------------------- state
const S = {
  geo: null, state: null, forecast: null, metrics: null,
  layer: "now", target: "active", horizon: 1, selected: null, view: "map",
  // raion layer: geometry, /api/raions, selected raion, current viewBox (zoom)
  unit: "oblast", rgeo: null, raions: null, rsel: null, vb: null, zoomOblast: "",
};

// ---------------------------------------------------------------- map
function mapDefs(svg) {
  const defs = el("defs", {}, svg);
  const pat = el("pattern", { id: "hatch", width: 7, height: 7, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
  el("rect", { width: 7, height: 7, fill: css("--calm") }, pat);
  el("line", { x1: 0, y1: 0, x2: 0, y2: 7, stroke: css("--nodata"), "stroke-width": 3 }, pat);
  // part of a raion under alert (hromada-level alert): red stripes
  const part = el("pattern", { id: "part", width: 5, height: 5, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
  el("rect", { width: 5, height: 5, fill: css("--alert-soft") }, part);
  el("line", { x1: 0, y1: 0, x2: 0, y2: 5, stroke: css("--alert"), "stroke-width": 2.6 }, part);
}

function buildMap() {
  if (S.unit === "raion" && S.rgeo) { buildRaionMap(); return; }
  const svg = $("#map");
  setFullView(S.geo.viewBox);
  svg.innerHTML = "";
  svg.classList.remove("raion-mode");
  svg.setAttribute("aria-label", "Мапа областей України");
  mapDefs(svg);

  const gReg = el("g", {}, svg);
  for (const [, d] of Object.entries(S.geo.other || {})) el("path", { d, class: "nodata" }, gReg);
  for (const [name, d] of Object.entries(S.geo.regions)) {
    const known = S.forecast?.regions.some((r) => r.name === name) || S.state?.regions.some((r) => r.name === name);
    const p = el("path", { d, class: known ? "reg" : "nodata", "data-id": slug(name) }, gReg);
    if (known) { p.setAttribute("tabindex", "0"); p.setAttribute("role", "button"); }
  }
  const c = S.geo.kyiv_city;
  el("circle", { cx: c.cx, cy: c.cy, r: c.r, class: "reg city", "data-id": "kyiv-city", tabindex: 0, role: "button" }, gReg);
  el("g", { id: "labels" }, svg);
  $("#attr").textContent = S.geo.attribution;
}

// Map events are attached once and work for both layers: oblasts carry data-id, raions data-rid.
function setupMapEvents() {
  const svg = $("#map");
  const tip = $("#tip");
  svg.addEventListener("pointermove", (ev) => {
    const t = ev.target.closest("[data-id], [data-rid]");
    const html = !t ? null : t.dataset.rid ? raionTipHTML(raionById(t.dataset.rid), t.dataset.rid) : (regionData(t.dataset.id) && tipHTML(regionData(t.dataset.id)));
    if (!html) { tip.hidden = true; return; }
    const box = $(".mapwrap").getBoundingClientRect();
    tip.style.left = `${ev.clientX - box.left}px`;
    tip.style.top = `${ev.clientY - box.top}px`;
    tip.innerHTML = html;
    tip.hidden = false;
  });
  svg.addEventListener("pointerleave", () => { tip.hidden = true; });
  svg.addEventListener("click", (ev) => {
    const t = ev.target.closest("[data-id], [data-rid]");
    if (!t) return;
    if (t.dataset.rid) { if (raionById(t.dataset.rid)) selectRaion(t.dataset.rid); }
    else if (regionData(t.dataset.id)) select(t.dataset.id);
  });
  svg.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const d = ev.target.dataset || {};
    if (d.rid && raionById(d.rid)) { ev.preventDefault(); selectRaion(d.rid); }
    else if (d.id) { ev.preventDefault(); select(d.id); }
  });
}

function regionData(id) {
  const st = S.state?.regions.find((r) => r.id === id);
  const fc = S.forecast?.regions.find((r) => r.id === id);
  if (!st && !fc) return null;
  return { ...(fc || {}), ...(st || {}), probs: fc?.probs };
}

// threat types: yellow (drones) -> red (missiles, ballistic, KAB, massed drones)
const LEVEL_UK = { yellow: "жовтий рівень", red: "червоний рівень" };
const TYPE_PHRASE = { drone: "ударні дрони", massed_drone: "масована атака дронів", kab: "КАБи", missile: "крилаті ракети", ballistic: "балістика" };
const typeMeta = (id) => S.forecast?.threats?.types.find((t) => t.id === id);
function hexRgb(h) { h = h.replace("#", ""); return [0, 2, 4].map((j) => parseInt(h.slice(j, j + 2), 16)); }
function levelColor(pRed) {
  const a = hexRgb(css("--yel")), b = hexRgb(css("--alert"));
  const t = Math.min(1, Math.max(0, pRed));
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * t)).join(",")})`;
}
const typeAt = (r) => r?.types?.[S.horizon];

function tipHTML(r) {
  if (S.layer === "forecast" && S.target === "type") {
    const t = typeAt(r);
    if (!t) return `<b>${esc(fullName(r))}</b><br>немає прогнозу типу`;
    return `<b>${esc(fullName(r))}</b><br>${LEVEL_UK[t.level] || ""}: найімовірніше ${TYPE_PHRASE[t.most_likely] || ""}`;
  }
  if (S.layer === "now") {
    const s = r.active ? `тривога ${r.since ? "вже " + duration(r.since) : ""}` : "тривоги немає";
    return `<b>${esc(fullName(r))}</b><br>${s}`;
  }
  const p = r.probs?.[S.target]?.[S.horizon];
  return `<b>${esc(fullName(r))}</b><br>${pct(p)}, ${p == null ? "" : LEVELS[levelIdx(p)][1]} ймовірність`;
}

function paintMap() {
  if (!S.geo) return;
  if (S.unit === "raion" && S.rgeo) { paintRaions(); return; }
  const svg = $("#map");
  const labels = $("#labels");
  labels.innerHTML = "";
  for (const node of svg.querySelectorAll(".reg")) {
    const r = regionData(node.dataset.id);
    node.classList.toggle("sel", node.dataset.id === S.selected);
    let fill = css("--calm");
    let label = null;
    let p = null;
    if (r && S.layer === "now") {
      fill = r.active ? css("--alert") : css("--calm");
    } else if (r && S.forecast && S.target === "type") {
      const t = typeAt(r);
      fill = t?.p_red == null ? css("--calm") : levelColor(t.p_red);
      label = t?.most_likely ? typeMeta(t.most_likely)?.short : null;
      p = t?.p_red > 0.75 ? 0.9 : 0;  // white text only on deep red
    } else if (r && S.forecast) {
      p = r.probs?.[S.target]?.[S.horizon];
      fill = p == null ? css("--calm") : rampColor(p);
      label = pct(p);
    }
    node.style.fill = fill;
    node.setAttribute("aria-label", r ? `${fullName(r)}: ${S.layer === "now" ? (r.active ? "тривога" : "немає тривоги") : pct(p)}` : "");
    const at = r && S.geo.labels[r.name];
    if (label && at) {
      const isCity = r.name === "Kyiv City";
      const t = el("text", { x: at[0], y: at[1] + (isCity ? -18 : 6), "text-anchor": "middle", class: "lbl" + (!isCity && invText(p) ? " inv" : "") }, labels);
      t.textContent = label;
    }
  }
  // the city circle goes on top of the oblast
  const city = svg.querySelector(".city");
  if (city) city.parentNode.appendChild(city);
  paintLegend();
}

function paintLegend() {
  const lg = $("#legend");
  if (S.unit === "raion" && S.layer === "now") {
    lg.innerHTML = `<span><i class="sw" style="background:${css("--alert")}"></i>Тривога на весь район</span>
      <span><i class="sw" style="background:repeating-linear-gradient(45deg,${css("--alert")} 0 2px,${css("--alert-soft")} 2px 4px)"></i>Тривога в частині району (громади)</span>
      <span><i class="sw" style="background:${css("--calm")}"></i>Немає тривоги</span>
      <span><i class="sw" style="background:repeating-linear-gradient(45deg,${css("--calm")} 0 3px,${css("--nodata")} 3px 6px)"></i>Немає даних (Крим, Луганщина)</span>
      ${liveRaions() ? "" : `<span class="warnnote">Немає живих даних по районах: стан районів показується, коли сервер має токен alerts.in.ua.</span>`}`;
    return;
  }
  if (S.layer === "now") {
    lg.innerHTML = `<span><i class="sw" style="background:${css("--alert")}"></i>Тривога</span>
      <span><i class="sw" style="background:${css("--calm")}"></i>Немає тривоги</span>
      <span><i class="sw" style="background:repeating-linear-gradient(45deg,${css("--calm")} 0 3px,${css("--nodata")} 3px 6px)"></i>Немає даних (Крим, Луганщина)</span>`;
  } else if (S.target === "type") {
    lg.innerHTML = `<span>жовтий</span><span class="bar" style="background:linear-gradient(90deg,${levelColor(0)},${levelColor(0.5)},${levelColor(1)})"></span><span>червоний</span>
      <span class="muted">жовтий: найімовірніше ударні дрони; червоний: ракети, балістика, КАБ або масована атака. Підпис: найімовірніший тип.</span>`;
  } else {
    const stops = [0, 0.2, 0.4, 0.6, 0.8, 1].map((p) => `${rampColor(p)} ${p * 100}%`).join(",");
    const t = S.unit === "raion" ? rTarget() : S.target;
    lg.innerHTML = `<span>0%</span><span class="bar" style="background:linear-gradient(90deg,${stops})"></span><span>100%</span>
      <span class="muted">ймовірність ${t === "active" ? "тривоги в цю годину" : "початку нової тривоги"}${S.unit === "raion" ? " у районі; наблизьте мапу, щоб побачити числа" : ""}</span>`;
  }
}

// ---------------------------------------------------------------- controls
function setupControls() {
  const seg = (id, key, after) => {
    $(id).addEventListener("click", (ev) => {
      const b = ev.target.closest("button");
      if (!b) return;
      S[key] = key === "horizon" ? Number(b.dataset.v) : b.dataset.v;
      for (const x of $(id).querySelectorAll("button")) x.setAttribute("aria-pressed", String(x === b));
      after && after();
      render();
    });
  };
  seg("#layer", "layer", () => {
    const f = S.layer === "forecast";
    $("#target").hidden = !f;
    $("#horizon").hidden = !f;
  });
  seg("#target", "target");
  seg("#horizon", "horizon");
  $("#unit").addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (b && b.dataset.v !== S.unit) setUnit(b.dataset.v);
  });
  $("#zoom-alerts").addEventListener("click", zoomToAlerts);
  $("#zoom-reset").addEventListener("click", () => { $("#zoom-info").textContent = ""; resetZoom(); });
  $("#zoom-oblast").addEventListener("change", (ev) => {
    S.zoomOblast = ev.target.value;
    if (S.unit === "raion") renderRank();
    if (S.zoomOblast) zoomToAlerts(); else { $("#zoom-info").textContent = ""; resetZoom(); }
  });
}

function renderHorizonButtons() {
  const typeMode = S.target === "type" && S.forecast?.threats;
  const hs = typeMode ? S.forecast.threats.horizons : S.forecast?.horizons || [1, 2, 3, 6];
  $('#target button[data-v="type"]').hidden = !S.forecast?.threats || S.unit === "raion";
  if (S.unit === "raion" && S.target === "type") {
    S.target = "active";
    for (const x of $("#target").querySelectorAll("button")) x.setAttribute("aria-pressed", String(x.dataset.v === "active"));
  }
  if (!hs.includes(S.horizon)) S.horizon = hs[0];
  const box = $("#horizon");
  if (box.dataset.hs === hs.join(",")) return;
  box.dataset.hs = hs.join(",");
  box.innerHTML = hs.map((h) => `<button data-v="${h}" aria-pressed="${h === S.horizon}">${h} год</button>`).join("");
}

function windowText() {
  if (S.layer === "now" && S.unit === "raion") {
    return liveRaions() ? `Живі дані alerts.in.ua по районах, ${fmtTime.format(new Date(S.raions.live_as_of))}` : "Немає живих даних по районах";
  }
  if (S.layer === "now") {
    if (!S.state) return "";
    return S.state.source === "alerts.in.ua"
      ? `Живі дані alerts.in.ua, ${fmtTime.format(new Date(S.state.as_of))}`
      : `Стан на кінець датасету: ${fmtFull.format(new Date(S.state.as_of))}`;
  }
  const w = S.forecast?.windows?.[S.horizon];
  if (!w) return "";
  if (S.target === "type") {
    const o = S.forecast.threats?.origin;
    return `Тип наступної тривоги протягом ${S.horizon} год${o ? `, прогноз на ${fmtTime.format(new Date(o.replace(" ", "T") + "Z"))}` : ""}`;
  }
  const [a, b] = S.target === "active" ? w.active : w.onset;
  return S.target === "active"
    ? `Тривога в інтервалі ${fmtTime.format(new Date(a))}–${fmtTime.format(new Date(b))}`
    : `Нова тривога між ${fmtTime.format(new Date(a))} і ${fmtTime.format(new Date(b))}`;
}

// ---------------------------------------------------------------- summary
function renderSummary() {
  const st = S.state, fc = S.forecast;
  const tiles = [];
  if (st) {
    const n = st.regions.filter((r) => r.active).length;
    tiles.push(`<div class="stat ${n ? "hot" : ""}"><div class="k">Під тривогою зараз</div>
      <div class="v">${n} <small>з ${st.regions.length} областей</small></div>
      <div class="s">${st.source === "alerts.in.ua" ? "alerts.in.ua, " + fmtTime.format(new Date(st.as_of)) : "за датасетом, " + fmtFull.format(new Date(st.as_of))}</div></div>`);
  }
  if (liveRaions()) {
    const on = activeRaions(), part = on.filter((r) => r.live.level === "hromada").length;
    tiles.push(`<div class="stat ${on.length ? "hot" : ""}"><div class="k">Районів під тривогою</div>
      <div class="v">${on.length} <small>з ${S.raions.raions.length}</small></div>
      <div class="s">${part ? `з них ${part} частково (окремі громади)` : "усі райони цілком"}</div></div>`);
  }
  if (fc) {
    const exp = fc.regions.reduce((s, r) => s + (r.probs.active[1] || 0), 0);
    tiles.push(`<div class="stat"><div class="k">Очікується під тривогою в першу годину</div>
      <div class="v">≈ ${exp.toFixed(1)} <small>областей</small></div><div class="s">сума ймовірностей по областях</div></div>`);
    const h3 = fc.horizons.includes(3) ? 3 : fc.horizons[0];
    const top = [...fc.regions].sort((a, b) => b.probs.onset[h3] - a.probs.onset[h3])[0];
    if (top) {
      tiles.push(`<div class="stat"><div class="k">Найімовірніша нова тривога за ${h3} год</div>
        <div class="v">${pct(top.probs.onset[h3])} <small>${esc(top.name_uk)}</small></div>
        <div class="s">прогноз станом на ${fmtFull.format(new Date(fc.origin))}</div></div>`);
    }
    tiles.push(`<div class="stat"><div class="k">Модель навчена</div>
      <div class="v" style="font-size:18px">${fc.model?.trained_at ? fmtFull.format(new Date(fc.model.trained_at)) : "–"}</div>
      <div class="s">історія до ${fmtFull.format(new Date(fc.history_end))}</div></div>`);
  }
  $("#summary").innerHTML = tiles.join("");
}

function renderHeadline() {
  const st = S.state, fc = S.forecast;
  if (!st || !fc) return;
  const on = st.regions.filter((r) => r.active);
  const live = st.source === "alerts.in.ua";
  const when = live ? "Зараз" : `На ${fmtTime.format(new Date(st.as_of))} (кінець датасету)`;
  let a = on.length
    ? `${when} тривога в <b class="hot">${on.length} ${on.length === 1 ? "області" : "областях"}</b>: ${esc(listUk(on.map((r) => r.name_uk)))}.`
    : `${when} тривог немає.`;
  const h = fc.horizons.includes(3) ? 3 : fc.horizons[0];
  const quiet = fc.regions.filter((r) => !on.some((x) => x.id === r.id));
  const top = [...quiet].sort((x, y) => y.probs.onset[h] - x.probs.onset[h]).filter((r) => r.probs.onset[h] >= 0.4).slice(0, 3);
  a += top.length
    ? ` Найближчими ${h} год нова тривога найімовірніша: ${top.map((r) => `${esc(r.name_uk)} <b>${pct(r.probs.onset[h])}</b>`).join(", ")}.`
    : ` Найближчими ${h} год висока ймовірність нової тривоги не очікується в жодній області без тривоги.`;
  if (liveRaions() && activeRaions().length) {
    const n = activeRaions().length;
    a += ` Під тривогою ${n} ${plural(n, "район", "райони", "районів")}: кнопка «До тривог» на мапі районів наблизить до них.`;
  }
  $("#headline").innerHTML = a;
  const dl = $("#region-list");
  if (!dl.childElementCount) {
    dl.innerHTML = fc.regions.map((r) => `<option value="${esc(r.name_uk)}"></option>`).join("")
      + (S.raions?.raions || []).map((r) => `<option value="${esc(r.name_uk)}">${esc(r.oblast_uk)}</option>`).join("");
  }
}

function renderBanner() {
  const w = [...(S.forecast?.warnings || [])];
  const b = $("#banner");
  b.hidden = !w.length;
  b.innerHTML = w.map((x) => `<p>${esc(x)}</p>`).join("");
}

// ---------------------------------------------------------------- ranking
function renderRank() {
  const fc = S.forecast;
  const t = $("#rank");
  if (S.unit === "raion" && S.raions) { renderRaionRank(); return; }
  $("#rank-title").textContent = "Усі області";
  if (!fc) { t.innerHTML = ""; return; }
  const hs = fc.horizons;
  const active = new Set((S.state?.regions || []).filter((r) => r.active).map((r) => r.id));
  const key = S.layer === "now" ? (r) => (active.has(r.id) ? 2 : 0) + r.probs.active[1]
    : S.target === "type" ? (r) => r.types?.[S.horizon]?.p_red ?? -1 : (r) => r.probs[S.target][S.horizon];
  const rows = [...fc.regions].sort((a, b) => key(b) - key(a));
  const cell = (p) => `<span class="cell" style="background:${rampColor(p)};color:${invText(p) ? "#fff" : "inherit"}">${pct(p)}</span>`;
  t.innerHTML = `<thead><tr><th>Область</th>${hs.map((h) => `<th>Тривога в ${h}-у год</th>`).join("")}${hs.map((h) => `<th>Нова за ${h} год</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr data-id="${r.id}" tabindex="0" class="${r.id === S.selected ? "sel" : ""}">
      <td>${esc(r.name_uk)}${active.has(r.id) ? '<span class="badge">тривога</span>' : ""}</td>
      ${hs.map((h) => `<td>${cell(r.probs.active[h])}</td>`).join("")}
      ${hs.map((h) => `<td>${cell(r.probs.onset[h])}</td>`).join("")}</tr>`).join("")}</tbody>`;
  $("#rank-sub").textContent = `прогноз станом на ${fmtFull.format(new Date(fc.origin))} за Києвом`;
}

// ---------------------------------------------------------------- detail
async function select(id) {
  S.selected = id;
  if (location.hash !== `#region/${id}`) history.replaceState(null, "", `#region/${id}`);
  paintMap();
  renderRank();
  const box = $("#detail");
  try {
    const d = await getJSON(`/api/regions/${encodeURIComponent(id)}`);
    if (S.selected === id) renderDetail(d);
  } catch (e) {
    box.innerHTML = `<p class="muted">Не вдалося завантажити дані області.</p>`;
  }
  if (window.matchMedia("(max-width: 900px)").matches) box.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderDetail(d) {
  const st = S.state?.regions.find((r) => r.id === d.id);
  const hs = Object.keys(d.probs.active).map(Number);
  const status = st?.active
    ? `<span class="status on"><span class="b"></span>Тривога${st.since ? ` з ${fmtTime.format(new Date(st.since))}, ${duration(st.since, S.state.source === "alerts.in.ua" ? new Date() : new Date(S.state.as_of))}` : ""}</span>`
    : `<span class="status off"><span class="b"></span>Тривоги немає</span>`;
  const locs = st?.locations?.length ? `<p class="small muted">Під тривогою: ${st.locations.map(esc).join(", ")}</p>` : "";
  const bar = (p) => `<div class="pbar"><div class="track"><div class="fill" style="width:${Math.round(p * 100)}%;background:${rampColor(Math.max(p, 0.15))}"></div></div><span class="num">${pct(p)}</span></div>`;
  const w = d.windows;
  const rows = hs.map((h) => `<tr><td class="h">${h} год<br><span class="small">${fmtTime.format(new Date(w[h].active[0]))}–${fmtTime.format(new Date(w[h].active[1]))}</span></td>
      <td>${bar(d.probs.active[h])}</td><td>${bar(d.probs.onset[h])}</td></tr>`).join("");
  const reg = S.metrics?.holdout?.by_region?.find((x) => x.region === d.name && x.target === "active" && x.horizon === 1);
  const h3 = hs.includes(3) ? 3 : hs[0];
  const LIKELY = ["малоймовірна", "можлива", "ймовірна", "дуже ймовірна"];
  const pa = d.probs.active[1], po = d.probs.onset[h3];
  const tt = d.types?.[3] || d.types?.[1];
  const typeLine = tt?.most_likely
    ? ` Якщо почнеться, найімовірніше це ${TYPE_PHRASE[tt.most_likely] || ""} (${LEVEL_UK[tt.level] || ""}).` : "";
  const brief = `<div class="brief">
    <p>Тривога в наступну годину ${LIKELY[levelIdx(pa)]}: ${level(pa)} ${pct(pa)}.</p>
    <p>Нова тривога протягом ${h3} год ${LIKELY[levelIdx(po)]}: ${level(po)} ${pct(po)}.${typeLine}</p></div>`;
  const ver = d.verification?.active_h1 || [];
  const done = ver.filter((x) => x.y != null);
  const right = done.filter((x) => (x.p >= 0.5) === (x.y === 1)).length;
  const strip = ver.length ? `<h3>Минулі прогнози і що сталося</h3>
    <div class="strip">${ver.map((x) => `<span class="c ${x.y == null ? "yn" : "y" + x.y}" style="background:${rampColor(x.p)}"
      title="${fmtTime.format(new Date(x.origin))}: прогноз ${pct(x.p)}, ${x.y == null ? "ще невідомо" : x.y ? "тривога була" : "тривоги не було"}"></span>`).join("")}</div>
    <div class="strip-axis"><span>${fmtFull.format(new Date(ver[0].origin))}</span><span>${fmtTime.format(new Date(ver[ver.length - 1].origin))}</span></div>
    <p class="small muted">Колір: прогноз тривоги на наступну годину. Крапка: тривога була, кружечок: не було.
      ${done.length ? `Справдилось ${right} з ${done.length} (поріг 50%).` : "Результати зʼявляться, щойно мине перша прогнозована година."}</p>` : "";
  $("#detail").innerHTML = `
    <h2>${esc(fullName(d))}</h2>${status}${locs}${brief}
    <p class="small muted">${S.state?.source === "alerts.in.ua" ? "Стан за alerts.in.ua" : "Стан на кінець датасету"}; прогноз станом на ${d.origin_kyiv} за Києвом.</p>
    <h3>Прогноз</h3>
    <table class="probs"><thead><tr><th></th><th>Тривога в цю годину</th><th>Нова тривога за цей час</th></tr></thead><tbody>${rows}</tbody></table>
    ${typesHTML(d.types)}
    ${strip}
    <h3>Останні 72 години і прогноз</h3>
    <div id="ch-hist"></div>
    <div class="chart-legend"><span><i style="background:${css("--alert")}"></i>частка години під тривогою</span><span><i style="background:${rampColor(0.5)}"></i>прогноз: ймовірність тривоги</span></div>
    ${d.daily_minutes?.values?.length ? `<h3>Календар тривог за рік</h3><div id="ch-cal"></div>
    <p class="small muted">Кожна клітинка: доба за Києвом, колір: скільки часу область була під тривогою. Наведіть, щоб побачити дату.</p>
    <h3>Дні тижня</h3><div id="ch-dow"></div>
    <div class="chart-legend"><span><i style="background:${css("--alert")}"></i>останні 8 тижнів</span><span><i style="background:${css("--ink3")}"></i>за рік</span></div>
    <p class="small muted">Частка днів з тривогою за кожним днем тижня.</p>` : ""}
    <details class="more"><summary>Детальніше для фахівців</summary>
    <h3>Типова доба (останні 28 днів)</h3>
    <div id="ch-prof"></div>
    <p class="small muted">Частка днів, коли в цю годину за Києвом була тривога. Поточна година виділена.</p>
    ${d.holdout?.active_h1 ? `<h3>Як модель вгадувала минулого тижня</h3><div id="ch-ho"></div>
    <div class="chart-legend"><span><i style="background:${css("--alert-soft")};border:1px solid ${css("--alert")}"></i>тривога була</span><span><i style="background:${css("--accent")}"></i>прогноз на наступну годину</span></div>
    ${reg ? `<p class="small muted">Для цієї області: ROC AUC ${num(reg.roc_auc, 2)}, Brier ${num(reg.brier, 3)}, покращення відносно кліматології ${signedPct(reg.bss_vs_clim)}.</p>` : ""}` : ""}
    </details>`;
  if (d.daily_minutes?.values?.length) { calendarChart($("#ch-cal"), d.daily_minutes); dowChart($("#ch-dow"), d.daily_minutes); }
  histChart($("#ch-hist"), d);
  profileChart($("#ch-prof"), d.hour_profile_28d);
  if (d.holdout?.active_h1) holdoutChart($("#ch-ho"), d.holdout.active_h1);
}

function typesHTML(types) {
  if (!types || !S.forecast?.threats) return "";
  const hs = Object.keys(types).map(Number).sort((a, b) => a - b);
  const block = (h) => {
    const t = types[h];
    const order = [...S.forecast.threats.types].sort((a, b) => (t.p_type[b.id] || 0) - (t.p_type[a.id] || 0));
    return `<div class="types-h"><b>Протягом ${h} год</b>${t.level ? `<span class="lvl ${t.level}">${LEVEL_UK[t.level]}</span>` : ""}</div>
      <table class="probs"><tbody>${order.map((m) => {
        const p = t.p_type[m.id] || 0;
        return `<tr><td style="width:46%">${esc(m.name_uk)}</td><td><div class="pbar"><div class="track"><div class="fill" style="width:${Math.round(p * 100)}%;background:${m.level === "red" ? css("--alert") : css("--yel")}"></div></div><span class="num">${pct(p)}</span></div></td></tr>`;
      }).join("")}</tbody></table>`;
  };
  return `<h3>Тип загрози, якщо тривога почнеться</h3>${hs.map(block).join("")}
    <p class="small muted">За повідомленнями Повітряних сил і історією тривог (окрема модель). Червоний рівень: ракети, балістика, КАБ, масована атака дронів.</p>`;
}

const signedPct = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${Math.round(v * 100)}%`);

// ---------------------------------------------------------------- charts
function axisTicks(svg, x0, y0, W, n, at, label) {
  for (let i = 0; i < n; i++) {
    const lab = label(i);
    if (lab == null) continue;
    const x = at(i);
    el("line", { x1: x, x2: x, y1: y0, y2: y0 + 4, class: "ax" }, svg);
    const t = el("text", { x, y: y0 + 15, "text-anchor": "middle" }, svg);
    t.textContent = lab;
  }
}

function histChart(box, d) {
  const hist = d.history.hourly;
  const start = new Date(d.history.start);
  const hs = Object.keys(d.probs.active).map(Number);
  const nF = Math.max(...hs);
  const n = hist.length + nF;
  const W = 360, Hh = 120, pad = { l: 26, r: 4, t: 8, b: 20 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${Hh}`, class: "chart" });
  const bw = (W - pad.l - pad.r) / n;
  const y = (v) => pad.t + (1 - v) * (Hh - pad.t - pad.b);
  for (const v of [0, 0.5, 1]) {
    el("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: "ax", "stroke-dasharray": v ? "2 3" : "" }, svg);
    const t = el("text", { x: pad.l - 4, y: y(v) + 3, "text-anchor": "end" }, svg);
    t.textContent = `${v * 100}%`;
  }
  hist.forEach((v, i) => {
    if (v > 0) el("rect", { x: pad.l + i * bw, y: y(v), width: Math.max(bw - 0.6, 0.6), height: y(0) - y(v), fill: css("--alert"), opacity: 0.85 }, svg);
  });
  for (const h of hs) {
    const p = d.probs.active[h];
    const i = hist.length + h - 1;
    el("rect", { x: pad.l + i * bw, y: y(p), width: Math.max(bw - 0.6, 0.6), height: y(0) - y(p), fill: rampColor(Math.max(0.35, p)) }, svg);
  }
  const xNow = pad.l + hist.length * bw;
  el("line", { x1: xNow, x2: xNow, y1: pad.t - 4, y2: y(0), stroke: css("--ink2"), "stroke-dasharray": "3 2" }, svg);
  const tn = el("text", { x: xNow + 3, y: pad.t + 2 }, svg);
  tn.textContent = "прогноз";
  axisTicks(svg, pad.l, y(0), W, n, (i) => pad.l + i * bw, (i) => {
    const t = new Date(start.getTime() + i * 3600e3);
    return kyivHour(t) === 0 && i < hist.length ? fmtDay.format(t) : null;
  });
  box.appendChild(svg);
}

function calendarChart(box, daily) {
  const vals = daily.values;
  const sorted = vals.filter((v) => v > 0).sort((a, b) => a - b);
  const top = sorted.length ? sorted[Math.floor(sorted.length * 0.9)] || sorted[sorted.length - 1] : 1;
  const start = new Date(daily.start + "T12:00:00Z");
  const dow0 = (start.getUTCDay() + 6) % 7; // Monday = 0
  const weeks = Math.ceil((vals.length + dow0) / 7);
  const cell = 6.2, gap = 1.2, padL = 16, padT = 12;
  const W = padL + weeks * (cell + gap), Hh = padT + 7 * (cell + gap) + 2;
  const svg = el("svg", { viewBox: `0 0 ${W} ${Hh}`, class: "chart" });
  ["пн", "", "ср", "", "пт", "", "нд"].forEach((l, i) => {
    if (!l) return;
    const t = el("text", { x: 0, y: padT + i * (cell + gap) + cell - 0.5, style: "font-size:6px" }, svg);
    t.textContent = l;
  });
  let lastMonth = -1;
  vals.forEach((m, i) => {
    const k = i + dow0, w = Math.floor(k / 7), dd = k % 7;
    const day = new Date(start.getTime() + i * 864e5);
    const x = padL + w * (cell + gap), y = padT + dd * (cell + gap);
    const r = el("rect", { x, y, width: cell, height: cell, rx: 1.2,
      fill: m ? rampColor(Math.min(1, 0.12 + 0.88 * m / top)) : css("--surface2") }, svg);
    const title = el("title", {}, r);
    title.textContent = `${fmtDay.format(day)}: ${m ? `${Math.floor(m / 60)} год ${m % 60} хв під тривогою` : "без тривог"}`;
    if (day.getUTCMonth() !== lastMonth && dd === 0) {
      lastMonth = day.getUTCMonth();
      const t = el("text", { x, y: 8, style: "font-size:6px" }, svg);
      t.textContent = new Intl.DateTimeFormat("uk-UA", { month: "short" }).format(day);
    }
  });
  box.appendChild(svg);
}

function dowChart(box, daily) {
  const vals = daily.values;
  const start = new Date(daily.start + "T12:00:00Z");
  const share = (from) => {
    const n = Array(7).fill(0), c = Array(7).fill(0);
    vals.forEach((m, i) => {
      if (i < from) return;
      const dd = (new Date(start.getTime() + i * 864e5).getUTCDay() + 6) % 7;
      c[dd] += 1; n[dd] += m > 0 ? 1 : 0;
    });
    return n.map((x, i) => (c[i] ? x / c[i] : 0));
  };
  const year = share(0), recent = share(Math.max(0, vals.length - 56));
  const W = 360, Hh = 100, pad = { l: 26, r: 4, t: 6, b: 20 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${Hh}`, class: "chart" });
  const gw = (W - pad.l - pad.r) / 7;
  const y = (v) => pad.t + (1 - v) * (Hh - pad.t - pad.b);
  for (const v of [0, 0.5, 1]) {
    el("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: "ax", "stroke-dasharray": v ? "2 3" : "" }, svg);
    const t = el("text", { x: pad.l - 4, y: y(v) + 3, "text-anchor": "end" }, svg);
    t.textContent = `${v * 100}%`;
  }
  ["пн", "вт", "ср", "чт", "пт", "сб", "нд"].forEach((l, i) => {
    const x0 = pad.l + i * gw;
    el("rect", { x: x0 + gw * 0.18, y: y(recent[i]), width: gw * 0.3, height: y(0) - y(recent[i]), fill: css("--alert"), rx: 1.5 }, svg);
    el("rect", { x: x0 + gw * 0.52, y: y(year[i]), width: gw * 0.3, height: y(0) - y(year[i]), fill: css("--ink3"), rx: 1.5 }, svg);
    const t = el("text", { x: x0 + gw / 2, y: Hh - 5, "text-anchor": "middle" }, svg);
    t.textContent = l;
  });
  box.appendChild(svg);
}

function profileChart(box, prof) {
  const W = 360, Hh = 100, pad = { l: 26, r: 4, t: 6, b: 20 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${Hh}`, class: "chart" });
  const bw = (W - pad.l - pad.r) / 24;
  const y = (v) => pad.t + (1 - v) * (Hh - pad.t - pad.b);
  const nowH = kyivHour(new Date());
  for (const v of [0, 0.5, 1]) {
    el("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: "ax", "stroke-dasharray": v ? "2 3" : "" }, svg);
    const t = el("text", { x: pad.l - 4, y: y(v) + 3, "text-anchor": "end" }, svg);
    t.textContent = `${v * 100}%`;
  }
  prof.forEach((v, i) => {
    el("rect", { x: pad.l + i * bw + 1, y: y(v), width: bw - 2, height: Math.max(0, y(0) - y(v)), rx: 1.5,
      fill: i === nowH ? css("--ink") : rampColor(Math.max(0.2, v)) }, svg);
  });
  axisTicks(svg, pad.l, y(0), W, 24, (i) => pad.l + i * bw + bw / 2, (i) => (i % 3 === 0 ? `${i}` : null));
  box.appendChild(svg);
}

function holdoutChart(box, ho) {
  const n = ho.p.length;
  const start = new Date(ho.start);
  const W = 360, Hh = 110, pad = { l: 26, r: 4, t: 6, b: 20 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${Hh}`, class: "chart" });
  const bw = (W - pad.l - pad.r) / n;
  const y = (v) => pad.t + (1 - v) * (Hh - pad.t - pad.b);
  ho.y.forEach((v, i) => {
    if (v) el("rect", { x: pad.l + i * bw, y: y(1), width: bw + 0.3, height: y(0) - y(1), fill: css("--alert-soft") }, svg);
  });
  for (const v of [0, 0.5, 1]) {
    el("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: "ax", "stroke-dasharray": v ? "2 3" : "" }, svg);
    const t = el("text", { x: pad.l - 4, y: y(v) + 3, "text-anchor": "end" }, svg);
    t.textContent = `${v * 100}%`;
  }
  const pts = ho.p.map((p, i) => `${(pad.l + (i + 0.5) * bw).toFixed(1)},${y(p).toFixed(1)}`).join(" ");
  el("polyline", { points: pts, fill: "none", stroke: css("--accent"), "stroke-width": 1.3, "stroke-linejoin": "round" }, svg);
  axisTicks(svg, pad.l, y(0), W, n, (i) => pad.l + i * bw, (i) => {
    const t = new Date(start.getTime() + i * 3600e3);
    return kyivHour(t) === 0 ? fmtDay.format(t) : null;
  });
  box.appendChild(svg);
}

function lineChart(series, { W = 420, Hh = 200, xs, yMin, yMax, yFmt = (v) => v, xFmt = (v) => v, diag = false, xLabel = "", yLabel = "" }) {
  const pad = { l: 42, r: 10, t: 10, b: 34 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${Hh}`, class: "chart" });
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const x = (v) => pad.l + ((v - xMin) / (xMax - xMin || 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - yMin) / (yMax - yMin || 1)) * (Hh - pad.t - pad.b);
  const step = [0.05, 0.1, 0.2, 0.25, 0.5, 1].find((st) => (yMax - yMin) / st <= 6) || 1;
  for (let v = Math.ceil(yMin / step - 1e-9) * step; v <= yMax + 1e-9; v += step) {
    el("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: "ax" }, svg);
    const t = el("text", { x: pad.l - 5, y: y(v) + 3, "text-anchor": "end" }, svg);
    t.textContent = yFmt(v);
  }
  for (const v of xs) {
    const t = el("text", { x: x(v), y: Hh - pad.b + 14, "text-anchor": "middle" }, svg);
    t.textContent = xFmt(v);
  }
  if (xLabel) { const t = el("text", { x: (W + pad.l) / 2, y: Hh - 4, "text-anchor": "middle" }, svg); t.textContent = xLabel; }
  if (yLabel) { const t = el("text", { x: 10, y: Hh / 2, "text-anchor": "middle", transform: `rotate(-90 10 ${Hh / 2})` }, svg); t.textContent = yLabel; }
  if (yMin < 0 && yMax > 0) el("line", { x1: pad.l, x2: W - pad.r, y1: y(0), y2: y(0), stroke: css("--ink3") }, svg);
  if (diag) el("line", { x1: x(xMin), y1: y(yMin), x2: x(xMax), y2: y(yMax), stroke: css("--ink3"), "stroke-dasharray": "4 3" }, svg);
  for (const s of series) {
    const pts = s.points.filter((p) => p[1] != null);
    el("polyline", { points: pts.map((p) => `${x(p[0])},${y(p[1])}`).join(" "), fill: "none", stroke: s.color, "stroke-width": 2, "stroke-dasharray": s.dash || "" }, svg);
    for (const p of pts) el("circle", { cx: x(p[0]), cy: y(p[1]), r: 3, fill: s.color }, svg);
  }
  return svg;
}

// ---------------------------------------------------------------- metrics view
const MODEL_UK = {
  lightgbm: "LightGBM (наша модель)", climatology_28d: "Кліматологія 28 днів", persistence: "Персистентність",
  markov_persistence: "Марковська персистентність", logistic_regression: "Логістична регресія", lightgbm_no_spatial: "LightGBM без сусідів",
};
const TARGET_UK = { active: "Тривога в годину N", onset: "Нова тривога протягом N год" };

async function renderMetrics() {
  const box = $("#metrics");
  if (!S.metrics) {
    try { S.metrics = await getJSON("/api/metrics"); } catch (e) { box.innerHTML = `<p class="muted">Метрики ще не готові.</p>`; return; }
  }
  const m = S.metrics, ho = m.holdout;
  if (!ho) { box.innerHTML = `<p class="muted">Метрики ще не готові.</p>`; return; }
  const sc = ho.scores;
  const get = (t, h, model) => sc.find((r) => r.target === t && r.horizon === h && r.model === model);
  const hs = [...new Set(sc.map((r) => r.horizon))].sort((a, b) => a - b);
  const tile = (t, h) => {
    const r = get(t, h, "lightgbm");
    if (!r) return "";
    return `<div class="stat"><div class="k">${TARGET_UK[t].replace("N", h)}</div>
      <div class="v ${r.bss_vs_clim > 0 ? "pos" : "neg"}">${signedPct(r.bss_vs_clim)}</div>
      <div class="s">Brier skill відносно кліматології · AUC ${num(r.roc_auc, 2)}</div></div>`;
  };
  const table = (t) => {
    const models = [...new Set(sc.filter((r) => r.target === t).map((r) => r.model))];
    const row = (model, metric, d, fmt = (v) => num(v, d)) =>
      `<tr class="${model === "lightgbm" ? "best" : ""}"><td>${MODEL_UK[model] || model}</td>${hs.map((h) => `<td>${fmt(get(t, h, model)?.[metric])}</td>`).join("")}</tr>`;
    return `<div class="card"><div class="card-h"><h2>${TARGET_UK[t]}</h2><span class="small muted">частота події: ${hs.map((h) => `${h} год ${pct(get(t, h, "lightgbm")?.base_rate)}`).join(", ")}</span></div>
      <div class="tablewrap"><table class="mt"><thead><tr><th>Brier score (менше краще)</th>${hs.map((h) => `<th>${h} год</th>`).join("")}</tr></thead>
      <tbody>${models.map((mm) => row(mm, "brier", 4)).join("")}</tbody></table>
      <table class="mt" style="margin-top:12px"><thead><tr><th>ROC AUC (більше краще)</th>${hs.map((h) => `<th>${h} год</th>`).join("")}</tr></thead>
      <tbody>${models.map((mm) => row(mm, "roc_auc", 3)).join("")}</tbody></table></div></div>`;
  };

  const planList = Object.entries(ho.plans || {});
  const same = planList.every(([, p]) => p.months === planList[0]?.[1].months && p.trees === planList[0]?.[1].trees);
  const plans = same && planList.length
    ? `${planList[0][1].months} міс. історії, ${planList[0][1].trees} дерев для кожної моделі`
    : planList.map(([k, p]) => `${k.replace("active_h", "тривога ").replace("onset_h", "нова ")} год: ${p.months} міс., ${p.trees} дерев`).join("; ");
  let ver = null;
  try { ver = await getJSON("/api/verification"); } catch (_) { /* optional */ }
  let verHTML = "";
  if (ver) {
    const vs = ver.scores || [];
    const vget = (t, h) => vs.find((r) => r.target === t && r.horizon === h);
    const vtile = (t, h) => {
      const r = vget(t, h);
      if (!r) return "";
      return `<div class="stat"><div class="k">${TARGET_UK[t].replace("N", h)}</div>
        <div class="v ${r.bss_vs_clim > 0 ? "pos" : "neg"}">${signedPct(r.bss_vs_clim)}</div>
        <div class="s">${r.n} прогнозів · ${r.hit_rate_high != null ? `коли ≥50%, подія була в ${pct(r.hit_rate_high)}` : ""}${r.event_rate_low != null ? `; коли &lt;20%, лише в ${pct(r.event_rate_low)}` : ""}</div></div>`;
    };
    verHTML = `<div class="card"><div class="card-h"><h2>Перевірка живих прогнозів</h2>
        <span class="small muted">${ver.journal_since ? `журнал з ${fmtFull.format(new Date(ver.journal_since))}` : ""}</span></div>
      <p class="small muted">Кожен виданий прогноз зберігається і, щойно відповідна година минає, звіряється з тим, що сталося
        (за alerts.in.ua і датасетом). Показано перевагу над кліматологією за Brier score.</p>
      ${vs.length ? `<div class="summary">${vtile("active", 1)}${vtile("onset", 1)}${vtile("onset", 3)}${vtile("onset", 6)}</div>`
        : `<p>Видано прогнозів: ${ver.n_issued}. Перевірених ще немає: потрібно, щоб минули прогнозовані години і зʼявились дані про них.</p>`}</div>`;
  }
  let html = `<h2 style="margin-top:0">Якість моделі</h2>${verHTML}
    <h3 style="margin:24px 0 8px">Перевірка на відкладеному тижні</h3>
    <p class="lead">Перед навчанням моделі, що зараз працює, останні ${ho.days} днів (${fmtFull.format(new Date(ho.start))} – ${fmtFull.format(new Date(ho.end))})
      були відкладені. Модель навчилась на даних до них і прогнозувала кожну годину для кожної області так, ніби це відбувалося наживо.
      Нижче її результат поруч із двома простими орієнтирами.</p>
    <div class="summary">${tile("active", 1)}${tile("onset", 1)}${tile("onset", 3)}${tile("onset", 6)}</div>
    ${table("active")}${table("onset")}`;

  html += `<div class="two"><div class="card"><div class="card-h"><h2>Перевага над кліматологією за горизонтом</h2></div><div id="ch-bss"></div>
      <div class="chart-legend"><span><i style="background:${css("--alert")}"></i>тривога в годину N</span><span><i style="background:${css("--accent")}"></i>нова тривога за N год</span></div>
      <p class="small muted">Brier skill score: 0% означає «не краще за кліматологію», 100% означає ідеальний прогноз.</p></div>
    <div class="card"><div class="card-h"><h2>Калібрування</h2></div><div id="ch-rel"></div>
      <div class="chart-legend"><span><i style="background:${css("--alert")}"></i>тривога в 1-у годину</span><span><i style="background:${css("--accent")}"></i>нова тривога за 3 год</span></div>
      <p class="small muted">Якщо модель каже 30%, подія має траплятися приблизно в 30% випадків. Ідеал: пунктирна діагональ.</p></div></div>`;

  if (ho.by_region) {
    const rows = ho.by_region.filter((r) => r.target === "onset" && r.horizon === 3).sort((a, b) => (b.roc_auc ?? 0) - (a.roc_auc ?? 0));
    html += `<div class="card"><div class="card-h"><h2>За областями: нова тривога за 3 год</h2><span class="small muted">відкладений тиждень</span></div>
      <div class="tablewrap"><table class="mt"><thead><tr><th>Область</th><th>Частота події</th><th>Brier</th><th>Перевага над кліматологією</th><th>ROC AUC</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${esc(r.name_uk)}</td><td>${pct(r.base_rate)}</td><td>${num(r.brier, 3)}</td><td class="${r.bss_vs_clim > 0 ? "pos" : "neg"}">${r.base_rate < 0.02 ? "–" : signedPct(r.bss_vs_clim)}</td><td>${num(r.roc_auc, 2)}</td></tr>`).join("")}
      </tbody></table></div></div>`;
  }

  if (m.walk_forward?.scores?.length) {
    const wf = m.walk_forward.scores;
    const whs = [...new Set(wf.map((r) => r.horizon_h))].sort((a, b) => a - b);
    const block = (t) => {
      const models = [...new Set(wf.filter((r) => r.target === t).map((r) => r.model))];
      return `<table class="mt" style="margin-top:8px"><thead><tr><th>${TARGET_UK[t]}: перевага над кліматологією</th>${whs.map((h) => `<th>${h} год</th>`).join("")}</tr></thead><tbody>
        ${models.map((mm) => `<tr class="${mm === "lightgbm" ? "best" : ""}"><td>${MODEL_UK[mm] || mm}</td>${whs.map((h) => {
          const v = wf.find((r) => r.target === t && r.model === mm && r.horizon_h === h)?.bss_vs_clim;
          return `<td class="${v > 0 ? "pos" : "neg"}">${signedPct(v)}</td>`;
        }).join("")}</tr>`).join("")}</tbody></table>`;
    };
    const per = m.walk_forward.test ? `${fmtFull.format(new Date(m.walk_forward.test[0]))} – ${fmtFull.format(new Date(m.walk_forward.test[1]))}` : "";
    html += `<div class="card"><div class="card-h"><h2>Повна оцінка: walk-forward, 4 тижні</h2><span class="small muted">${per}</span></div>
      <p class="small muted">Результат дослідницького скрипта run.py: щотижневе перенавчання, більше орієнтирів, вибір вікна навчання на окремому місяці валідації.</p>
      <div class="tablewrap">${[...new Set(wf.map((r) => r.target))].map(block).join("")}</div></div>`;
  }
  html += `<p class="small muted">Модель навчена ${ho.trained_at ? fmtFull.format(new Date(ho.trained_at)) : "–"}. Вікна навчання (${ho.selection_source === "defaults" ? "типові" : "вибрані на валідації в run.py"}): ${esc(plans)}.</p>`;
  box.innerHTML = html;

  const xs = hs;
  const series = ["active", "onset"].map((t, i) => ({
    color: i ? css("--accent") : css("--alert"),
    points: hs.map((h) => [h, get(t, h, "lightgbm")?.bss_vs_clim]),
  }));
  const allV = series.flatMap((s) => s.points.map((p) => p[1])).filter((v) => v != null);
  $("#ch-bss").appendChild(lineChart(series, {
    xs, yMin: Math.min(0, Math.floor(Math.min(...allV) * 10) / 10), yMax: Math.max(0.1, Math.ceil(Math.max(...allV) * 10) / 10),
    yFmt: (v) => `${Math.round(v * 100)}%`, xFmt: (v) => `${v} год`, xLabel: "горизонт прогнозу",
  }));
  const rel = ho.reliability || {};
  const relSeries = [["active_h1", css("--alert")], ["onset_h3", css("--accent")]]
    .filter(([k]) => rel[k]).map(([k, color]) => ({ color, points: rel[k].map((r) => [r[0], r[1]]) }));
  $("#ch-rel").appendChild(lineChart(relSeries, {
    xs: [0, 0.2, 0.4, 0.6, 0.8, 1], yMin: 0, yMax: 1, diag: true,
    yFmt: (v) => `${Math.round(v * 100)}%`, xFmt: (v) => `${Math.round(v * 100)}%`, xLabel: "прогнозована ймовірність", yLabel: "фактична частота",
  }));
}

// ---------------------------------------------------------------- raions
const METHOD_SHORT = {
  lightgbm: "модель району + історія", lightgbm_state: "модель району", oblast_x_share: "область × частка району", oblast: "прогноз області",
};
const shareStale = (sp) => !!sp && new Date() - new Date(sp[1]) > 3 * 864e5;
const LEVEL_PHRASE = { oblast: "тривога на всю область", raion: "тривога на весь район", hromada: "тривога в частині району" };
const raionById = (id) => S.raions?.raions.find((r) => r.id === id);
const raionGeo = (id) => S.rgeo?.raions[id];
const rTarget = () => (S.target === "type" ? "active" : S.target);
const liveRaions = () => !!S.raions?.live_source;
const activeRaions = (oblast = "") => (S.raions?.raions || []).filter((r) => r.live?.active && (!oblast || r.oblast === oblast));
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function setFullView(viewBox) {
  const svg = $("#map");
  S.full = viewBox.split(" ").map(Number);
  svg.style.aspectRatio = `${S.full[2]} / ${S.full[3]}`;
  applyViewBox(S.full);
}

function applyViewBox(vb) {
  S.vb = vb;
  $("#map").setAttribute("viewBox", vb.map((v) => v.toFixed(1)).join(" "));
  const zoomed = S.full && vb[2] < S.full[2] * 0.98;
  $("#zoom-reset").disabled = !zoomed;
  $("#map").style.setProperty("--k", (S.full ? S.full[2] / vb[2] : 1).toFixed(3));
}

// Fit a bbox [x0, y0, x1, y1] with padding, keeping the map's aspect ratio; animated.
function zoomTo(bbox) {
  const [W, H] = [S.full[2], S.full[3]];
  let [x0, y0, x1, y1] = bbox;
  let w = Math.max(x1 - x0, W * 0.12), h = Math.max(y1 - y0, H * 0.12);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  w *= 1.25; h *= 1.25;
  if (w / h > W / H) h = w * H / W; else w = h * W / H;
  if (w > W) { w = W; h = H; }
  const to = [Math.min(Math.max(cx - w / 2, -W * 0.05), W * 1.05 - w), Math.min(Math.max(cy - h / 2, -H * 0.05), H * 1.05 - h), w, h];
  const from = S.vb || S.full;
  if (reducedMotion()) { applyViewBox(to); paintRaionLabels(); return; }
  const t0 = performance.now(), dur = 420;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    applyViewBox(from.map((v, i) => v + (to[i] - v) * e));
    if (k < 1) requestAnimationFrame(step); else paintRaionLabels();
  };
  requestAnimationFrame(step);
}

function unionBox(ids) {
  const boxes = ids.map((id) => raionGeo(id)?.bbox).filter(Boolean);
  if (!boxes.length) return null;
  return [Math.min(...boxes.map((b) => b[0])), Math.min(...boxes.map((b) => b[1])), Math.max(...boxes.map((b) => b[2])), Math.max(...boxes.map((b) => b[3]))];
}

function resetZoom() {
  if (!S.full) return;
  const from = S.vb || S.full;
  if (reducedMotion()) { applyViewBox(S.full); paintRaionLabels(); return; }
  const t0 = performance.now(), dur = 360;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    applyViewBox(from.map((v, i) => v + (S.full[i] - v) * e));
    if (k < 1) requestAnimationFrame(step); else paintRaionLabels();
  };
  requestAnimationFrame(step);
}

async function ensureRaionGeo() {
  if (!S.rgeo) S.rgeo = await getJSON("static/raions-map.json");
}

async function setUnit(unit) {
  if (unit === "raion" && !S.raions) return;
  if (unit === "raion") await ensureRaionGeo();
  S.unit = unit;
  for (const b of $("#unit").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.v === unit));
  $("#zoombar").hidden = unit !== "raion";
  buildMap();
  render();
  if (unit === "raion" && S.rsel) renderRaionDetailById(S.rsel);
  else if (unit === "oblast" && S.selected) select(S.selected);
  else $("#detail").innerHTML = `<p class="muted">Оберіть ${unit === "raion" ? "район" : "область"} на мапі або в списку, щоб побачити деталі.</p>`;
}

async function zoomToAlerts() {
  if (S.unit !== "raion") await setUnit("raion");
  const ob = S.zoomOblast;
  const act = activeRaions(ob).map((r) => r.id);
  const info = $("#zoom-info");
  if (act.length) {
    zoomTo(unionBox(act));
    info.textContent = `${act.length} ${plural(act.length, "район", "райони", "районів")} під тривогою${ob ? ` (${S.raions.raions.find((r) => r.oblast === ob)?.oblast_uk} обл.)` : ""}`;
  } else if (ob) {
    zoomTo(unionBox(S.raions.raions.filter((r) => r.oblast === ob).map((r) => r.id)));
    info.textContent = liveRaions() ? "У цій області зараз тривоги немає" : "Немає живих даних по районах";
  } else {
    info.textContent = liveRaions() ? "Зараз жоден район не під тривогою" : "Немає живих даних по районах";
  }
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
}

function renderZoomOblasts() {
  const sel = $("#zoom-oblast");
  if (!S.raions) return;
  const by = new Map();
  for (const r of S.raions.raions) {
    const v = by.get(r.oblast) || { uk: r.oblast_uk, n: 0, on: 0 };
    v.n += 1; v.on += r.live?.active ? 1 : 0;
    by.set(r.oblast, v);
  }
  const opts = [...by.entries()].sort((a, b) => (b[1].on - a[1].on) || a[1].uk.localeCompare(b[1].uk, "uk"));
  const html = `<option value="">усі області</option>` + opts.map(([o, v]) =>
    `<option value="${esc(o)}">${esc(o === "Kyiv City" ? "м. Київ" : v.uk + " обл.")}${v.on ? ` · ${v.on} під тривогою` : ""}</option>`).join("");
  if (sel.dataset.html !== html) { sel.innerHTML = html; sel.dataset.html = html; sel.value = S.zoomOblast; }
  const n = activeRaions().length;
  $("#zoom-alerts").disabled = false;
  $("#zoom-alerts").title = liveRaions() ? (n ? `Наблизити до ${n} ${plural(n, "району", "районів", "районів")} під тривогою` : "Зараз жоден район не під тривогою")
    : "Немає живих даних по районах (потрібен токен alerts.in.ua)";
}

function buildRaionMap() {
  const svg = $("#map");
  const g = S.rgeo;
  const keep = S.vb && S.full && S.full.join(" ") === g.viewBox ? S.vb : null;
  setFullView(g.viewBox);
  if (keep) applyViewBox(keep);
  svg.innerHTML = "";
  svg.classList.add("raion-mode");
  svg.setAttribute("aria-label", "Мапа районів України");
  mapDefs(svg);
  const gR = el("g", { id: "raion-layer" }, svg);
  for (const [id, r] of Object.entries(g.raions)) {
    const known = !!raionById(id);
    const p = el("path", { d: r.d, class: known ? "reg rn" : "nodata rn", "data-rid": id }, gR);
    if (known) { p.setAttribute("tabindex", "0"); p.setAttribute("role", "button"); }
  }
  el("path", { d: g.oblast_borders, class: "obl-border" }, svg);
  el("g", { id: "labels" }, svg);
  $("#attr").textContent = g.attribution;
}

function raionFill(r) {
  if (!r) return css("--calm");
  if (S.layer === "now") {
    const lv = r.live;
    if (!lv?.active) return css("--calm");
    return lv.level === "hromada" ? "url(#part)" : css("--alert");
  }
  const p = r.probs?.[rTarget()]?.[S.horizon];
  return p == null ? css("--calm") : rampColor(p);
}

function paintRaions() {
  const svg = $("#map");
  let sel = null;
  for (const node of svg.querySelectorAll("[data-rid]")) {
    const r = raionById(node.dataset.rid);
    if (!r) continue;
    node.style.fill = raionFill(r);
    node.classList.toggle("sel", r.id === S.rsel);
    if (r.id === S.rsel) sel = node;
    const p = r.probs?.[rTarget()]?.[S.horizon];
    node.setAttribute("aria-label", `${r.name_uk}: ${S.layer === "now" ? (r.live?.active ? LEVEL_PHRASE[r.live.level] || "тривога" : "немає тривоги") : pct(p)}`);
  }
  if (sel) sel.parentNode.appendChild(sel);  // selected outline on top of its neighbours
  paintRaionLabels();
  paintLegend();
  renderZoomOblasts();
}

// Probability labels only when zoomed in enough to read them.
function paintRaionLabels() {
  const labels = $("#labels");
  if (!labels || S.unit !== "raion" || !S.rgeo) return;
  labels.innerHTML = "";
  const k = S.full[2] / S.vb[2];
  if (S.layer === "now" || k < 2.2) return;
  const [x0, y0, w, h] = S.vb;
  for (const r of S.raions.raions) {
    const at = raionGeo(r.id)?.label;
    if (!at || at[0] < x0 || at[0] > x0 + w || at[1] < y0 || at[1] > y0 + h) continue;
    const p = r.probs?.[rTarget()]?.[S.horizon];
    if (p == null) continue;
    const t = el("text", { x: at[0], y: at[1], "text-anchor": "middle", "dominant-baseline": "middle",
      class: "lbl rl" + (invText(p) ? " inv" : ""), style: `font-size:${(15 / k).toFixed(2)}px;stroke-width:${(3 / k).toFixed(2)}px` }, labels);
    t.textContent = pct(p);
  }
}

function raionTipHTML(r, id) {
  if (!r) {
    const g = raionGeo(id);
    return g ? `<b>${esc(g.name_uk)}</b><br>немає даних` : null;
  }
  const head = `<b>${esc(r.name_uk)}</b> <span class="tipsub">${esc(r.oblast === "Kyiv City" ? "" : r.oblast_uk + " обл.")}</span>`;
  if (S.layer === "now") {
    if (!r.live) return `${head}<br>немає живих даних`;
    return `${head}<br>${r.live.active ? `${LEVEL_PHRASE[r.live.level] || "тривога"}${r.live.since ? ", " + duration(r.live.since) : ""}` : "тривоги немає"}`;
  }
  const t = rTarget(), p = r.probs?.[t]?.[S.horizon];
  return `${head}<br>${pct(p)}, ${p == null ? "" : LEVELS[levelIdx(p)][1]} ймовірність<br><span class="tipsub">${METHOD_SHORT[r.method?.[t]?.[S.horizon]] || ""}</span>`;
}

async function selectRaion(id, { zoom = false } = {}) {
  if (S.unit !== "raion") await setUnit("raion");
  S.rsel = id;
  if (location.hash !== `#raion/${id}`) history.replaceState(null, "", `#raion/${id}`);
  paintMap();
  renderRank();
  if (zoom) zoomTo(raionGeo(id).bbox);
  await renderRaionDetailById(id);
  if (window.matchMedia("(max-width: 900px)").matches) $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function renderRaionDetailById(id) {
  try {
    const d = await getJSON(`/api/raions/${encodeURIComponent(id)}`);
    if (S.rsel === id) renderRaionDetail(d);
  } catch (e) {
    $("#detail").innerHTML = `<p class="muted">Не вдалося завантажити дані району.</p>`;
  }
}

function renderRaionDetail(d) {
  const lv = d.live;
  const status = !lv
    ? `<span class="status off"><span class="b"></span>Немає живих даних по районах</span>`
    : lv.active
      ? `<span class="status on"><span class="b"></span>${LEVEL_PHRASE[lv.level] || "Тривога"}${lv.since ? ` з ${fmtTime.format(new Date(lv.since))}, ${duration(lv.since)}` : ""}</span>`
      : `<span class="status off"><span class="b"></span>Тривоги немає</span>`;
  const locs = lv?.locations?.length ? `<p class="small muted">Під тривогою: ${lv.locations.map(esc).join(", ")}</p>` : "";
  const hs = Object.keys(d.probs.active).map(Number);
  const bar = (p) => (p == null ? "–" : `<div class="pbar"><div class="track"><div class="fill" style="width:${Math.round(p * 100)}%;background:${rampColor(Math.max(p, 0.15))}"></div></div><span class="num">${pct(p)}</span></div>`);
  const w = d.windows;
  const methods = new Set(["active", "onset"].flatMap((t) => hs.map((h) => d.method[t][h])));
  const tag = (m) => `<span class="mtag m-${m}">${METHOD_SHORT[m] || m}</span>`;
  const rows = hs.map((h) => `<tr><td class="h">${h} год<br><span class="small">${fmtTime.format(new Date(w[h].active[0]))}–${fmtTime.format(new Date(w[h].active[1]))}</span></td>
      <td>${bar(d.probs.active[h])}</td><td>${bar(d.probs.onset[h])}</td></tr>`).join("");
  const h3 = hs.includes(3) ? 3 : hs[0];
  const ob = d.oblast_probs;
  const cmp = ob && d.oblast !== "Kyiv City" ? `<p class="small muted">Для всієї ${esc(d.oblast_uk)} області: тривога в наступну годину ${pct(ob.active[1])}, нова тривога за ${h3} год ${pct(ob.onset[h3])}.</p>` : "";
  const sp = d.share_period;
  const shareNote = methods.has("oblast_x_share") && sp
    ? `<p class="small muted">Частка району: скільки тривог області за 28 днів (${fmtDay.format(new Date(sp[0]))} – ${fmtDay.format(new Date(sp[1]))}) зачепили цей район:
       ${pct(d.share?.onset_h3)} для «нової тривоги за ${h3} год».${shareStale(sp) ? " <b>Оцінка застаріла:</b> новіших районних даних немає." : ""}</p>` : "";
  const ev = d.evaluation;
  let evHTML = "";
  if (ev?.scores?.length) {
    const get = (t, h, m) => ev.scores.find((x) => x.target === t && x.horizon_h === h && x.model === m);
    const per = ev.test ? `${fmtDay.format(new Date(ev.test[0]))} – ${fmtDay.format(new Date(ev.test[1]))}` : "";
    // a share estimated long ago is scored by its stale variant (share from 30 days earlier)
    const evalName = (m) => (m === "oblast_x_share" && shareStale(d.share_period) ? "oblast_x_share_stale30" : m);
    const line = (t, h) => {
      const m = d.method[t][h];
      const r = get(t, h, evalName(m)), o = get(t, h, "oblast");
      return r ? `<tr><td>${t === "active" ? `Тривога в ${h}-у год` : `Нова за ${h} год`}</td><td>${tag(m)}</td>
        <td class="${r.bss_vs_clim > 0 ? "pos" : "neg"}">${signedPct(r.bss_vs_clim)}</td><td>${o ? signedPct(o.bss_vs_clim) : "–"}</td></tr>` : "";
    };
    const obl = (ev.oblast || []).find((x) => x.target === "onset" && x.horizon_h === 3);
    evHTML = `<h3>Наскільки цьому можна вірити</h3>
      <p class="small muted">Перевірка на ${per} (останні 4 тижні з районними даними), усі райони разом. Перевага над кліматологією району за Brier score:</p>
      <div class="tablewrap"><table class="mt"><thead><tr><th></th><th>Метод</th><th>Перевага</th><th>Просто прогноз області</th></tr></thead>
      <tbody>${line("active", 1)}${line("onset", 1)}${line("onset", 3)}${line("onset", 6)}</tbody></table></div>
      ${obl && obl[`bss_${evalName(d.method.onset[3])}`] !== undefined ? `<p class="small muted">Райони ${esc(d.oblast_uk)} області, нова тривога за 3 год: частота ${pct(obl.base_rate)}, перевага
        ${signedPct(obl[`bss_${evalName(d.method.onset[3])}`])} (метод «${METHOD_SHORT[d.method.onset[3]]}»).</p>` : ""}`;
  }
  $("#detail").innerHTML = `
    <p class="crumbs small"><a href="#region/${esc(d.oblast_id)}" data-oblast="${esc(d.oblast_id)}">${esc(d.oblast === "Kyiv City" ? "м. Київ" : d.oblast_uk + " область")}</a> › район</p>
    <h2>${esc(d.name_uk)}</h2>${status}${locs}
    <p class="small muted">${d.live_source ? `Стан за alerts.in.ua, ${fmtTime.format(new Date(d.live_as_of))}` : "Живий стан районів зʼявляється, коли сервер має токен alerts.in.ua"};
      прогноз станом на ${d.origin_kyiv} за Києвом.</p>
    <div class="detail-actions"><button type="button" class="zbtn" id="zoom-this">Наблизити до району</button></div>
    <h3>Прогноз для району</h3>
    <p class="small">Звідки: ${[...methods].map(tag).join(" ")}</p>
    <table class="probs"><thead><tr><th></th><th>Тривога в цю годину</th><th>Нова тривога за цей час</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="small muted">${[...methods].map((m) => `«${METHOD_SHORT[m]}»: ${esc(d.methods?.[m] || "")}`).join("<br>")}</p>
    ${cmp}${shareNote}${evHTML}`;
  $("#zoom-this").addEventListener("click", () => zoomTo(raionGeo(d.id).bbox));
  $("#detail .crumbs a").addEventListener("click", (ev) => { ev.preventDefault(); setUnit("oblast").then(() => select(d.oblast_id)); });
}

function renderRaionRank() {
  const t = $("#rank");
  const rs = S.raions?.raions || [];
  const hs = S.forecast?.horizons || [1, 2, 3, 6];
  const h3 = hs.includes(3) ? 3 : hs[0];
  const ob = S.zoomOblast;
  const key = S.layer === "now" ? (r) => (r.live?.active ? 2 : 0) + (r.probs.active[1] ?? 0) : (r) => r.probs[rTarget()][S.horizon] ?? -1;
  const rows = rs.filter((r) => !ob || r.oblast === ob).sort((a, b) => key(b) - key(a));
  const cell = (p) => (p == null ? "–" : `<span class="cell" style="background:${rampColor(p)};color:${invText(p) ? "#fff" : "inherit"}">${pct(p)}</span>`);
  t.innerHTML = `<thead><tr><th>Район</th><th>Тривога в 1-у год</th><th>Нова за ${h3} год</th><th>Нова за ${hs[hs.length - 1]} год</th><th>Звідки прогноз</th></tr></thead>
    <tbody>${rows.map((r) => `<tr data-rid="${r.id}" tabindex="0" class="${r.id === S.rsel ? "sel" : ""}">
      <td>${esc(r.name_uk)}<span class="small muted"> · ${esc(r.oblast === "Kyiv City" ? "" : r.oblast_uk)}</span>${r.live?.active ? `<span class="badge">${r.live.level === "hromada" ? "частково" : "тривога"}</span>` : ""}</td>
      <td>${cell(r.probs.active[1])}</td><td>${cell(r.probs.onset[h3])}</td><td>${cell(r.probs.onset[hs[hs.length - 1]])}</td>
      <td class="small muted">${METHOD_SHORT[r.method.onset[h3]] || ""}</td></tr>`).join("")}</tbody>`;
  $("#rank-title").textContent = ob ? `Райони: ${ob === "Kyiv City" ? "м. Київ" : rs.find((r) => r.oblast === ob)?.oblast_uk + " область"}` : "Усі райони";
  $("#rank-sub").textContent = S.raions?.origin ? `прогноз станом на ${fmtFull.format(new Date(S.raions.origin))} за Києвом` : "";
}

// ---------------------------------------------------------------- views & loading
function setView() {
  const h = location.hash.replace("#", "") || "map";
  const [view, arg] = h.split("/");
  S.view = ["region", "raion"].includes(view) ? "map" : (["map", "metrics", "about"].includes(view) ? view : "map");
  for (const a of document.querySelectorAll(".tabs a")) a.setAttribute("aria-current", a.dataset.view === S.view ? "page" : "false");
  for (const v of ["map", "metrics", "about"]) $(`#view-${v}`).hidden = v !== S.view || (v !== "about" && !$("#loading").hidden);
  if (S.view === "metrics" && $("#loading").hidden) renderMetrics();
  if (view === "region" && arg && S.forecast) {
    if (S.unit === "raion") setUnit("oblast").then(() => select(arg)); else if (arg !== S.selected) select(arg);
  }
  if (view === "raion" && arg && arg !== S.rsel && S.raions) selectRaion(arg);
}

function render() {
  renderHorizonButtons();
  $("#window").textContent = windowText();
  renderHeadline();
  renderSummary();
  renderBanner();
  paintMap();
  renderRank();
  $("#foot-status").textContent = S.state ? `Оновлено о ${fmtTime.format(new Date())}` : "";
}

async function refresh() {
  try {
    const [st, fc, rs] = await Promise.all([getJSON("/api/state"), getJSON("/api/forecast"),
      getJSON("/api/raions").catch(() => null)]);  // raion layer is optional
    const firstLoad = !S.forecast;
    S.state = st; S.forecast = fc; S.raions = rs;
    $("#unit").hidden = !rs;
    if (!rs && S.unit === "raion") S.unit = "oblast";
    $("#loading").hidden = true;
    if (firstLoad) { buildMap(); setView(); }
    render();
    if (!firstLoad && S.unit === "raion" && S.rsel) renderRaionDetailById(S.rsel);
    else if (S.selected && !firstLoad && S.unit === "oblast") {
      const d = await getJSON(`/api/regions/${S.selected}`).catch(() => null);
      if (d) renderDetail(d);
    }
    return true;
  } catch (e) {
    if (e.status === 503) {
      $("#loading").hidden = false;
      $("#loading-msg").textContent = e.body?.detail?.message || "Підготовка";
      for (const v of ["map", "metrics"]) $(`#view-${v}`).hidden = true;
      return false;
    }
    console.error(e);
    return !!S.forecast;
  }
}

async function main() {
  readRamp();
  setupControls();
  setupMapEvents();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readRamp(); if (S.geo) { buildMap(); render(); } });
  window.addEventListener("hashchange", setView);
  const pick = (tr) => (tr.dataset.rid ? selectRaion(tr.dataset.rid) : select(tr.dataset.id));
  $("#rank").addEventListener("click", (ev) => { const tr = ev.target.closest("tr[data-id], tr[data-rid]"); if (tr) pick(tr); });
  $("#rank").addEventListener("keydown", (ev) => { const tr = ev.target.closest("tr[data-id], tr[data-rid]"); if (tr && ev.key === "Enter") pick(tr); });
  $("#search").addEventListener("change", (ev) => {
    const q = ev.target.value.trim().toLowerCase();
    if (!q) return;
    const r = S.forecast?.regions.find((x) => x.name_uk.toLowerCase() === q);
    const rr = S.raions?.raions.find((x) => x.name_uk.toLowerCase() === q)
      || (!r && S.raions?.raions.find((x) => x.name_uk.toLowerCase().startsWith(q)));
    const r2 = r || (!rr && S.forecast?.regions.find((x) => x.name_uk.toLowerCase().startsWith(q)));
    if (rr) selectRaion(rr.id, { zoom: true });
    else if (r2) { if (S.unit === "raion") setUnit("oblast").then(() => select(r2.id)); else select(r2.id); }
    else return;
    ev.target.value = ""; ev.target.blur();
  });
  S.geo = await getJSON("static/ukraine-map.json");
  setView();
  let ok = await refresh();
  while (!ok) {
    await new Promise((r) => setTimeout(r, 5000));
    ok = await refresh();
  }
  const first = location.hash.match(/^#region\/(.+)$/);
  if (first) select(first[1]);
  const firstRaion = location.hash.match(/^#raion\/(.+)$/);
  if (firstRaion && S.raions) selectRaion(firstRaion[1], { zoom: true });
  setInterval(async () => {
    const prevOrigin = S.forecast?.origin;
    await refresh();
    if (S.forecast?.origin !== prevOrigin) { S.metrics = null; if (S.view === "metrics") renderMetrics(); }
  }, 60000);
  getJSON("/api/metrics").then((m) => { S.metrics = m; }).catch(() => {});
}

main();
