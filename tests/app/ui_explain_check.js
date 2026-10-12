// Browser check of the "why this forecast" block and the How-it-works page, run by
// tests/app/test_ui_explain.py:   node ui_explain_check.js <site-url>
// Prints one JSON line per language: the Why block of the first oblast (heading, driver bars, the
// Telegram line, the forecast it explains before and after picking another one in the block and in
// the forecast table), the raion panel's Why (its oblast's drivers), the How-it-works page (diagram
// steps, Telegram table rows, collapsed details, visible words), overflow at 360 px and console errors.
const { chromium } = require("playwright");

const base = process.argv[2].replace(/\/$/, "");

async function oblast(page, lang) {
  await page.goto(`${base}/?lang=${lang}#map`, { waitUntil: "networkidle" });
  await page.waitForSelector("#rank tbody tr");
  await page.click("#rank tbody tr");
  await page.waitForSelector("#why .why-q");
  const read = () => page.evaluate(() => ({
    heading: document.querySelector("#why h3 span")?.textContent || "",
    question: document.querySelector("#why .why-q")?.innerText || "",
    bars: document.querySelectorAll("#why .why-bars li").length,
    tg: document.querySelector("#why .why-tg")?.innerText || "",
    picked: [...document.querySelectorAll("#detail td.picked")].map((c) => `${c.dataset.whyT}${c.dataset.whyH}`),
    firstH3: document.querySelector("#detail h3")?.innerText || "",
  }));
  const before = await read();
  await page.click('#why button[data-why-t="active"]');
  await page.click('#why button[data-why-h="1"]');
  const picked = await read();
  await page.click('#detail td[data-why-t="onset"][data-why-h="6"]');
  const cell = await read();
  return { before, picked, cell };
}

async function raion(page) {
  const n = await page.$$eval(".rrow", (x) => x.length);
  if (!n) return null;
  await page.click(".rrow");
  await page.waitForSelector("#detail .crumbs");
  await page.waitForSelector("#why .why-q");
  return page.evaluate(() => ({ heading: document.querySelector("#why h3 span")?.textContent || "",
    bars: document.querySelectorAll("#why .why-bars li").length, method: !!document.querySelector("#why .mtag") }));
}

async function about(page, lang) {
  await page.goto(`${base}/?lang=${lang}#about`, { waitUntil: "networkidle" });
  await page.waitForSelector(`[data-lang="${lang}"] [data-method] table`);
  return page.evaluate((lang) => {
    const root = document.querySelector(`#view-about [data-lang="${lang}"]`);
    return {
      steps: root.querySelectorAll(".flow .fl").length,
      rows: root.querySelectorAll("[data-method] tbody tr").length,
      details: root.querySelectorAll("details").length,
      openDetails: root.querySelectorAll("details[open]").length,
      channels: root.querySelectorAll("[data-method-ch] li").length,
      words: root.innerText.split(/\s+/).filter(Boolean).length,
      history: !!root.querySelector('a[href="#history"]'),
      api: !!root.querySelector(`#api-note-${lang}`),
    };
  }, lang);
}

(async () => {
  const opts = process.env.AIRRAID_UI_CHANNEL ? { channel: process.env.AIRRAID_UI_CHANNEL } : {};
  const browser = await chromium.launch(opts);
  const errors = [];
  const out = {};
  for (const lang of ["en", "uk"]) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    page.on("pageerror", (e) => errors.push(e.message));
    out[lang] = { oblast: await oblast(page, lang), raion: await raion(page), about: await about(page, lang) };
    await ctx.close();
  }
  const phone = await browser.newContext({ viewport: { width: 360, height: 780 }, deviceScaleFactor: 2 });
  const mp = await phone.newPage();
  mp.on("pageerror", (e) => errors.push(e.message));
  const overflow = {};
  await mp.goto(`${base}/#about`, { waitUntil: "networkidle" });
  await mp.waitForSelector("[data-method] table");
  overflow.about = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  await mp.goto(`${base}/#map`, { waitUntil: "networkidle" });
  await mp.waitForSelector("#rank tbody tr");
  await mp.click("#rank tbody tr");
  await mp.waitForSelector("#why .why-q");
  overflow.oblast = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  console.log(JSON.stringify({ ...out, overflow, errors }));
  await browser.close();
})().catch((e) => { console.log(JSON.stringify({ fatal: String(e && e.stack || e) })); process.exit(1); });
