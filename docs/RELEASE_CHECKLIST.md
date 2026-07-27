# Precision Curator release and submission checklist

This checklist prepares a staging candidate; it does not authorize deployment.

## Source package

- [ ] `precision_app:app` is the only canonical Flask application.
- [ ] `app.py` remains a side-effect-free compatibility import.
- [ ] `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, templates, source assets, services, tools, tests, README, `.env.example`, and `render.yaml` are included.
- [ ] Runtime charts/uploads, caches, coverage output, temporary exports, logs, virtual environments, IDE state, `.env`, and real user data are excluded.

## Secrets and accounts

- [ ] Search the resulting tracked tree for credentials and private keys.
- [ ] Revoke/rotate any credential that previously appeared in source history, including the removed legacy eBay test credential.
- [ ] Configure Render and MongoDB Atlas secrets only through environment settings.
- [ ] Create the first administrator explicitly with `python -m tools.create_admin`; never seed it at startup.

## Data preparation

- [ ] Provision an authenticated MongoDB Atlas database and restrict access.
- [ ] Keep `ALLOW_MEMORY_FALLBACK=false` in production.
- [ ] Prepare a small, labelled demonstration dataset rather than migrating all historical test data.
- [ ] Confirm provenance labels for eBay, third-party Walmart retrieval, mock data, calculations, and AI assistance.

## Validation

- [ ] `python -m py_compile precision_app.py app.py services/runtime_config.py services/database.py`
- [ ] `python -m pip check`
- [ ] `python -m pytest tests src/tests -q` with offline provider doubles
- [ ] `git diff --check`
- [ ] `GET /health` returns only service health JSON.
- [ ] Production configuration rejects weak/missing secrets and memory fallback.
- [ ] No external provider call occurs during the offline suite.

## Deployment stop gate

- [ ] Stop and report the prepared diff and validation results.
- [ ] Obtain explicit authorization before creating/changing Render services, Atlas clusters/users/network rules, or deploying data/code.
