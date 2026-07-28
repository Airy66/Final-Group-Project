import json

import pytest
from pymongo.errors import AutoReconnect
from bs4 import BeautifulSoup
from werkzeug.security import check_password_hash

import precision_app
from services import mail_service
from services.database import MongoRepository


PASSWORD = "RegistrationPassword1!"
SMTP_PASSWORD = "smtp-password-must-never-appear"


def _client(monkeypatch, *, enabled=True, mode="console"):
    monkeypatch.setenv("EMAIL_MODE", mode)
    monkeypatch.setenv("APP_BASE_URL", "https://precision.example.test/base/")
    monkeypatch.setenv("MAIL_PASSWORD", SMTP_PASSWORD)
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
        return "printed"

    monkeypatch.setattr(precision_app.PasswordResetMailService, "send_registration_welcome", send)
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
        return "printed"

    monkeypatch.setattr(precision_app.repository, "create_user", create_with_persisted_basic)
    monkeypatch.setattr(
        precision_app.PasswordResetMailService, "send_registration_welcome", send
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
        precision_app.PasswordResetMailService,
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
        precision_app.PasswordResetMailService,
        "send_registration_welcome",
        lambda *_args, **_kwargs: calls.append(True) or "printed",
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
        precision_app.PasswordResetMailService,
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
    client = _client(monkeypatch, mode="smtp")
    attempts = []

    def fail(*_args, **_kwargs):
        attempts.append(True)
        raise TimeoutError(f"provider detail {SMTP_PASSWORD}")

    monkeypatch.setattr(precision_app.PasswordResetMailService, "send_registration_welcome", fail)
    response = _post_registration(client)
    assert response.status_code == 302 and response.headers["Location"].endswith("/login")
    user = precision_app.repository.get_user_by_email("welcome.user@example.test")
    assert user is not None and attempts == [True]
    page = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "Account created" in page and "could not be delivered" in page
    assert "provider detail" not in page and SMTP_PASSWORD not in page
    login = client.post("/login", data={"email": user["email"], "password": PASSWORD})
    assert login.status_code == 302
    logs = json.dumps(precision_app.repository.list_audit_logs(user["_id"], limit=20), default=str)
    assert "registration_welcome_email_failed" in logs
    assert PASSWORD not in logs and SMTP_PASSWORD not in logs and "provider detail" not in logs
    assert PASSWORD not in caplog.text and SMTP_PASSWORD not in caplog.text and "provider detail" not in caplog.text


def test_disabled_welcome_email_still_registers_without_attempt(monkeypatch):
    client = _client(monkeypatch, enabled=False)
    calls = []
    monkeypatch.setattr(
        precision_app.PasswordResetMailService,
        "send_registration_welcome",
        lambda *_args, **_kwargs: calls.append(True),
    )
    response = _post_registration(client)
    assert response.status_code == 302
    assert precision_app.repository.get_user_by_email("welcome.user@example.test") is not None
    assert calls == []


def test_console_mode_welcome_is_plain_safe_and_never_uses_smtp(monkeypatch, capsys):
    client = _client(monkeypatch, mode="console")
    monkeypatch.setattr(
        mail_service.smtplib,
        "SMTP",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("SMTP network call is forbidden")),
    )
    response = _post_registration(client)
    output = capsys.readouterr().out
    assert response.status_code == 302
    assert "Welcome to Precision Curator" in output
    assert "Your account was created successfully" in output
    assert "https://precision.example.test/base/login" in output
    assert "No action is required" in output
    assert PASSWORD not in output and SMTP_PASSWORD not in output


class _FakeSocket:
    def __init__(self, calls):
        self.calls = calls

    def settimeout(self, value):
        self.calls.append(("socket_timeout", value))


class _FakeSMTP:
    calls = []
    message = None

    def __init__(self, host, port, timeout):
        self.calls.append(("connect", host, port, timeout))
        self.sock = _FakeSocket(self.calls)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        type(self).message = message
        self.calls.append(("send_message",))


def test_smtp_welcome_reuses_transport_timeout_and_multipart_content(monkeypatch):
    _FakeSMTP.calls = []
    _FakeSMTP.message = None
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_PORT", "587")
    monkeypatch.setenv("MAIL_USERNAME", "mailer")
    monkeypatch.setenv("MAIL_PASSWORD", SMTP_PASSWORD)
    monkeypatch.setenv("MAIL_FROM_ADDRESS", "support@example.test")
    monkeypatch.setenv("MAIL_USE_TLS", "true")
    monkeypatch.setenv("MAIL_USE_SSL", "false")
    monkeypatch.setenv("MAIL_TIMEOUT_SECONDS", "8")
    monkeypatch.setattr(mail_service.smtplib, "SMTP", _FakeSMTP)
    result = mail_service.PasswordResetMailService(mode="smtp").send_registration_welcome(
        "user@example.test", "User <Admin>", "https://precision.example.test/login"
    )
    assert result == "queued"
    assert ("connect", "smtp.example.test", 587, 8.0) in _FakeSMTP.calls
    assert ("socket_timeout", 8.0) in _FakeSMTP.calls and ("send_message",) in _FakeSMTP.calls
    message = _FakeSMTP.message
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert message["Subject"] == "Welcome to Precision Curator"
    assert "Welcome to Precision Curator" in plain and "No action is required" in plain
    assert "Sign in" in html and 'href="https://precision.example.test/login"' in html
    assert "User &lt;Admin&gt;" in html and "<img" not in html.lower()
    assert PASSWORD not in plain + html and SMTP_PASSWORD not in plain + html


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
