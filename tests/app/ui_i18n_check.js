// Browser check of the static site, run by tests/app/test_ui_browser.py:
//   node ui_i18n_check.js <site-url>      (NODE_PATH must resolve "playwright")
// Prints one JSON line: the language the page starts in (the browser asks for Ukrainian, English must
// still be the default), visible lines with Cyrillic per view in English mode, the language switch and
// whether it is remembered, the ?lang= override, horizontal overflow on a phone and console errors.
const { chromium } = require("playwright");

const base = process.argv[2].replace(/\/$/, "");
const CYR = /[Ѐ-ӿ]/;

(async () => {
  const opts = process.env.AIRRAID_UI_CHANNEL ? { channel: process.env.AIRRAID_UI_CHANNEL } : {};
  const browser = await chromium.launch(opts);
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: "uk-UA" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(e.message));
  const cyrillic = {};
  const check = async (name) => {
    await page.waitForTimeout(300);
    const text = await page.evaluate(() => document.body.innerText);
    cyrillic[name] = text.split("\n").filter((l) => CYR.test(l)).slice(0, 10);
  };
  await page.goto(`${base}/`, { waitUntil: "networkidle" });
  await page.waitForSelector("#rank tbody tr");
  const lang0 = await page.evaluate(() => document.documentElement.lang);
  await check("map");
  await page.click("#rank tbody tr");
  await page.waitForSelector("#stats .chartbox");
  await check("oblast");
  const raionRows = await page.$$eval(".rrow", (x) => x.length);
  await page.click("#layer button[data-v=forecast]");
  await check("forecast");
  await page.click("#target button[data-v=onset]");
  await check("forecast-onset");
  if (raionRows) {
    await page.click(".rrow");
    await page.waitForSelector("#detail .crumbs");
    await check("raion");
  }
  if (await page.isVisible("#unit")) {
    await page.click("#unit button[data-v=raion]");
    await check("raion-view");
  }
  await page.click(".expand >> nth=0").catch(() => {});
  await check("dialog");
  await page.keyboard.press("Escape");
  await page.goto(`${base}/#metrics`, { waitUntil: "networkidle" });
  await page.waitForSelector("#metrics h2");
  await check("metrics");
  await page.goto(`${base}/#about`, { waitUntil: "networkidle" });
  await check("about");

  await page.click("#lang button[data-setlang=uk]");
  const ukLang = await page.evaluate(() => document.documentElement.lang);
  const ukText = CYR.test(await page.evaluate(() => document.body.innerText));
  await page.goto(`${base}/#map`, { waitUntil: "networkidle" });
  await page.waitForSelector("#rank tbody tr");
  const remembered = await page.evaluate(() => document.documentElement.lang);
  const ukMapText = CYR.test(await page.evaluate(() => document.querySelector("#rank").innerText));
  await page.goto(`${base}/?lang=en#map`, { waitUntil: "networkidle" });
  const override = await page.evaluate(() => document.documentElement.lang);

  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const mp = await phone.newPage();
  mp.on("pageerror", (e) => errors.push(e.message));
  await mp.goto(`${base}/`, { waitUntil: "networkidle" });
  await mp.waitForSelector("#rank tbody tr");
  await mp.click("#rank tbody tr");
  await mp.waitForSelector("#stats .chartbox");
  await mp.waitForTimeout(500);
  const overflow = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

  console.log(JSON.stringify({ lang0, cyrillic, raionRows, ukLang, ukText, remembered, ukMapText, override, overflow, errors }));
  await browser.close();
})().catch((e) => { console.log(JSON.stringify({ fatal: String(e && e.stack || e) })); process.exit(1); });
