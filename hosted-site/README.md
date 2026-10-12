# Integrated research dashboard and News

This is the Regime Router / Sector Forecast web source with the News worker routes and static UI integrated under `/news/`. It is separate from the Python local dashboard in `../web/`.

The source includes `app/`, `lib/`, `db/`, `drizzle/`, `worker/`, static assets, and the required `build/sites-vite-plugin.ts`. Generated output, deployed resource identifiers, real snapshots, preview images, and secrets are excluded. `hosting.example.json` describes a local `DB` binding only. Configure a real hosting environment separately before deployment.

Node >=22.13 is required. `npm ci` installs the pinned dependencies; `npm run build` produces local output and may download fonts. `npm test` builds before its rendered HTML suite. This snapshot passed an offline cache-only install (`npm ci --offline --ignore-scripts --no-audit --no-fund`), `npm run build`, and all 36 tests from `node --test tests/*.test.mjs` on Node 24.18.1, with external sockets, DNS, and fetch blocked. No remote migration, live-service validation, or deployment was performed. A fresh uncached installation was not tested. Standalone news tests use synthetic data:

```sh
node tests/news.test.mjs
node tests/news-data.test.mjs
```

See the [main README](../README.md) and [hosting notes](../HOSTING_RUNBOOK.md) for configuration and validation limits. Keep runtime secrets in an ignored `.dev.vars` file or the hosting environment. Existing code and third-party notices remain; this snapshot does not add a license grant for external code or data.
