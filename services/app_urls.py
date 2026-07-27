"""Canonical internal application URLs used in account and alert emails."""

import ipaddress
import os
import re
from urllib.parse import quote, urlencode, urlsplit, urlunsplit


DEVELOPMENT_APP_BASE_URL = "http://127.0.0.1:5000"
PRODUCTION_ENVIRONMENTS = {"production", "prod"}
LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".test", ".invalid")
EMAIL_PATHS = {
    "login": "/login",
    "forgot_password": "/forgot-password",
    "change_password": "/change-password",
    "membership": "/membership",
    "profile": "/profile",
    "password_reset": "/reset-password/{token}",
    "watchlist_monitor": "/watchlist",
}


class AppBaseUrlError(ValueError):
    """Raised when APP_BASE_URL cannot safely identify this application."""


def _is_production(environ):
    return str(environ.get("APP_ENV") or environ.get("FLASK_ENV") or "development").strip().lower() in PRODUCTION_ENVIRONMENTS


def _is_local_hostname(hostname):
    normalized = str(hostname or "").strip().rstrip(".").casefold()
    if not normalized or normalized == "localhost" or normalized.endswith(LOCAL_HOST_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return "." not in normalized
    return not address.is_global


def normalize_app_base_url(value, *, production=False):
    """Validate and normalize one configured application origin/base path."""
    raw = str(value or "").strip()
    if not raw:
        raise AppBaseUrlError("APP_BASE_URL must be configured")
    if any(character.isspace() or ord(character) < 32 for character in raw):
        raise AppBaseUrlError("APP_BASE_URL must not contain whitespace or control characters")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise AppBaseUrlError("APP_BASE_URL is malformed") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise AppBaseUrlError("APP_BASE_URL must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise AppBaseUrlError("APP_BASE_URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise AppBaseUrlError("APP_BASE_URL must not contain a query string or fragment")
    if "\\" in parsed.path or any(segment in {".", ".."} for segment in parsed.path.split("/")):
        raise AppBaseUrlError("APP_BASE_URL contains an unsafe path")
    if production and parsed.scheme != "https":
        raise AppBaseUrlError("APP_BASE_URL must use HTTPS in production")
    if production and _is_local_hostname(parsed.hostname):
        raise AppBaseUrlError("APP_BASE_URL must use a public, non-local hostname in production")

    normalized_path = re.sub(r"/+", "/", parsed.path or "").rstrip("/")
    hostname = parsed.hostname.casefold()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, normalized_path, "", ""))


def canonical_app_base_url(environ=None):
    """Return APP_BASE_URL without consulting a request or Host header."""
    values = os.environ if environ is None else environ
    production = _is_production(values)
    configured = values.get("APP_BASE_URL")
    if not str(configured or "").strip() and not production:
        configured = DEVELOPMENT_APP_BASE_URL
    return normalize_app_base_url(configured, production=production)


def application_email_url(destination, *, token=None, monitor_id=None, environ=None):
    """Build an allow-listed internal application URL for email content."""
    if destination not in EMAIL_PATHS:
        raise ValueError("Unsupported application email URL destination.")
    path = EMAIL_PATHS[destination]
    query = ""
    if destination == "password_reset":
        if not str(token or ""):
            raise ValueError("A password-reset token is required.")
        path = path.format(token=quote(str(token), safe="-._~"))
    elif destination == "watchlist_monitor":
        if not str(monitor_id or ""):
            raise ValueError("A Watchlist Monitor identifier is required.")
        query = urlencode({"item_id": str(monitor_id)})
    base = canonical_app_base_url(environ)
    url = f"{base}/{path.lstrip('/')}"
    return f"{url}?{query}" if query else url
