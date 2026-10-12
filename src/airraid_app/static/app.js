"use strict";

// ---------------------------------------------------------------- helpers
const $ = (sel, el = document) => el.querySelector(sel);
const SVGNS = "http://www.w3.org/2000/svg";
const KYIV_TZ = "Europe/Kyiv";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const slug = (name) => name.replace(/ oblast$/, "").toLowerCase().replace(/ /g, "-");
const pct = (p) => (p == null ? "–" : `${Math.round(p * 100)}%`);
const num = (v, d = 3) => (v == null ? "–" : Number(v).toFixed(d));
const signedPct = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${Math.round(v * 100)}%`);
const kyivHour = (d) => Number(new Intl.DateTimeFormat("en-GB", { timeZone: KYIV_TZ, hour: "2-digit", hour12: false }).format(d)) % 24;

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

// ---------------------------------------------------------------- i18n
// English is the default; ?lang=uk|en overrides, the choice is remembered per viewer (when storage works).
const LANGS = ["en", "uk"];
function storedLang() {
  try { return localStorage.getItem("lang"); } catch (_) { return null; }
}
function initialLang() {
  let q = null;
  try { q = new URLSearchParams(location.search).get("lang"); } catch (_) { /* ignore */ }
  if (LANGS.includes(q)) return q;
  const s = storedLang();
  return LANGS.includes(s) ? s : "en";
}
let LANG = initialLang();
let PLURAL = new Intl.PluralRules(LANG === "uk" ? "uk" : "en");

// t(key, {n: 3, name: "..."}): placeholders {name}; plural forms {one, few, many, other} chosen by `n`.
// Values are inserted as given: escape data before passing it.
function t(key, vars = {}) {
  let v = window.I18N?.[LANG]?.[key] ?? window.I18N?.en?.[key];
  if (v == null) return key;
  if (typeof v === "object") v = v[PLURAL.select(Number(vars.n ?? 0))] ?? v.other ?? v.many ?? "";
  return v.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? ""));
}

let fmtTime, fmtDay, fmtFull, fmtMonth, fmtDow;
function setFormatters() {
  const loc = LANG === "uk" ? "uk-UA" : "en-GB";
  fmtTime = new Intl.DateTimeFormat(loc, { timeZone: KYIV_TZ, hour: "2-digit", minute: "2-digit", hour12: false });
  fmtDay = new Intl.DateTimeFormat(loc, { timeZone: KYIV_TZ, day: "numeric", month: "short" });
  fmtFull = new Intl.DateTimeFormat(loc, { timeZone: KYIV_TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
  fmtMonth = new Intl.DateTimeFormat(loc, { timeZone: "UTC", month: "short" });
  fmtDow = new Intl.DateTimeFormat(loc, { timeZone: "UTC", weekday: "short" });
}
setFormatters();
// Monday-first short weekday names (2024-01-01 was a Monday)
const dowNames = () => [0, 1, 2, 3, 4, 5, 6].map((i) => fmtDow.format(new Date(Date.UTC(2024, 0, 1 + i, 12))));

function duration(fromIso, toDate = new Date()) {
  const m = Math.max(0, Math.round((toDate - new Date(fromIso)) / 60000));
  const h = Math.floor(m / 60);
  if (h >= 48) return t("dur.d", { n: Math.floor(h / 24) });  // frontline hromadas stay under alert for months
  return h ? t("dur.hm", { h, m: m % 60 }) : t("dur.m", { m });
}
// a start time: HH:MM today, with the date when it is older than 20 hours
const sinceTime = (iso, to = new Date()) => (to - new Date(iso) > 20 * 3600e3 ? fmtFull : fmtTime).format(new Date(iso));
const quote = (s) => (LANG === "uk" ? `«${s}»` : `“${s}”`);
const listJoin = (names) => (names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} ${t("and")} ${names[names.length - 1]}`);

// Place names. Oblasts: the dataset names are English ("Kyivska oblast"), shown as "Kyiv Oblast";
// raions: COD-AB English names ("Obukhivskyi raion" -> "Obukhivskyi Raion"); Ukrainian names in UK mode.
const OBLAST = {
  "Avtonomna Respublika Krym": ["Crimea", "АР Крим"], "Cherkaska oblast": ["Cherkasy", "Черкаська"],
  "Chernihivska oblast": ["Chernihiv", "Чернігівська"], "Chernivetska oblast": ["Chernivtsi", "Чернівецька"],
  "Dnipropetrovska oblast": ["Dnipropetrovsk", "Дніпропетровська"], "Donetska oblast": ["Donetsk", "Донецька"],
  "Ivano-Frankivska oblast": ["Ivano-Frankivsk", "Івано-Франківська"], "Kharkivska oblast": ["Kharkiv", "Харківська"],
  "Khersonska oblast": ["Kherson", "Херсонська"], "Khmelnytska oblast": ["Khmelnytskyi", "Хмельницька"],
  "Kirovohradska oblast": ["Kirovohrad", "Кіровоградська"], "Kyiv City": ["Kyiv City", "м. Київ"],
  "Kyivska oblast": ["Kyiv", "Київська"], "Luhanska oblast": ["Luhansk", "Луганська"], "Lvivska oblast": ["Lviv", "Львівська"],
  "Mykolaivska oblast": ["Mykolaiv", "Миколаївська"], "Odeska oblast": ["Odesa", "Одеська"], "Poltavska oblast": ["Poltava", "Полтавська"],
  "Rivnenska oblast": ["Rivne", "Рівненська"], "Sevastopol City": ["Sevastopol", "м. Севастополь"], "Sumska oblast": ["Sumy", "Сумська"],
  "Ternopilska oblast": ["Ternopil", "Тернопільська"], "Vinnytska oblast": ["Vinnytsia", "Вінницька"], "Volynska oblast": ["Volyn", "Волинська"],
  "Zakarpatska oblast": ["Zakarpattia", "Закарпатська"], "Zaporizka oblast": ["Zaporizhzhia", "Запорізька"],
  "Zhytomyrska oblast": ["Zhytomyr", "Житомирська"],
};
const CITY = new Set(["Kyiv City", "Sevastopol City"]);
const oblShort = (name) => (OBLAST[name] ? OBLAST[name][LANG === "uk" ? 1 : 0] : name);
function oblFull(name) {
  if (CITY.has(name) || name === "Avtonomna Respublika Krym") return LANG === "uk" && name === "Avtonomna Respublika Krym" ? "АР Крим" : oblShort(name);
  return t("oblast.full", { name: oblShort(name) });
}
// "of the oblast": "Харківської області" / "Kharkiv Oblast"
function oblGen(name) {
  if (LANG !== "uk") return oblFull(name);
  if (name === "Kyiv City") return "м. Києва";
  return `${oblShort(name).replace(/а$/, "ої")} області`;
}
const raionMeta = (id) => GEO.base?.meta.raions[id];
function raionName(id, fallback) {
  const m = raionMeta(id) || fallback || {};
  if (LANG === "uk") return m.name_uk || id;
  const n = m.name || id;
  return n === "Kyiv City" ? "Kyiv City" : n === "Sevastopol City" ? "Sevastopol" : n.replace(/ raion$/, " Raion");
}
const raionShort = (id) => raionName(id).replace(/ (район|Raion)$/, "");

// Ukrainian place names from alerts.in.ua (hromadas, cities) in English: official transliteration (2010).
const TR = { а: "a", б: "b", в: "v", г: "h", ґ: "g", д: "d", е: "e", є: "ie", ж: "zh", з: "z", и: "y", і: "i", ї: "i", й: "i", к: "k",
  л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "kh", ц: "ts", ч: "ch", ш: "sh", щ: "shch",
  ь: "", ю: "iu", я: "ia", "'": "", "’": "", "ʼ": "" };
const TR_INITIAL = { є: "ye", ї: "yi", й: "y", ю: "yu", я: "ya" };
function translit(s) {
  let out = "";
  for (let i = 0; i < s.length; i++) {
    const c = s[i], lc = c.toLowerCase();
    if (!(lc in TR)) { out += c; continue; }
    const initial = i === 0 || !/[\p{L}'’ʼ]/u.test(s[i - 1]);
    let r = lc === "г" && s[i - 1]?.toLowerCase() === "з" ? "gh" : (initial && TR_INITIAL[lc]) || TR[lc];
    if (c !== lc && r) r = r[0].toUpperCase() + r.slice(1);
    out += r;
  }
  return out;
}
const normUk = (s) => s.toLowerCase().replace(/[ʼ’‘`´]/g, "'").replace(/\s+/g, " ").trim();
let UK_RAION = null;  // Ukrainian raion name -> id
function locName(title) {
  if (LANG === "uk") return title;
  if (!UK_RAION && GEO.base) UK_RAION = new Map(Object.entries(GEO.base.meta.raions).map(([id, m]) => [normUk(m.name_uk), id]));
  const rid = UK_RAION?.get(normUk(title));
  if (rid) return raionName(rid);
  let s = title.trim();
  const city = /^м\.\s*/.test(s);
  s = s.replace(/^м\.\s*/, "").replace(/\s+(сільська|селищна|міська)?\s*територіальна громада$/i, " hromada")
    .replace(/\s+область$/i, " Oblast").replace(/\s+район$/i, " Raion");
  return translit(s) + (city ? ` ${t("city")}` : "");
}

// Static text of index.html: data-i18n (text), data-i18n-html, data-i18n-attr="attr:key;attr:key".
function applyStatic() {
  document.documentElement.lang = LANG;
  for (const n of document.querySelectorAll("[data-i18n]")) n.textContent = t(n.dataset.i18n);
  for (const n of document.querySelectorAll("[data-i18n-html]")) n.innerHTML = t(n.dataset.i18nHtml);
  for (const n of document.querySelectorAll("[data-i18n-attr]")) {
    for (const pair of n.dataset.i18nAttr.split(";")) {
      const [a, k] = pair.split(":");
      n.setAttribute(a.trim(), t(k.trim()));
    }
  }
  document.title = t("doc.title");
  $('meta[name="description"]')?.setAttribute("content", t("doc.description"));
  for (const b of document.querySelectorAll("#lang button")) b.setAttribute("aria-pressed", String(b.dataset.setlang === LANG));
}

function setLang(lang) {
  if (!LANGS.includes(lang) || lang === LANG) return;
  LANG = lang;
  PLURAL = new Intl.PluralRules(lang === "uk" ? "uk" : "en");
  try { localStorage.setItem("lang", lang); } catch (_) { /* private mode: the URL or the default decides */ }
  try {
    const u = new URL(location.href);
    if (u.searchParams.has("lang")) { u.searchParams.set("lang", lang); history.replaceState(null, "", u); }
  } catch (_) { /* ignore */ }
  setFormatters();
  applyStatic();
  renderAttr();
  $("#region-list").innerHTML = "";
  if (S.view === "about") renderAbout();
  if (!S.forecast) return;
  render();
  renderPanel();
  if (S.view === "metrics") renderMetrics();
  if (S.view === "history") renderHistory();
}

// ---------------------------------------------------------------- data access
const STATIC_SITE = !!document.querySelector('meta[name="airraid-static"]');
const apiUrl = (url) => (STATIC_SITE && url.startsWith("/api/") ? `${url.slice(1).split("?")[0]}.json` : url);

async function getJSON(url, cache = "no-store") {
  url = apiUrl(url);
  const r = await fetch(url, { cache });
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
  const x = Math.min(1, Math.max(0, p)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  const f = x - i;
  const c = RAMP[i].map((v, k) => Math.round(v + (RAMP[i + 1][k] - v) * f));
  return `rgb(${c.join(",")})`;
}
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const darkMode = () => window.matchMedia("(prefers-color-scheme: dark)").matches;
const invText = (p) => (darkMode() ? p > 0.6 : p > 0.55);
const LEVEL_CUTS = [0.15, 0.4, 0.7, 1.01];  // low / moderate / high / very high (shown in the "?" explanation)
const levelIdx = (p) => LEVEL_CUTS.findIndex((x) => Math.round(p * 100) / 100 < x);
const levelWord = (p) => t(`lvl.${levelIdx(p)}`);
const level = (p) => (p == null ? "" : `<span class="lvl l${levelIdx(p)}">${levelWord(p)}</span>`);
const probCell = (p) => (p == null ? `<span class="cell">–</span>` : `<span class="cell" style="background:${rampColor(p)};color:${invText(p) ? "#fff" : "inherit"}">${pct(p)}</span>`);
// small shares (threat type x chance of a new alert) round to 0%: say "<1%" instead
const pctSmall = (p) => (p != null && p > 0 && p < 0.005 ? "<1%" : pct(p));

// ---------------------------------------------------------------- "what do the percentages mean"
// A short line next to every forecast table plus a "?" button that opens the full definition:
// what is forecast (an air raid alert of any type), both targets with their time windows, what a
// percentage means (a calibrated probability), the risk words and how the history charts differ.
const OPEN = new Set();  // ids of the explanations the viewer opened; kept across re-renders
function infoBtn(id, label = t("info.btn")) {
  return `<button type="button" class="info-btn" aria-expanded="${OPEN.has(id)}" aria-controls="${id}" title="${esc(label)}">`
    + `<span aria-hidden="true">?</span><span class="sr">${label}</span></button>`;
}
function levelsText() {
  const cuts = LEVEL_CUTS.slice(0, -1).map((x) => Math.round(x * 100));
  return [0, 1, 2, 3].map((i) => {
    const range = i === 0 ? t("ex.below", { p: `${cuts[0]}%` })
      : i === 3 ? t("ex.above", { p: `${cuts[2]}%` }) : t("ex.range", { a: cuts[i - 1], b: `${cuts[i] - 1}%` });
    return `<span class="nw"><span class="lvl l${i}">${t(`lvl.${i}`)}</span> ${range}</span>`;
  }).join(", ");
}
function explainHTML(id, windows, { raion = false, types = false } = {}) {
  const w1 = windows?.[1]?.active;
  const tm = (x) => (x ? fmtTime.format(new Date(x)) : "?");
  return `<div class="explain" id="${id}"${OPEN.has(id) ? "" : " hidden"}>
    <p>${t("ex.what")}</p>
    <p>${t("ex.active", { a: tm(w1?.[0]), b: tm(w1?.[1]) })}</p>
    <p>${t("ex.onset", { a: tm(w1?.[0]) })}</p>
    <p>${t("ex.pct")}</p>
    <p>${t("ex.levels", { list: levelsText() })}</p>
    ${raion ? `<p>${t("ex.raion")}</p>` : ""}
    ${types ? `<p>${t("ex.types")}</p>` : ""}
    <p>${t("ex.charts")}</p></div>`;
}
// the short line under a forecast heading, its "?" button and the (hidden) full explanation
const pctNote = (id, key, windows, opts) => `<p class="pnote">${t(key)} ${infoBtn(id)}</p>${explainHTML(id, windows, opts)}`;

// ---------------------------------------------------------------- state
const S = {
  state: null, forecast: null, metrics: null, status: null, raions: null,
  layer: "now", target: "active", horizon: 1, view: "map",
  unit: "oblast",       // colour the country by oblast or by raion
  selected: null,       // selected oblast id (also the oblast of the selected raion)
  rsel: null,           // selected raion id
  panel: null,          // what the side panel shows: {kind: "region"|"raion", data}
  vb: null, full: null, zoomOblast: "",
  charts: {},           // statistics charts of the selected oblast: id -> {title, draw, legend}
};

// ---------------------------------------------------------------- geometry (two levels of detail)
// ukraine-map.json: arcs (delta-encoded integers, scripts/build_map.py) and rings of arc indices for
// raions and oblasts; the oblasts are the raions dissolved, so all borders coincide. The detailed
// level (ukraine-map-detail.json) is fetched the first time the map zooms in.
const GEO = { base: null, detail: null, lod: "coarse", arcs: {}, paths: { coarse: {}, detail: {} }, loading: null };
const DETAIL_K = 1.6;  // zoom factor from which the detailed outlines are used

function decodeArcs(g) {
  return g.arcs.map((a) => {
    const out = new Float64Array(a.length);
    let x = 0, y = 0;
    for (let i = 0; i < a.length; i += 2) { x += a[i]; y += a[i + 1]; out[i] = x / g.q; out[i + 1] = y / g.q; }
    return out;
  });
}
// Each arc is formatted once per direction (without its last point) and shared by both neighbours,
// the oblast that contains it and the border lines.
function arcText(arcs, r) {
  const cache = arcs.text || (arcs.text = new Map());
  let s = cache.get(r);
  if (s != null) return s;
  const a = arcs[r >= 0 ? r : ~r], n = a.length / 2, out = new Array(n - 1);
  for (let k = 0; k < n - 1; k++) {
    const j = r >= 0 ? k : n - 1 - k;
    out[k] = a[2 * j].toFixed(1) + "," + a[2 * j + 1].toFixed(1);
  }
  s = out.join("L");
  cache.set(r, s);
  return s;
}
function ringsPath(rings, arcs) {
  return rings.map((ring) => "M" + ring.map((r) => arcText(arcs, r)).join("L") + "Z").join("");
}
function linesPath(idx, arcs) {
  return idx.map((i) => {
    const a = arcs[i], n = a.length;
    return "M" + arcText(arcs, i) + "L" + a[n - 2].toFixed(1) + "," + a[n - 1].toFixed(1);
  }).join("");
}
// SVG path of an oblast ("o:<dataset name>"), a raion ("r:<id>") or the oblast borders ("borders") at the current level.
function geoPath(key) {
  const lod = GEO.lod, cache = GEO.paths[lod];
  if (cache[key] != null) return cache[key];
  const g = lod === "detail" ? GEO.detail : GEO.base;
  const arcs = GEO.arcs[lod] || (GEO.arcs[lod] = decodeArcs(g));
  const [kind, id] = key.split(/:(.*)/s);
  cache[key] = kind === "borders" ? linesPath(g.borders, arcs) : ringsPath((kind === "o" ? g.oblasts : g.raions)[id] || [], arcs);
  return cache[key];
}
function setLOD(lod) {
  if (lod === GEO.lod || (lod === "detail" && !GEO.detail)) return;
  GEO.lod = lod;
  // only what is drawn now; hidden raions are updated when they are shown (syncGeo)
  for (const n of $("#map").querySelectorAll("[data-geo]")) if (n.getAttribute("d") != null && n.getClientRects().length) syncGeo(n);
}
function syncGeo(n) {
  if (n.dataset.lod === GEO.lod) return;
  n.setAttribute("d", geoPath(n.dataset.geo));
  n.dataset.lod = GEO.lod;
}
function ensureDetail() {
  if (GEO.detail || GEO.loading || !GEO.base?.detail) return;
  GEO.loading = getJSON(`static/${GEO.base.detail.file}`, "force-cache").then((g) => {
    GEO.detail = g;
    if (zoomK() >= DETAIL_K) setLOD("detail");
  }).catch((e) => { console.warn("detailed map unavailable", e); }).finally(() => { GEO.loading = null; });
}
const oblastMeta = (name) => GEO.base?.meta.oblasts[name];

// ---------------------------------------------------------------- map
const SCALE_PATTERNS = [["hatch", 7], ["part", 5]];
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
  const svg = $("#map");
  const g = GEO.base;
  setFullView(g.viewBox);
  svg.innerHTML = "";
  mapDefs(svg);
  const gO = el("g", { id: "g-obl" }, svg);
  const names = Object.keys(g.oblasts).sort((a, b) => (a === "Kyiv City") - (b === "Kyiv City"));  // the city on top
  for (const name of names) {
    const id = slug(name);
    const known = !!regionData(id);
    const p = el("path", { d: geoPath(`o:${name}`), "data-geo": `o:${name}`, "data-lod": GEO.lod, class: known ? "ob reg" : "ob nodata" }, gO);
    if (known) { p.dataset.id = id; p.setAttribute("tabindex", "0"); p.setAttribute("role", "button"); } else p.dataset.nd = name;
  }
  // a wider invisible touch target around Kyiv City (small at country scale)
  el("path", { d: geoPath("o:Kyiv City"), "data-geo": "o:Kyiv City", "data-lod": GEO.lod, "data-id": "kyiv-city", class: "hit", "aria-hidden": "true" }, gO);
  const gR = el("g", { id: "g-rn" }, svg);
  for (const id of Object.keys(g.raions)) {
    const p = el("path", { d: geoPath(`r:${id}`), "data-geo": `r:${id}`, "data-lod": GEO.lod, class: "rn reg", "data-rid": id }, gR);
    p.setAttribute("role", "button");
  }
  el("path", { d: geoPath("borders"), "data-geo": "borders", "data-lod": GEO.lod, class: "obl-border" }, svg);
  el("path", { id: "hover-line", class: "hover-line" }, svg);
  el("path", { id: "sel-obl", class: "sel-line" }, svg);
  el("path", { id: "sel-rn", class: "sel-line rn-line" }, svg);
  el("g", { id: "labels" }, svg);
  renderAttr();
  applyViewBox(S.vb || S.full);
}

function renderAttr() {
  const a = GEO.base?.attribution;
  if (!a) return;
  $("#attr").innerHTML = `${esc(a[LANG] || a.en)} · <a href="static/ukraine-map.LICENSE.txt">${t("map.licence")}</a>`;
}

// Map events are attached once: oblasts carry data-id, raions data-rid, no-data areas data-nd.
function setupMapEvents() {
  const svg = $("#map");
  const tip = $("#tip");
  let hoverKey = null;
  svg.addEventListener("pointermove", (ev) => {
    const tg = ev.target.closest("[data-id], [data-rid], [data-nd]");
    const html = !tg ? null : tg.dataset.rid ? raionTipHTML(tg.dataset.rid)
      : tg.dataset.nd ? `<b>${esc(oblFull(tg.dataset.nd))}</b><br>${t("map.nodata")}` : (regionData(tg.dataset.id) && tipHTML(regionData(tg.dataset.id)));
    const key = tg && !tg.dataset.nd ? (tg.dataset.rid ? `r:${tg.dataset.rid}` : `o:${regionData(tg.dataset.id)?.name}`) : null;
    if (key !== hoverKey) { hoverKey = key; setHover(key); }
    if (!html) { tip.hidden = true; return; }
    const box = $(".mapwrap").getBoundingClientRect();
    tip.style.left = `${ev.clientX - box.left}px`;
    tip.style.top = `${ev.clientY - box.top}px`;
    tip.innerHTML = html;
    tip.hidden = false;
  });
  svg.addEventListener("pointerleave", () => { tip.hidden = true; hoverKey = null; setHover(null); });
  svg.addEventListener("click", (ev) => {
    const tg = ev.target.closest("[data-id], [data-rid]");
    if (!tg) return;
    if (tg.dataset.rid) { if (raionById(tg.dataset.rid)) selectRaion(tg.dataset.rid); }
    else if (regionData(tg.dataset.id)) select(tg.dataset.id);
  });
  svg.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { resetZoom(); return; }
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const d = ev.target.dataset || {};
    if (d.rid && raionById(d.rid)) { ev.preventDefault(); selectRaion(d.rid); }
    else if (d.id) { ev.preventDefault(); select(d.id); }
  });
  svg.addEventListener("focusin", (ev) => {
    const d = ev.target.dataset || {};
    setHover(d.rid ? `r:${d.rid}` : d.id ? `o:${regionData(d.id)?.name}` : null);
  });
  svg.addEventListener("focusout", () => setHover(null));
}
function setHover(key) {
  const h = $("#hover-line");
  if (!h) return;
  if (key && !key.endsWith(":undefined")) { h.setAttribute("d", geoPath(key)); h.dataset.geo = key; h.dataset.lod = GEO.lod; }
  else { h.removeAttribute("d"); delete h.dataset.geo; }
}

// What the map shows as "under alert now": alerts.in.ua reports an air raid alert somewhere in the
// oblast (`reported`, display only). The model's input (`active`, training definition) can be off
// while a long frontline alert is still open, so it is not used for colouring.
const isOn = (r) => !!(r && (r.reported ?? r.active));

// id -> rows, rebuilt on every refresh
const IDX = { st: new Map(), fc: new Map(), rn: new Map() };
function indexData() {
  IDX.st = new Map((S.state?.regions || []).map((r) => [r.id, r]));
  IDX.fc = new Map((S.forecast?.regions || []).map((r) => [r.id, r]));
  IDX.rn = new Map((S.raions?.raions || []).map((r) => [r.id, r]));
}
function regionData(id) {
  const st = IDX.st.get(id), fc = IDX.fc.get(id);
  if (!st && !fc) return null;
  return { ...(fc || {}), ...(st || {}), probs: fc?.probs };
}
// what the oblast panel shows from the live data: its own state and its raions' state and forecast
const panelLive = (id, name) => JSON.stringify([IDX.st.get(id), (S.raions?.raions || []).filter((r) => r.oblast === name).map((r) => [r.live, r.probs, r.method])]);
function clearSelection() {
  S.selected = null; S.rsel = null; S.panel = null;
  if (location.hash.startsWith("#region/") || location.hash.startsWith("#raion/")) history.replaceState(null, "", `${location.search}#map`);
  $("#zoom-info").textContent = "";
  paintMap(); renderRank(); renderPanel(); resetZoom();
}

// threat types: yellow (drones) -> red (missiles, ballistic, KAB, massed drones)
function hexRgb(h) { h = h.replace("#", ""); return [0, 2, 4].map((j) => parseInt(h.slice(j, j + 2), 16)); }
function levelColor(pRed) {
  const a = hexRgb(css("--yel")), b = hexRgb(css("--alert"));
  const x = Math.min(1, Math.max(0, pRed));
  return `rgb(${a.map((v, k) => Math.round(v + (b[k] - v) * x)).join(",")})`;
}
const typeAt = (r) => r?.types?.[S.horizon];

function tipHTML(r) {
  const head = `<b>${esc(oblFull(r.name))}</b>`;
  if (S.layer === "forecast" && S.target === "type") {
    const x = typeAt(r);
    if (!x) return `${head}<br>${t("tip.notype")}`;
    return `${head}<br>${t("tip.type", { type: t(`typePhrase.${x.most_likely}`), level: t(`lvlc.${x.level}`) })}`;
  }
  if (S.layer === "now") {
    const s = isOn(r) ? (r.active && r.since ? t("tip.alertFor", { d: duration(r.since) }) : t("tip.alert")) : t("tip.noAlert");
    return `${head}<br>${s}`;
  }
  return `${head}<br>${probTip(S.target, r.probs?.[S.target]?.[S.horizon], true)}`;
}
// "chance of an alert in hour 2: 45% (high)" / "chance of a new alert within 3 h: 60% (high)"
function probTip(target, p, bold = false) {
  if (p == null) return "–";
  return t(target === "active" ? "tip.probActive" : "tip.probOnset", { h: S.horizon, p: bold ? `<b>${pct(p)}</b>` : pct(p), level: levelWord(p) });
}

const focusOblast = () => (S.unit === "oblast" && S.selected ? regionData(S.selected)?.name || null : null);
const zoomK = () => (S.full && S.vb ? S.full[2] / S.vb[2] : 1);

function paintMap() {
  const svg = $("#map");
  if (!GEO.base || !svg.querySelector("#g-obl")) return;
  const raionMode = S.unit === "raion";
  const focus = focusOblast();
  svg.classList.toggle("mode-raion", raionMode);
  if (raionMode) syncGeo(svg.querySelector(".obl-border"));
  svg.classList.toggle("has-focus", !!focus);
  svg.setAttribute("aria-label", raionMode ? t("map.ariaRaions") : t("map.ariaOblasts"));
  COL = { calm: css("--calm"), alert: css("--alert") };
  for (const node of svg.querySelectorAll(".ob[data-id]")) {
    const r = regionData(node.dataset.id);
    const isFocus = !!focus && r?.name === focus;
    node.classList.toggle("focus", isFocus);
    node.classList.toggle("sel", node.dataset.id === S.selected);
    let fill = COL.calm, p = null;
    if (r && S.layer === "now") fill = isOn(r) ? COL.alert : COL.calm;
    else if (r && S.forecast && S.target === "type") { const x = typeAt(r); fill = x?.p_red == null ? COL.calm : levelColor(x.p_red); }
    else if (r && S.forecast) { p = r.probs?.[S.target]?.[S.horizon]; fill = p == null ? COL.calm : rampColor(p); }
    node.style.fill = fill;
    node.setAttribute("aria-label", r ? `${oblFull(r.name)}: ${S.layer === "now" ? (isOn(r) ? t("tip.alert") : t("tip.noAlert")) : S.target === "type" ? t("ctl.type") : probTip(S.target, p)}` : "");
    // the focused oblast is drawn by its raions; keep it out of the tab order
    if (isFocus && S.raions) node.setAttribute("tabindex", "-1"); else node.setAttribute("tabindex", "0");
  }
  for (const node of svg.querySelectorAll(".rn")) {
    const id = node.dataset.rid;
    const m = raionMeta(id);
    const r = raionById(id);
    const shown = raionMode || (!!focus && m?.oblast === focus);
    node.classList.toggle("in", shown);
    if (shown) syncGeo(node);
    node.classList.toggle("nodata", !r);
    node.classList.toggle("reg", !!r);
    node.classList.toggle("sel", id === S.rsel);
    node.style.fill = r ? raionFill(r) : (raionMode ? "url(#hatch)" : "transparent");
    if (shown && r) {
      node.setAttribute("tabindex", "0");
      const p = r.probs?.[rTarget()]?.[S.horizon];
      node.setAttribute("aria-label", `${raionName(id)}: ${S.layer === "now" ? (r.live?.active ? t(`live.${r.live.level}`) : r.live ? t("tip.noAlert") : t("raion.noLive")) : probTip(rTarget(), p)}`);
    } else node.removeAttribute("tabindex");
  }
  // selection outlines on top of everything (a clean line, not clipped by the neighbours)
  const so = $("#sel-obl"), sr = $("#sel-rn");
  const selName = S.selected ? regionData(S.selected)?.name : null;
  for (const [n, key] of [[so, selName && `o:${selName}`], [sr, S.rsel && `r:${S.rsel}`]]) {
    if (key) { n.dataset.geo = key; n.setAttribute("d", geoPath(key)); n.dataset.lod = GEO.lod; } else { delete n.dataset.geo; n.removeAttribute("d"); }
  }
  paintLabels();
  paintLegend();
  renderZoomOblasts();
}

let COL = null;  // theme colours read once per paint
function raionFill(r) {
  const c = COL || { calm: css("--calm"), alert: css("--alert") };
  if (!r) return c.calm;
  if (S.layer === "now") {
    const lv = r.live;
    if (!lv?.active) return c.calm;
    return lv.level === "hromada" ? "url(#part)" : c.alert;
  }
  // threat type is forecast per oblast only: let the oblast's colour show instead of a raion alert chance
  if (S.target === "type" && S.unit === "oblast") return "transparent";
  const p = r.probs?.[rTarget()]?.[S.horizon];
  return p == null ? c.calm : rampColor(p);
}

// Labels keep their on-screen size while zooming: font sizes are divided by the zoom factor.
function paintLabels() {
  const labels = $("#labels");
  if (!labels || !GEO.base) return;
  labels.innerHTML = "";
  const k = zoomK();
  const small = window.matchMedia("(max-width: 600px)").matches;
  const [x0, y0, w, h] = S.vb;
  const inView = (at) => at[0] >= x0 && at[0] <= x0 + w && at[1] >= y0 && at[1] <= y0 + h;
  const focus = focusOblast();
  // sizes are given in screen pixels: one map unit is (svg width / viewBox width) pixels
  const px = ($("#map").clientWidth || 800) / w;
  const text = (at, s, cls, size, dy = 0) => {
    const n = el("text", { x: at[0], y: at[1] + dy / px, "text-anchor": "middle", "dominant-baseline": "middle", class: cls,
      style: `font-size:${(size / px).toFixed(2)}px;stroke-width:${(3 / px).toFixed(2)}px` }, labels);
    n.textContent = s;
  };
  if (S.unit === "oblast" && S.layer === "forecast") {
    for (const r of S.forecast?.regions || []) {
      if (r.name === focus) continue;
      const at = oblastMeta(r.name)?.label;
      if (!at || !inView(at)) continue;
      let label = null, p = null;
      if (S.target === "type") { const x = typeAt(r); label = x?.most_likely ? t(`typeShort.${x.most_likely}`) : null; p = x?.p_red > 0.75 ? 0.9 : 0; }
      else { p = r.probs?.[S.target]?.[S.horizon]; label = pct(p); }
      if (!label) continue;
      const isCity = r.name === "Kyiv City";
      text(at, label, "lbl" + (!isCity && invText(p) ? " inv" : ""), small ? 10 : 13.5, isCity && k < 2 ? -14 : 0);
    }
  }
  // raions: of the focused oblast (oblast view), or all when zoomed in enough (raion view)
  if (!S.raions || (S.unit === "raion" && k < 2.2) || (S.unit === "oblast" && (!focus || k < 1.8))) return;
  if (S.layer === "forecast" && S.target === "type") return;  // no raion threat-type forecast to label
  for (const r of S.raions.raions) {
    if (S.unit === "oblast" && r.oblast !== focus) continue;
    const at = raionMeta(r.id)?.label;
    if (!at || !inView(at)) continue;
    if (S.layer === "now") {
      if (S.unit === "raion" && k < 4) continue;
      text(at, raionShort(r.id), "lbl rname", small ? 10.5 : 12);
    } else {
      const p = r.probs?.[rTarget()]?.[S.horizon];
      if (p != null) text(at, pct(p), "lbl rl" + (invText(p) ? " inv" : ""), small ? 11 : 13);
    }
  }
}

function paintLegend() {
  const lg = $("#legend");
  const stripes = (a, b, w) => `repeating-linear-gradient(45deg,${a} 0 ${w}px,${b} ${w}px ${2 * w}px)`;
  const raionsShown = S.unit === "raion" || (!!focusOblast() && !!S.raions);
  if (S.layer === "now") {
    lg.innerHTML = `<span><i class="sw" style="background:${css("--alert")}"></i>${raionsShown ? t("legend.alertRaion") : t("legend.alert")}</span>
      ${raionsShown ? `<span><i class="sw" style="background:${stripes(css("--alert"), css("--alert-soft"), 2)}"></i>${t("legend.partial")}</span>` : ""}
      <span><i class="sw" style="background:${css("--calm")}"></i>${t("legend.noAlert")}</span>
      <span><i class="sw" style="background:${stripes(css("--calm"), css("--nodata"), 3)}"></i>${t("legend.noData")}</span>
      ${raionsShown && !liveRaions() ? `<span class="warnnote">${t("legend.noLiveRaions")}</span>` : ""}`;
  } else if (S.target === "type" && S.unit === "oblast") {
    lg.innerHTML = `<span>${t("legend.yellow")}</span><span class="bar" style="background:linear-gradient(90deg,${levelColor(0)},${levelColor(0.5)},${levelColor(1)})"></span><span>${t("legend.red")}</span>
      <span class="muted">${t("legend.typeNote")}</span>`;
  } else {
    const stops = [0, 0.2, 0.4, 0.6, 0.8, 1].map((p) => `${rampColor(p)} ${p * 100}%`).join(",");
    const tg = S.unit === "raion" ? rTarget() : S.target;
    lg.innerHTML = `<span>0%</span><span class="bar" style="background:linear-gradient(90deg,${stops})"></span><span>100%</span>
      <span class="lg-what" title="${esc(t("legend.pctHint"))}">${tg === "active" ? t("legend.probActive") : t("legend.probOnset")}${S.unit === "raion" ? t("legend.raionZoom") : ""} ${infoBtn("ex-legend")}</span>
      ${explainHTML("ex-legend", S.forecast?.windows, { raion: S.unit === "raion" })}`;
  }
}

// ---------------------------------------------------------------- zoom
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function setFullView(viewBox) {
  const svg = $("#map");
  S.full = viewBox.split(" ").map(Number);
  svg.style.aspectRatio = `${S.full[2]} / ${S.full[3]}`;
  if (!S.vb) S.vb = S.full;
}

function applyViewBox(vb) {
  S.vb = vb;
  const svg = $("#map");
  svg.setAttribute("viewBox", vb.map((v) => v.toFixed(1)).join(" "));
  const k = zoomK();
  $("#zoom-reset").disabled = !(k > 1.02);
  svg.classList.toggle("zoomed", k > 1.5);
  // hatching keeps its on-screen size
  for (const [id, base] of SCALE_PATTERNS) {
    const p = svg.querySelector(`#${id}`);
    if (p) p.setAttribute("patternTransform", `rotate(45) scale(${(1 / Math.max(1, k)).toFixed(4)})`);
  }
}

function afterZoom() {
  const k = zoomK();
  if (k >= DETAIL_K) { ensureDetail(); setLOD("detail"); } else setLOD("coarse");
  paintLabels();
}

function animateTo(to, dur) {
  const from = S.vb || S.full;
  if (reducedMotion()) { applyViewBox(to); afterZoom(); return; }
  const t0 = performance.now();
  const step = (now) => {
    const x = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - x, 3);
    applyViewBox(from.map((v, i) => v + (to[i] - v) * e));
    if (x < 1) requestAnimationFrame(step); else afterZoom();
  };
  requestAnimationFrame(step);
}

// Fit a bbox [x0, y0, x1, y1] with padding, keeping the map's aspect ratio; animated.
function zoomTo(bbox, minFrac = 0.12) {
  if (!bbox || !S.full) return;
  const [W, H] = [S.full[2], S.full[3]];
  const [x0, y0, x1, y1] = bbox;
  let w = Math.max(x1 - x0, W * minFrac), h = Math.max(y1 - y0, H * minFrac);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  w *= 1.18; h *= 1.18;
  if (w / h > W / H) h = w * H / W; else w = h * W / H;
  if (w > W) { w = W; h = H; }
  const to = [Math.min(Math.max(cx - w / 2, -W * 0.05), W * 1.05 - w), Math.min(Math.max(cy - h / 2, -H * 0.05), H * 1.05 - h), w, h];
  if (to[2] / W < 1 / DETAIL_K) ensureDetail();  // start loading the detailed outlines while animating
  animateTo(to, 420);
}
function resetZoom() { if (S.full) animateTo(S.full, 360); }

function unionBox(ids) {
  const boxes = ids.map((id) => raionMeta(id)?.bbox).filter(Boolean);
  if (!boxes.length) return null;
  return [Math.min(...boxes.map((b) => b[0])), Math.min(...boxes.map((b) => b[1])), Math.max(...boxes.map((b) => b[2])), Math.max(...boxes.map((b) => b[3]))];
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
  $("#lang").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-setlang]");
    if (b) setLang(b.dataset.setlang);
  });
  $("#zoom-alerts").addEventListener("click", zoomToAlerts);
  $("#zoom-reset").addEventListener("click", () => { $("#zoom-info").textContent = ""; resetZoom(); });
  $("#zoom-oblast").addEventListener("change", (ev) => {
    S.zoomOblast = ev.target.value;
    if (S.unit === "oblast") {
      if (S.zoomOblast) select(slug(S.zoomOblast)); else clearSelection();
      return;
    }
    renderRank();
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
  const key = `${hs.join(",")}|${LANG}`;
  if (box.dataset.hs === key) return;
  box.dataset.hs = key;
  box.innerHTML = hs.map((h) => `<button data-v="${h}" aria-pressed="${h === S.horizon}">${t("h.n", { n: h })}</button>`).join("");
}

function windowText() {
  if (S.layer === "now" && S.unit === "raion") {
    return liveRaions() ? t("win.liveRaions", { time: fmtTime.format(new Date(S.raions.live_as_of)) }) : t("win.noLiveRaions");
  }
  if (S.layer === "now") {
    if (!S.state) return "";
    return S.state.source === "alerts.in.ua"
      ? t("win.live", { time: fmtTime.format(new Date(S.state.as_of)) })
      : t("win.dataset", { time: fmtFull.format(new Date(S.state.as_of)) });
  }
  const w = S.forecast?.windows?.[S.horizon];
  if (!w) return "";
  if (S.target === "type") {
    const o = S.forecast.threats?.origin;
    return t("win.type", { h: S.horizon }) + (o ? t("win.typeAt", { time: fmtTime.format(new Date(o.replace(" ", "T") + "Z")) }) : "");
  }
  const [a, b] = S.target === "active" ? w.active : w.onset;
  return t(S.target === "active" ? "win.active" : "win.onset", { a: fmtTime.format(new Date(a)), b: fmtTime.format(new Date(b)) });
}

// ---------------------------------------------------------------- summary
function renderSummary() {
  const st = S.state, fc = S.forecast;
  const tiles = [];
  if (st) {
    const n = st.regions.filter(isOn).length;
    tiles.push(`<div class="stat ${n ? "hot" : ""}"><div class="k">${t("sum.now")}</div>
      <div class="v">${n} <small>${t("sum.ofOblasts", { n: st.regions.length })}</small></div>
      <div class="s">${st.source === "alerts.in.ua" ? `alerts.in.ua, ${fmtTime.format(new Date(st.as_of))}` : t("sum.dataset", { time: fmtFull.format(new Date(st.as_of)) })}</div></div>`);
  }
  if (liveRaions()) {
    const on = activeRaions(), part = on.filter((r) => r.live.level === "hromada").length;
    tiles.push(`<div class="stat ${on.length ? "hot" : ""}"><div class="k">${t("sum.raions")}</div>
      <div class="v">${on.length} <small>${t("sum.of", { n: S.raions.raions.length })}</small></div>
      <div class="s">${part ? t("sum.partial", { n: part }) : t("sum.whole")}</div></div>`);
  }
  if (fc) {
    const exp = fc.regions.reduce((s, r) => s + (r.probs.active[1] || 0), 0);
    tiles.push(`<div class="stat"><div class="k">${t("sum.expected")}</div>
      <div class="v">≈ ${exp.toFixed(1)} <small>${t("sum.oblasts")}</small></div><div class="s">${t("sum.expectedNote")}</div></div>`);
  }
  $("#summary").innerHTML = tiles.join("");
}

function renderHeadline() {
  const st = S.state, fc = S.forecast;
  if (!st || !fc) return;
  const on = st.regions.filter(isOn);
  const live = st.source === "alerts.in.ua";
  const when = live ? t("head.now") : t("head.at", { time: fmtTime.format(new Date(st.as_of)) });
  let a = on.length ? t("head.alerts", { when, n: on.length }) : t("head.none", { when });
  const h = fc.horizons.includes(3) ? 3 : fc.horizons[0];
  const quiet = fc.regions.filter((r) => !on.some((x) => x.id === r.id));
  const top = [...quiet].sort((x, y) => y.probs.onset[h] - x.probs.onset[h]).filter((r) => r.probs.onset[h] >= 0.4).slice(0, 3);
  a += " " + (top.length
    ? t("head.next", { h, list: top.map((r) => `${esc(oblShort(r.name))} <b>${pct(r.probs.onset[h])}</b>`).join(", ") })
    : t("head.nextNone", { h }));
  $("#headline").innerHTML = a;
  const dl = $("#region-list");
  if (!dl.childElementCount) {
    dl.innerHTML = fc.regions.map((r) => `<option value="${esc(oblFull(r.name))}"></option>`).join("")
      + (S.raions?.raions || []).map((r) => `<option value="${esc(raionName(r.id, r))}">${esc(oblFull(r.oblast))}</option>`).join("");
  }
}

// When the data was last updated (api/status.json from the pipeline, /api/status on a server).
const STALE_MIN = 30;
function updatedAt() {
  const x = S.status?.updated_at || S.forecast?.issued_at;
  return x ? new Date(x) : null;
}
function staleWarning() {
  const u = updatedAt();
  if (!u) return null;
  const age = Math.round((Date.now() - u) / 60000);
  if (age <= STALE_MIN) return null;
  const ago = age >= 120 ? t("dur.hm", { h: Math.floor(age / 60), m: age % 60 }) : t("dur.m", { m: age });
  return t("stale", { ago, time: fmtFull.format(u) });
}

function warningText(w) {
  switch (w.code) {
    case "no_live": return t("warn.noLive");
    case "history_gap": return t("warn.gap", { h: w.hours });
    case "live_unavailable": return t("warn.liveUnavailable", { error: esc(w.error || "") });
    case "live_stale": return t("warn.liveStale", { time: w.seen ? fmtFull.format(new Date(w.seen)) : "?" });
    default: return esc(w.text || w.code || "");
  }
}

function renderBanner() {
  const stale = staleWarning();
  const fc = S.forecast;
  const ws = fc?.warning_codes ? fc.warning_codes.map(warningText) : (fc?.warnings || []).map(esc);
  const w = [...(stale ? [stale] : []), ...ws];
  const b = $("#banner");
  b.hidden = !w.length;
  b.innerHTML = w.map((x) => `<p>${x}</p>`).join("");
}

// ---------------------------------------------------------------- ranking
function renderRank() {
  const fc = S.forecast;
  const tb = $("#rank");
  if (S.unit === "raion" && S.raions) { renderRaionRank(); return; }
  $("#rank-title").textContent = t("rank.oblasts");
  if (!fc) { tb.innerHTML = ""; return; }
  const hs = fc.horizons;
  const active = new Set((S.state?.regions || []).filter(isOn).map((r) => r.id));
  const key = S.layer === "now" ? (r) => (active.has(r.id) ? 2 : 0) + r.probs.active[1]
    : S.target === "type" ? (r) => r.types?.[S.horizon]?.p_red ?? -1 : (r) => r.probs[S.target][S.horizon];
  const rows = [...fc.regions].sort((a, b) => key(b) - key(a));
  const w = fc.windows || {};
  const tm = (x) => fmtTime.format(new Date(x));
  const th = (tg, h, i) => `<th scope="col"${i ? "" : ' class="gs"'} title="${esc(w[h] ? t(tg === "active" ? "rank.colActiveTitle" : "rank.colOnsetTitle", { a: tm(w[h][tg][0]), b: tm(w[h][tg][1]) }) : "")}">${t(tg === "active" ? "rank.colActive" : "rank.colOnset", { h })}</th>`;
  tb.innerHTML = `<caption class="sr">${t("rank.oblasts")}: ${t("rank.note")}</caption><thead>
    <tr class="grp"><th scope="col" rowspan="2">${t("rank.oblast")}</th><th scope="colgroup" colspan="${hs.length}" class="gs">${t("rank.grpActive")}</th><th scope="colgroup" colspan="${hs.length}" class="gs">${t("rank.grpOnset")}</th></tr>
    <tr>${hs.map((h, i) => th("active", h, i)).join("")}${hs.map((h, i) => th("onset", h, i)).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr data-id="${r.id}" tabindex="0" class="${r.id === S.selected ? "sel" : ""}">
      <td>${esc(oblShort(r.name))}${active.has(r.id) ? `<span class="badge">${t("badge.alert")}</span>` : ""}</td>
      ${hs.map((h, i) => `<td${i ? "" : ' class="gs"'}>${probCell(r.probs.active[h])}</td>`).join("")}
      ${hs.map((h, i) => `<td${i ? "" : ' class="gs"'}>${probCell(r.probs.onset[h])}</td>`).join("")}</tr>`).join("")}</tbody>`;
  $("#rank-note").innerHTML = pctNote("ex-rank", "rank.note", fc.windows);
  const w1 = fc.windows?.[1]?.active;
  $("#rank-sub").textContent = t("rank.sub", { time: fmtFull.format(new Date(fc.origin)) })
    + (w1 ? t("rank.sub1", { a: fmtTime.format(new Date(w1[0])), b: fmtTime.format(new Date(w1[1])) }) : "");
}

// ---------------------------------------------------------------- oblast panel
async function select(id, { zoom = true } = {}) {
  const r = regionData(id);
  if (!r) return;
  S.selected = id;
  S.rsel = null;
  if (location.hash !== `#region/${id}`) history.replaceState(null, "", `${location.search}#region/${id}`);
  paintMap();
  renderRank();
  if (zoom) zoomTo(oblastMeta(r.name)?.bbox, 0.18);
  const box = $("#detail");
  try {
    const d = await getJSON(`/api/regions/${encodeURIComponent(id)}`);
    if (S.selected === id && !S.rsel) { S.panel = { kind: "region", data: d }; renderPanel(); }
  } catch (e) {
    if (S.selected === id && !S.rsel) { S.panel = null; hideStats(); box.innerHTML = `<p class="muted">${t("err.region")}</p>`; }
  }
  if (window.matchMedia("(max-width: 900px)").matches) box.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderPanel() {
  const p = S.panel;
  if (!p) { $("#detail").innerHTML = `<p class="muted">${t(S.unit === "raion" ? "detail.pickRaion" : "detail.pick")}</p>`; hideStats(); return; }
  if (p.kind === "raion") { renderRaionDetail(p.data); hideStats(); } else renderDetail(p.data);
}

const pbar = (p) => (p == null ? "–" : `<div class="pbar"><div class="track"><div class="fill" style="width:${Math.round(p * 100)}%;background:${rampColor(Math.max(p, 0.15))}"></div></div><span class="num">${pct(p)}</span></div>`);
function probsTable(d, hs) {
  const w = d.windows;
  const rows = hs.map((h) => `<tr><th scope="row" class="h">${t("probs.hour", { h })}<br><span class="small">${fmtTime.format(new Date(w[h].active[0]))}–${fmtTime.format(new Date(w[h].active[1]))}</span></th>
      ${["active", "onset"].map((tg) => `<td${whyCell(tg, h)}>${pbar(d.probs[tg][h])}</td>`).join("")}</tr>`).join("");
  return `<table class="probs"><thead><tr><th></th><th scope="col">${t("probs.active")}</th><th scope="col">${t("probs.onset")}</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderDetail(d) {
  const st = IDX.st.get(d.id);
  const hs = Object.keys(d.probs.active).map(Number);
  const status = isOn(st) && !st.active
    ? `<span class="status on"><span class="b"></span>${t("status.reported")}</span>`
    : st?.active
      ? `<span class="status on"><span class="b"></span>${st.since ? t("status.since", { time: sinceTime(st.since), d: duration(st.since, S.state.source === "alerts.in.ua" ? new Date() : new Date(S.state.as_of)) }) : t("status.alert")}</span>`
      : `<span class="status off"><span class="b"></span>${t("status.none")}</span>`;
  const raionRows = (S.raions?.raions || []).filter((r) => r.oblast === d.name);
  const locs = !raionRows.length && st?.locations?.length ? `<p class="small muted">${t("detail.locations", { list: st.locations.map((x) => esc(locName(x))).join(", ") })}</p>` : "";
  const h3 = hs.includes(3) ? 3 : hs[0];
  const pa = d.probs.active[1], po = d.probs.onset[h3];
  const tt = d.types?.[3] || d.types?.[1];
  const typeLine = tt?.most_likely ? `<p>${t("brief.type", { type: t(`typePhrase.${tt.most_likely}`), level: t(`lvlc.${tt.level}`) })}</p>` : "";
  const brief = `<div class="brief">
    <p>${t("brief.active", { time: fmtTime.format(new Date(d.origin)), level: level(pa), p: pct(pa) })}</p>
    <p>${t("brief.onset", { h: h3, level: level(po), p: pct(po) })}</p>${typeLine}</div>`;
  $("#detail").innerHTML = `
    <h2>${esc(oblFull(d.name))}</h2>${status}${locs}${brief}
    <p class="small muted">${t(S.state?.source === "alerts.in.ua" ? "detail.srcLive" : "detail.srcDataset")} · ${t("detail.origin", { time: fmtTime.format(new Date(d.origin)) })}</p>
    <h3>${t("detail.forecast")}</h3>
    ${pctNote("ex-region", "detail.note", d.windows, { types: !!(d.types && S.forecast?.threats) })}
    ${probsTable(d, hs)}
    <section class="why" id="why" aria-live="polite">${whyHTML(d.why, d.probs, d.origin)}</section>
    ${raionListHTML(d.name)}
    ${typesHTML(d.types)}
    <p class="small"><a href="#stats" class="jump">${t("detail.toStats")}</a></p>`;
  for (const b of $("#detail").querySelectorAll(".rrow")) {
    b.addEventListener("click", () => selectRaion(b.dataset.rid, { zoom: true }));
    b.addEventListener("pointerenter", () => setHover(`r:${b.dataset.rid}`));
    b.addEventListener("pointerleave", () => setHover(null));
    b.addEventListener("focus", () => setHover(`r:${b.dataset.rid}`));
    b.addEventListener("blur", () => setHover(null));
  }
  $("#detail .jump")?.addEventListener("click", (ev) => { ev.preventDefault(); $("#stats").scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" }); });
  renderStats(d);
}

// The oblast's raions: live state (whole raion / part of it, since when, which hromadas), forecast and method.
function raionListHTML(oblast) {
  const rs = (S.raions?.raions || []).filter((r) => r.oblast === oblast);
  if (!rs.length) return "";
  const hs = S.raions.horizons || S.forecast?.horizons || [1, 2, 3, 6];
  const h3 = hs.includes(3) ? 3 : hs[0], hL = hs[hs.length - 1];
  const rank = (r) => (r.live?.active ? (r.live.level === "hromada" ? 2 : 3) : 0) + (r.probs.onset[h3] ?? 0);
  const rows = [...rs].sort((a, b) => rank(b) - rank(a));
  const on = rs.filter((r) => r.live?.active), part = on.filter((r) => r.live.level === "hromada");
  const to = liveTo();
  const line = (r) => {
    const lv = r.live;
    if (!lv) return `<span class="rs muted">${t("raion.noLive")}</span>`;
    if (!lv.active) return `<span class="rs muted">${t("tip.noAlert")}</span>`;
    const since = lv.since ? t("rlist.since", { time: sinceTime(lv.since, to), d: duration(lv.since, to) }) : "";
    if (lv.level !== "hromada") return `<span class="rs on">${t(`live.${lv.level}`)}${since ? `, ${since}` : ""}</span>`;
    const locs = (lv.locations || []).map(locName);
    const shown = locs.slice(0, 3).map(esc).join(", ") + (locs.length > 3 ? ` ${t("rlist.more", { n: locs.length - 3 })}` : "");
    return `<span class="rs on">${t("live.hromada")}${since ? `, ${since}` : ""}${shown ? `: ${shown}` : ""}</span>`;
  };
  const methods = (r) => [...new Set([r.method.active[1], r.method.onset[h3], r.method.onset[hL]])].map(mtag).join(" ");
  return `<h3>${t("rlist.title")}</h3>
    <p class="small rsum">${liveRaions() ? t("rlist.summary", { n: on.length, m: rs.length }) + (part.length ? t("rlist.summaryPart", { n: part.length }) : "") : t("raion.noLive")}</p>
    <div class="rcap small muted" aria-hidden="true"><span></span><span>${t("rlist.cap")}</span></div>
    <div class="rhead small muted" aria-hidden="true"><span></span><span>${t("rlist.h1")}</span><span>${t("rlist.h3", { h: h3 })}</span><span>${t("rlist.h3", { h: hL })}</span></div>
    <ul class="rlist">${rows.map((r) => `<li><button type="button" class="rrow${r.live?.active ? " on" : ""}${r.id === S.rsel ? " sel" : ""}" data-rid="${r.id}"
        aria-label="${esc(t("rlist.aria", { name: raionName(r.id, r), state: r.live?.active ? t(`live.${r.live.level}`) : t("tip.noAlert"), a: pct(r.probs.active[1]), h: h3, b: pct(r.probs.onset[h3]), hL, c: pct(r.probs.onset[hL]) }))}">
        <span class="rn-main"><span class="rn-name">${r.live?.active ? `<i class="dotsw ${r.live.level === "hromada" ? "part" : ""}"></i>` : `<i class="dotsw off"></i>`}${esc(raionName(r.id, r))}</span>
          ${line(r)}<span class="rn-m">${methods(r)}</span></span>
        ${probCell(r.probs.active[1])}${probCell(r.probs.onset[h3])}${probCell(r.probs.onset[hL])}</button></li>`).join("")}</ul>
    <p class="small muted">${t("rlist.note")}</p>`;
}

function typesHTML(types) {
  if (!types || !S.forecast?.threats) return "";
  const hs = Object.keys(types).map(Number).sort((a, b) => a - b);
  // p_onset_type (chance of a new alert within h) x (share of the type) comes with the threat-type forecast
  const hasOn = (x) => !!x?.p_onset_type && Object.keys(x.p_onset_type).length > 0;
  const block = (h) => {
    const x = types[h];
    const on = hasOn(x);
    const order = [...S.forecast.threats.types].sort((a, b) => (x.p_type[b.id] || 0) - (x.p_type[a.id] || 0));
    return `<div class="types-h"><b>${t("types.within", { h })}</b>${x.level ? `<span class="lvl ${x.level}">${t(`lvlc.${x.level}`)}</span>` : ""}</div>
      <table class="probs types"><thead><tr><th scope="col"><span class="sr">${t("ctl.type")}</span></th><th scope="col">${t("types.colShare")}</th>${on ? `<th scope="col" class="on">${t("types.colOnset")}</th>` : ""}</tr></thead>
      <tbody>${order.map((m) => {
        const p = x.p_type[m.id] || 0;
        return `<tr><td class="tname">${t(`type.${m.id}`)}</td><td><div class="pbar"><div class="track"><div class="fill" style="width:${Math.round(p * 100)}%;background:${m.level === "red" ? css("--alert") : css("--yel")}"></div></div><span class="num">${pct(p)}</span></div></td>${on ? `<td class="on">${pctSmall(x.p_onset_type[m.id])}</td>` : ""}</tr>`;
      }).join("")}</tbody></table>`;
  };
  const anyOn = hs.some((h) => hasOn(types[h]));
  return `<h3>${t("types.title")}</h3><p class="tsub">${t("types.sub")}</p>${hs.map(block).join("")}
    <p class="small muted">${t("types.note")}${anyOn ? ` ${t("types.noteOnset")}` : ""}</p>`;
}

// ---------------------------------------------------------------- statistics (full width, pixel-sized charts)
function hideStats() { $("#stats").hidden = true; S.charts = {}; }

function renderStats(d) {
  const box = $("#stats");
  box.hidden = false;
  const reg = S.metrics?.holdout?.by_region?.find((x) => x.region === d.name && x.target === "active" && x.horizon === 1);
  const ver = d.verification?.active_h1 || [];
  const done = ver.filter((x) => x.y != null);
  const right = done.filter((x) => (x.p >= 0.5) === (x.y === 1)).length;
  S.charts = {};
  const charts = [];
  const add = (id, wide, title, draw, legend = "", note = "", tag = "") => {
    S.charts[id] = { title, draw, legend };
    charts.push(`<figure class="chartbox${wide ? " wide" : ""}" data-chart="${id}">
      <figcaption><h3>${title}${tag ? ` <span class="ctag">${tag}</span>` : ""}</h3>
        <button type="button" class="expand" data-chart="${id}" aria-label="${esc(t("stats.expand", { title }))}" title="${esc(t("stats.expandShort"))}">
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12 3h5v5M8 17H3v-5M17 3l-6 6M3 17l6-6"/></svg></button></figcaption>
      <div class="chart-host" data-chart="${id}"></div>${legend ? `<div class="chart-legend">${legend}</div>` : ""}${note ? `<p class="small muted">${note}</p>` : ""}</figure>`);
  };
  if (ver.length) {
    add("strip", true, t("stats.strip"), (host, o) => stripChart(host, ver, o),
      `<span><i class="sw-dot y1"></i>${t("stats.stripYes")}</span><span><i class="sw-dot y0"></i>${t("stats.stripNo")}</span><span><i class="sw-dot yn"></i>${t("stats.stripUnknown")}</span>`,
      `${t("stats.stripNote")} ${done.length ? t("stats.stripScore", { right, n: done.length }) : t("stats.stripWait")}`);
  }
  add("hist", true, t("stats.hist"), (host, o) => histChart(host, d, o),
    `<span><i style="background:${css("--alert")}"></i>${t("stats.histBar")}</span><span><i class="fc" style="background:${rampColor(0.5)}"></i>${t("stats.histFc")}</span>`,
    t("stats.histNote"));
  if (d.daily_minutes?.values?.length) {
    add("cal", true, t("stats.cal"), (host, o) => calendarChart(host, d.daily_minutes, o),
      `<span>${t("stats.calLess")}</span>${[0.12, 0.35, 0.6, 0.85, 1].map((p) => `<i style="background:${rampColor(p)}"></i>`).join("")}<span>${t("stats.calMore")}</span>`,
      t("stats.calNote"));
    add("dow", false, t("stats.dow"), (host, o) => dowChart(host, d.daily_minutes, o),
      `<span><i style="background:${css("--alert")}"></i>${t("stats.dow8")}</span><span><i style="background:${css("--ink3")}"></i>${t("stats.dowYear")}</span>`,
      t("stats.dowNote"));
  }
  add("prof", false, t("stats.prof"), (host, o) => profileChart(host, d.hour_profile_28d, o), "", t("stats.profNote"), t("stats.pro"));
  if (d.holdout?.active_h1) {
    add("ho", true, t("stats.ho"), (host, o) => holdoutChart(host, d.holdout.active_h1, o),
      `<span><i style="background:${css("--alert-soft")};border:1px solid ${css("--alert")}"></i>${t("stats.hoYes")}</span><span><i style="background:${css("--accent")}"></i>${t("stats.hoFc")}</span>`,
      reg ? t("stats.hoScore", { auc: num(reg.roc_auc, 2), brier: num(reg.brier, 3), bss: signedPct(reg.bss_vs_clim) }) : "", t("stats.pro"));
  }
  box.innerHTML = `<div class="card-h"><h2>${t("stats.title", { name: esc(oblFull(d.name)) })}</h2><span class="muted small">${t("stats.sub")}</span></div>
    <div class="chartgrid">${charts.join("")}</div>`;
  drawCharts();
}

function drawCharts() {
  for (const host of document.querySelectorAll("#stats .chart-host")) {
    const c = S.charts[host.dataset.chart];
    if (!c) continue;
    const w = Math.floor(host.clientWidth);
    if (!w || host.dataset.w === String(w)) continue;
    host.dataset.w = String(w);
    host.innerHTML = "";
    c.draw(host, { w, big: false });
  }
}

function openChart(id) {
  const c = S.charts[id];
  const dlg = $("#chart-dialog");
  if (!c || !dlg) return;
  $("#cd-title").textContent = c.title;
  $("#cd-legend").innerHTML = c.legend;
  dlg.showModal ? dlg.showModal() : dlg.setAttribute("open", "");
  const body = $("#cd-body");
  body.innerHTML = "";
  const w = Math.floor(body.clientWidth);
  c.draw(body, { w, h: Math.max(260, Math.min(520, Math.round(window.innerHeight * 0.55))), big: true });
  dlg.dataset.chart = id;
  $("#cd-close").focus();
}

// Chart tooltips: any element with data-tip inside a chart.
function setupChartTips() {
  const tip = $("#ctip");
  const show = (tg, x, y) => {
    const host = tg.closest("dialog") || document.body;  // a modal dialog is in the top layer: the tip must be inside it
    if (tip.parentNode !== host) host.appendChild(tip);
    tip.innerHTML = tg.dataset.tip;
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    const left = Math.min(window.innerWidth - r.width / 2 - 6, Math.max(r.width / 2 + 6, x));
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(r.height + 16, y)}px`;
  };
  const move = (ev) => {
    const tg = ev.target.closest?.("[data-tip]");
    if (!tg || !ev.target.closest("#stats, #chart-dialog, #history")) { tip.hidden = true; return; }
    show(tg, ev.clientX, ev.clientY);
  };
  document.addEventListener("pointermove", move);
  document.addEventListener("pointerdown", move);
  document.addEventListener("focusin", (ev) => {
    const tg = ev.target.closest?.("[data-tip]");
    if (!tg || !ev.target.closest("#stats, #chart-dialog, #history")) { tip.hidden = true; return; }
    const r = tg.getBoundingClientRect();
    show(tg, r.left + r.width / 2, r.top);
  });
  document.addEventListener("scroll", () => { tip.hidden = true; }, { passive: true });
}

function frame(host, w, h, pad, big) {
  const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, width: w, height: h, class: `chart${big ? " big" : ""}`, role: "img" }, host);
  const y = (v) => pad.t + (1 - v) * (h - pad.t - pad.b);
  return { svg, y, x0: pad.l, x1: w - pad.r, h };
}
function yAxis(f, w, pad, ticks = [0, 0.5, 1]) {
  for (const v of ticks) {
    el("line", { x1: pad.l, x2: w - pad.r, y1: f.y(v), y2: f.y(v), class: "ax", "stroke-dasharray": v ? "3 4" : "" }, f.svg);
    const tx = el("text", { x: pad.l - 6, y: f.y(v), "text-anchor": "end", "dominant-baseline": "middle" }, f.svg);
    tx.textContent = `${Math.round(v * 100)}%`;
  }
}

function stripChart(host, ver, { big }) {
  const box = document.createElement("div");
  box.className = `strip${big ? " big" : ""}`;
  box.innerHTML = ver.map((x) => {
    const tipText = `${fmtFull.format(new Date(x.origin))}: ${t("stats.stripTip", { p: pct(x.p) })}, ${x.y == null ? t("stats.stripUnknown") : x.y ? t("stats.stripYes") : t("stats.stripNo")}`;
    return `<span class="c ${x.y == null ? "yn" : "y" + x.y}" style="background:${rampColor(x.p)}" tabindex="0" role="img" aria-label="${esc(tipText)}" data-tip="${esc(tipText)}"></span>`;
  }).join("");
  host.appendChild(box);
  const ax = document.createElement("div");
  ax.className = "strip-axis";
  if (!big) ax.style.maxWidth = `${Math.max(ver.length * 38, 220)}px`;
  const mid = ver[Math.floor(ver.length / 2)];
  ax.innerHTML = `<span>${fmtFull.format(new Date(ver[0].origin))}</span>${ver.length > 6 ? `<span>${fmtTime.format(new Date(mid.origin))}</span>` : ""}<span>${fmtTime.format(new Date(ver[ver.length - 1].origin))}</span>`;
  host.appendChild(ax);
}

function histChart(host, d, { w, h, big }) {
  const hist = d.history.hourly;
  const start = new Date(d.history.start);
  const hs = Object.keys(d.probs.active).map(Number);
  const nF = Math.max(...hs);
  const n = hist.length + nF;
  h = h || (w < 500 ? 190 : 230);
  const pad = { l: 44, r: 10, t: 22, b: 30 };
  const f = frame(host, w, h, pad, big);
  const bw = (w - pad.l - pad.r) / n;
  yAxis(f, w, pad);
  const at = (i) => pad.l + i * bw;
  hist.forEach((v, i) => {
    if (v > 0) el("rect", { x: at(i), y: f.y(v), width: Math.max(bw - 1, 0.8), height: f.y(0) - f.y(v), fill: css("--alert"), opacity: 0.9, rx: Math.min(2, bw / 4) }, f.svg);
  });
  for (const hh of hs) {
    const p = d.probs.active[hh];
    const i = hist.length + hh - 1;
    el("rect", { x: at(i), y: f.y(p), width: Math.max(bw - 1, 0.8), height: f.y(0) - f.y(p), fill: rampColor(Math.max(0.35, p)), rx: Math.min(2, bw / 4),
      stroke: css("--ink"), "stroke-width": 1, "stroke-dasharray": "3 2" }, f.svg);
  }
  const xNow = at(hist.length);
  el("line", { x1: xNow, x2: xNow, y1: pad.t - 10, y2: f.y(0), stroke: css("--ink2"), "stroke-dasharray": "4 3" }, f.svg);
  const tn = el("text", { x: xNow - 4, y: pad.t - 10, "text-anchor": "end", "dominant-baseline": "middle", class: "strong" }, f.svg);
  tn.textContent = t("stats.now");
  const tf = el("text", { x: xNow + 4, y: pad.t - 10, "dominant-baseline": "middle", class: "strong" }, f.svg);
  tf.textContent = t("stats.fc");
  if (tf.getComputedTextLength && xNow + 4 + tf.getComputedTextLength() > w) { tf.setAttribute("x", w - 1); tf.setAttribute("text-anchor", "end"); tf.setAttribute("y", pad.t - 24 > 0 ? pad.t - 24 : 2); }
  // x axis: days at Kyiv midnight, hours 06/12/18 when there is room
  for (let i = 0; i <= hist.length; i++) {
    const tm = new Date(start.getTime() + i * 3600e3);
    const hr = kyivHour(tm);
    if (hr === 0 && i < hist.length) {
      el("line", { x1: at(i), x2: at(i), y1: f.y(0), y2: f.y(0) + 6, class: "ax" }, f.svg);
      const tx = el("text", { x: at(i) + 3, y: h - 8, class: "strong" }, f.svg);
      tx.textContent = fmtDay.format(tm);
    } else if (hr % 6 === 0 && bw * 6 >= 30 && i < hist.length - 1) {
      const tx = el("text", { x: at(i), y: f.y(0) + 13, "text-anchor": "middle", class: "minor" }, f.svg);
      tx.textContent = String(hr).padStart(2, "0");
    }
  }
  // hover columns with tooltips
  const g = el("g", { class: "hits" }, f.svg);
  for (let i = 0; i < n; i++) {
    let tip = null;
    if (i < hist.length) {
      const a = new Date(start.getTime() + i * 3600e3);
      tip = t("tip.hist", { from: fmtFull.format(a), to: fmtTime.format(new Date(a.getTime() + 3600e3)), p: pct(hist[i]) });
    } else {
      const hh = i - hist.length + 1;
      if (!hs.includes(hh)) continue;
      const wnd = d.windows?.[hh]?.active;
      tip = t("tip.histFc", { from: wnd ? fmtTime.format(new Date(wnd[0])) : "", to: wnd ? fmtTime.format(new Date(wnd[1])) : "", p: pct(d.probs.active[hh]), h: hh });
    }
    el("rect", { x: at(i), y: pad.t, width: bw, height: f.y(0) - pad.t, "data-tip": tip }, g);
  }
}

function calendarChart(host, daily, { w, big }) {
  const vals = daily.values;
  const sorted = vals.filter((v) => v > 0).sort((a, b) => a - b);
  const top = sorted.length ? sorted[Math.floor(sorted.length * 0.9)] || sorted[sorted.length - 1] : 1;
  const start = new Date(daily.start + "T12:00:00Z");
  const dow0 = (start.getUTCDay() + 6) % 7; // Monday = 0
  const weeks = Math.ceil((vals.length + dow0) / 7);
  const padL = big ? 34 : 30, padT = 18, gap = big ? 3 : 2, bandGap = 14;
  const minCell = big ? 14 : 11, maxCell = big ? 26 : 20;
  const bands = Math.max(1, Math.ceil(weeks * (minCell + gap) / (w - padL)));
  const perBand = Math.ceil(weeks / bands);
  const cell = Math.max(4, Math.min(maxCell, (w - padL) / perBand - gap));
  const bandH = padT + 7 * (cell + gap);
  const H = bands * bandH + (bands - 1) * bandGap;
  const svg = el("svg", { viewBox: `0 0 ${w} ${H}`, width: w, height: H, class: `chart${big ? " big" : ""}`, role: "img", "aria-label": t("stats.cal") }, host);
  const names = dowNames();
  for (let b = 0; b < bands; b++) {
    const oy = b * (bandH + bandGap);
    [0, 2, 4, 6].forEach((i) => {
      const tx = el("text", { x: 0, y: oy + padT + i * (cell + gap) + cell / 2, "dominant-baseline": "middle" }, svg);
      tx.textContent = names[i];
    });
  }
  let lastMonth = -1;
  vals.forEach((m, i) => {
    const k = i + dow0, wk = Math.floor(k / 7), dd = k % 7;
    const b = Math.floor(wk / perBand), col = wk % perBand;
    const day = new Date(start.getTime() + i * 864e5);
    const x = padL + col * (cell + gap), y = b * (bandH + bandGap) + padT + dd * (cell + gap);
    const tip = `${fmtDay.format(day)}: ${m ? t("tip.cal", { d: t("dur.hm", { h: Math.floor(m / 60), m: m % 60 }) }) : t("tip.calNone")}`;
    el("rect", { x, y, width: cell, height: cell, rx: Math.min(3, cell / 4),
      fill: m ? rampColor(Math.min(1, 0.12 + 0.88 * m / top)) : css("--surface2"), "data-tip": tip }, svg);
    if (dd === 0 && day.getUTCMonth() !== lastMonth) {
      lastMonth = day.getUTCMonth();
      const tx = el("text", { x, y: b * (bandH + bandGap) + padT - 6 }, svg);
      tx.textContent = fmtMonth.format(day);
    }
  });
}

function dowChart(host, daily, { w, h, big }) {
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
  h = h || 200;
  const pad = { l: 44, r: 8, t: 10, b: 28 };
  const f = frame(host, w, h, pad, big);
  yAxis(f, w, pad);
  const gw = (w - pad.l - pad.r) / 7;
  dowNames().forEach((l, i) => {
    const x0 = pad.l + i * gw;
    el("rect", { x: x0 + gw * 0.16, y: f.y(recent[i]), width: gw * 0.32, height: f.y(0) - f.y(recent[i]), fill: css("--alert"), rx: 2 }, f.svg);
    el("rect", { x: x0 + gw * 0.52, y: f.y(year[i]), width: gw * 0.32, height: f.y(0) - f.y(year[i]), fill: css("--ink3"), rx: 2 }, f.svg);
    const tx = el("text", { x: x0 + gw / 2, y: h - 9, "text-anchor": "middle", class: "strong" }, f.svg);
    tx.textContent = l;
    el("rect", { x: x0, y: pad.t, width: gw, height: f.y(0) - pad.t, class: "hit", "data-tip": t("tip.dow", { day: l, a: pct(recent[i]), b: pct(year[i]) }) }, f.svg);
  });
}

function profileChart(host, prof, { w, h, big }) {
  h = h || 200;
  const pad = { l: 44, r: 8, t: 10, b: 28 };
  const f = frame(host, w, h, pad, big);
  yAxis(f, w, pad);
  const bw = (w - pad.l - pad.r) / 24;
  const nowH = kyivHour(new Date());
  prof.forEach((v, i) => {
    el("rect", { x: pad.l + i * bw + 1, y: f.y(v), width: Math.max(1, bw - 2), height: Math.max(0, f.y(0) - f.y(v)), rx: 2,
      fill: i === nowH ? css("--ink") : rampColor(Math.max(0.2, v)) }, f.svg);
    el("rect", { x: pad.l + i * bw, y: pad.t, width: bw, height: f.y(0) - pad.t, class: "hit",
      "data-tip": t("tip.prof", { h: `${String(i).padStart(2, "0")}:00–${String((i + 1) % 24).padStart(2, "0")}:00`, p: pct(v) }) + (i === nowH ? ` (${t("stats.nowHour")})` : "") }, f.svg);
  });
  const step = bw >= 22 ? 1 : bw >= 12 ? 2 : 3;
  for (let i = 0; i < 24; i += step) {
    const tx = el("text", { x: pad.l + i * bw + bw / 2, y: h - 9, "text-anchor": "middle", class: i === nowH ? "strong" : "" }, f.svg);
    tx.textContent = String(i);
  }
}

function holdoutChart(host, ho, { w, h, big }) {
  const n = ho.p.length;
  const start = new Date(ho.start);
  h = h || 220;
  const pad = { l: 44, r: 8, t: 10, b: 30 };
  const f = frame(host, w, h, pad, big);
  const bw = (w - pad.l - pad.r) / n;
  ho.y.forEach((v, i) => {
    if (v) el("rect", { x: pad.l + i * bw, y: f.y(1), width: bw + 0.4, height: f.y(0) - f.y(1), fill: css("--alert-soft") }, f.svg);
  });
  yAxis(f, w, pad);
  const pts = ho.p.map((p, i) => `${(pad.l + (i + 0.5) * bw).toFixed(1)},${f.y(p).toFixed(1)}`).join(" ");
  el("polyline", { points: pts, fill: "none", stroke: css("--accent"), "stroke-width": big ? 2 : 1.6, "stroke-linejoin": "round" }, f.svg);
  const perDay = (w - pad.l - pad.r) / (n / 24);
  let day = 0;
  for (let i = 0; i < n; i++) {
    const tm = new Date(start.getTime() + i * 3600e3);
    if (kyivHour(tm) === 0) {
      el("line", { x1: pad.l + i * bw, x2: pad.l + i * bw, y1: f.y(0), y2: f.y(0) + 6, class: "ax" }, f.svg);
      if (perDay > 50 || (perDay > 25 && day % 2 === 0)) {
        const tx = el("text", { x: pad.l + i * bw + 3, y: h - 9, class: "strong" }, f.svg);
        tx.textContent = fmtDay.format(tm);
      }
      day += 1;
    }
  }
  const g = el("g", { class: "hits" }, f.svg);
  for (let i = 0; i < n; i++) {
    const tm = new Date(start.getTime() + i * 3600e3);
    el("rect", { x: pad.l + i * bw, y: pad.t, width: bw, height: f.y(0) - pad.t,
      "data-tip": t("tip.ho", { time: fmtFull.format(tm), p: pct(ho.p[i]), y: ho.y[i] ? t("stats.stripYes") : t("stats.stripNo") }) }, g);
  }
}

function lineChart(series, { W = 420, Hh = 200, xs, yMin, yMax, yFmt = (v) => v, xFmt = (v) => v, diag = false, xLabel = "", yLabel = "" }) {
  const pad = { l: 46, r: 10, t: 10, b: 36 };
  const svg = el("svg", { viewBox: `0 0 ${W} ${Hh}`, class: "chart scaled" });
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const x = (v) => pad.l + ((v - xMin) / (xMax - xMin || 1)) * (W - pad.l - pad.r);
  const y = (v) => pad.t + (1 - (v - yMin) / (yMax - yMin || 1)) * (Hh - pad.t - pad.b);
  const step = [0.05, 0.1, 0.2, 0.25, 0.5, 1].find((st) => (yMax - yMin) / st <= 6) || 1;
  for (let v = Math.ceil(yMin / step - 1e-9) * step; v <= yMax + 1e-9; v += step) {
    el("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), class: "ax" }, svg);
    const tx = el("text", { x: pad.l - 5, y: y(v) + 4, "text-anchor": "end" }, svg);
    tx.textContent = yFmt(v);
  }
  for (const v of xs) {
    const tx = el("text", { x: x(v), y: Hh - pad.b + 16, "text-anchor": "middle" }, svg);
    tx.textContent = xFmt(v);
  }
  if (xLabel) { const tx = el("text", { x: (W + pad.l) / 2, y: Hh - 3, "text-anchor": "middle" }, svg); tx.textContent = xLabel; }
  if (yLabel) { const tx = el("text", { x: 11, y: Hh / 2, "text-anchor": "middle", transform: `rotate(-90 11 ${Hh / 2})` }, svg); tx.textContent = yLabel; }
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
const modelName = (m) => (window.I18N?.en?.[`model.${m}`] ? t(`model.${m}`) : m);
const targetName = (tg, h) => t(`target.${tg}`, { h: h ?? "N" });

async function renderMetrics() {
  const box = $("#metrics");
  if (!S.metrics) {
    try { S.metrics = await getJSON("/api/metrics"); } catch (e) { box.innerHTML = `<p class="muted">${t("m.notReady")}</p>`; return; }
  }
  const m = S.metrics, ho = m.holdout;
  if (!ho) { box.innerHTML = `<p class="muted">${t("m.notReady")}</p>`; return; }
  const sc = ho.scores;
  const get = (tg, h, model) => sc.find((r) => r.target === tg && r.horizon === h && r.model === model);
  const hs = [...new Set(sc.map((r) => r.horizon))].sort((a, b) => a - b);
  const tile = (tg, h) => {
    const r = get(tg, h, "lightgbm");
    if (!r) return "";
    return `<div class="stat"><div class="k">${targetName(tg, h)}</div>
      <div class="v ${r.bss_vs_clim > 0 ? "pos" : "neg"}">${signedPct(r.bss_vs_clim)}</div>
      <div class="s">${t("m.tileSub", { auc: num(r.roc_auc, 2) })}</div></div>`;
  };
  const table = (tg) => {
    const models = [...new Set(sc.filter((r) => r.target === tg).map((r) => r.model))];
    const row = (model, metric, d, fmt = (v) => num(v, d)) =>
      `<tr class="${model === "lightgbm" ? "best" : ""}"><td>${modelName(model)}</td>${hs.map((h) => `<td>${fmt(get(tg, h, model)?.[metric])}</td>`).join("")}</tr>`;
    return `<div class="card"><div class="card-h"><h2>${targetName(tg)}</h2><span class="small muted">${t("m.baseRate")}: ${hs.map((h) => `${t("h.n", { n: h })} ${pct(get(tg, h, "lightgbm")?.base_rate)}`).join(", ")}</span></div>
      <div class="tablewrap"><table class="mt"><thead><tr><th>${t("m.brier")}</th>${hs.map((h) => `<th>${t("h.n", { n: h })}</th>`).join("")}</tr></thead>
      <tbody>${models.map((mm) => row(mm, "brier", 4)).join("")}</tbody></table>
      <table class="mt" style="margin-top:12px"><thead><tr><th>${t("m.auc")}</th>${hs.map((h) => `<th>${t("h.n", { n: h })}</th>`).join("")}</tr></thead>
      <tbody>${models.map((mm) => row(mm, "roc_auc", 3)).join("")}</tbody></table></div></div>`;
  };

  const planList = Object.entries(ho.plans || {});
  const same = planList.every(([, p]) => p.months === planList[0]?.[1].months && p.trees === planList[0]?.[1].trees);
  const plans = same && planList.length
    ? t("m.planSame", { months: planList[0][1].months, trees: planList[0][1].trees })
    : planList.map(([k, p]) => t("m.plan", { what: k.startsWith("active") ? t("m.planActive", { h: k.replace("active_h", "") }) : t("m.planOnset", { h: k.replace("onset_h", "") }), months: p.months, trees: p.trees })).join("; ");
  // hold-out test only; the live check of issued forecasts is the History tab (renderHistory)
  const range = ho.start ? `${fmtFull.format(new Date(ho.start))} – ${fmtFull.format(new Date(ho.end))}` : "";
  let html = `<div class="card-h"><h2 style="margin:0">${t("m.title")} ${infoBtn("ex-m-pct")}</h2>
      <a class="small" href="#history">${t("m.toHistory")}</a></div>
    ${explainBox("ex-m-pct", t("m.pctNote"))}
    ${ho.start ? "" : `<p class="small muted">${t("m.noHoldout")}</p>`}
    <p class="pnote">${t("m.hoLine", { days: ho.days, range })} ${infoBtn("ex-m-ho")}</p>
    ${explainBox("ex-m-ho", t("m.hoLead", { days: ho.days, from: ho.start ? fmtFull.format(new Date(ho.start)) : "–", to: ho.end ? fmtFull.format(new Date(ho.end)) : "–" }))}
    <div class="summary">${tile("active", 1)}${tile("onset", 1)}${tile("onset", 3)}${tile("onset", 6)}</div>
    ${table("active")}${table("onset")}`;

  html += `<div class="two"><div class="card"><div class="card-h"><h2>${t("m.bssTitle")} ${infoBtn("ex-m-bss")}</h2></div>
      ${explainBox("ex-m-bss", t("m.bssNote"))}<div id="ch-bss"></div>
      <div class="chart-legend"><span><i style="background:${css("--alert")}"></i>${targetName("active")}</span><span><i style="background:${css("--accent")}"></i>${targetName("onset")}</span></div></div>
    <div class="card"><div class="card-h"><h2>${t("m.relTitle")} ${infoBtn("ex-m-rel")}</h2></div>
      ${explainBox("ex-m-rel", t("m.relNote"))}<div id="ch-rel"></div>
      <div class="chart-legend"><span><i style="background:${css("--alert")}"></i>${t("m.relActive")}</span><span><i style="background:${css("--accent")}"></i>${t("m.relOnset")}</span></div></div></div>`;

  if (ho.by_region) {
    const rows = ho.by_region.filter((r) => r.target === "onset" && r.horizon === 3).sort((a, b) => (b.roc_auc ?? 0) - (a.roc_auc ?? 0));
    html += `<div class="card"><div class="card-h"><h2>${t("m.byRegion")}</h2><span class="small muted">${t("m.hoShort")}</span></div>
      <div class="tablewrap"><table class="mt"><thead><tr><th>${t("rank.oblast")}</th><th>${t("m.rate")}</th><th>Brier</th><th>${t("m.gain")}</th><th>ROC AUC</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${esc(oblShort(r.region))}</td><td>${pct(r.base_rate)}</td><td>${num(r.brier, 3)}</td><td class="${r.bss_vs_clim > 0 ? "pos" : "neg"}">${r.base_rate < 0.02 ? "–" : signedPct(r.bss_vs_clim)}</td><td>${num(r.roc_auc, 2)}</td></tr>`).join("")}
      </tbody></table></div></div>`;
  }

  if (m.walk_forward?.scores?.length) {
    const wf = m.walk_forward.scores;
    const whs = [...new Set(wf.map((r) => r.horizon_h))].sort((a, b) => a - b);
    const block = (tg) => {
      const models = [...new Set(wf.filter((r) => r.target === tg).map((r) => r.model))];
      return `<table class="mt" style="margin-top:8px"><thead><tr><th>${t("m.wfHead", { target: targetName(tg) })}</th>${whs.map((h) => `<th>${t("h.n", { n: h })}</th>`).join("")}</tr></thead><tbody>
        ${models.map((mm) => `<tr class="${mm === "lightgbm" ? "best" : ""}"><td>${modelName(mm)}</td>${whs.map((h) => {
          const v = wf.find((r) => r.target === tg && r.model === mm && r.horizon_h === h)?.bss_vs_clim;
          return `<td class="${v > 0 ? "pos" : "neg"}">${signedPct(v)}</td>`;
        }).join("")}</tr>`).join("")}</tbody></table>`;
    };
    const per = m.walk_forward.test ? `${fmtFull.format(new Date(m.walk_forward.test[0]))} – ${fmtFull.format(new Date(m.walk_forward.test[1]))}` : "";
    html += `<div class="card"><div class="card-h"><h2>${t("m.wfTitle")} ${infoBtn("ex-m-wf")}</h2><span class="small muted">${per}</span></div>
      ${explainBox("ex-m-wf", t("m.wfNote"))}
      <div class="tablewrap">${[...new Set(wf.map((r) => r.target))].map(block).join("")}</div></div>`;
  }
  html += `<p class="small muted">${t("m.trained", { time: ho.trained_at ? fmtFull.format(new Date(ho.trained_at)) : "–", how: ho.selection_source === "defaults" ? t("m.defaults") : t("m.selected"), plans: esc(plans) })}</p>`;
  box.innerHTML = html;

  const series = ["active", "onset"].map((tg, i) => ({
    color: i ? css("--accent") : css("--alert"),
    points: hs.map((h) => [h, get(tg, h, "lightgbm")?.bss_vs_clim]),
  }));
  const allV = series.flatMap((s) => s.points.map((p) => p[1])).filter((v) => v != null);
  $("#ch-bss").appendChild(lineChart(series, {
    xs: hs, yMin: Math.min(0, Math.floor(Math.min(...allV) * 10) / 10), yMax: Math.max(0.1, Math.ceil(Math.max(...allV) * 10) / 10),
    yFmt: (v) => `${Math.round(v * 100)}%`, xFmt: (v) => t("h.n", { n: v }), xLabel: t("m.horizon"),
  }));
  const rel = ho.reliability || {};
  const relSeries = [["active_h1", css("--alert")], ["onset_h3", css("--accent")]]
    .filter(([k]) => rel[k]).map(([k, color]) => ({ color, points: rel[k].map((r) => [r[0], r[1]]) }));
  $("#ch-rel").appendChild(lineChart(relSeries, {
    xs: [0, 0.2, 0.4, 0.6, 0.8, 1], yMin: 0, yMax: 1, diag: true,
    yFmt: (v) => `${Math.round(v * 100)}%`, xFmt: (v) => `${Math.round(v * 100)}%`, xLabel: t("m.relX"), yLabel: t("m.relY"),
  }));
}

// ---------------------------------------------------------------- history view: issued forecasts vs what happened
// /api/history (scores, skill over time, reliability, the learning layer) and /api/history/timeline
// (per oblast, the forecast issued each hour and the outcome) are loaded only when the tab opens.
const explainBox = (id, html) => `<div class="explain" id="${id}"${OPEN.has(id) ? "" : " hidden"}><p>${html}</p></div>`;
const HKEYS = ["active_h1", "active_h2", "active_h3", "active_h6", "onset_h1", "onset_h2", "onset_h3", "onset_h6"];
const HV = { data: null, tl: null, key: "active_h1", region: null, hours: 72, loading: null, anyCal: false };
const hkName = (k) => { const [tg, h] = k.split("_h"); return targetName(tg, Number(h)); };
const hkSplit = (k) => { const [tg, h] = k.split("_h"); return [tg, Number(h)]; };

async function loadHistory() {
  if (HV.data) return true;
  if (!HV.loading) {
    HV.loading = Promise.all([getJSON("/api/history"), getJSON("/api/history/timeline").catch(() => null)])
      .then(([d, tl]) => { HV.data = d; HV.tl = tl; return true; })
      .catch(() => false)
      .finally(() => { HV.loading = null; });
  }
  return HV.loading;
}

function historyRow(win, k) {
  const [tg, h] = hkSplit(k);
  return (HV.data?.windows?.[win] || []).find((r) => r.target === tg && r.horizon === h) || null;
}

function learnTile(cal) {
  const on = cal?.n_active || 0, total = cal?.n_total || 8;
  const hours = Math.max(0, ...(cal?.groups || []).map((g) => g.n_hours || 0));
  const need = (cal?.min_fit_hours || 24) + (cal?.check_hours || 24);
  return `<div class="stat"><div class="k">${t("hi.learn")} ${infoBtn("ex-h-learn")}</div>
    <div class="v">${on ? `${on}<small> / ${total}</small>` : `<small>${t("hi.learnOff")}</small>`}</div>
    <div class="s">${on ? t("hi.learnOn", { n: on, total }) : t("hi.learnWait", { h: hours, need })}</div></div>`;
}

function skillTile(k) {
  const d24 = historyRow("24h", k), d7 = historyRow("7d", k);
  if (!d24 && !d7) return "";
  const r = d24 || d7;
  const v = r.bss_vs_clim;
  return `<div class="stat"><div class="k">${hkName(k)}</div>
    <div class="v ${v > 0 ? "pos" : "neg"}">${signedPct(v)}</div>
    <div class="s">${t("hi.tileSub", { win: d24 ? t("hi.w24") : t("hi.w7"), d7: signedPct(d7?.bss_vs_clim), pers: signedPct(r.bss_vs_pers) })}</div></div>`;
}

async function renderHistory() {
  const box = $("#history");
  if (!HV.data) {
    if (!box.innerHTML) box.innerHTML = `<p class="muted">${t("hi.loading")}</p>`;
    const ok = await loadHistory();
    if (!ok || !HV.data) { box.innerHTML = `<p class="muted">${t("hi.unavailable")}</p>`; return; }
  }
  const d = HV.data;
  const regions = HV.tl?.regions || [];
  if (!HV.region || !regions.includes(HV.region)) {
    const sel = S.forecast?.regions?.find((r) => r.id === S.selected)?.name;
    HV.region = regions.includes(sel) ? sel : (regions.includes("Kyiv City") ? "Kyiv City" : regions[0] || null);
  }
  const known = d.known_until ? fmtFull.format(new Date(d.known_until)) : "–";
  const head = `<div class="card-h"><h2 style="margin:0">${t("hi.title")} ${infoBtn("ex-h-what")}</h2>
      <span class="small muted">${t("hi.sub", { time: known })}</span></div>
    ${explainBox("ex-h-what", t("hi.what"))}`;
  if (!d.n_verified) {
    box.innerHTML = `${head}<p>${t("hi.none", { n: d.n_runs || 0 })}</p><div class="summary">${learnTile(d.calibration)}</div>${explainBox("ex-h-learn", t("hi.learnWhat"))}`;
    return;
  }
  if (!d.blocks?.series?.[HV.key]) HV.key = HKEYS.find((k) => d.blocks?.series?.[k]) || HV.key;
  const opts = HKEYS.filter((k) => d.blocks?.series?.[k] || HV.tl?.series?.[k])
    .map((k) => `<option value="${k}"${k === HV.key ? " selected" : ""}>${esc(hkName(k))}</option>`).join("");
  const regOpts = regions.slice().sort((a, b) => oblShort(a).localeCompare(oblShort(b), LANG))
    .map((r) => `<option value="${esc(r)}"${r === HV.region ? " selected" : ""}>${esc(oblShort(r))}</option>`).join("");
  const anyCal = Object.values(d.blocks?.series || {}).some((s) => s.brier.some((v, i) => v != null && s.brier_raw[i] != null && Math.abs(v - s.brier_raw[i]) > 1e-6));
  HV.anyCal = anyCal;
  box.innerHTML = `${head}
    <div class="summary hsum">
      <div class="stat"><div class="k">${t("hi.checked")}</div><div class="v">${d.n_verified.toLocaleString(LANG === "uk" ? "uk-UA" : "en-GB")}</div>
        <div class="s">${t("hi.checkedSub", { runs: d.n_runs_verified, wait: d.n_pending.toLocaleString(LANG === "uk" ? "uk-UA" : "en-GB") })}</div></div>
      ${skillTile("active_h1")}${skillTile("onset_h3")}${learnTile(d.calibration)}
    </div>
    ${explainBox("ex-h-learn", t("hi.learnWhat"))}
    <div class="controls hctl">
      <label class="zsel"><span class="small muted">${t("hi.type")}</span> <select id="h-key">${opts}</select></label>
    </div>
    <div class="two">
      <div class="card"><div class="card-h"><h2>${t("hi.skill")} ${infoBtn("ex-h-skill")}</h2></div>
        ${explainBox("ex-h-skill", t("hi.skillWhat"))}
        <div class="chart-host" data-hchart="skill"></div>
        <div class="chart-legend"><span><i style="background:${css("--accent")}"></i>${t("hi.served")}</span>${anyCal ? `<span><i class="dash" style="border-color:${css("--ink2")}"></i>${t("hi.raw")}</span>` : ""}<span><i style="background:${css("--ink3")}"></i>${t("hi.pers")}</span></div></div>
      <div class="card"><div class="card-h"><h2>${t("hi.rel")} ${infoBtn("ex-h-rel")}</h2><span class="small muted">${t("hi.w7")}</span></div>
        ${explainBox("ex-h-rel", t("hi.relWhat"))}
        <div class="chart-host" data-hchart="rel"></div>
        <div class="chart-legend"><span><i style="background:${css("--accent")}"></i>${t("hi.served")}</span>${d.reliability?.[HV.key]?.raw ? `<span><i class="dash" style="border-color:${css("--ink2")}"></i>${t("hi.raw")}</span>` : ""}</div></div>
    </div>
    <div class="card">
      <div class="card-h"><h2>${t("hi.tl")} ${infoBtn("ex-h-tl")}</h2>
        <div class="hctl2">
          <label class="zsel"><span class="sr">${t("hi.oblast")}</span><select id="h-region">${regOpts}</select></label>
          <div class="seg" role="group" aria-label="${esc(t("hi.range"))}" id="h-range">
            <button type="button" data-v="72" aria-pressed="${HV.hours === 72}">${t("hi.h72")}</button>
            <button type="button" data-v="168" aria-pressed="${HV.hours === 168}">${t("hi.d7")}</button></div>
        </div></div>
      ${explainBox("ex-h-tl", t("hi.tlWhat"))}
      <div class="chart-host" data-hchart="tl"></div>
      <div class="chart-legend"><span><i style="background:${rampColor(0.6)}"></i>${t("hi.tlP")}</span><span><i style="background:${css("--alert-soft")};border:1px solid ${css("--alert")}"></i>${t("stats.stripYes")}</span><span><i class="pend"></i>${t("stats.stripUnknown")}</span></div>
      <h3 class="hsub">${t("hi.recent")}</h3>
      <div class="tablewrap" id="h-recent"></div>
    </div>
    <details class="more"><summary>${t("hi.all")}</summary>${historyTables(d)}</details>`;
  $("#h-key").addEventListener("change", (ev) => { HV.key = ev.target.value; renderHistory(); });
  $("#h-region").addEventListener("change", (ev) => { HV.region = ev.target.value; drawHistory(true); renderRecent(); });
  for (const b of document.querySelectorAll("#h-range button")) {
    b.addEventListener("click", () => {
      HV.hours = Number(b.dataset.v);
      for (const x of document.querySelectorAll("#h-range button")) x.setAttribute("aria-pressed", String(x === b));
      drawHistory(true);
    });
  }
  drawHistory(true);
  renderRecent();
}

function historyTables(d) {
  const keys = HKEYS.filter((k) => historyRow("7d", k) || historyRow("24h", k));
  const f = (v) => num(v, 3);
  const rows = keys.map((k) => {
    const a = historyRow("24h", k), b = historyRow("7d", k);
    const cell = (r) => (r ? `<td>${r.n}</td><td>${f(r.brier)}</td>${HV.anyCal ? `<td>${f(r.brier_raw)}</td>` : ""}<td>${f(r.brier_clim)}</td><td>${f(r.brier_pers)}</td>` : `<td colspan="${HV.anyCal ? 5 : 4}">–</td>`);
    return `<tr><td>${esc(hkName(k))}</td>${cell(a)}${cell(b)}</tr>`;
  }).join("");
  const sub = `<th>n</th><th>Brier</th>${HV.anyCal ? `<th>${t("hi.colRaw")}</th>` : ""}<th>${t("hi.colClim")}</th><th>${t("hi.colPers")}</th>`;
  const span = HV.anyCal ? 5 : 4;
  const scores = `<div class="tablewrap"><table class="mt"><thead><tr><th></th><th colspan="${span}">${t("hi.w24")}</th><th colspan="${span}">${t("hi.w7")}</th></tr>
    <tr><th></th>${sub}${sub}</tr></thead><tbody>${rows}</tbody></table></div>`;
  const cal = d.calibration || {};
  const groups = cal.groups || [];
  const calRows = groups.map((g) => `<tr><td>${esc(targetName(g.target, g.horizon))}</td>
    <td class="${g.active ? "pos" : ""}">${t(`hi.state.${g.reason || "collecting"}`)}</td><td>${g.n_hours ?? "–"}</td>
    <td>${f(g.brier_raw)}</td><td>${f(g.brier_cal)}</td><td>${g.gain == null ? "–" : signedPct(g.gain)}</td></tr>`).join("");
  const calTable = groups.length ? `<h3 class="hsub">${t("hi.calTitle")}</h3>
    <div class="tablewrap"><table class="mt"><thead><tr><th></th><th>${t("hi.colState")}</th><th>${t("hi.colHours")}</th><th>${t("hi.colBrierRaw")}</th><th>${t("hi.colBrierCal")}</th><th>${t("hi.colGain")}</th></tr></thead>
    <tbody>${calRows}</tbody></table></div>
    <p class="small muted">${t("hi.calFitted", { time: cal.fitted_at ? fmtFull.format(new Date(cal.fitted_at)) : "–" })}</p>` : "";
  const src = Object.entries(d.label_sources || {}).map(([k, n]) => `${t(`hi.src.${k === "dataset" ? "dataset" : "store"}`)}: ${n.toLocaleString()}`).join(" · ");
  return `${scores}${calTable}<p class="small muted">${t("hi.labels", { list: src || "–" })}</p>`;
}

// the charts are drawn at the pixel width of their box (redrawn on resize)
function drawHistory(force = false) {
  if (!HV.data || S.view !== "history") return;
  for (const host of document.querySelectorAll("#history .chart-host[data-hchart]")) {
    const w = Math.floor(host.clientWidth);
    const sig = `${w}|${HV.key}|${HV.region}|${HV.hours}`;
    if (!w || (!force && host.dataset.sig === sig)) continue;
    host.dataset.sig = sig;
    host.innerHTML = "";
    if (host.dataset.hchart === "skill") skillChart(host, HV.data.blocks, HV.key, { w });
    else if (host.dataset.hchart === "rel") relChart(host, { w });
    else timelineChart(host, { w });
  }
}

function renderRecent() {
  const box = $("#h-recent");
  if (!box) return;
  const tl = HV.tl, s = tl?.series?.[HV.key];
  const i = tl?.regions?.indexOf(HV.region) ?? -1;
  if (!s || i < 0) { box.innerHTML = ""; return; }
  const [tg, h] = hkSplit(HV.key);
  const start = new Date(tl.start).getTime();
  const rows = [];
  for (let k = tl.n_hours - 1; k >= 0 && rows.length < 10; k--) {
    const y = s.y[i][k], p = s.p[i][k];
    if (y == null || p == null) continue;
    const o = new Date(start + k * 3600e3 + (s.minute[k] || 0) * 60e3);
    rows.push(`<tr><td>${fmtFull.format(o)}</td><td class="c-obl">${esc(oblShort(HV.region))}</td><td class="c-what">${esc(targetName(tg, h))}</td>
      <td>${probCell(p)}</td><td class="${y ? "yes" : ""}">${y ? t("hi.yes") : t("hi.no")}</td></tr>`);
  }
  box.innerHTML = rows.length ? `<table class="mt hrecent"><thead><tr><th>${t("hi.colTime")}</th><th class="c-obl">${t("rank.oblast")}</th><th class="c-what">${t("hi.colWhat")}</th><th>${t("hi.colP")}</th><th>${t("hi.colY")}</th></tr></thead>
    <tbody>${rows.join("")}</tbody></table>` : `<p class="small muted">${t("stats.stripWait")}</p>`;
}

function relChart(host, { w }) {
  const rel = HV.data?.reliability?.[HV.key] || {};
  const series = [["served", css("--accent"), ""], ["raw", css("--ink2"), "5 4"]]
    .filter(([k]) => rel[k]?.length).map(([k, color, dash]) => ({ color, dash, points: rel[k].map((r) => [r[0], r[1]]) }));
  host.appendChild(lineChart(series, {
    W: w, Hh: Math.round(Math.min(260, Math.max(200, w * 0.5))), xs: [0, 0.2, 0.4, 0.6, 0.8, 1], yMin: 0, yMax: 1, diag: true,
    yFmt: (v) => `${Math.round(v * 100)}%`, xFmt: (v) => `${Math.round(v * 100)}%`, xLabel: t("m.relX"), yLabel: t("m.relY"),
  }));
}

function skillChart(host, blocks, key, { w, h }) {
  const s = blocks?.series?.[key];
  if (!s) return;
  const n = blocks.start.length;
  const sk = (arr) => arr.map((b, i) => (b == null || !s.brier_clim[i] ? null : 1 - b / s.brier_clim[i]));
  const served = sk(s.brier), raw = sk(s.brier_raw), pers = sk(s.brier_pers);
  const all = [...served, ...(HV.anyCal ? raw : []), ...pers].filter((v) => v != null);
  const lo = Math.max(-1, Math.min(-0.2, Math.floor(Math.min(...all) * 5) / 5));
  const hi = Math.min(1, Math.max(0.2, Math.ceil(Math.max(...all) * 5) / 5));
  h = h || (w < 500 ? 190 : 220);
  const pad = { l: 44, r: 10, t: 10, b: 30 };
  const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, width: w, height: h, class: "chart", role: "img", "aria-label": t("hi.skill") }, host);
  const y = (v) => pad.t + (hi - v) / (hi - lo) * (h - pad.t - pad.b);
  const bw = (w - pad.l - pad.r) / Math.max(1, n);
  const x = (i) => pad.l + (i + 0.5) * bw;
  const step = (hi - lo) / 0.2 > 6 ? 0.5 : 0.2;
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + 1e-9; v += step) {
    el("line", { x1: pad.l, x2: w - pad.r, y1: y(v), y2: y(v), class: "ax", "stroke-dasharray": Math.abs(v) < 1e-9 ? "" : "3 4" }, svg);
    const tx = el("text", { x: pad.l - 6, y: y(v), "text-anchor": "end", "dominant-baseline": "middle" }, svg);
    tx.textContent = `${v > 1e-9 ? "+" : ""}${Math.round(v * 100)}%`;
  }
  el("line", { x1: pad.l, x2: w - pad.r, y1: y(0), y2: y(0), stroke: css("--ink3") }, svg);
  const line = (vals, color, dash, width, dots) => {
    let seg = [];
    const flush = () => { if (seg.length > 1) el("polyline", { points: seg.join(" "), fill: "none", stroke: color, "stroke-width": width, "stroke-dasharray": dash, "stroke-linejoin": "round" }, svg); seg = []; };
    vals.forEach((v, i) => { if (v == null) flush(); else seg.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`); });
    flush();
    if (dots) vals.forEach((v, i) => { if (v != null) el("circle", { cx: x(i), cy: y(v), r: n > 20 ? 2.5 : 3.5, fill: color }, svg); });
  };
  line(pers, css("--ink3"), "", 1.4, false);
  if (HV.anyCal) line(raw, css("--ink2"), "5 4", 1.6, false);
  line(served, css("--accent"), "", 2.2, true);
  // x axis: Kyiv dates at the first block of each day
  let last = "";
  blocks.start.forEach((iso, i) => {
    const d = new Date(iso), day = fmtDay.format(d);
    if (day !== last) {
      last = day;
      if (kyivHour(d) < 6 || i === 0) {
        el("line", { x1: pad.l + i * bw, x2: pad.l + i * bw, y1: h - pad.b, y2: h - pad.b + 5, class: "ax" }, svg);
        if (bw * 4 >= 44 || i % 8 === 0) { const tx = el("text", { x: pad.l + i * bw + 2, y: h - 9, class: "strong" }, svg); tx.textContent = day; }
      }
    }
  });
  const g = el("g", { class: "hits" }, svg);
  blocks.start.forEach((iso, i) => {
    if (s.n[i] == null) return;
    const a = new Date(iso), b = new Date(a.getTime() + (blocks.hours || 6) * 3600e3);
    const tip = t("hi.skillTip", { from: fmtFull.format(a), to: fmtTime.format(b), n: s.n[i], v: signedPct(served[i]) })
      + (HV.anyCal ? ` · ${t("hi.raw")} ${signedPct(raw[i])}` : "") + (pers[i] != null ? ` · ${t("hi.pers")} ${signedPct(pers[i])}` : "");
    el("rect", { x: pad.l + i * bw, y: pad.t, width: bw, height: h - pad.t - pad.b, "data-tip": tip }, g);
  });
}

function timelineChart(host, { w, h }) {
  const tl = HV.tl, s = tl?.series?.[HV.key];
  const ri = tl?.regions?.indexOf(HV.region) ?? -1;
  if (!s || ri < 0) { host.innerHTML = `<p class="small muted">${t("stats.stripWait")}</p>`; return; }
  const [tg, hz] = hkSplit(HV.key);
  const n0 = Math.max(0, tl.n_hours - HV.hours);
  const P = s.p[ri].slice(n0), Y = s.y[ri].slice(n0), M = s.minute.slice(n0);
  const n = P.length;
  const start = new Date(tl.start).getTime() + n0 * 3600e3;
  h = h || (w < 500 ? 180 : 210);
  const pad = { l: 44, r: 8, t: 10, b: 30 };
  const f = frame(host, w, h, pad, false);
  f.svg.setAttribute("aria-label", t("hi.tl"));
  const bw = (w - pad.l - pad.r) / Math.max(1, n);
  for (let i = 0; i < n; i++) {
    if (P[i] == null) continue;
    if (Y[i] === 1) el("rect", { x: pad.l + i * bw, y: f.y(1), width: bw + 0.4, height: f.y(0) - f.y(1), fill: css("--alert-soft") }, f.svg);
    else if (Y[i] == null) el("rect", { x: pad.l + i * bw, y: f.y(1), width: bw + 0.4, height: f.y(0) - f.y(1), fill: "url(#h-pend)" }, f.svg);
  }
  const defs = el("defs", {}, f.svg);
  const pat = el("pattern", { id: "h-pend", width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, defs);
  el("rect", { width: 6, height: 6, fill: css("--surface2") }, pat);
  el("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: css("--line"), "stroke-width": 2 }, pat);
  yAxis(f, w, pad);
  for (let i = 0; i < n; i++) {
    const p = P[i];
    if (p == null) continue;
    el("rect", { x: pad.l + i * bw + bw * 0.15, y: f.y(p), width: Math.max(1, bw * 0.7), height: Math.max(0.5, f.y(0) - f.y(p)),
      fill: rampColor(Math.max(0.2, p)), rx: Math.min(2, bw / 4) }, f.svg);
  }
  for (let i = 0; i <= n; i++) {
    const tm = new Date(start + i * 3600e3);
    if (kyivHour(tm) === 0 && i < n) {
      el("line", { x1: pad.l + i * bw, x2: pad.l + i * bw, y1: f.y(0), y2: f.y(0) + 6, class: "ax" }, f.svg);
      if (bw * 24 > 46) { const tx = el("text", { x: pad.l + i * bw + 3, y: h - 9, class: "strong" }, f.svg); tx.textContent = fmtDay.format(tm); }
    } else if (kyivHour(tm) % 6 === 0 && bw * 6 >= 30 && i < n - 1) {
      const tx = el("text", { x: pad.l + i * bw, y: f.y(0) + 13, "text-anchor": "middle", class: "minor" }, f.svg);
      tx.textContent = String(kyivHour(tm)).padStart(2, "0");
    }
  }
  const g = el("g", { class: "hits" }, f.svg);
  for (let i = 0; i < n; i++) {
    if (P[i] == null) continue;
    const o = new Date(start + i * 3600e3 + (M[i] || 0) * 60e3);
    const a = new Date(o.getTime() + (tg === "active" ? hz - 1 : 0) * 3600e3), b = new Date(o.getTime() + hz * 3600e3);
    const tip = t("hi.tlTip", { time: fmtFull.format(o), from: fmtTime.format(a), to: fmtTime.format(b), p: pct(P[i]),
      y: Y[i] == null ? t("stats.stripUnknown") : Y[i] ? t("stats.stripYes") : t("stats.stripNo") });
    el("rect", { x: pad.l + i * bw, y: pad.t, width: bw, height: f.y(0) - pad.t, "data-tip": tip }, g);
  }
}

// ---------------------------------------------------------------- raions
const mtag = (m) => `<span class="mtag m-${m}">${t(`method.${m}`)}</span>`;
const shareStale = (sp) => !!sp && new Date() - new Date(sp[1]) > 3 * 864e5;
// alert duration up to the moment the live state describes (a static page can be hours old)
const liveTo = () => (S.raions?.live_as_of ? new Date(S.raions.live_as_of) : new Date());
const raionById = (id) => IDX.rn.get(id);
const rTarget = () => (S.target === "type" ? "active" : S.target);
const liveRaions = () => !!S.raions?.live_source;
const activeRaions = (oblast = "") => (S.raions?.raions || []).filter((r) => r.live?.active && (!oblast || r.oblast === oblast));

async function setUnit(unit) {
  if (unit === "raion" && !S.raions) return;
  S.unit = unit;
  for (const b of $("#unit").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.v === unit));
  render();
  if (!S.panel) renderPanel();
}

async function zoomToAlerts() {
  if (S.unit !== "raion") await setUnit("raion");
  const ob = S.zoomOblast;
  const act = activeRaions(ob).map((r) => r.id);
  const info = $("#zoom-info");
  if (act.length) {
    zoomTo(unionBox(act));
    info.textContent = t("zoom.n", { n: act.length }) + (ob ? ` (${oblFull(ob)})` : "");
  } else if (ob) {
    zoomTo(unionBox(S.raions.raions.filter((r) => r.oblast === ob).map((r) => r.id)));
    info.textContent = liveRaions() ? t("zoom.noneHere") : t("win.noLiveRaions");
  } else {
    info.textContent = liveRaions() ? t("zoom.none") : t("win.noLiveRaions");
  }
}

function renderZoomOblasts() {
  const sel = $("#zoom-oblast");
  if (!S.forecast) return;
  const by = new Map();
  for (const r of S.forecast.regions) by.set(r.name, { n: 0, on: 0 });
  for (const r of S.raions?.raions || []) {
    const v = by.get(r.oblast) || { n: 0, on: 0 };
    v.n += 1; v.on += r.live?.active ? 1 : 0;
    by.set(r.oblast, v);
  }
  const opts = [...by.entries()].sort((a, b) => (b[1].on - a[1].on) || oblShort(a[0]).localeCompare(oblShort(b[0]), LANG));
  const html = `<option value="">${t("zoom.all")}</option>` + opts.map(([o, v]) =>
    `<option value="${esc(o)}">${esc(oblFull(o))}${v.on ? ` · ${t("zoom.opt", { n: v.on })}` : ""}</option>`).join("");
  if (sel.dataset.html !== html) { sel.innerHTML = html; sel.dataset.html = html; }
  sel.value = S.unit === "oblast" ? (regionData(S.selected)?.name || "") : S.zoomOblast;
  const n = activeRaions().length;
  $("#zoom-alerts").hidden = !S.raions;
  $("#zoom-alerts").title = liveRaions() ? (n ? t("zoom.title", { n }) : t("zoom.none")) : t("zoom.titleNoLive");
}

function raionTipHTML(id) {
  const r = raionById(id);
  const m = raionMeta(id);
  if (!r) return m ? `<b>${esc(raionName(id))}</b><br>${t("map.nodata")}` : null;
  const head = `<b>${esc(raionName(id, r))}</b> <span class="tipsub">${esc(r.oblast === "Kyiv City" ? "" : oblFull(r.oblast))}</span>`;
  if (S.layer === "now") {
    if (!r.live) return `${head}<br>${t("raion.noLive")}`;
    return `${head}<br>${r.live.active ? `${t(`live.${r.live.level}`)}${r.live.since ? ", " + duration(r.live.since, liveTo()) : ""}` : t("tip.noAlert")}`;
  }
  const tg = rTarget(), p = r.probs?.[tg]?.[S.horizon];
  return `${head}<br>${probTip(tg, p, true)}<br><span class="tipsub">${t(`method.${r.method?.[tg]?.[S.horizon]}`)}</span>`;
}

async function selectRaion(id, { zoom = false } = {}) {
  const r = raionById(id);
  if (!r) return;
  S.rsel = id;
  S.selected = r.oblast_id;
  if (location.hash !== `#raion/${id}`) history.replaceState(null, "", `${location.search}#raion/${id}`);
  paintMap();
  renderRank();
  if (zoom) zoomTo(raionMeta(id)?.bbox, 0.1);
  await renderRaionDetailById(id);
  if (window.matchMedia("(max-width: 900px)").matches) $("#detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function renderRaionDetailById(id) {
  try {
    const d = await getJSON(`/api/raions/${encodeURIComponent(id)}`);
    if (S.rsel === id) { S.panel = { kind: "raion", data: d }; renderPanel(); }
  } catch (e) {
    if (S.rsel === id) { S.panel = null; hideStats(); $("#detail").innerHTML = `<p class="muted">${t("err.raion")}</p>`; }
  }
}

function renderRaionDetail(d) {
  const lv = d.live;
  const status = !lv
    ? `<span class="status off"><span class="b"></span>${t("raion.noLive")}</span>`
    : lv.active
      ? `<span class="status on"><span class="b"></span>${t(`live.${lv.level}`)}${lv.since ? `, ${t("rlist.since", { time: sinceTime(lv.since, liveTo()), d: duration(lv.since, liveTo()) })}` : ""}</span>`
      : `<span class="status off"><span class="b"></span>${t("status.none")}</span>`;
  const locs = lv?.locations?.length ? `<p class="small muted">${t("detail.locations", { list: lv.locations.map((x) => esc(locName(x))).join(", ") })}</p>` : "";
  const hs = Object.keys(d.probs.active).map(Number);
  const methods = new Set(["active", "onset"].flatMap((tg) => hs.map((h) => d.method[tg][h])));
  const h3 = hs.includes(3) ? 3 : hs[0];
  const ob = d.oblast_probs;
  const whole = d.oblast === "Kyiv City" ? t("raion.cityModel") : t("raion.whole", { name: esc(oblGen(d.oblast)) });
  const cmp = ob ? `<p class="small muted">${t("raion.cmp", { whole, a: pct(ob.active[1]), h: h3, b: pct(ob.onset[h3]) })}</p>` : "";
  const sp = d.share_period;
  const shareNote = methods.has("oblast_x_share") && sp
    ? `<p class="small muted">${t("raion.share", { from: fmtDay.format(new Date(sp[0])), to: fmtDay.format(new Date(sp[1])), p: pct(d.share?.onset_h3), h: h3 })}${shareStale(sp) ? " " + t("raion.shareStale") : ""}</p>` : "";
  const ev = d.evaluation;
  let evHTML = "";
  if (ev?.scores?.length) {
    const get = (tg, h, m) => ev.scores.find((x) => x.target === tg && x.horizon_h === h && x.model === m);
    const per = ev.test ? `${fmtDay.format(new Date(ev.test[0]))} – ${fmtDay.format(new Date(ev.test[1]))}` : "";
    // a share estimated long ago is scored by its stale variant (share from 30 days earlier)
    const evalName = (m) => (m === "oblast_x_share" && shareStale(d.share_period) ? "oblast_x_share_stale30" : m);
    const rowName = (tg, h) => (tg === "active" ? t("rank.activeH", { h }) : t("rank.onsetH", { h }));
    const line = (tg, h) => {
      const m = d.method[tg][h];
      const r = get(tg, h, evalName(m)), o = get(tg, h, "oblast");
      return r ? `<tr><td>${rowName(tg, h)}</td>
        <td class="${r.bss_vs_clim > 0 ? "pos" : "neg"}">${signedPct(r.bss_vs_clim)}</td><td>${o ? signedPct(o.bss_vs_clim) : "–"}</td></tr>` : "";
    };
    const obl = (ev.oblast || []).find((x) => x.target === "onset" && x.horizon_h === 3);
    const lt = ev.latest;
    const lget = (tg, h, m) => lt?.scores?.find((x) => x.target === tg && x.horizon_h === h && x.model === m);
    const lline = (tg, h) => {
      const m = d.method[tg][h], r = lget(tg, h, m), o = lget(tg, h, "oblast");
      return r ? `<tr><td>${rowName(tg, h)}</td>
        <td class="${r.bss_vs_clim > 0 ? "pos" : "neg"}">${signedPct(r.bss_vs_clim)}</td><td>${o ? signedPct(o.bss_vs_clim) : "–"}</td></tr>` : "";
    };
    const head = `<thead><tr><th></th><th>${t("raion.thisMethod")}</th><th>${t("raion.oblastCol")}</th></tr></thead>`;
    const latestRows = lt ? ["active_1", "onset_1", "onset_3", "onset_6"].map((k) => lline(k.split("_")[0], Number(k.split("_")[1]))).join("") : "";
    const latestHTML = latestRows ? `<p class="small muted">${t("raion.latest", { period: lt.test ? ` (${fmtDay.format(new Date(lt.test[0]))} – ${fmtDay.format(new Date(lt.test[1]))})` : "" })}</p>
      <div class="tablewrap"><table class="mt">${head}<tbody>${latestRows}</tbody></table></div>` : "";
    const oblKey = `bss_${evalName(d.method.onset[3])}`;
    evHTML = `<details class="more"><summary>${t("raion.trust")}</summary>
      <p class="small muted">${t("raion.trustNote", { period: per })}</p>
      <div class="tablewrap"><table class="mt">${head}
      <tbody>${line("active", 1)}${line("onset", 1)}${line("onset", 3)}${line("onset", 6)}</tbody></table></div>
      ${latestHTML}
      ${obl && obl[oblKey] !== undefined ? `<p class="small muted">${t("raion.oblScore", { name: esc(oblGen(d.oblast)), rate: pct(obl.base_rate), bss: signedPct(obl[oblKey]), method: t(`method.${d.method.onset[3]}`) })}</p>` : ""}</details>`;
  }
  $("#detail").innerHTML = `
    <p class="crumbs small"><a href="#region/${esc(d.oblast_id)}" data-oblast="${esc(d.oblast_id)}">${esc(oblFull(d.oblast))}</a> › ${t("raion.crumb")}</p>
    <h2>${esc(raionName(d.id, d))}</h2>${status}${locs}
    <p class="small muted">${d.live_source ? t("raion.src", { time: fmtTime.format(new Date(d.live_as_of)) }) : t("raion.srcNone")} ·
      ${t("raion.asOf", { time: fmtTime.format(new Date(d.origin)) })}</p>
    <div class="detail-actions"><button type="button" class="zbtn" id="zoom-this">${t("raion.zoom")}</button>
      <button type="button" class="zbtn" id="back-oblast">${t("raion.back", { name: esc(oblFull(d.oblast)) })}</button></div>
    <h3>${t("raion.forecast")}</h3>
    <p class="small">${t("raion.from")} ${[...methods].map(mtag).join(" ")}</p>
    ${pctNote("ex-raion", "raion.note", d.windows, { raion: true })}
    ${probsTable(d, hs)}
    ${cmp}
    <details class="more"><summary>${t("raion.methodMore")}</summary>
      <p class="small muted">${[...methods].map((m) => `${quote(t(`method.${m}`))}: ${t(`methodLong.${m}`)}`).join("<br>")}</p>${shareNote}</details>
    ${evHTML}
    <section class="why" id="why" aria-live="polite">${raionWhyHTML(d)}</section>`;
  $("#zoom-this").addEventListener("click", () => zoomTo(raionMeta(d.id)?.bbox, 0.1));
  const back = (ev) => { ev.preventDefault(); select(d.oblast_id); };
  $("#detail .crumbs a").addEventListener("click", back);
  $("#back-oblast").addEventListener("click", back);
}

function renderRaionRank() {
  const tb = $("#rank");
  const rs = S.raions?.raions || [];
  const hs = S.raions?.horizons || S.forecast?.horizons || [1, 2, 3, 6];
  const h3 = hs.includes(3) ? 3 : hs[0];
  const ob = S.zoomOblast;
  const key = S.layer === "now" ? (r) => (r.live?.active ? 2 : 0) + (r.probs.active[1] ?? 0) : (r) => r.probs[rTarget()][S.horizon] ?? -1;
  const rows = rs.filter((r) => !ob || r.oblast === ob).sort((a, b) => key(b) - key(a));
  tb.innerHTML = `<caption class="sr">${t("rank.raions")}: ${t("rank.noteRaions")}</caption><thead>
    <tr class="grp"><th scope="col" rowspan="2">${t("rank.raion")}</th><th scope="colgroup" colspan="3" class="gs">${t("rank.grpRaion")}</th><th scope="col" rowspan="2" class="gs">${t("rank.method")}</th></tr>
    <tr><th scope="col" class="gs">${t("rlist.h1")}</th><th scope="col">${t("rlist.h3", { h: h3 })}</th><th scope="col">${t("rlist.h3", { h: hs[hs.length - 1] })}</th></tr></thead>
    <tbody>${rows.map((r) => `<tr data-rid="${r.id}" tabindex="0" class="${r.id === S.rsel ? "sel" : ""}">
      <td>${esc(raionName(r.id, r))}<span class="small muted"> · ${esc(r.oblast === "Kyiv City" ? "" : oblShort(r.oblast))}</span>${r.live?.active ? `<span class="badge">${r.live.level === "hromada" ? t("badge.part") : t("badge.alert")}</span>` : ""}</td>
      <td class="gs">${probCell(r.probs.active[1])}</td><td>${probCell(r.probs.onset[h3])}</td><td>${probCell(r.probs.onset[hs[hs.length - 1]])}</td>
      <td class="small muted gs">${t(`method.${r.method.onset[h3]}`)}</td></tr>`).join("")}</tbody>`;
  $("#rank-title").textContent = ob ? t("rank.raionsOf", { name: oblFull(ob) }) : t("rank.raions");
  $("#rank-sub").textContent = S.raions?.origin ? t("rank.subRaions", { time: fmtFull.format(new Date(S.raions.origin)) }) : "";
  $("#rank-note").innerHTML = pctNote("ex-rank", "rank.noteRaions", S.forecast?.windows, { raion: true });
}

// ---------------------------------------------------------------- why this forecast (oblast and raion panels)
// d.why (api/regions/{id}; a raion panel gets its oblast's as oblast_why), per target and horizon:
// the served model and probability, the history-only probability (Telegram effect) and the drivers:
// log-odds contributions of the served LightGBM summed into a few groups (airraid.explain). A bar is the
// push up or down; ×/÷ is the factor on the odds (exp of the contribution). They add up in log-odds,
// so they are never shown as percentage points.
const WHY_MIN = 0.05;   // |log-odds| below this (odds changed by less than 5%) is not drawn
const WHY_TOP = 6;
let WHYSEL = null;      // {t, h} picked in the block or in the forecast table; null: follow the map controls

function whyPick(w) {
  const items = w?.items || {};
  let tg = WHYSEL?.t || (S.layer === "forecast" && S.target !== "type" ? S.target : "onset");
  if (!items[tg]) tg = Object.keys(items)[0];
  const hs = Object.keys(items[tg] || {}).map(Number).sort((a, b) => a - b);
  let h = WHYSEL?.h ?? (S.layer === "forecast" && S.target !== "type" ? S.horizon : 3);
  if (!hs.includes(h)) h = hs.includes(3) ? 3 : hs[0];
  return { tg, h, hs };
}
// a cell of the forecast table that explains itself when tapped
function whyCell(tg, h) {
  const w = S.panel?.kind === "raion" ? S.panel.data.oblast_why : S.panel?.data?.why;
  if (!w?.items?.[tg]?.[h]) return "";
  const cur = whyPick(w);
  return ` class="whyc${cur.tg === tg && cur.h === h ? " picked" : ""}" data-why-t="${tg}" data-why-h="${h}" tabindex="0" title="${esc(t("why.pick"))}"`;
}
const oddsFactor = (v) => { const f = Math.exp(Math.abs(v)); return f >= 9.95 ? f.toFixed(0) : f.toFixed(1); };
const agoText = (m) => (m >= 60 ? t("dur.hm", { h: Math.floor(m / 60), m: m % 60 }) : t("dur.m", { m }));
function sigText(x) {
  switch (x.code) {
    case "reg_threat": return t(x.w === 60 ? "sig.regThreat60" : "sig.regThreat180", { threat: t(`sigThreat.${x.threat}`), n: x.n });
    case "reg_clear": return t("sig.regClear", { n: x.n });
    case "reg_last": return t("sig.regLast", { ago: agoText(x.min) });
    case "reg_quiet": return t("sig.regQuiet");
    case "platform": return t("sig.platform", { ago: agoText(x.min) });
    case "national": return t("sig.national", { ago: agoText(x.min) });
    case "launch": return t("sig.launch", { n: x.n });
    case "massed": return t("sig.massed");
    case "explosion": return t("sig.explosion", { n: x.n });
    case "nat_regions": return t("sig.natRegions", { n: x.n });
    case "summary": return t("sig.summary", { drones: x.drones, missiles: x.missiles, h: x.age_h });
    default: return "";
  }
}
function whyHTML(w, probs, origin, { title = t("why.title"), lead = "" } = {}) {
  const head = `<h3><span>${title}</span> ${infoBtn("ex-why", t("why.help"))}</h3>
    <div class="explain" id="ex-why"${OPEN.has("ex-why") ? "" : " hidden"}><p>${t("why.ex.tg")}</p><p>${t("why.ex.drivers")}</p>
      <p>${t("why.ex.signals")}</p><p><a href="#about">${t("why.help")} →</a></p></div>${lead}`;
  if (!w?.items) return `${head}<p class="small muted">${t("why.none")}</p>`;
  const { tg, h, hs } = whyPick(w);
  const it = w.items[tg]?.[h];
  if (!it) return `${head}<p class="small muted">${t("why.none")}</p>`;
  const shown = probs?.[tg]?.[h] ?? it.p;
  const pick = `<div class="why-pick"><div class="seg sm" role="group" aria-label="${esc(t("ctl.target"))}">${["active", "onset"].filter((x) => w.items[x]).map((x) =>
      `<button type="button" data-why-t="${x}" aria-pressed="${x === tg}">${t(x === "active" ? "ctl.active" : "ctl.onset")}</button>`).join("")}</div>
    <div class="seg sm" role="group" aria-label="${esc(t("ctl.horizon"))}">${hs.map((x) =>
      `<button type="button" data-why-h="${x}" aria-pressed="${x === h}">${t("h.n", { n: x })}</button>`).join("")}</div></div>`;
  const q = `<p class="why-q">${t(`target.${tg}`, { h })}: <b>${pct(shown)}</b> ${level(shown)}</p>`;
  let tgl;
  if (it.text === "served" && it.p_hist != null) {
    const dd = Math.round(it.p * 100) - Math.round(it.p_hist * 100);
    const d = t("why.pp", { d: `${dd > 0 ? "+" : dd < 0 ? "−" : "±"}${Math.abs(dd)}` });
    tgl = `<div class="why-tg" title="${esc(t("why.tgTitle"))}"><span class="k">${t("why.tgk")}</span>
      <span class="v">${pct(it.p_hist)} → <b>${pct(it.p)}</b> <span class="dd ${dd > 0 ? "up" : dd < 0 ? "down" : ""}">${d}</span></span></div>`;
  } else {
    tgl = `<div class="why-tg off"><span class="k">${t("why.tgk")}</span><span class="v">${t(`why.tgNot.${it.text}`)}</span></div>`;
  }
  const calib = Math.abs(shown - it.p) >= 0.005 ? `<p class="small muted">${t("why.calib", { a: pct(it.p), b: pct(shown) })}</p>` : "";
  const gs = Object.entries(it.groups || {}).filter(([, v]) => Math.abs(v) >= WHY_MIN)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, WHY_TOP);
  const max = Math.max(1, ...gs.map(([, v]) => Math.abs(v)));
  const bars = gs.map(([g, v]) => {
    const up = v > 0, half = Math.max(1.5, (Math.abs(v) / max) * 50);
    const tip = t(up ? "why.oddsUp" : "why.oddsDown", { x: oddsFactor(v) });
    return `<li class="${up ? "up" : "down"}" title="${esc(tip)}"><span class="g">${t(`grp.${g}`)}</span>
      <span class="bar" aria-hidden="true"><i style="left:${up ? 50 : 50 - half}%;width:${half}%"></i></span>
      <span class="f"><span class="sr">${esc(tip)}</span><span aria-hidden="true">${up ? "×" : "÷"}${oddsFactor(v)}</span></span></li>`;
  }).join("");
  const drivers = `<div class="why-dh"><span class="k">${t("why.drivers")}</span>
      <span class="small muted" title="${esc(t("why.startTitle"))}">${t("why.start", { p: pct(1 / (1 + Math.exp(-it.base))) })} → ${pct(it.p)}</span></div>
    ${gs.length ? `<div class="why-axis small muted" aria-hidden="true"><span></span><span class="ax"><span>◀ ${t("why.lowers")}</span><span>${t("why.raises")} ▶</span></span><span></span></div><ul class="why-bars">${bars}</ul>` : ""}
    ${Object.keys(it.groups || {}).length > gs.length ? `<p class="small muted">${t("why.small")}</p>` : ""}`;
  const sig = w.has_text ? `<div class="why-dh"><span class="k">${t("why.signals", { time: fmtTime.format(new Date(origin)) })}</span></div>
    ${w.signals?.length ? `<ul class="why-sig">${w.signals.map((x) => `<li>${sigText(x)}</li>`).join("")}</ul>` : `<p class="small muted">${t("why.noSignals")}</p>`}` : "";
  return `${head}${pick}${q}${tgl}${calib}${drivers}${sig}`;
}
function raionWhyHTML(d) {
  const ms = [...new Set(["active", "onset"].flatMap((tg) => Object.values(d.method?.[tg] || {})))];
  const lead = `<p class="small">${t("why.raionMethod", { method: ms.map(mtag).join(" ") })}</p>`;
  return whyHTML(d.oblast_why, d.oblast_probs, d.origin, { title: t("why.parent", { name: esc(oblFull(d.oblast)) }), lead });
}
function renderWhy() {
  const box = $("#why"), p = S.panel;
  if (!box || !p) return;
  box.innerHTML = p.kind === "raion" ? raionWhyHTML(p.data) : whyHTML(p.data.why, p.data.probs, p.data.origin);
  const w = p.kind === "raion" ? p.data.oblast_why : p.data.why;
  const { tg, h } = whyPick(w);
  for (const c of $("#detail").querySelectorAll("td[data-why-t]")) c.classList.toggle("picked", c.dataset.whyT === tg && Number(c.dataset.whyH) === h);
}
function setupWhy() {
  const pick = (b) => {
    const p = S.panel;
    if (!p) return;
    const cur = whyPick(p.kind === "raion" ? p.data.oblast_why : p.data.why);
    WHYSEL = { t: b.dataset.whyT || cur.tg, h: b.dataset.whyH ? Number(b.dataset.whyH) : cur.h };
    renderWhy();
    if (b.tagName === "TD") $("#why")?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "nearest" });
  };
  $("#detail").addEventListener("click", (ev) => { const b = ev.target.closest("[data-why-t], [data-why-h]"); if (b) pick(b); });
  $("#detail").addEventListener("keydown", (ev) => {
    const b = ev.target.closest?.("td[data-why-t]");
    if (b && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); pick(b); }
  });
  // the map controls pick what the block explains until the viewer picks something in the panel
  for (const id of ["#layer", "#target", "#horizon"]) $(id).addEventListener("click", () => { WHYSEL = null; requestAnimationFrame(renderWhy); });
}

// ---------------------------------------------------------------- how it works: the parts that come from data (api/method.json)
let METHOD = null;
const NEWS_NAME = { ukrinform: "Ukrinform", pravda: "Ukrainska Pravda", unian: "UNIAN", rbc: "RBC-Ukraine", gdelt: "GDELT" };
function textStatusCode(st) {
  if (!st || st === "off" || st.startsWith("off")) return "off";
  if (st === "ok") return "ok";
  if (/does not cover|features failed/.test(st)) return "no_archive";
  return "no_model";
}
async function renderAbout() {
  if (!METHOD) {
    try { METHOD = await getJSON("/api/method"); } catch (_) { return; }  // not ready yet: the static text stays
  }
  const m = METHOD;
  const chg = (v) => (v == null ? `<span class="muted">${t("meth.na")}</span>` : `${v < 0 ? "−" : "+"}${Math.abs(v * 100).toFixed(1)}%`);
  const cls = (v) => (v == null ? "" : v < 0 ? "pos" : "neg");
  const rows = (m.telegram || []).map((r) => `<tr class="${r.used ? "on" : "off"}"><td>${targetName(r.target, r.horizon)}</td>
    <td>${r.used ? `<span class="tick">✓</span> ${t("meth.used")}` : `<span class="muted">– ${t("meth.notUsed")}</span>`}</td>
    <td class="${cls(r.val_change)}">${chg(r.val_change)}</td>
    <td class="${cls(r.test_change)}">${chg(r.test_change)}${r.test_verdict ? `<br><span class="small muted">${t(`meth.${r.test_verdict}`)}</span>` : ""}</td></tr>`).join("");
  const per = m.test_period ? ` ${t("meth.period", { a: fmtDay.format(new Date(m.test_period[0])), b: fmtDay.format(new Date(m.test_period[1])) })}` : "";
  const now = m.text_loaded || m.text_status ? `<p class="small meth-now">${t(`meth.now.${textStatusCode(m.text_status)}`)}</p>` : "";
  const html = `${now}<div class="tablewrap"><table class="mt meth"><thead><tr><th>${t("meth.colWhat")}</th><th>${t("meth.colUsed")}</th>
      <th>${t("meth.colVal")}</th><th>${t("meth.colTest")}</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="small muted">${t("meth.note")}${per}</p>`;
  for (const b of document.querySelectorAll("[data-method]")) b.innerHTML = html;
  const news = (m.news?.sources || []).map((x) => NEWS_NAME[x] || x).join(", ");
  const ch = (m.channels || []).map((c) => `<li><a href="https://t.me/s/${esc(c.id)}" rel="noopener">${esc(c.id)}</a> · ${t(`meth.ch.${c.id}`)}
      <span class="mtag ${c.in_model ? "m-lightgbm" : "m-oblast"}">${t(c.in_model ? "meth.inModel" : "meth.liveOnly")}</span></li>`).join("")
    + (news ? `<li>${t(m.news.in_model ? "meth.newsYes" : "meth.newsNo", { list: esc(news) })}</li>` : "");
  for (const b of document.querySelectorAll("[data-method-ch]")) b.innerHTML = ch;
}

// ---------------------------------------------------------------- views & loading
function setView() {
  const h = location.hash.replace("#", "") || "map";
  const [view, arg] = h.split("/");
  S.view = ["region", "raion", "stats"].includes(view) ? "map" : (["map", "history", "metrics", "about"].includes(view) ? view : "map");
  for (const a of document.querySelectorAll(".tabs a")) a.setAttribute("aria-current", a.dataset.view === S.view ? "page" : "false");
  for (const v of ["map", "history", "metrics", "about"]) $(`#view-${v}`).hidden = v !== S.view || (v !== "about" && !$("#loading").hidden);
  if (S.view === "metrics" && $("#loading").hidden) renderMetrics();
  if (S.view === "history" && $("#loading").hidden) renderHistory();
  if (S.view === "about") renderAbout();
  if (view === "region" && arg && S.forecast && (arg !== S.selected || S.rsel)) select(arg);
  if (view === "raion" && arg && arg !== S.rsel && S.raions) selectRaion(arg, { zoom: true });
  if (S.view === "map") requestAnimationFrame(drawCharts);
}

function render() {
  renderHorizonButtons();
  $("#window").textContent = windowText();
  renderHeadline();
  renderSummary();
  renderBanner();
  paintMap();
  renderRank();
  const up = updatedAt();
  $("#foot-status").textContent = (up ? t("foot.updated", { time: fmtTime.format(up) }) + (staleWarning() ? ` ${t("foot.stale")}` : "") : "")
    + (S.forecast?.model?.trained_at ? ` · ${t("foot.model", { time: fmtFull.format(new Date(S.forecast.model.trained_at)), to: fmtFull.format(new Date(S.forecast.history_end)) })}` : "");
}

function loadingText(detail) {  // shown with textContent
  const c = detail?.message_code;
  if (c?.code) return t(`load.${c.code}`, { n: c.n ?? "", total: c.total ?? "", error: c.error || "" }) + (c.n != null ? ` (${c.n}/${c.total})` : "");
  return detail?.status ? t(`load.${detail.status}`) : t("load.preparing");
}

async function refresh() {
  try {
    const [st, fc, rs, status] = await Promise.all([getJSON("/api/state"), getJSON("/api/forecast"),
      getJSON("/api/raions").catch(() => null),  // raion layer is optional
      getJSON("/api/status").catch(() => null)]);
    const firstLoad = !S.forecast;
    const before = S.panel?.kind === "region" ? panelLive(S.panel.data.id, S.panel.data.name) : null;
    S.state = st; S.forecast = fc; S.raions = rs; S.status = status;
    indexData();
    $("#unit").hidden = !rs;
    if (!rs && S.unit === "raion") S.unit = "oblast";
    $("#loading").hidden = true;
    if (firstLoad) { buildMap(); setView(); }
    render();
    if (!firstLoad && S.panel?.kind === "raion" && S.rsel) renderRaionDetailById(S.rsel);
    else if (!firstLoad && S.panel?.kind === "region" && S.selected) {
      const d = await getJSON(`/api/regions/${S.selected}`).catch(() => null);
      if (d && S.panel?.kind === "region") {
        // keep the panel and its charts in place unless the oblast's data or live state (its own, its raions') changed
        const same = before === panelLive(d.id, d.name) && JSON.stringify(d) === JSON.stringify(S.panel.data);
        S.panel = { kind: "region", data: d };
        if (!same) renderPanel();
      }
    }
    return true;
  } catch (e) {
    if (e.status === 503) {
      $("#loading").hidden = false;
      $("#loading-msg").textContent = loadingText(e.body?.detail);
      for (const v of ["map", "history", "metrics"]) $(`#view-${v}`).hidden = true;
      return false;
    }
    console.error(e);
    return !!S.forecast;
  }
}

async function main() {
  applyStatic();
  readRamp();
  setupControls();
  setupMapEvents();
  setupChartTips();
  setupWhy();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    readRamp();
    if (GEO.base && S.forecast) { buildMap(); render(); renderPanel(); }
  });
  window.addEventListener("hashchange", setView);
  const pick = (tr) => (tr.dataset.rid ? selectRaion(tr.dataset.rid, { zoom: true }) : select(tr.dataset.id));
  $("#rank").addEventListener("click", (ev) => { const tr = ev.target.closest("tr[data-id], tr[data-rid]"); if (tr) pick(tr); });
  $("#rank").addEventListener("keydown", (ev) => { const tr = ev.target.closest("tr[data-id], tr[data-rid]"); if (tr && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); pick(tr); } });
  $("#stats").addEventListener("click", (ev) => { const b = ev.target.closest("button.expand"); if (b) openChart(b.dataset.chart); });
  const toggleInfo = (b, open) => {
    const box = document.getElementById(b.getAttribute("aria-controls"));
    if (!box) return;
    box.hidden = !open;
    b.setAttribute("aria-expanded", String(open));
    if (open) OPEN.add(box.id); else OPEN.delete(box.id);
  };
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest?.("button.info-btn");
    if (b) toggleInfo(b, b.getAttribute("aria-expanded") !== "true");
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    const box = ev.target.closest?.(".explain") || (ev.target.matches?.("button.info-btn[aria-expanded=true]") && document.getElementById(ev.target.getAttribute("aria-controls")));
    const b = box && document.querySelector(`button.info-btn[aria-controls="${box.id}"]`);
    if (b) { toggleInfo(b, false); b.focus(); }
  });
  $("#cd-close").addEventListener("click", () => $("#chart-dialog").close());
  $("#chart-dialog").addEventListener("click", (ev) => { if (ev.target === ev.currentTarget) ev.currentTarget.close(); });
  $("#chart-dialog").addEventListener("close", () => { $("#ctip").hidden = true; });
  let rz = null;
  new ResizeObserver(() => { clearTimeout(rz); rz = setTimeout(() => { drawCharts(); paintLabels(); drawHistory(); }, 120); }).observe($("main"));
  $("#search").addEventListener("change", (ev) => {
    const q = normUk(ev.target.value);
    if (!q) return;
    const names = (r) => [oblFull(r.name), oblShort(r.name), r.name_uk, OBLAST[r.name]?.[0]].filter(Boolean).map(normUk);
    const rnames = (r) => [raionName(r.id, r), r.name_uk, r.name].filter(Boolean).map(normUk);
    const fr = S.forecast?.regions || [], rr = S.raions?.raions || [];
    const reg = fr.find((x) => names(x).includes(q));
    const rai = rr.find((x) => rnames(x).includes(q)) || (!reg && rr.find((x) => rnames(x).some((n) => n.startsWith(q))));
    const reg2 = reg || (!rai && fr.find((x) => names(x).some((n) => n.startsWith(q))));
    if (rai) selectRaion(rai.id, { zoom: true });
    else if (reg2) select(reg2.id);
    else return;
    ev.target.value = ""; ev.target.blur();
  });
  GEO.base = await getJSON("static/ukraine-map.json", "default");
  setView();
  let ok = await refresh();
  while (!ok) {
    await new Promise((r) => setTimeout(r, 5000));
    ok = await refresh();
  }
  if (!S.panel && !S.selected) renderPanel();
  setInterval(async () => {
    const prevOrigin = S.forecast?.origin;
    await refresh();
    if (S.forecast?.origin !== prevOrigin) {
      S.metrics = null; if (S.view === "metrics") renderMetrics();
      HV.data = null; if (S.view === "history") renderHistory();
    }
  }, 60000);
  getJSON("/api/metrics").then((m) => { S.metrics = m; if (S.panel?.kind === "region") renderPanel(); }).catch(() => {});
}

main();
