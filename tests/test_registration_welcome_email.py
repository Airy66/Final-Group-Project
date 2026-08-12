import json

import pytest
from pymongo.errors import AutoReconnect
from bs4 import BeautifulSoup
from werkzeug.security import check_password_hash

import precision_app
from services import email_service
from services.database import MongoRepository


PASSWORD = "RegistrationPassword1!"
BREVO_API_KEY = "brevo-key-must-never-appear"


def _client(monkeypatch, *, enabled=True):
    monkeypatch.setenv("MAIL_PROVIDER", "brevo_api")
    monkeypatch.setenv("MAIL_ENABLED", "true")
    monkeypatch.setenv("APP_BASE_URL", "https://precision.example.test/base/")
    monkeypatch.setenv("BREVO_API_KEY", BREVO_API_KEY)
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "sender@example.test")
    monkeypatch.setenv("BREVO_SENDER_NAME", "Precision Curator")
    precision_app.app.config.update(
        TESTING=True,
        PROPAGATE_EXCEPTIONS=True,
        SECRET_KEY="registration-welcome-tests",
        REGISTRATION_WELCOME_EMAIL_ENABLED=enabled,
    )
    precision_app.repository = MongoRepository(uri="", database_name="registration_welcome_tests")
    return precision_app.app.test_client()


def _registration_data(**updates):
    data = {
        "display_name": "Welcome User",
        "email": " Welcome.User@Example.Test ",
        "password": PASSWORD,
        "confirm_password": PASSWORD,
        "role": "consumer",
        "membership_tier": "basic",
    }
    data.update(updates)
    return data


def _post_registration(client, **updates):
    return client.post("/register", data=_registration_data(**updates))


def test_successful_registration_persists_before_one_welcome_attempt(monkeypatch):
    client = _client(monkeypatch)
    calls = []

    def send(_service, recipient, display_name, login_url, membership_tier):
        user = precision_app.repository.get_user_by_email(recipient)
        assert user is not None
        assert check_password_hash(user["password_hash"], PASSWORD)
        calls.append((recipient, display_name, login_url, membership_tier))
        return "message-id"

    monkeypatch.setattr(precision_app.EmailService, "send_registration_welcome", send)
    response = _post_registration(client)
    user = precision_app.repository.get_user_by_email("welcome.user@example.test")
    assert response.status_code == 302 and response.headers["Location"].endswith("/login")
    assert user is not None and user["email"] == "welcome.user@example.test"
    assert calls == [("welcome.user@example.test", "Welcome User", "https://precision.example.test/base/login", "basic")]
    events = [row.get("event_type") for row in precision_app.repository.list_audit_logs(user["_id"], limit=20)]
    assert events.count("register") == 1
    assert events.count("registration_welcome_email_delivered") == 1


def test_welcome_plan_comes_from_persisted_account_not_submitted_value(monkeypatch):
    client = _client(monkeypatch)
    original_create = precision_app.repository.create_user
    delivered = []

    def create_with_persisted_basic(*args, **kwargs):
        user = original_create(*args, **kwargs)
        precision_app.repository.update_user(
            user["_id"], {"membership_tier": "basic", "plan": "basic"}
        )
        return precision_app.repository.get_user_by_id(user["_id"])

    def send(_service, _recipient, _display_name, _login_url, membership_tier):
        delivered.append(membership_tier)
        return "message-id"

    monkeypatch.setattr(precision_app.repository, "create_user", create_with_persisted_basic)
    monkeypatch.setattr(
        precision_app.EmailService, "send_registration_welcome", send
    )
    response = _post_registration(client, membership_tier="professional")
    assert response.status_code == 302
    assert delivered == ["basic"]


@pytest.mark.parametrize("updates", [
    {"email": ""},
    {"password": "short", "confirm_password": "short"},
    {"password": PASSWORD, "confirm_password": "DifferentPassword1!"},
    {"role": "administrator"},
])
def test_invalid_registration_never_sends_welcome(monkeypatch, updates):
    client = _client(monkeypatch)
    calls = []
    monkeypatch.setattr(
        precision_app.EmailService,
        "send_registration_welcome",
        lambda *_args, **_kwargs: calls.append(True),
    )
    response = _post_registration(client, **updates)
    assert response.status_code == 200
    assert calls == []
    assert precision_app.repository.get_user_by_display_name("Welcome User") is None


def test_duplicate_email_and_resubmission_do_not_send_again(monkeypatch):
    client = _client(monkeypatch)
    calls = []
    monkeypatch.setattr(
        precision_app.EmailService,
        "send_registration_welcome",
        lambda *_args, **_kwargs: calls.append(True) or "message-id",
    )
    first = _post_registration(client)
    repeated = _post_registration(client, display_name="Second Button Click")
    assert first.status_code == 302 and repeated.status_code == 200
    assert calls == [True]
    assert precision_app.repository.get_user_by_display_name("Second Button Click") is None


def test_database_creation_failure_sends_no_welcome(monkeypatch):
    client = _client(monkeypatch)
    calls = []
    monkeypatch.setattr(
        precision_app.EmailService,
        "send_registration_welcome",
        lambda *_args, **_kwargs: calls.append(True),
    )
    monkeypatch.setattr(
        precision_app.repository,
        "create_user",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AutoReconnect("database unavailable")),
    )
    response = _post_registration(client)
    assert response.status_code == 503
    assert b"Database temporarily unavailable" in response.data
    assert calls == []


def test_delivery_failure_keeps_account_safe_and_sign_in_available(monkeypatch, caplog):
    client = _client(monkeypatch)
    attempts = []

    def fail(*_args, **_kwargs):
        attempts.append(True)
        raise TimeoutError(f"provider detail {BREVO_API_KEY}")

    monkeypatch.setattr(precision_app.EmailService, "send_registration_welcome", fail)
    response = _post_registration(client)
    assert response.status_code == 302 and response.headers["Location"].endswith("/login")
    user = precision_app.repository.get_user_by_email("welcome.user@example.test")
    assert user is not None and attempts == [True]
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Account created" in page and "could not be delivered" in page
    assert "provider detail" not in page and BREVO_API_KEY not in page
    login = client.post("/login", data={"email": user["email"], "password": PASSWORD})
    assert login.status_code == 302
    logs = json.dumps(precision_app.repository.list_audit_logs(user["_id"], limit=20), default=str)
    assert "registration_welcome_email_failed" in logs
    assert PASSWORD not in logs and BREVO_API_KEY not in logs and "provider detail" not in logs
    assert PASSWORD not in caplog.text and BREVO_API_KEY not in caplog.text and "provider detail" not in caplog.text


def test_disabled_welcome_email_still_registers_without_attempt(monkeypatch):
    client = _client(monkeypatch, enabled=False)
    calls = []
    monkeypatch.setattr(
        precision_app.EmailService,
        "send_registration_welcome",
        lambda *_args, **_kwargs: calls.append(True),
    )
    response = _post_registration(client)
    assert response.status_code == 302
    assert precision_app.repository.get_user_by_email("welcome.user@example.test") is not None
    assert calls == []


def test_brevo_welcome_uses_https_payload_without_exposing_secrets(monkeypatch, caplog):
    client = _client(monkeypatch)
    captured = {}

    class Response:
        status_code = 201

        @staticmethod
        def json():
            return {"messageId": "welcome-message-id"}

    def post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return Response()

    monkeypatch.setattr(email_service.requests, "post", post)
    response = _post_registration(client)
    assert response.status_code == 302
    payload = captured["json"]
    assert captured["url"] == "https://api.brevo.com/v3/smtp/email"
    assert payload["subject"] == "Welcome to Precision Curator"
    assert payload["to"] == [{"email": "welcome.user@example.test"}]
    assert "https://precision.example.test/base/login" in payload["textContent"]
    assert 'href="https://precision.example.test/base/login"' in payload["htmlContent"]
    assert "Welcome User" in payload["textContent"]
    assert PASSWORD not in caplog.text and BREVO_API_KEY not in caplog.text


def test_registration_loading_state_and_duplicate_browser_guard(monkeypatch):
    client = _client(monkeypatch, enabled=False)
    body = client.get("/register").get_data(as_text=True)
    assert 'data-registration-form' in body and 'aria-busy="false"' in body
    assert "data-submit-spinner" in body and "animate-spin" in body
    assert "Creating account…" in body
    assert "form.dataset.submitting === 'true'" in body
    assert "event.preventDefault()" in body
    assert "button.disabled = true" in body and "button.disabled = false" in body
    assert "form?.addEventListener('invalid', restore, true)" in body
    assert "form.setAttribute('aria-busy', 'true')" in body
    assert "form.setAttribute('aria-busy', 'false')" in body


def test_registration_with_explicit_valid_csrf_still_succeeds(monkeypatch):
    client = _client(monkeypatch, enabled=False)
    register_page = client.get("/register")
    token = BeautifulSoup(register_page.data, "html.parser").select_one('meta[name="csrf-token"]')["content"]
    response = client.post(
        "/register",
        data={**_registration_data(), "csrf_token": token},
        auto_csrf=False,
    )
    assert response.status_code == 302
    assert precision_app.repository.get_user_by_email("welcome.user@example.test") is not None
