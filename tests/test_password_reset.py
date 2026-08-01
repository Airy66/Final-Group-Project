import hashlib
import json
import logging
import re
from datetime import timedelta

from werkzeug.security import check_password_hash, generate_password_hash

import precision_app
from services.database import MongoRepository, utcnow
from services import email_service


GENERIC_MESSAGE = "If an account exists for this email, a password reset link has been sent."
CAPTURED_RESET_EMAILS = []
REAL_SEND_PASSWORD_RESET = email_service.EmailService.send_password_reset


def _client(monkeypatch, username="Reset User", email="reset@example.com", password="OldPassword1", capture_email=True):
    monkeypatch.setenv("MAIL_PROVIDER", "brevo_api")
    monkeypatch.setenv("MAIL_ENABLED", "true")
    monkeypatch.setenv("BREVO_API_KEY", "test-brevo-api-key")
    monkeypatch.setenv("BREVO_SENDER_EMAIL", "sender@example.test")
    monkeypatch.setenv("BREVO_SENDER_NAME", "Precision Curator")
    monkeypatch.setenv("APP_BASE_URL", "http://127.0.0.1:5000")
    monkeypatch.setenv("PASSWORD_RESET_TOKEN_TTL_MINUTES", "30")
    CAPTURED_RESET_EMAILS.clear()
    if capture_email:
        def capture(_service, recipient, reset_url, ttl_minutes):
            CAPTURED_RESET_EMAILS.append((recipient, reset_url, ttl_minutes))
            return "message-id"
        monkeypatch.setattr(email_service.EmailService, "send_password_reset", capture)
    precision_app.app.config.update(TESTING=True, SECRET_KEY="password-reset-tests")
    precision_app.repository = MongoRepository(uri="", database_name="password_reset_tests")
    user = precision_app.repository.create_user(username, email, generate_password_hash(password), "consumer")
    return precision_app.app.test_client(), user


def _request_reset(client, email, capsys):
    before = len(CAPTURED_RESET_EMAILS)
    response = client.post("/forgot-password", data={"email": email})
    output = capsys.readouterr().out
    url = CAPTURED_RESET_EMAILS[-1][1] if len(CAPTURED_RESET_EMAILS) > before else None
    match = re.search(r"/reset-password/([^/?#]+)", url or "")
    return response, url, (match.group(1) if match else None), output


def _age_token(token_id, **updates):
    precision_app.repository._soft_update("password_reset_tokens", token_id, updates)


def test_login_and_forgot_pages_render_with_required_controls(monkeypatch):
    client, _user = _client(monkeypatch)
    login = client.get("/login")
    forgot = client.get("/forgot-password")
    assert login.status_code == 200 and b"Forgot password?" in login.data
    assert forgot.status_code == 200
    assert b'input id="email"' in forgot.data and b"Send reset link" in forgot.data and b"Back to sign in" in forgot.data


def test_forgot_form_has_immediate_accessible_loading_and_restoration_logic(monkeypatch):
    client, _user = _client(monkeypatch)
    body = client.get("/forgot-password").get_data(as_text=True)
    assert 'aria-busy="false"' in body
    assert "data-submit-spinner" in body and "animate-spin" in body
    assert "Sending reset link…" in body
    assert "email.disabled = true" in body
    assert "button.disabled = true" in body
    assert "form.dataset.submitting === 'true'" in body and "event.preventDefault()" in body
    assert "form.addEventListener('invalid', restore, true)" in body
    assert "email.disabled = false" in body and "button.disabled = false" in body
    assert "form.setAttribute('aria-busy', 'false')" in body


def test_known_and_unknown_email_share_generic_response_and_unknown_creates_nothing(monkeypatch, capsys):
    client, user = _client(monkeypatch)
    known, url, _token, _output = _request_reset(client, " RESET@EXAMPLE.COM ", capsys)
    known_count = len(precision_app.repository.list_password_reset_tokens(user["_id"]))
    unknown, unknown_url, _unknown_token, _unknown_output = _request_reset(client, "missing@example.com", capsys)
    assert GENERIC_MESSAGE in known.get_data(as_text=True)
    assert GENERIC_MESSAGE in unknown.get_data(as_text=True)
    assert known_count == 1 and len(precision_app.repository.list_password_reset_tokens()) == 1
    assert url and unknown_url is None


def test_development_mail_uses_base_url_and_only_hash_is_persisted(monkeypatch, capsys):
    client, user = _client(monkeypatch)
    monkeypatch.setenv("APP_BASE_URL", "http://local.test/base/")
    response, url, token, output = _request_reset(client, user["email"], capsys)
    stored = precision_app.repository.list_password_reset_tokens(user["_id"])[0]
    assert response.status_code == 200
    assert url.startswith("http://local.test/base/reset-password/")
    assert url not in output and token not in output
    assert token not in json.dumps(stored, default=str)
    assert stored["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert 29 * 60 <= (stored["expires_at"] - stored["created_at"]).total_seconds() <= 31 * 60


def test_invalid_expired_revoked_and_used_tokens_are_rejected(monkeypatch, capsys):
    client, user = _client(monkeypatch)
    invalid = client.get("/reset-password/not-a-real-token")
    assert invalid.status_code == 200
    assert invalid.headers["Cache-Control"] == "no-store" and invalid.headers["Referrer-Policy"] == "no-referrer"
    assert "invalid or has expired" in invalid.get_data(as_text=True)

    _response, _url, token, _output = _request_reset(client, user["email"], capsys)
    record = precision_app.repository.list_password_reset_tokens(user["_id"])[0]
    _age_token(record["_id"], expires_at=utcnow() - timedelta(seconds=1))
    assert "invalid or has expired" in client.get(f"/reset-password/{token}").get_data(as_text=True)

    _age_token(record["_id"], expires_at=utcnow() + timedelta(minutes=30), revoked_at=utcnow(), status="revoked")
    assert "invalid or has expired" in client.get(f"/reset-password/{token}").get_data(as_text=True)

    _age_token(record["_id"], revoked_at=None, used_at=utcnow(), status="used")
    assert "invalid or has expired" in client.get(f"/reset-password/{token}").get_data(as_text=True)


def test_new_request_revokes_old_token_after_throttle_window(monkeypatch, capsys):
    client, user = _client(monkeypatch)
    _response, _url, first_token, _output = _request_reset(client, user["email"], capsys)
    first = precision_app.repository.list_password_reset_tokens(user["_id"])[0]
    _age_token(first["_id"], created_at=utcnow() - timedelta(seconds=61))
    _response, _url, second_token, _output = _request_reset(client, user["email"], capsys)
    tokens = precision_app.repository.list_password_reset_tokens(user["_id"])
    old = next(row for row in tokens if row["token_hash"] == hashlib.sha256(first_token.encode()).hexdigest())
    new = next(row for row in tokens if row["token_hash"] == hashlib.sha256(second_token.encode()).hexdigest())
    assert old["revoked_at"] is not None and old["status"] == "revoked"
    assert new["revoked_at"] is None and new["status"] == "active"


def test_throttling_preserves_generic_response_and_prevents_second_token(monkeypatch, capsys):
    client, user = _client(monkeypatch)
    first, _url, _token, _output = _request_reset(client, user["email"], capsys)
    second, second_url, _second_token, _second_output = _request_reset(client, user["email"], capsys)
    assert GENERIC_MESSAGE in first.get_data(as_text=True) and GENERIC_MESSAGE in second.get_data(as_text=True)
    assert second_url is None
    assert len(precision_app.repository.list_password_reset_tokens(user["_id"])) == 1
    assert any(row.get("event_type") == "password_reset_throttled" for row in precision_app.repository.list_audit_logs(user["_id"], limit=20))


def test_reset_validation_rejects_missing_mismatch_and_short_passwords(monkeypatch, capsys):
    client, user = _client(monkeypatch)
    _response, _url, token, _output = _request_reset(client, user["email"], capsys)
    missing = client.post(f"/reset-password/{token}", data={"password": "", "confirm_password": ""})
    mismatch = client.post(f"/reset-password/{token}", data={"password": "NewPassword1", "confirm_password": "Different1"})
    short = client.post(f"/reset-password/{token}", data={"password": "short", "confirm_password": "short"})
    assert "Both password fields are required" in missing.get_data(as_text=True)
    assert "Passwords do not match" in mismatch.get_data(as_text=True)
    assert "at least 8 characters" in short.get_data(as_text=True)
    assert precision_app.valid_password_reset_record(token)[0] is not None


def test_successful_reset_changes_login_consumes_token_and_revokes_others(monkeypatch, capsys):
    client, user = _client(monkeypatch)
    _response, _url, token, _output = _request_reset(client, user["email"], capsys)
    primary = precision_app.repository.list_password_reset_tokens(user["_id"])[0]
    other_id = precision_app.repository._insert("password_reset_tokens", {
        "user_id": user["_id"], "token_hash": hashlib.sha256(b"other-token").hexdigest(),
        "created_at": utcnow(), "expires_at": utcnow() + timedelta(minutes=30), "used_at": None,
        "revoked_at": None, "status": "active",
    })
    reset = client.post(f"/reset-password/{token}", data={"password": "NewPassword2", "confirm_password": "NewPassword2"})
    assert reset.status_code == 302 and reset.headers["Location"].endswith("/login")
    updated = precision_app.repository.get_user_by_id(user["_id"])
    assert not check_password_hash(updated["password_hash"], "OldPassword1")
    assert check_password_hash(updated["password_hash"], "NewPassword2")
    assert updated.get("password_updated_at") is not None
    tokens = precision_app.repository.list_password_reset_tokens(user["_id"])
    consumed = next(row for row in tokens if row["_id"] == primary["_id"])
    other = next(row for row in tokens if row["_id"] == other_id)
    assert consumed["used_at"] is not None and consumed["status"] == "used"
    assert other["revoked_at"] is not None and other["status"] == "revoked"
    assert "invalid or has expired" in client.get(f"/reset-password/{token}").get_data(as_text=True)

    old_login = client.post("/login", data={"email": user["email"], "password": "OldPassword1"})
    new_login = client.post("/login", data={"email": user["email"], "password": "NewPassword2"})
    assert b"Invalid email or password" in old_login.data
    assert new_login.status_code == 302


def test_security_logs_never_contain_token_or_password(monkeypatch, capsys):
    client, user = _client(monkeypatch)
    _response, _url, token, _output = _request_reset(client, user["email"], capsys)
    password = "PrivatePassword9"
    client.post(f"/reset-password/{token}", data={"password": password, "confirm_password": password})
    logs = json.dumps(precision_app.repository.list_audit_logs(user["_id"], limit=50), default=str)
    assert token not in logs and password not in logs
    events = {row.get("event_type") for row in precision_app.repository.list_audit_logs(user["_id"], limit=50)}
    assert {"password_reset_requested", "password_reset_email_delivered", "password_reset_completed"}.issubset(events)
    access_record = logging.LogRecord("werkzeug", logging.INFO, __file__, 1, f'GET /reset-password/{token} HTTP/1.1', (), None)
    precision_app._ResetTokenLogFilter().filter(access_record)
    assert token not in access_record.getMessage()
    assert "/reset-password/[redacted]" in access_record.getMessage()


def test_brevo_timeout_is_generic_safe_and_revokes_token(monkeypatch, caplog):
    client, user = _client(monkeypatch, capture_email=False)
    raw_token = "fixed-raw-reset-token"
    api_key = "never-log-this-brevo-key"
    monkeypatch.setenv("BREVO_API_KEY", api_key)
    monkeypatch.setattr(precision_app.secrets, "token_urlsafe", lambda _size: raw_token)
    monkeypatch.setattr(email_service.EmailService, "send_password_reset", REAL_SEND_PASSWORD_RESET)
    monkeypatch.setattr(
        email_service.requests,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            email_service.requests.Timeout(f"Brevo timed out {api_key} {raw_token}")
        ),
    )
    response = client.post("/forgot-password", data={"email": user["email"]})
    unknown = client.post("/forgot-password", data={"email": "unknown@example.test"})
    assert response.status_code == 200 and unknown.status_code == 200
    assert GENERIC_MESSAGE in response.get_data(as_text=True) and GENERIC_MESSAGE in unknown.get_data(as_text=True)
    assert "Brevo" not in response.get_data(as_text=True) and "timed out" not in response.get_data(as_text=True)
    token_record = precision_app.repository.list_password_reset_tokens(user["_id"])[0]
    assert token_record["revoked_at"] is not None and token_record["status"] == "revoked"
    logs = json.dumps(precision_app.repository.list_audit_logs(user["_id"], limit=20), default=str)
    assert "password_reset_mail_failed" in logs
    assert raw_token not in logs and api_key not in logs
    assert raw_token not in caplog.text and api_key not in caplog.text
