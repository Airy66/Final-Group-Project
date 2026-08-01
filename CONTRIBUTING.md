# Contributing to Precision Curator

Thank you for helping improve Precision Curator. The project is release-bound for a public academic SaaS demonstration, so contributions must be focused, testable, secure, and compatible with the Render deployment workflow.

By contributing, you agree that your contribution may be distributed under the repository's [MIT License](LICENSE).

## Ground rules

- Preserve working functionality and existing user data.
- Make the smallest change that completely solves the confirmed problem.
- Keep unrelated refactors, formatting changes, and UI redesigns out of the same change.
- Maintain the canonical `precision_app:app` entry point.
- Keep authentication, authorization, ownership, membership, and CSRF decisions on the server.
- Keep automated tests deterministic and independent of live external services.
- Update documentation whenever configuration, behaviour, deployment, or data semantics change.
- Never describe a check as passing unless it was actually run.

Do not introduce a new framework, duplicate application entry point, destructive migration, background system, or broad architecture rewrite without a demonstrated need, an upgrade path, and explicit review.

## Branch and review workflow

Start from the current shared deployment branch and create one focused branch:

```bash
git switch <deployment-branch>
git pull --ff-only
git switch -c fix/<short-description>
```

Recommended prefixes:

```text
feature/  fix/  security/  ui/  docs/  test/  release/
```

Do not force-push a shared branch without team agreement. Preserve unrelated changes in an already dirty worktree.

A pull request or review handoff should state:

- the problem and expected behaviour;
- changed files and affected routes/workflows;
- environment-variable or database impact;
- security and deployment impact;
- tests and manual checks actually performed;
- external services mocked or intentionally not tested;
- known limitations and follow-up work.

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

Use `.env` only for local development and never commit it. Persistent development should use `MONGO_URI` and `MONGO_DATABASE`. In-memory storage is allowed only through explicit `DEMO_MODE=true`; production must remain fail-closed with `DEMO_MODE=false`.

## Required checks

Before requesting review, run:

```bash
python -m pytest tests src/tests -q -p no:cacheprovider
python -m compileall precision_app.py services tools
python -m pip check
git diff --check
git status --short
```

Run targeted tests first while developing, then the complete offline suite. Investigate failures instead of hiding, skipping, or weakening assertions without a valid behavioural reason.

## Render deployment contract

`precision_app:app` is the only production WSGI entry point. Deployment changes must preserve the Gunicorn command, `/health` endpoint, fail-closed Atlas startup, and public HTTPS URL generation unless the replacement is reviewed and documented.

- Never run schema-destructive work or administrator creation during application import or startup.
- Keep secrets in Render Environment settings. Values declared with `sync: false` in `render.yaml` must be entered manually when added to an existing Blueprint.
- Keep `APP_BASE_URL` set to the canonical public origin; do not derive email links from the request Host header.
- Keep the Web Service and Monitor Cron Job on the same code revision and Atlas database.
- Treat the Cron Job as optional paid infrastructure. The site must describe Daily Refresh truthfully when it is not provisioned.
- Scheduled commands must be bounded, idempotent, safe to retry, and exit when complete.
- Do not write durable application state to Render's ephemeral filesystem.

A release handoff must include the Render deploy result, `/health` result, sanitized startup target, post-restart Atlas persistence check, Brevo transactional status, and any external provider limitation observed during smoke testing.

## Testing external integrations

Automated tests must not require live access to:

- MongoDB Atlas;
- eBay, Walmart, or SerpAPI;
- Gemini, OpenAI, or another model provider;
- Brevo or another email provider;
- Render or another hosting platform.

Mock the production adapter boundary and exercise the internal pipeline wherever practical:

```text
provider response
  -> parsing and validation
  -> normalization and comparability
  -> persistent workflow object
  -> server-calculated metrics
  -> rendered result and audit metadata
```

Use clearly fake credentials in tests. Never use a real API key to make a test pass, and never insert pre-rendered final rows merely to bypass production logic.

## Security requirements

Never commit or log:

- `.env` files or secret files;
- API keys or email-provider credentials;
- MongoDB connection strings or database passwords;
- user passwords, administrator passwords, or password hashes copied from production;
- reset tokens or complete password-reset URLs;
- Session cookies, CSRF tokens, or private authentication tokens;
- real user data or generated exports containing sensitive data.

Do not weaken password hashing, reset-token expiry/revocation, secure Session configuration, CSRF validation, login controls, ownership checks, membership checks, workspace-role checks, administrator checks, safe redirects, source URL validation, or safe error handling.

Roles, membership tiers, owner IDs, recipients, and record IDs supplied by a browser are untrusted. Re-read authoritative values from the authenticated Session and persistent account/record.

## Authentication and administration

Do not:

- add username-only or empty-credential login;
- accept a browser-submitted role as authority;
- create an administrator Session from request data;
- seed or reset administrators during application import/startup;
- print generated or supplied administrator passwords.

Create administrators explicitly:

```bash
python -m tools.create_admin --email administrator@example.com
```

Administrator system access is separate from customer membership. Changes must not make administrator capability depend on Basic, Premium, or Professional membership.

## MongoDB and persistence

All persistent workflows use the shared repository/database connection configured by `MONGO_URI` and `MONGO_DATABASE`.

Database changes must:

- preserve ownership and existing collection semantics;
- retain UTC storage timestamps and the established user-facing timezone;
- use bounded queries and appropriate indexes;
- keep test and production databases separate;
- avoid destructive startup migrations;
- fail safely without pretending an unsuccessful write succeeded.

Do not add another `MongoClient`, hard-code localhost, silently fall back to memory, create a temporary Session account after registration failure, or return one user's records to another user.

## Marketplace and comparison integrity

Marketplace responses change over time. Preserve truthful distinctions among success with results, success with no results, no comparable results, unusable prices, timeout, quota/rate limit, authentication failure, bot blocking, and provider error.

Exclude records that are not safe comparable prices, including:

- missing, zero, or negative prices;
- instalments, subscriptions, deposits, or contract-only offers;
- accessories and incompatible product models;
- malformed records and unsafe source URLs.

Do not mix historical evidence into a new live Search Run without explicit provenance. Saved evidence must retain source, collection time, owner, and Search Run lineage.

Platform conclusions require one comparable product configuration, one condition, and at least two marketplaces. Qualified platform ranking uses platform median price; a single unusually low listing must not determine the winning marketplace. Descriptive cards may continue to show lowest, average, highest, and spread for the current result set.

## Analysis, evidence, and exports

An Analysis Run must use its frozen included record IDs. Do not silently rebuild historical analysis from a later Search Run.

Charts, cards, tables, reports, and exports must describe the same selected scope. Export changes must preserve:

- user ownership and frozen record IDs;
- safe CSV cell handling;
- meaningful, stable schemas;
- source provenance and collection timestamps;
- the established user-facing timezone.

Keep lifecycle actions such as Archive and Delete separate from Export menus.

## Watchlist, forecast, and alert integrity

A Monitor is a persistent frozen scope, not an alias for the latest unrelated Search Run.

Scheduled refresh must:

- respect owner, active/paused state, and configured due time;
- prevent duplicate daily Snapshots and overlapping refresh claims;
- reuse the same normalization and comparability rules;
- preserve the last valid Snapshot when collection fails;
- avoid zero-price or empty failure Snapshots;
- avoid automatically generating an AI forecast.

Forecast changes must preserve immutable Forecast Cycles, source Snapshot lineage, deterministic benchmark, optional AI prediction, later validation Snapshot, and accurate error metrics.

Price Alerts evaluate existing valid Snapshots. They must not launch an additional marketplace search, send duplicate notifications, bypass cooldown/scope quality rules, or use a recipient supplied by the browser. Email failure must not delete the Snapshot.

## Email delivery

All welcome, password-reset, test-alert, and price-alert messages must use the shared mail service. Do not create separate transports for each email type.

The deployment uses Brevo Transactional Email over HTTPS. SMTP transports and port settings are not part of the supported configuration. `BREVO_API_KEY` must contain an API v3 key, not an SMTP key; the Brevo Transactional platform and sender must both be active.

Email changes must keep `services/email_service.py` as the single transport boundary, preserve the existing text/HTML and audit semantics, use a bounded timeout, and keep `APP_BASE_URL` as the only source for public application links. Mock Brevo HTTP calls in automated tests. Never log the API key, provider response body, raw reset token, or complete reset URL.

Production emails must never contain localhost, passwords, API keys, database URIs, Session tokens, or raw provider payloads. Release smoke tests should check Brevo's Transactional Logs for accepted and delivered events and must not paste reset links or provider credentials into issues or review notes.

## UI and accessibility

UI changes should preserve the established product design system and remain usable at desktop, tablet, and approximately 375-416px mobile widths.

- Keep one clear primary action for each workflow state.
- Do not use colour as the only status indicator.
- Preserve keyboard focus, labels, accessible names, and meaningful button behaviour.
- Avoid fake controls, dead overflow menus, and buttons that only jump without performing their stated action.
- Keep tables horizontally usable and navigation reachable on touch devices.
- Do not redesign unrelated pages as part of a focused fix.

## Documentation and configuration

Update README, `.env.example`, deployment files, tests, and operational notes when their contracts change. Documentation must use the implemented variable names and must distinguish available functionality from planned work.

Do not claim complete coverage, guaranteed lowest prices, guaranteed forecasts, payment processing, perfect security, commercial production certification, or a provider integration that has not been implemented and tested.

## Commit messages

Use concise, descriptive messages:

```text
fix: keep mobile workspace navigation scrollable
feat: add Brevo transactional email adapter
security: fail closed when Atlas is unavailable
docs: finalize Render deployment guide
test: cover qualified platform median comparison
```

Avoid messages such as `update files`, `fix stuff`, or `final changes`.

## Release discipline

For every release-bound change:

1. inspect the existing implementation and dirty worktree;
2. make the smallest safe change;
3. run targeted tests;
4. run the complete offline suite;
5. run compilation and diff checks;
6. document environment and deployment impact;
7. preserve a recoverable stable commit.
8. deploy to Render and run the post-deploy smoke checks when the change affects runtime behaviour or configuration.

External marketplace, AI, email, database, and hosting services remain third-party dependencies. Application safeguards reduce risk but do not eliminate service failure or guarantee every security property.
