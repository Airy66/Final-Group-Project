# Precision Curator

Precision Curator is an academic Flask prototype for collecting, comparing, monitoring, and analysing marketplace price records. It provides role-aware workspaces, evidence preservation, Search Run history, comparison sets, analytics, Watchlist snapshots, deterministic forecasts, and controlled AI-assisted explanations.

The canonical production WSGI entry point is `precision_app:app`. The root `app.py` exists only as a compatibility import.

## 1. Project overview

The application demonstrates a traceable price-intelligence workflow rather than claiming complete marketplace coverage or commercial production readiness. Search results retain source and collection metadata so users can distinguish marketplace evidence, deterministic calculations, mock data, and AI assistance.

## 2. Main features

- authenticated Search Runs with source diagnostics and saved history
- comparable-product filtering and comparison sets
- evidence and research-record preservation
- analytics dashboards and CSV/XLSX/report exports
- Watchlist snapshots, baseline forecasts, and later validation
- password-reset email workflow and explicit administrator bootstrap
- CSRF, ownership, role, membership, session, and production configuration controls

## 3. Roles and workspaces

- **Consumer**: search, compare, save evidence, monitor prices, and view permitted analytics.
- **Retailer**: market monitoring and retailer-oriented summaries.
- **Researcher**: research records, advanced analytics, audit views, and research exports.
- **Administrator**: account, role, membership, test-data, and audit administration.

Each account has exactly one assigned workspace role. An administrator may replace that role when the user's responsibilities change.

## 4. Membership tiers

- **Basic**: product search and comparison.
- **Premium**: saved evidence/research, Watchlist, analytics, AI-assisted summaries, forecasts, later-snapshot validation, and standard exports.
- **Professional**: everything in Premium plus advanced analytics, audit/log access, provenance workflows, and research/report packages.

Membership upgrades in this repository are demonstration controls, not payment processing. They must remain disabled in production.

## 5. Data sources and provenance

- **eBay**: official eBay API integration when credentials are configured.
- **Walmart**: retrieval through a configured third-party marketplace service (SerpAPI), not direct official Walmart API access.
- **MongoDB**: persistent Search Runs, normalized records, evidence, comparisons, analytics, Watchlist state, users, and audit events.
- **Mock/demo records**: deterministic and explicitly labelled for offline tests or prepared demonstrations.
- **AI**: optional assistance and explanation. AI output is not an authoritative marketplace source and does not replace stored source records.

Coverage depends on provider availability, configured credentials, query quality, and returned listings. It is not complete global market coverage.

Watchlist snapshots exclude source records identified as instalments, subscriptions, deposits, contracts, or other non-comparable price types. New Forecast Cycles use a deterministic benchmark plus optional Gemini assistance. The next successful manual or scheduled snapshot collected after forecast creation validates the pending cycle automatically.

## 6. Architecture summary

```text
Browser -> Flask routes/templates -> service adapters -> MongoDB
                               |-> eBay official API
                               |-> SerpAPI marketplace retrieval
                               |-> optional Gemini/OpenAI assistance
```

`precision_app.py` owns the Flask application and workflows. `services/` contains persistence, mail, provider, category, and runtime-configuration modules. Runtime charts and uploaded avatars are written beneath `static/` but are not source assets.

## 7. Technology stack

- Python 3.11–3.13 (deployment reference: Python 3.13.6)
- Flask and Gunicorn
- MongoDB/PyMongo
- Jinja templates and JavaScript
- pandas, NumPy, SciPy, matplotlib, Plotly, and openpyxl
- pytest for offline regression coverage

## 8. Repository structure

```text
precision_app.py          canonical Flask application
app.py                    compatibility import only
services/                 database, mail, AI and provider adapters
src/ecommerce_price_monitor/ bundled analysis/collector package
templates/                Jinja pages
static/                   required source assets plus ignored runtime folders
tools/                    administrator and optional development utilities
tests/, src/tests/        application and package tests
render.yaml               Render staging blueprint
requirements.txt          direct runtime dependencies
requirements-dev.txt      test/development dependencies
.env.example              placeholder-only environment template
docs/                     release and legacy inventories
```

## 9. Prerequisites

- Python 3.11–3.13
- MongoDB for persistent local or staging operation
- provider credentials only for the providers being exercised

Linux is expected for Gunicorn deployment. Gunicorn is not used as a Windows development server.

## 10. Local installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Edit the local `.env` without committing it. Production secrets belong in the hosting platform's environment settings.

## 11. Environment configuration

See `.env.example` for every supported setting. Important production values are:

- `APP_ENV=production`
- a unique `FLASK_SECRET_KEY` of at least 32 characters
- public HTTPS `APP_BASE_URL`
- authenticated `MONGODB_URI` and intended `MONGODB_DATABASE`
- `ALLOW_MEMORY_FALLBACK=false`
- all demo and membership-upgrade switches set to `false`

Provider and SMTP secrets are optional only when those capabilities are unused. Never copy real secrets into `.env.example`, `render.yaml`, README, tests, or screenshots.

## 12. MongoDB setup

For local development, use local MongoDB or explicitly set `ALLOW_MEMORY_FALLBACK=true`. For staging/production, use MongoDB Atlas or equivalent persistent MongoDB, restrict network/database users appropriately, store the authenticated URI only as an environment secret, and keep memory fallback disabled.

Local MongoDB data does not automatically appear in Atlas. Prepare a clean staging dataset rather than copying all prototype/test history.

## 13. Demo and mock setup

Offline tests replace providers with deterministic doubles. Optional utilities under `tools/` may prepare labelled demo data; review their help/source before running them because seed tools can write database records or local documentation assets.

Example:

```powershell
python -m tools.seed_demo_snapshots --user demo@example.com
```

Do not seed at application startup. Create intentional demo users, roles, membership examples, Search Runs, and Watchlist records only.

## 14. Running locally

```powershell
$env:APP_ENV="development"
python -m flask --app precision_app:app run
```

The canonical module does not start a development server when imported. `app:app` remains import-compatible, but new commands and deployment configuration should use `precision_app:app`.

## 15. Creating the first administrator

Use a password of at least 12 characters that is not a public/default password. The command does not print the password, does not overwrite an existing administrator by default, and requires explicit `--reset-password` for replacement.

```powershell
$env:PRECISION_ADMIN_PASSWORD="use-a-strong-unique-password"
python -m tools.create_admin --email administrator@example.com
Remove-Item Env:PRECISION_ADMIN_PASSWORD
```

To intentionally reset an existing administrator:

```powershell
python -m tools.create_admin --email administrator@example.com --reset-password
```

Production startup never creates or resets an administrator automatically.

## 16. Running tests

The full offline command is:

```powershell
$env:APP_ENV="development"
$env:USE_MOCK_SCRAPER="true"
$env:ALLOW_DEMO_DATA="true"
$env:ALLOW_MEMORY_FALLBACK="true"
python -m pytest tests src/tests -q -p no:cacheprovider
```

Provider check utilities in `tools/` are manual, may use network access, and are not part of the offline suite.

Coverage is a separate diagnostic run so the normal regression command remains
fast and its result is not confused with the bundled legacy package. Measure the
canonical production application and services with:

```powershell
python -m pytest tests src/tests -q -p no:cacheprovider --cov=precision_app --cov=services --cov-report=term-missing --cov-report=html
```

The coverage headline intentionally excludes tests, development tools, virtual
environments, generated files, and the legacy/experimental collector package.
No minimum percentage is asserted; report the measured result as generated.

## 17. Production deployment

Install `requirements.txt` and start the canonical WSGI app with:

```text
gunicorn precision_app:app --workers 2 --timeout 120 --access-logfile -
```

Do not use Flask debug mode or reload in production. Verify `/health`, authentication, password reset, provider availability, and a clean staging dataset before any public release.

## 18. Render configuration

`render.yaml` defines:

- build: `python -m pip install -r requirements.txt`
- start: the conservative two-worker Gunicorn command above
- health check: `GET /health`
- Python expectation: 3.13.6
- production-safe demo and memory-fallback values
- secret/environment prompts without embedded credentials

Render's filesystem is ephemeral. MongoDB Atlas is required for persistent application data. Dynamically uploaded avatars and runtime PNG charts may disappear after deploy/restart; use preloaded source assets or external object storage for durable production uploads. Object-storage integration is outside this academic batch.

### Daily Watchlist Monitor Cron preparation

The blueprint also prepares, but does not deploy automatically from this repository, a daily Cron command:

```text
python -m tools.refresh_due_monitors --limit 5
```

The Render UTC schedule `0 0 * * *` corresponds to 08:00 in Asia/Singapore. The Web Service and Cron Job must use the same repository version, MongoDB Atlas database, provider credentials, and application settings. Keep every secret in Render environment settings; never place credentials in `render.yaml` or documentation. No scheduler runs inside Flask or Gunicorn.

For a read-only local due-list check:

```powershell
python -m tools.refresh_due_monitors --dry-run
```

For a controlled development/administrator test after explicitly enabling `MONITOR_DAILY_REFRESH_ENABLED`, use:

```powershell
python -m tools.refresh_due_monitors --monitor-id <ID> --force --limit 1
```

The forced command may call configured marketplace providers and mutate the selected Monitor. It is a local CLI path only and is not exposed to unauthenticated web users. Existing Monitors remain disabled until a user explicitly enables daily refresh.

## 19. Password-reset email

Local `EMAIL_MODE=console` avoids SMTP and is suitable only for development. Staging should use `EMAIL_MODE=smtp` with `MAIL_HOST`, port, username, password, sender address/name, TLS/SSL, timeout, public `APP_BASE_URL`, and token TTL configured through environment variables. Test delivery without exposing reset tokens in logs or screenshots.

## 20. Security controls

- strong production secret and fail-closed production configuration
- secure, HttpOnly, SameSite session cookies and bounded lifetime
- password hashing, reset-token hashing/expiry/revocation, and login failure controls
- central CSRF validation for HTML and JSON mutations
- POST-only mutations and safe internal redirect validation
- authentication, active-role, membership, ownership, and administrator checks
- safe upload validation and audit events without credential/token contents

## 21. Known limitations

- academic prototype, not independently security-certified or commercially production-ready
- provider availability and quotas affect live results
- Walmart data uses a third-party retrieval service
- forecasts are experimental and accuracy is not guaranteed
- no payment system, distributed task queue, Redis, or object storage
- Render local files are ephemeral
- legacy collectors remain for package/manual review and are not claims of active source coverage

## 22. Data provenance statement

Every demonstration should distinguish official eBay API records, third-party Walmart retrieval, stored MongoDB records, deterministic mock/demo data, calculated analytics, and optional AI text. Historical prototype/test records should not be presented as current marketplace observations.

## 23. Academic prototype disclaimer

Precision Curator is supplied for academic evaluation and controlled staging demonstration. It does not provide purchasing, investment, or commercial pricing guarantees. Marketplace names and trademarks belong to their owners.

## 24. Troubleshooting

- **Production refuses to start:** check the strong secret, disabled demo flags, and `ALLOW_MEMORY_FALLBACK=false`.
- **MongoDB unavailable:** verify Atlas network access, database user, URI encoding, and `MONGODB_DATABASE`.
- **No provider results:** verify credentials, provider enablement, timeout, quota, and source diagnostics.
- **Password email missing:** verify SMTP mode, sender, TLS/SSL selection, base URL, and provider logs without sharing secrets.
- **Avatar/chart disappeared on Render:** the local filesystem is ephemeral; restore a source asset or use external durable storage.
- **Gunicorn fails on Windows:** use Flask locally; Gunicorn is the Linux production server.

## 25. Submission contents

Include canonical source, compatibility `app.py`, requirements files, `pyproject.toml`, services, templates, required static assets, tools, tests, README, `.env.example`, `render.yaml`, and release/legacy documentation.

Exclude `.env`, credentials, caches, coverage output, virtual environments, local database files, generated charts/uploads, temporary CSV/HTML exports, personal IDE state, and real user data. See `docs/RELEASE_CHECKLIST.md` before packaging.
