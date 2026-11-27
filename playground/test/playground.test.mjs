// Headless test. `npm test` serves the playground folder (..) itself on a free port;
// set PLAYGROUND_URL to test a deployed copy instead.
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".py": "text/plain", ".whl": "application/octet-stream" };

let server = null;
let URL_ = process.env.PLAYGROUND_URL;
if (!URL_) {
  server = createServer(async (req, res) => {
    const path = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname)).replace(/^[/\\]+/, "") || "index.html";
    const file = join(ROOT, path);
    if (!file.startsWith(ROOT) || path.split(sep).includes("test")) { res.writeHead(404).end(); return; }
    try {
      const body = await readFile(file);
      res.writeHead(200, { "content-type": TYPES[extname(file)] || "application/octet-stream" }).end(body);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
  URL_ = `http://127.0.0.1:${server.address().port}/`;
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 900 }, locale: "en-US" });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
const t0 = Date.now();
await page.goto(URL_);
await page.waitForFunction(() => window.__ready === true, null, { timeout: 120000 });
console.log(`engine ready after ${Date.now() - t0} ms (cold, no HTTP cache)`);

const verdict = async (p = page) => (await p.locator("#out .verdict").innerText()).trim();
const policy = async (p = page) => JSON.parse(await p.inputValue("#policy"));
const waitVerdict = (p, v) => p.waitForFunction((v) => document.querySelector("#out .verdict")?.textContent === v, v);

// 1. DELETE hidden in a CTE is rejected
await page.click('button[title^="postgres: WITH d AS"]');
await page.waitForSelector("#out .verdict");
assert.equal(await verdict(), "REJECTED");
assert.match(await page.locator("#out").innerText(), /write_operation/);
assert.match(await page.locator("#out .why").innerText(), /can't be switched off/);

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
const policyText = await page.inputValue("#policy");
const page2 = await browser.newPage();
const reqs = [];
page2.on("request", (r) => reqs.push(r.url()));
await page2.goto(url);
assert.equal(await page2.inputValue("#sql"), "SELECT id FROM invoices WHERE total > 100 -- ñ ✓");
assert.equal(await page2.inputValue("#policy"), policyText);
assert.equal(await page2.inputValue("#role"), "south");
await page2.waitForSelector("#out .verdict", { timeout: 120000 }); // auto-runs shared link
assert.ok(!reqs.some((u) => u.includes("#")), "fragment must not be sent");
await page2.close();

// 4. Policy rules panel. Start from the default policy: the email is hidden.
await page.click("#reset");
await page.selectOption("#role", "north");
assert.equal(await page.isChecked('input[name="email"][value="hidden"]'), true);
assert.equal(await page.isChecked("#rowFilter"), true);
assert.equal(await page.inputValue("#maxRows"), "50");
assert.equal(await page.isChecked("#allowUsers"), false);

// 4a. Email hidden: rejected, and the explanation says why and how to allow it
await page.fill("#sql", "SELECT name, email FROM customers ORDER BY id");
await page.click("#run");
await waitVerdict(page, "REJECTED");
const why = page.locator('#out .why li[data-code="hidden_column"]');
const whyText = await why.innerText();
assert.match(whyText, /hidden_columns lists customers\.email/);
assert.match(whyText, /“Visible”/);
assert.match(whyText, /“Masked”/);
assert.equal(await why.locator("a").getAttribute("href"), "https://jmmana.github.io/sqlguardllm/policy-reference/#hidden_columns");

// 4b. Email visible: allowed, and the real addresses come back
await page.check('input[name="email"][value="visible"]');
await waitVerdict(page, "ALLOWED"); // a switch rewrites the JSON and checks the last query again
cells = await page.locator("#out td").allInnerTexts();
assert.deepEqual(cells, ["Acme", "ceo@acme.example", "Globex", "hi@globex.example", "Initech", "bill@initech.example"]);
assert.deepEqual((await policy()).hidden_columns, []);

// 4c. Email masked: allowed, values masked
await page.check('input[name="email"][value="masked"]');
await page.waitForFunction(() => document.querySelector("#out td:nth-child(2)")?.textContent === "ce***");
cells = await page.locator("#out td").allInnerTexts();
assert.deepEqual(cells, ["Acme", "ce***", "Globex", "hi***", "Initech", "bi***"]);
assert.deepEqual((await policy()).masked_columns, { "customers.email": "partial" });
assert.match(await page.locator("#out .note").innerText(), /customers\.email \(partial\)/);
await page.selectOption("#maskStrategy", "redact");
await page.waitForFunction(() => document.querySelector("#out td:nth-child(2)")?.textContent === "***");
// Masked columns still can't be filtered on, and the explanation says so
await page.fill("#sql", "SELECT name FROM customers WHERE email LIKE 'ceo%'");
await page.click("#run");
await waitVerdict(page, "REJECTED");
assert.match(await page.locator("#out .why").innerText(), /may only appear in the select list/);

// 4d. Fix buttons: back to hidden, then "Mask the email" from the explanation
await page.check('input[name="email"][value="hidden"]');
await page.fill("#sql", "SELECT name, email FROM customers ORDER BY id");
await page.click("#run");
await waitVerdict(page, "REJECTED");
await page.click('#out button[data-fix="fx_mask"]');
await waitVerdict(page, "ALLOWED");
assert.equal(await page.isChecked('input[name="email"][value="masked"]'), true);

// 4e. Row filter off: both branches' rows, whatever the role
await page.fill("#sql", "SELECT id, branch_id FROM invoices ORDER BY id");
await page.click("#run");
await waitVerdict(page, "ALLOWED");
assert.deepEqual(await page.locator("#out td").allInnerTexts(), ["1", "1", "3", "1", "5", "1"]);
assert.equal(await page.isVisible("#roleNote"), false);
await page.uncheck("#rowFilter");
await page.waitForFunction(() => document.querySelectorAll("#out td").length === 12);
assert.deepEqual(await page.locator("#out td").allInnerTexts(), ["1", "1", "2", "2", "3", "1", "4", "2", "5", "1", "6", "2"]);
assert.equal(await page.isVisible("#roleNote"), true);
assert.deepEqual((await policy()).row_filters, {});

// 4f. max_rows and the users table
await page.fill("#maxRows", "2");
await page.waitForFunction(() => document.querySelectorAll("#out td").length === 4);
assert.match(await page.locator("#out pre").innerText(), /LIMIT 2$/);
await page.fill("#sql", "SELECT id, name FROM users");
await page.click("#run");
await waitVerdict(page, "REJECTED");
assert.match(await page.locator('#out .why li[data-code="table_not_allowed"]').innerText(), /Allow the users table/);
await page.check("#allowUsers");
await waitVerdict(page, "ALLOWED");
assert.deepEqual((await policy()).hidden_columns.includes("users.password_hash"), true);
await page.fill("#sql", "SELECT password_hash FROM users");
await page.click("#run");
await waitVerdict(page, "REJECTED");

// 4g. Share link carries the edited policy, and the switches follow it
await page.click("#share");
const page3 = await browser.newPage({ locale: "en-US" });
await page3.goto(page.url());
assert.equal(await page3.isChecked('input[name="email"][value="masked"]'), true);
assert.equal(await page3.isChecked("#rowFilter"), false);
assert.equal(await page3.inputValue("#maxRows"), "2");
assert.equal(await page3.isChecked("#allowUsers"), true);
await page3.close();

// 4h. Editing the JSON moves the switches; invalid JSON pauses them
await page.fill("#policy", JSON.stringify({ dialect: "sqlite", tables: ["invoices", "customers"], masked_columns: { "customers.email": "null" }, row_filters: { invoices: "branch_id IN ({user.branches})" }, max_rows: 7 }));
assert.equal(await page.isChecked('input[name="email"][value="masked"]'), true);
assert.equal(await page.inputValue("#maskStrategy"), "null");
assert.equal(await page.isChecked("#rowFilter"), true);
assert.equal(await page.inputValue("#maxRows"), "7");
assert.equal(await page.isChecked("#allowUsers"), false);
assert.equal(await page.isDisabled("#hidePwd"), true);
await page.fill("#policy", JSON.stringify({ dialect: "sqlite", tables: ["invoices"], max_rows: null }));
assert.equal(await page.isChecked('input[name="email"][value="visible"]'), true);
assert.equal(await page.isChecked("#rowFilter"), false);
assert.equal(await page.inputValue("#maxRows"), "");
await page.fill("#policy", "{ not json");
assert.equal(await page.isVisible("#rulesErr"), true);
assert.equal(await page.isDisabled("#rowFilter"), true);
await page.click("#run");
assert.match(await page.locator("#out .err").innerText(), /Invalid policy/);
await page.click("#reset");
assert.equal(await page.isVisible("#rulesErr"), false);

// 5. Language toggle, explanations included
await page.fill("#sql", "SELECT name, email FROM customers");
await page.click("#run");
await waitVerdict(page, "REJECTED");
await page.click("#lang");
assert.equal(await page.locator("h1").innerText(), "Intenta romperlo");
await waitVerdict(page, "RECHAZADA");
assert.match(await page.locator("#out .why").innerText(), /«Enmascarada»/);
assert.equal(await page.locator("#h-pol").innerText(), "Reglas de la política");

// 6. Mobile, dark: no horizontal scroll at 360px, with the explanation shown
const m = await browser.newPage({ viewport: { width: 360, height: 740 }, colorScheme: "dark" });
await m.goto(URL_);
await m.waitForFunction(() => window.__ready === true, null, { timeout: 120000 });
await m.click('button[title^="postgres: WITH d AS"]');
await m.waitForSelector("#out .verdict");
await m.fill("#sql", "SELECT * FROM customers");
await m.click("#run");
await m.waitForSelector("#out .why");
const over = await m.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
assert.ok(over <= 0, `horizontal overflow ${over}px`);
assert.equal(await m.evaluate(() => getComputedStyle(document.body).backgroundColor), "rgb(16, 22, 28)");

assert.deepEqual(errors, []);
await browser.close();
server?.close();
console.log("playground tests passed");
