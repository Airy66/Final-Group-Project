# Precision Curator Security Notes

This project is an academic prototype. It uses defensive defaults for common web application risks while keeping payment and commercial approval workflows out of scope.

## Input Validation

- User-facing search keywords are trimmed, whitespace-normalized, and capped before use.
- Role and membership values are allow-listed. Normal user roles are `consumer`, `retailer`, and `researcher`; membership tiers are `basic`, `premium`, and `professional`.
- Legacy or invalid membership values, including `academic`, default safely to `basic`.
- Object-like route identifiers are checked before record lookup so malformed IDs return a friendly 404 instead of a server error.
- Numeric filters use safe parsing and invalid values are ignored rather than passed into queries.

## NoSQL Injection Prevention

- Request data is extracted field-by-field instead of passing raw `request.form` or `request.args` into MongoDB queries.
- Repository queries are built from explicit keys such as `user_id`, `search_record_id`, or `_id`.
- User-provided IDs are converted or rejected through controlled helper functions before lookup.

## XSS Prevention

- Jinja auto-escaping is used for product titles, sellers, platforms, conditions, and marketplace metadata.
- External source URLs are only rendered as links when they use `http://` or `https://` with a valid host.
- Unsafe or missing source URLs are shown as `Source unavailable`.
- Client-side detail panels escape interpolated values before inserting them into the page.

## Secrets And Environment

- Gemini, eBay, MongoDB, and Flask secrets are read from environment variables.
- `.env` is listed in `.gitignore` and should not be committed.
- If external collectors or AI services are unavailable, the application uses clearly labelled fallback or demo data for prototype evaluation.

## Access Control

- Route handlers enforce role and membership checks server-side. Sidebar badges are informational only.
- Premium features include saved evidence, watchlists, analytics, and AI summaries.
- Professional features include source audit, activity logs, prediction validation, chart data export, and report export.
- Administrator accounts use system access and are not assigned a membership tier.

## Administrator bootstrap and production runtime

Normal application startup never creates an administrator or changes account passwords.
Create the first administrator explicitly against the configured MongoDB database:

```powershell
$env:PRECISION_ADMIN_PASSWORD = "a-unique-password-with-at-least-12-characters"
python -m tools.create_admin --email administrator@example.com
```

The command refuses an existing administrator unless `--reset-password` is supplied and
never prints the password. In production, set `APP_ENV=production`, provide a strong
`FLASK_SECRET_KEY`, and keep `ALLOW_MEMORY_FALLBACK`, `DEMO_LOGIN_ENABLED`,
`DEMO_TOOLS_ENABLED`, and `DEMO_MEMBERSHIP_UPGRADE_ENABLED` set to `false`.

## Error Handling

- Friendly 403, 404, and 500 pages are registered.
- Production runs should keep `FLASK_DEBUG=false` so raw tracebacks are not exposed to users.
