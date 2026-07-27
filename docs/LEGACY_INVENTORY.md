# Legacy and development inventory

## Compatibility and routes

| Item | Classification | Decision |
| --- | --- | --- |
| `app.py` | Academic compatibility entry | Retained as a minimal import of `precision_app.app`; no duplicate Flask instance or server startup. |
| `/search-legacy` | Authenticated compatibility search | Retained for existing tests/demonstrations. Mutations require POST and central CSRF; GET cannot execute a search. |
| `/search-mongo` | Authenticated MongoDB/demo compatibility search | Retained for controlled local demonstrations and regression coverage. Mutations require POST and central CSRF. |
| `/debug/walmart-records` | Development source diagnostic | Retained because it is role controlled, read-only, and returns 404 unless debug/search diagnostics are explicitly enabled; not a public production feature. |
| `/api/admin/serpapi/account` | Administrator provider diagnostic | Retained with administrator authorization; it is not part of health checking. |

The canonical `/search` and Search Run workflow remains unchanged. The retained compatibility routes do not bypass authentication, CSRF, role, membership, or ownership enforcement.

## Development utilities

The following are optional and never imported during normal application startup:

- deterministic demo/documentation generators
- MongoDB/demo snapshot seed tools
- manual eBay, Gemini, and MongoDB connectivity checks
- legacy bundled collector/system verification utilities

Manual provider checks may make real network calls and must never be included in the offline test command. Legacy marketplace collectors under `src/ecommerce_price_monitor/collectors/` are retained for manual review because the bundled analysis package still imports them; retention is not a claim that every collector is active in Precision Curator production.

## Generated-file classification

| Candidate | Classification |
| --- | --- |
| `static/charts/*.png` | GENERATE AT RUNTIME; removed from tracked source, directory retained with `.gitkeep`. |
| `static/uploads/avatars/*` | GENERATE AT RUNTIME; removed personal/runtime upload, directory retained with `.gitkeep`. |
| `login_rendered.html`, root result/test CSV files | DELETE FROM REPOSITORY; temporary output. |
| `package.json`, `package-lock.json` | DELETE FROM REPOSITORY; no Node build pipeline exists and ECharts is a retained vendored source asset. |
| `__pycache__`, `.pytest_cache`, `pytest-cache-files-*`, `htmlcov`, `.coverage` | DELETE/IGNORE; runtime test/cache output. |
| `docs/images/`, `static/vendor/`, favicon and UI assets | KEEP AS SOURCE ASSET. |
| provider/manual checks and seed scripts | MOVE TO DEVELOPMENT TOOLS. |
| bundled old collectors | UNCERTAIN — RETAIN for manual review. |
