import logging

import pytest
import requests

from services.email_service import (
    BREVO_REQUEST_TIMEOUT_SECONDS,
    BREVO_TRANSACTIONAL_EMAIL_URL,
    EmailConfigurationError,
    EmailDeliveryError,
    EmailService,
)


API_KEY = "test-brevo-key-that-must-never-be-logged"


class _Response:
    def __init__(self, status_code=201, payload=None):
        self.status_code = status_code
        self._payload = {"messageId": "<brevo-message-id>"} if payload is None else payload

    def json(self):
        return self._payload


def _configure(monkeypatch):
    monkeypatch.setenv("MAIL_PROVIDER", "brevo_api")
    monkeypatch.setenv("MAIL_ENABLED", "true")
    monkeypatch.setenv("BREVO_API_KEY", API_KEY)
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "verified-sender@example.test")
    monkeypatch.setenv("BREVO_SENDER_NAME", "Precision Curator")


def test_brevo_success_returns_message_id_and_uses_https_contract(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, payload=json, timeout=timeout)
        return _Response()

    monkeypatch.setattr("services.email_service.requests.post", post)
    message_id = EmailService().send_registration_welcome(
        "new.user@example.test", "New User", "https://app.example.test/login", "premium"
    )

    assert message_id == "<brevo-message-id>"
    assert captured["url"] == BREVO_TRANSACTIONAL_EMAIL_URL
    assert captured["headers"] == {"api-key": API_KEY, "content-type": "application/json"}
    assert captured["timeout"] == BREVO_REQUEST_TIMEOUT_SECONDS
    assert captured["payload"]["sender"] == {
        "email": "verified-sender@example.test", "name": "Precision Curator"
    }
    assert captured["payload"]["to"] == [{"email": "new.user@example.test"}]
    assert "Welcome to Precision Curator" in captured["payload"]["textContent"]
    assert "https://app.example.test/login" in captured["payload"]["htmlContent"]


def test_missing_brevo_api_key_is_an_explicit_safe_configuration_error(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.setattr(
        "services.email_service.requests.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not be called")),
    )

    with pytest.raises(EmailConfigurationError, match="BREVO_API_KEY") as error:
        EmailService().send_password_reset(
            "user@example.test", "https://app.example.test/reset-password/redacted", 30
        )
    assert API_KEY not in str(error.value)


def test_brevo_timeout_fails_safely_without_secret_or_content(monkeypatch, caplog):
    _configure(monkeypatch)
    token = "raw-reset-token-must-not-be-logged"
    monkeypatch.setattr(
        "services.email_service.requests.post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout(f"timeout {API_KEY} {token}")),
    )
    caplog.set_level(logging.WARNING)

    with pytest.raises(EmailDeliveryError, match="timed out") as error:
        EmailService().send_password_reset(
            "user@example.test", f"https://app.example.test/reset-password/{token}", 30
        )
    assert error.value.code == "delivery_timeout"
    assert API_KEY not in str(error.value) + caplog.text
    assert token not in str(error.value) + caplog.text


@pytest.mark.parametrize("status_code", [400, 401, 429, 500, 503])
def test_brevo_http_errors_are_safe(monkeypatch, status_code):
    _configure(monkeypatch)
    monkeypatch.setattr(
        "services.email_service.requests.post",
        lambda *_args, **_kwargs: _Response(status_code, {"message": f"private {API_KEY}"}),
    )

    with pytest.raises(EmailDeliveryError) as error:
        EmailService().send_registration_welcome(
            "user@example.test", "User", "https://app.example.test/login"
        )
    assert error.value.code == "provider_rejected"
    assert str(status_code) in str(error.value)
    assert API_KEY not in str(error.value)


def test_success_without_message_id_is_rejected(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        "services.email_service.requests.post",
        lambda *_args, **_kwargs: _Response(201, {}),
    )
    with pytest.raises(EmailDeliveryError, match="no message identifier"):
        EmailService().send_registration_welcome(
            "user@example.test", "User", "https://app.example.test/login"
        )
