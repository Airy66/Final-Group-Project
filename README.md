# Precision Curator

Precision Curator is a SaaS-style marketplace price-intelligence application built with Flask and MongoDB. It turns marketplace listings into comparable price records, saved evidence, monitored snapshots, analytics, forecasts, and traceable research outputs.

This repository is developed for academic evaluation and controlled public demonstration. It is not a payment-enabled commercial service and does not guarantee marketplace coverage, future prices, or the lowest available offer.

## Product capabilities

- Multi-source product search using eBay and a configured Walmart retrieval provider.
- Product normalization, comparability checks, configuration and condition filtering.
- Search Run history and persistent marketplace records.
- Like-for-like comparison sets and platform-level price interpretation.
- Saved evidence and research packages with source provenance.
- Retailer Watchlist monitors, scheduled snapshots, price trends, and alerts.
- Deterministic forecast benchmarks, optional Gemini assistance, and later-snapshot validation.
- Analytics dashboards and CSV, XLSX, and report exports.
- Password reset, welcome email, administrator tools, role controls, and audit logs.
- Responsive Consumer, Retailer / Reseller, Researcher, and Administrator workspaces.

## Roles and membership

Every account has exactly one workspace role:

| Role | Primary workflow |
| --- | --- |
| Consumer | Search, compare, save, and monitor products. |
| Retailer / Reseller | Monitor competitor prices, trends, alerts, and forecast cycles. |
| Researcher | Preserve evidence, review provenance, analyse records, and export research. |
| Administrator | Manage users, membership, source status, evidence history, and activity logs. |

Membership controls feature access independently of workspace role:

- **Basic:** product search and comparison.
- **Premium:** saved evidence, Watchlist, analytics, AI-assisted explanations and forecasts, validation, and standard exports.
- **Professional:** Premium capabilities plus source audit, activity logs, advanced provenance, and research/report workflows.

Membership changes are administrative demonstration controls. This repository contains no billing or payment processing.

## Price and forecast semantics

Precision Curator deliberately separates descriptive metrics from decision signals:

- Lowest, average, highest, and spread cards describe the current comparable result set.
- A platform recommendation is calculated only for a qualified like-for-like scope: one product configuration, one condition, and at least two marketplaces.
- Qualified platform comparison uses each platform's median comparable price so one abnormal listing does not decide the result.
- Watchlist trend and alert calculations retain the monitor's comparable average-price series and apply snapshot-quality and scope-consistency checks.
- Daily refresh collects a new monitor snapshot. It does not automatically generate an AI forecast.
- A pending Forecast Cycle is validated by the next eligible snapshot collected after that forecast.
- AI text explains server-calculated facts; it is not marketplace evidence and cannot replace source records.

## Data sources and provenance

| Source | Usage |
| --- | --- |
| eBay | Official eBay API when credentials are configured. |
| Walmart | Marketplace retrieval through configured SerpAPI integration; not an official Walmart API. |
| MongoDB Atlas | Persistent users, Search Runs, results, evidence, comparisons, analytics, monitors, snapshots, forecasts, and audit data. |
| Gemini | Optional market explanation and forecast assistance grounded in the selected records. |
| Demo records | Deterministic, explicitly labelled data available only in intentional demo/test mode. |

Provider availability, quotas, query quality, and the listings returned at collection time determine coverage.

## Architecture

```text
Browser
  -> Flask routes and Jinja templates
      -> MongoRepository -> MongoDB Atlas
      -> eBay / SerpAPI provider adapters
      -> Brevo Transactional Email HTTPS API
      -> Gemini assistance

Render Web Service -> Gunicorn -> precision_app:app
Render Cron Job    -> tools.refresh_due_monitors -> the same Atlas database
```

`precision_app.py` is the canonical application and WSGI entry point. Root-level `app.py` is compatibility-only and must not become a second application.

## Technology

- Python 3.11-3.13; Render reference version: Python 3.13.6
- Flask, Gunicorn, Jinja, and browser JavaScript
- MongoDB Atlas and PyMongo
- pandas, NumPy, SciPy, matplotlib, Plotly, and openpyxl
- Gemini and OpenAI SDK integrations where configured
- pytest for deterministic offline regression coverage

## Repository layout

```text
precision_app.py       Canonical Flask application and workflows
app.py                 Compatibility import
services/              Database, mail, provider, AI, category, and config services
templates/             Product and public Jinja templates
static/                Versioned assets and ignored runtime output directories
tools/                 Administrator, maintenance, and monitor-refresh commands
tests/                  Flask application regression tests
src/                    Bundled analysis package and package tests
docs/                   Release, API, and legacy notes
render.yaml             Render Web Service and monitor Cron blueprint
.env.example            Placeholder-only environment reference
```

## Local setup

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python -m flask --app precision_app:app run
```

macOS or Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -m flask --app precision_app:app run
```

Never commit `.env`. Development may use `DEMO_MODE=true` for explicit in-memory test/demo operation. Normal development with persistent data and every production deployment must use MongoDB.

## Environment configuration

`.env.example` is the canonical variable reference. The most important settings are:

| Variable | Production requirement |
| --- | --- |
| `APP_ENV` | Set to `production`. |
| `FLASK_SECRET_KEY` | Unique random value of at least 32 characters. |
| `APP_BASE_URL` | Public HTTPS Render or custom-domain URL. |
| `DEMO_MODE` | Must be `false`. Memory fallback is allowed only when explicitly `true`. |
| `MONGO_URI` | Authenticated MongoDB Atlas URI stored as a secret. |
| `MONGO_DATABASE` | `precision_curator_production` unless intentionally changed. |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | Required for live eBay collection. |
| `SERPAPI_API_KEY` | Required for configured Walmart retrieval. |
| `GEMINI_API_KEY` | Required for Gemini-assisted explanations and forecasts. |
| `MAIL_PROVIDER` | Set to `brevo_api`. |
| `MAIL_ENABLED` | Set to `true` to deliver transactional email. |
| `BREVO_API_KEY` | Brevo API key stored only as a Render secret. |
| `BREVO_SENDER_EMAIL` | Sender address verified in Brevo. |
| `BREVO_SENDER_NAME` | Display name, normally `Precision Curator`. |

The application uses `MONGO_URI` and `MONGO_DATABASE`. The old names `MONGODB_URI` and `MONGODB_DATABASE` are not the production configuration contract.

At startup the database service loads the configured URI, executes `client.admin.command("ping")`, and logs only the Atlas hostname and database name. It never logs the URI, username, password, API keys, or Flask secret.

## MongoDB Atlas persistence

Production is fail-closed:

- Atlas connection failure must not silently create users or evidence in memory.
- `DEMO_MODE=false` disables in-memory fallback.
- Registration failure must not create a temporary Session account.
- All application modules use the shared repository/database connection.

Before release, verify registration, hashed password storage, login after restart, saved evidence, Watchlist snapshots, and persistence after another restart while local MongoDB remains stopped.

## Administrator bootstrap

Create the first administrator explicitly. The command does not print the password or overwrite an existing administrator unless requested.

```powershell
$env:PRECISION_ADMIN_PASSWORD="use-a-strong-unique-password"
python -m tools.create_admin --email administrator@example.com
Remove-Item Env:PRECISION_ADMIN_PASSWORD
```

Intentional password replacement:

```powershell
python -m tools.create_admin --email administrator@example.com --reset-password
```

Administrators have system access rather than a customer membership tier. Production startup never creates an administrator automatically.

## Testing

Run the complete offline suite:

```powershell
$env:APP_ENV="development"
$env:DEMO_MODE="true"
$env:USE_MOCK_SCRAPER="true"
$env:ALLOW_DEMO_DATA="true"
python -m pytest tests src/tests -q -p no:cacheprovider
```

Additional release checks:

```powershell
python -m compileall precision_app.py services tools
python -m pip check
git diff --check
```

Automated tests must use deterministic substitutes and must not require live Atlas, marketplace, AI, email, or hosting services.

## Render deployment

`render.yaml` defines the Web Service, `/health` check, and the daily monitor refresh command. Install runtime dependencies and start the canonical application with:

```text
gunicorn precision_app:app --workers 2 --timeout 120 --access-logfile -
```

Set secret values in Render environment settings, not in `render.yaml` or source control. At minimum configure the application URL, Flask secret, Atlas URI, marketplace credentials, and AI credentials used by the deployment.

Render's filesystem is ephemeral. MongoDB Atlas provides durable application records, but uploaded avatars and generated local chart files can disappear after a deploy or restart. Use source-controlled assets or external object storage for any file that must persist.

### Scheduled Watchlist refresh

The scheduled command is:

```text
python -m tools.refresh_due_monitors --limit 5
```

The blueprint schedule `0 0 * * *` runs at 00:00 UTC, which is 08:00 Asia/Singapore. The Web Service and Cron Job must use the same code revision, `MONGO_URI`, `MONGO_DATABASE`, and marketplace credentials. Flask and Gunicorn do not run an internal scheduler.

Read-only local inspection:

```powershell
python -m tools.refresh_due_monitors --dry-run
```

Existing Monitors remain disabled until a user enables daily refresh.

## Email delivery with Brevo

Welcome, password-reset, test-alert, and price-alert messages use the shared `services/email_service.py` adapter and Brevo Transactional Email over HTTPS. The application does not use SMTP ports 25, 465, or 587.

Set `MAIL_PROVIDER=brevo_api`, `MAIL_ENABLED=true`, `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`, `BREVO_SENDER_NAME`, and `APP_BASE_URL` on both the Render Web Service and Cron Job. The sender address must be verified in Brevo. For production, `APP_BASE_URL` must be the public HTTPS application origin, not localhost.

Registration is committed to MongoDB before the welcome message is attempted. A provider failure is recorded safely and does not roll back the account. Password-reset delivery failures revoke the newly created reset record while preserving generic account-enumeration-safe responses. API calls have a bounded timeout and provider response bodies are not copied into logs.

Automated tests mock the HTTPS boundary and never contact Brevo. Before release, perform one controlled welcome email, password reset, and price-alert delivery with a verified sender, then inspect the Brevo transactional log without copying credentials or reset links into project records.

Real Brevo API key values must never appear in `.env.example`, `render.yaml`, documentation, test fixtures, screenshots, or Git history.

## Security baseline

- Password hashing and hashed, expiring, revocable reset tokens.
- Secure production Session cookies and bounded Session lifetime.
- CSRF validation for HTML forms and JSON mutations.
- Server-side role, membership, ownership, and administrator checks.
- Safe internal redirects, upload validation, and source-link validation.
- Fail-closed production database and secret configuration.
- Audit events that exclude passwords, API keys, connection strings, and reset tokens.

See [SECURITY_NOTES.md](SECURITY_NOTES.md) for implementation notes and responsible reporting guidance.

## Known limitations

- Academic SaaS project, not independently security-certified.
- No payment gateway, billing engine, Redis, Celery, or distributed task queue.
- Provider availability, quotas, and changing marketplace responses affect coverage.
- Walmart retrieval is third-party rather than an official Walmart API.
- Forecasts are experimental decision support and are not guarantees.
- Render runtime files are ephemeral without external object storage.
- Email deliverability still depends on Brevo sender/domain verification, account status, recipient policy, and provider availability.

## Release checklist

Before presenting or publishing the application:

- [ ] Production starts with `DEMO_MODE=false` and a strong Flask secret.
- [ ] Startup logs show the expected Atlas hostname and `precision_curator_production` without credentials.
- [ ] User, Evidence, comparison, Analysis, Monitor, Snapshot, and Forecast data persist across restarts.
- [ ] eBay and Walmart source statuses truthfully distinguish no results from provider failure.
- [ ] The Render Cron Job can collect one eligible due Monitor without duplicate snapshots.
- [ ] Brevo sender/domain is verified and controlled welcome/password-reset/alert deliveries succeed.
- [ ] `/health`, authentication, ownership, membership, exports, and administrator access are tested.
- [ ] `.env`, real customer data, runtime uploads, reports, caches, and secrets are absent from Git.

See [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) for the operational checklist.

## Contributing and license

Contributions should follow [CONTRIBUTING.md](CONTRIBUTING.md). Precision Curator is distributed under the [MIT License](LICENSE).

Marketplace names and trademarks belong to their respective owners. The software is provided without warranty and does not provide purchasing, investment, or commercial pricing guarantees.
