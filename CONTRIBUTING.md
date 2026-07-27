# Contributing to Precision Curator

Thank you for contributing to Precision Curator.

Precision Curator is an academic Flask and MongoDB platform for marketplace search, price comparison, evidence preservation, analytics, monitoring, forecast validation, and price-alert delivery.

---

## 1. Development Principles

Contributions should:

- preserve existing completed functionality;
- make focused and reviewable changes;
- avoid unrelated changes in the same branch;
- keep the application runnable;
- preserve existing URLs and endpoint behaviour where reasonably possible;
- maintain server-side authentication and authorization;
- retain deterministic offline tests;
- avoid unnecessary architectural rewrites close to release.

Do not create empty wrapper modules only to make the project appear more structured.

Do not redesign the complete user interface unless a separately approved UI task requires it.

---

## 2. Branch Workflow

Use the current deployment branch as the base for release-bound work:

```bash
git switch release/final-deployment
git pull
```

Create a focused branch:

```bash
git switch -c feature/<short-name>
```

Recommended prefixes:

```text
feature/
fix/
security/
ui/
docs/
test/
release/
```

Examples:

```text
feature/scheduled-monitor-refresh
fix/walmart-result-status
security/session-hardening
ui/watchlist-layout
docs/deployment-guide
test/provider-fixtures
```

Use one branch for one clear feature, fix, test, or documentation task.

Do not force-push shared deployment branches without team agreement.

---

## 3. Local Setup

Create and activate a virtual environment.

Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

macOS or Linux:

```bash
python -m venv venv
source venv/bin/activate
```

Install runtime and development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

Copy the environment template:

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

macOS or Linux:

```bash
cp .env.example .env
```

Never commit the real `.env` file.

---

## 4. Running Locally

Use the canonical Flask application:

```bash
python -m flask --app precision_app:app run --host 127.0.0.1 --port 5000
```

Open:

```text
http://127.0.0.1:5000
```

`precision_app:app` is the canonical application entry point.

`app.py` is retained only as a compatibility import and must not become a second independent Flask application.

---

## 5. Required Checks Before Committing

Run the full offline test suite:

```bash
python -m pytest tests src/tests -q -p no:cacheprovider
```

Run compilation and dependency checks:

```bash
python -m compileall precision_app.py services tools
python -m pip check
git diff --check
```

Check the working tree:

```bash
git status
```

Do not report tests as passing unless they were actually executed successfully.

Do not ignore unexplained test failures.

---

## 6. External-Service Testing

Automated tests must not depend on live availability of:

- eBay;
- Walmart;
- SerpAPI;
- AI providers;
- SMTP;
- MongoDB Atlas;
- other external marketplace or network services.

Use deterministic fixtures and mocks.

Mock the actual current external-provider boundary rather than an obsolete import path.

Tests should exercise the internal production pipeline where practical:

```text
mocked provider response
→ parsing
→ normalization
→ price validation
→ comparability
→ Search Run IDs
→ displayed records
→ KPIs and audit information
```

Do not insert final rendered rows directly merely to make a test pass.

Use clearly fake test-only credentials where constructor validation requires a non-empty value.

Never use a real API key to make an automated test pass.

---

## 7. Security Requirements

Do not commit:

- `.env` files;
- API keys;
- MongoDB connection strings;
- SMTP credentials;
- passwords;
- password-reset tokens;
- session cookies;
- administrator passwords;
- private authentication tokens;
- generated exports containing sensitive data;
- local virtual environments.

Do not weaken:

- email-and-password authentication;
- password hashing;
- server-side authorization;
- membership checks;
- workspace-role checks;
- object ownership checks;
- CSRF protection;
- secure session configuration;
- production secret validation;
- development-route restrictions;
- safe error handling;
- source-link validation.

Do not trust roles, membership tiers, owner IDs, or recipient email addresses submitted by the browser.

Sensitive decisions must use persisted server-side account and database values.

---

## 8. Authentication and Administration

Do not:

- add username-only login;
- accept empty credentials;
- trust a submitted role;
- create a browser-controlled administrator session;
- seed administrators during module import;
- reset administrator passwords during application startup;
- print administrator passwords.

Administrators must be created explicitly:

```bash
python -m tools.create_admin --email administrator@example.com
```

A password must be supplied securely through the supported administrator tool workflow.

---

## 9. Database Changes

Production uses MongoDB.

Database changes must:

- preserve user ownership;
- preserve existing collection semantics;
- avoid unbounded queries;
- use reasonable query limits;
- retain UTC database timestamps;
- render user-facing timestamps in Asia/Singapore where required;
- avoid destructive startup migrations;
- keep test and production databases separate.

Do not:

- seed users during module import;
- silently switch production to memory fallback;
- write production records during ordinary application import;
- delete existing evidence without explicit authorization;
- return one user’s saved records to another user.

Keep the existing repository layer unless a clear maintenance reason justifies a split.

---

## 10. Search and Marketplace Data Integrity

External marketplace responses may change between collection runs.

Do not force the following records into price calculations:

- zero-price records;
- negative-price records;
- missing-price records;
- installment-only records;
- accessories;
- incompatible product models;
- malformed records;
- records with unsafe source URLs.

A successful provider request does not automatically mean that usable comparable listings exist.

Preserve truthful distinctions among:

- provider success with results;
- provider success with no results;
- provider success with no comparable results;
- provider success with unusable price data;
- timeout;
- rate limit;
- authentication failure;
- provider error.

Do not label successful zero-result responses as provider unavailable.

Do not mix historical Walmart evidence into a new live eBay Search Run.

Saved evidence must remain clearly timestamped and separately scoped.

---

## 11. Analysis and Export Integrity

Analyses must use the exact frozen included result IDs stored for that Analysis Run.

Do not silently rebuild an old Analysis from a different Search Run.

Charts, KPI cards, tables, reports, and exports must use the same selected scope.

Export changes must preserve:

- current user ownership;
- current frozen record IDs;
- safe CSV cell handling;
- meaningful table schemas;
- Asia/Singapore user-facing timestamps;
- truthful source provenance.

Do not place lifecycle actions such as Delete or Archive inside an Export menu.

---

## 12. Watchlist, Forecast, and Alert Integrity

A Watchlist Monitor represents a persistent frozen monitoring scope.

Do not replace its scope with an unrelated latest Search Run.

Scheduled refresh must:

- respect ownership;
- skip paused, archived, or deleted Monitors;
- prevent duplicate daily Snapshots;
- use existing comparability rules;
- create no zero-price or empty failure Snapshot;
- preserve the previous valid Snapshot after failure;
- avoid automatic AI forecast generation.

Forecast changes must preserve:

- immutable Forecast Cycles;
- Source Snapshot lineage;
- Benchmark prediction;
- AI-assisted prediction;
- later validation Snapshot;
- selected-cycle consistency;
- accurate error metrics.

Price Alert changes must:

- evaluate existing valid Snapshots;
- perform no additional marketplace search;
- prevent duplicate emails;
- respect cooldown;
- preserve the Snapshot if email delivery fails;
- use the persisted account email.

Test emails must remain clearly labelled as tests.

---

## 13. Email Requirements

Use the shared mail-delivery service.

Do not create separate SMTP implementations for different email types.

Email links must use the canonical configured `APP_BASE_URL`.

Production emails must not contain:

- `localhost`;
- `127.0.0.1`;
- passwords;
- API keys;
- MongoDB connection strings;
- raw provider payloads;
- session tokens.

Password-reset emails may include a fallback full URL.

Welcome and Price Alert HTML emails should use their action buttons without unnecessary raw URL duplication, while plain-text fallbacks retain usable links.

Do not log full password-reset URLs or raw reset tokens.

---

## 14. Development and Demonstration Tools

Development-only tools must remain protected by:

- a non-production environment;
- an explicit enable flag;
- administrator or developer authorization.

Normal production configuration must disable:

```env
DEMO_TOOLS_ENABLED=false
DEMO_MEMBERSHIP_UPGRADE_ENABLED=false
SEARCH_DIAGNOSTICS_ENABLED=false
```

Do not rely only on hiding a navigation link.

Disabled development routes should not perform database changes.

---

## 15. Documentation Changes

Documentation must match the current implementation.

Update relevant documentation when changing:

- environment variables;
- application commands;
- database requirements;
- provider behaviour;
- deployment configuration;
- scheduled tasks;
- membership or workspace-role behaviour;
- security controls;
- known limitations.

Do not claim:

- complete DDoS prevention;
- complete global marketplace coverage;
- guaranteed future prices;
- guaranteed lowest prices;
- real payment processing;
- perfect security.

---

## 16. Commit Messages

Use concise and descriptive commit messages.

Examples:

```text
feat: add scheduled monitor refresh
fix: preserve Product Search values during loading
fix: distinguish unusable Walmart price data
security: enforce user-scoped audit access
style: polish final Analytics layout
docs: update deployment documentation
test: isolate Walmart provider fixtures
```

Avoid vague messages such as:

```text
update files
fix stuff
changes
final version
```

---

## 17. Pull Request or Review Description

A reviewed change should explain:

- the problem addressed;
- expected behaviour;
- files changed;
- routes or workflows affected;
- environment-variable changes;
- database impact;
- security impact;
- tests executed;
- external-service mocks used;
- known limitations;
- deployment impact.

Do not hide failures or describe unexecuted tests as successful.

---

## 18. Release-Stage Discipline

Precision Curator is in the deployment-preparation stage.

Near release, avoid:

- broad MVC rewrites;
- replacing the frontend framework;
- changing all route names;
- changing database models without a clear need;
- adding Redis, Celery, or queues without a demonstrated requirement;
- replacing working providers immediately before demonstration;
- introducing unrelated user-facing features;
- redesigning stable pages.

Prefer the smallest safe change that solves the confirmed problem.

After every release-bound change:

1. run targeted tests;
2. run the complete offline suite;
3. run compilation checks;
4. inspect the Git diff;
5. preserve a recoverable stable commit.

---

## 19. Academic Prototype

Precision Curator is an academic prototype.

No live payment is processed.

External marketplace, AI, email, hosting, and database services remain third-party dependencies.

Application-level security controls reduce risk but do not guarantee protection against every attack or service failure.