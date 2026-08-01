import logging

import pytest
from bs4 import BeautifulSoup

import precision_app
from services.app_urls import (
    AppBaseUrlError,
    application_email_url,
    canonical_app_base_url,
    normalize_app_base_url,
)
from services.email_service import EmailService
from services.runtime_config import ProductionConfigurationError, validate_production_configuration


PUBLIC_ENV = {
    "APP_ENV": "production",
    "APP_BASE_URL": "https://precision-curator.onrender.com/app/",
    "FLASK_SECRET_KEY": "x" * 40,
    "DEMO_MODE": "false",
}


def test_development_and_production_email_urls_use_configured_base():
    development = {"APP_ENV": "development", "APP_BASE_URL": "http://127.0.0.1:5000/"}
    token = "reset-token_123"

    assert application_email_url("password_reset", token=token, environ=development) == (
        f"http://127.0.0.1:5000/reset-password/{token}"
    )
    assert application_email_url("password_reset", token=token, environ=PUBLIC_ENV) == (
        f"https://precision-curator.onrender.com/app/reset-password/{token}"
    )
    assert application_email_url("login", environ=PUBLIC_ENV) == (
        "https://precision-curator.onrender.com/app/login"
    )
    assert application_email_url("watchlist_monitor", monitor_id="monitor 1", environ=PUBLIC_ENV) == (
        "https://precision-curator.onrender.com/app/watchlist?item_id=monitor+1"
    )
    assert application_email_url("membership", environ=PUBLIC_ENV).endswith("/app/membership")


def test_base_url_normalization_removes_duplicate_slashes_and_rejects_malformed_values():
    assert normalize_app_base_url("https://app.example.com//workspace///") == "https://app.example.com/workspace"
    with pytest.raises(AppBaseUrlError):
        normalize_app_base_url("not-a-url")
    with pytest.raises(AppBaseUrlError):
        normalize_app_base_url("https://user:password@app.example.com")
    with pytest.raises(AppBaseUrlError):
        normalize_app_base_url("https://app.example.com/base?from=browser")


def test_production_startup_rejects_http_and_local_app_base_urls():
    with pytest.raises(ProductionConfigurationError, match="APP_BASE_URL must use HTTPS"):
        validate_production_configuration({**PUBLIC_ENV, "APP_BASE_URL": "http://app.example.com"})
    for local_url in (
        "https://localhost",
        "https://127.0.0.1:5000",
        "https://app.local",
        "https://devbox",
    ):
        with pytest.raises(ProductionConfigurationError, match="public, non-local hostname"):
            validate_production_configuration({**PUBLIC_ENV, "APP_BASE_URL": local_url})
    validate_production_configuration(PUBLIC_ENV)


def test_canonical_email_urls_never_use_request_host(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com/")
    with precision_app.app.test_request_context(
        "/forgot-password",
        base_url="https://attacker.invalid/",
    ):
        assert canonical_app_base_url() == "https://app.example.com"
        assert precision_app.registration_login_url() == "https://app.example.com/login"


def test_production_email_bodies_use_public_urls_without_localhost(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.com/")
    service = EmailService(enabled=False)
    reset_url = precision_app.password_reset_url("token-abc")
    login_url = precision_app.registration_login_url()
    monitor_url = precision_app.price_alert_monitor_url("monitor-123")
    reset_text = service._text_body(reset_url, 30)
    reset_html = service._html_body(reset_url, 30)
    welcome_text = service._welcome_text_body("Person", login_url, "premium")
    welcome_html = service._welcome_html_body("Person", login_url, "premium")
    alert_text, alert_html = service._price_alert_bodies(
        "Phone monitor", "drop", 5, 100, 90, -10, None, monitor_url, False
    )
    content = "\n".join((reset_text, reset_html, welcome_text, welcome_html, alert_text, alert_html))

    assert reset_url == "https://app.example.com/reset-password/token-abc"
    assert login_url == "https://app.example.com/login"
    assert monitor_url == "https://app.example.com/watchlist?item_id=monitor-123"
    assert "127.0.0.1" not in content and "localhost" not in content


def test_email_html_url_display_policy_and_test_alert_copy():
    service = EmailService(enabled=False)
    reset_url = "https://app.example.com/reset-password/token-abc"
    login_url = "https://app.example.com/login"
    monitor_url = "https://app.example.com/watchlist?item_id=monitor-123"
    reset_html = service._html_body(reset_url, 30)
    welcome_text = service._welcome_text_body("Person", login_url, "professional")
    welcome_html = service._welcome_html_body("Person", login_url, "professional")
    alert_text, alert_html = service._price_alert_bodies(
        "Phone monitor", "drop", 5, 100, 90, -10, None, monitor_url, False
    )
    test_text, test_html = service._price_alert_bodies(
        "Phone monitor", "either", 5, None, None, None, None, monitor_url, True
    )

    assert "Button not working? Copy and paste this link into your browser:" in reset_html
    assert "overflow-wrap:anywhere" in reset_html and "word-break:break-all" in reset_html
    assert reset_url in BeautifulSoup(reset_html, "html.parser").get_text(" ", strip=True)
    assert login_url in welcome_text
    assert login_url not in BeautifulSoup(welcome_html, "html.parser").get_text(" ", strip=True)
    assert monitor_url in alert_text
    assert monitor_url not in BeautifulSoup(alert_html, "html.parser").get_text(" ", strip=True)
    assert 'href="https://app.example.com/login"' in welcome_html
    assert 'href="https://app.example.com/watchlist?item_id=monitor-123"' in alert_html
    assert "This is a test notification. No marketplace threshold was triggered." in test_text + test_html
    assert "/reset-password/" not in test_text + test_html


def test_brevo_reset_delivery_does_not_log_token_or_complete_url(monkeypatch, caplog):
    token = "secret-reset-token"
    url = f"https://app.example.com/reset-password/{token}"
    caplog.set_level(logging.INFO)

    api_key = "secret-brevo-api-key"
    monkeypatch.setenv("MAIL_ENABLED", "true")
    monkeypatch.setenv("MAIL_PROVIDER", "brevo_api")
    monkeypatch.setenv("BREVO_API_KEY", api_key)
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "sender@example.com")
    monkeypatch.setenv("BREVO_SENDER_NAME", "Precision Curator")

    class Response:
        status_code = 201

        @staticmethod
        def json():
            return {"messageId": "message-123"}

    monkeypatch.setattr("services.email_service.requests.post", lambda *_args, **_kwargs: Response())
    EmailService().send_password_reset("user@example.com", url, 30)

    assert token not in caplog.text
    assert url not in caplog.text
    assert api_key not in caplog.text
