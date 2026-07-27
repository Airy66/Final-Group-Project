"""Runtime environment policy shared by the web app and persistence layer."""

import os
from datetime import timedelta

from services.app_urls import AppBaseUrlError, canonical_app_base_url


TRUE_VALUES = {"1", "true", "yes", "on"}
PRODUCTION_ENVIRONMENTS = {"production", "prod"}


class ProductionConfigurationError(RuntimeError):
    """Raised when production starts with an unsafe configuration."""


def env_flag(name, default=False, environ=None):
    values = os.environ if environ is None else environ
    fallback = "true" if default else "false"
    return str(values.get(name, fallback)).strip().lower() in TRUE_VALUES


def runtime_environment(environ=None):
    values = os.environ if environ is None else environ
    return str(values.get("APP_ENV") or values.get("FLASK_ENV") or "development").strip().lower()


def is_production_environment(environ=None):
    return runtime_environment(environ) in PRODUCTION_ENVIRONMENTS


def memory_fallback_allowed(environ=None):
    values = os.environ if environ is None else environ
    production = is_production_environment(values)
    allowed = env_flag("ALLOW_MEMORY_FALLBACK", default=not production, environ=values)
    if production and allowed:
        raise ProductionConfigurationError(
            "ALLOW_MEMORY_FALLBACK must be false in production."
        )
    return allowed


def validate_production_configuration(environ=None):
    """Reject configuration that would weaken production startup safety."""
    values = os.environ if environ is None else environ
    if not is_production_environment(values):
        return

    errors = []
    secret = str(values.get("FLASK_SECRET_KEY") or "").strip()
    weak_secret_markers = {"secret", "changeme", "change-me", "replace-me", "dev", "development"}
    if len(secret) < 32 or secret.lower() in weak_secret_markers:
        errors.append("FLASK_SECRET_KEY must be a non-default value of at least 32 characters")
    for flag in ("DEMO_LOGIN_ENABLED", "DEMO_TOOLS_ENABLED", "DEMO_MEMBERSHIP_UPGRADE_ENABLED"):
        if env_flag(flag, environ=values):
            errors.append(f"{flag} must be false in production")
    try:
        memory_fallback_allowed(values)
    except ProductionConfigurationError as exc:
        errors.append(str(exc))
    if values.get("SEEDED_ADMIN_PASSWORD") or values.get("RESET_SEEDED_ADMIN_PASSWORD"):
        errors.append("legacy seeded administrator password configuration is not permitted")
    try:
        canonical_app_base_url(values)
    except AppBaseUrlError as exc:
        errors.append(str(exc))
    configured_admin_password = str(values.get("PRECISION_ADMIN_PASSWORD") or "").strip().lower()
    if configured_admin_password in {"admin123!", "administrator", "changeme", "change-me", "password", "password123"}:
        errors.append("a public or default administrator password is not permitted")
    if errors:
        raise ProductionConfigurationError("Unsafe production configuration: " + "; ".join(errors))


def web_security_configuration(environ=None):
    """Build stable environment-aware Flask session settings."""
    values = os.environ if environ is None else environ
    production = is_production_environment(values)
    if production:
        validate_production_configuration(values)
    secret = str(values.get("FLASK_SECRET_KEY") or "").strip()
    if not secret:
        secret = "precision-curator-development-session-key-only"
    try:
        lifetime_hours = int(values.get("SESSION_LIFETIME_HOURS", "12"))
    except (TypeError, ValueError):
        lifetime_hours = 12
    lifetime_hours = max(1, min(lifetime_hours, 24 * 7))
    return {
        "SECRET_KEY": secret,
        "SESSION_COOKIE_NAME": "precision_curator_session",
        "SESSION_COOKIE_SECURE": production,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": timedelta(hours=lifetime_hours),
    }
