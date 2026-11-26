// Headless test: serve ../ first (python -m http.server 8765 --directory ..), then `npm test`.
import { chromium } from "playwright";
import assert from "node:assert/strict";

const URL_ = process.env.PLAYGROUND_URL || "http://localhost:8765/";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
const t0 = Date.now();
await page.goto(URL_);
await page.waitForFunction(() => window.__ready === true, null, { timeout: 120000 });
console.log(`engine ready after ${Date.now() - t0} ms (cold, no HTTP cache)`);

const verdict = async () => (await page.locator("#out .verdict").innerText()).trim();

// 1. DELETE hidden in a CTE is rejected
await page.click('button[title^="postgres: WITH d AS"]');
await page.waitForSelector("#out .verdict");
assert.equal(await verdict(), "REJECTED");
assert.match(await page.locator("#out").innerText(), /write_operation/);

// 2. Allowed query returns only the role's rows
await page.selectOption("#role", "north");
await page.fill("#policy", JSON.stringify({ dialect: "sqlite", tables: ["invoices"], row_filters: { invoices: "branch_id IN ({user.branches})" }, max_rows: 50 }));
await page.fill("#sql", "SELECT id, branch_id FROM invoices ORDER BY id");
await page.click("#run");
await page.waitForFunction(() => document.querySelector("#out .verdict")?.textContent === "ALLOWED");
let cells = await page.locator("#out td").allInnerTexts();
assert.deepEqual(cells, ["1", "1", "3", "1", "5", "1"]);
await page.selectOption("#role", "south");
await page.click("#run");
await page.waitForFunction(() => document.querySelector("#out td")?.textContent === "2");
cells = await page.locator("#out td").allInnerTexts();
assert.deepEqual(cells, ["2", "2", "4", "2", "6", "2"]);
// OR 1=1 cannot widen the filter
await page.fill("#sql", "SELECT id FROM invoices WHERE branch_id = 1 OR 1 = 1 ORDER BY id");
await page.click("#run");
await page.waitForFunction(() => document.querySelector("#out td")?.textContent === "2");
assert.deepEqual(await page.locator("#out td").allInnerTexts(), ["2", "4", "6"]);

// 3. Share link round-trips (policy, SQL, role) in a fresh page
await page.fill("#sql", "SELECT id FROM invoices WHERE total > 100 -- ñ ✓");
await page.evaluate(() => { navigator.clipboard.writeText = async () => {}; });
await page.click("#share");
const url = page.url();
assert.ok(url.includes("#"));
const policy = await page.inputValue("#policy");
const page2 = await browser.newPage();
const reqs = [];
page2.on("request", (r) => reqs.push(r.url()));
await page2.goto(url);
assert.equal(await page2.inputValue("#sql"), "SELECT id FROM invoices WHERE total > 100 -- ñ ✓");
assert.equal(await page2.inputValue("#policy"), policy);
assert.equal(await page2.inputValue("#role"), "south");
await page2.waitForSelector("#out .verdict", { timeout: 120000 }); // auto-runs shared link
assert.ok(!reqs.some((u) => u.includes("#")), "fragment must not be sent");

// 4. Language toggle
await page.click("#lang");
assert.equal(await page.locator("h1").innerText(), "Intenta romperlo");

// 5. Mobile: no horizontal scroll at 360px
const m = await browser.newPage({ viewport: { width: 360, height: 740 } });
await m.goto(URL_);
await m.waitForFunction(() => window.__ready === true, null, { timeout: 120000 });
await m.click('button[title^="postgres: WITH d AS"]');
await m.waitForSelector("#out .verdict");
const over = await m.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
assert.ok(over <= 0, `horizontal overflow ${over}px`);

assert.deepEqual(errors, []);
await browser.close();
console.log("playground tests passed");
