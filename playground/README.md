# Playground: try to break it

A static site (plain HTML, CSS and JS, no build step, no backend) that runs `Guard.check()` in your browser with [Pyodide](https://pyodide.org) 0.29.5, then executes allowed, rewritten SQL on a demo SQLite database (Pyodide's own `sqlite3`, so no sql.js is needed). Policy is editable JSON, with a **Policy rules** panel of plain-language switches that edit it (email visible, hidden or masked; row filter per branch; `max_rows`; the `users` table), an explanation of every rejection (the rule that fired, why it protects data, how to allow it, a link to the policy reference) and a plain-language guide to each policy field. Two roles have different row filters, and presets cover the [attack gallery](../docs/ATTACK_GALLERY.md). English and Spanish.

Privacy: no analytics, no cookies, no storage. The only third-party request is Pyodide from `cdn.jsdelivr.net` (`pyodide.js` is pinned and has an SRI hash; the engine files it fetches come from the same pinned version URL). `sqlguardllm` and `sqlglot` wheels are served from `wheels/`. The share link stores `{policy, sql, role, lang}` as base64url JSON in the URL fragment, which browsers never send to a server.

## Run locally

```bash
python3 -m http.server 8765 --directory playground   # open http://localhost:8765
```

## Test

```bash
cd playground/test && npm ci && npx playwright install chromium
npm test                                   # serves ../ itself on a free port
PLAYGROUND_URL=https://jmmana.github.io/sqlguardllm/playground/ npm test   # or test a deployed copy
```

It checks that Pyodide loads, a DELETE inside a CTE is rejected, an allowed query returns only the role's rows (`OR 1 = 1` cannot widen them), the share link round-trips in a new page, the policy rules panel (email hidden, visible and masked, row filter off, `max_rows`, the `users` table, JSON edits moving the switches, share links carrying an edited policy), the explanations shown for a rejection, the language toggle, and no horizontal scroll at 360 px in dark mode. CI: `.github/workflows/playground.yml`.

## Measured load time (honest numbers)

Headless Chromium 131 on a laptop over home broadband, cold browser profile, served from `localhost` with Pyodide fetched from jsDelivr: the engine (Pyodide + sqlite3 + both wheels) was ready in about 1.5 s. The page itself (HTML, CSS, JS, about 15 kB) paints before Pyodide starts: it is loaded after first paint and a progress bar is shown. About 10 MB of WebAssembly and about 0.9 MB of wheels are downloaded on a first visit (cached after). I did not measure a throttled 3G/4G phone, so the "under 5 s on a typical connection" goal is verified only for broadband, not for slow mobile networks.

## Update the wheels

`wheels/` holds the project's own wheel and `sqlglot`. After changing `src/`, run `playground/update-wheels.sh` from the repo root and commit the result.

## Deploy (left to the owner)

Nothing here deploys itself. The site is the `playground/` folder; no build command.

- **Cloudflare Pages**: create a project from the repo, framework preset None, build command empty, build output directory `playground`.
- **GitHub Pages**: Settings > Pages > deploy from a GitHub Actions workflow that uploads `playground/` with `actions/upload-pages-artifact` and `actions/deploy-pages`, or publish the folder to a `gh-pages` branch.

Both serve `.wasm` and `.whl` correctly. After deploying, replace "after deployment" in `README.md` and `README.es.md` with the real URL.
