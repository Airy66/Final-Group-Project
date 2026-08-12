from datetime import timedelta
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import precision_app
from services.database import MongoRepository
from services.runtime_config import ProductionConfigurationError, validate_production_configuration, web_security_configuration


PASSWORD = "OfflineTestPassword1!"


def _client(name="Batch2B", role="consumer", tier="professional"):
    precision_app.app.config.update(
        TESTING=True,
        SECRET_KEY="batch2b-test-session-secret",
        DEMO_TOOLS_ENABLED=False,
        DEMO_MEMBERSHIP_UPGRADE_ENABLED=False,
        RUNTIME_ENVIRONMENT="test",
    )
    precision_app.MOCK_SEARCH_MODE = True
    precision_app.SHOW_DEMO_FALLBACK = False
    precision_app.repository = MongoRepository(uri="", database_name="security_batch2b")
    user = precision_app.repository.create_user(
        name,
        f"{name.lower()}@example.test",
        precision_app.generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000"),
        role,
        roles=[role],
        active_role=role,
    )
    precision_app.repository.update_user(user["_id"], {"membership_tier": tier, "plan": tier})
    client = precision_app.app.test_client()
    client.post("/login", data={"email": user["email"], "password": PASSWORD})
    return client, user


def _csrf_token(client, path="/profile"):
    response = client.get(path)
    soup = BeautifulSoup(response.data, "html.parser")
    return soup.select_one('meta[name="csrf-token"]')["content"]


@pytest.mark.parametrize("path", [
    "/logout",
    "/profile",
    "/profile/avatar/remove",
    "/saved/evidence/delete",
    "/analytics/000000000000000000000001/delete",
    "/watchlist/000000000000000000000001/delete",
    "/watchlist/000000000000000000000001/refresh",
    "/watchlist/000000000000000000000001/prediction",
    "/dashboard/administrator/users/delete",
    "/test-data/reset",
])
def test_state_changing_form_posts_reject_missing_and_invalid_csrf(path):
    client, _user = _client()
    missing = client.post(path, auto_csrf=False)
    invalid = client.post(path, data={"csrf_token": "invalid-token-marker"}, auto_csrf=False)
    assert missing.status_code == 400
    assert invalid.status_code == 400
    assert precision_app.CSRF_ERROR_MESSAGE.encode() in missing.data
    assert b"invalid-token-marker" not in invalid.data


def test_valid_html_form_csrf_and_logout_succeed():
    client, user = _client()
    token = _csrf_token(client)
    profile = client.post(
        "/profile",
        data={"csrf_token": token, "display_name": "Batch2B Updated", "institution": "Offline Lab"},
        auto_csrf=False,
    )
    assert profile.status_code == 302
    assert precision_app.repository.get_user_by_id(user["_id"])["display_name"] == "Batch2B Updated"
    token = _csrf_token(client)
    logout = client.post("/logout", data={"csrf_token": token}, auto_csrf=False)
    assert logout.status_code == 302


def test_json_post_requires_header_and_accepts_valid_header():
    client, _user = _client()
    missing = client.post("/api/search", json={"q": "phone", "source": "demo"}, auto_csrf=False)
    assert missing.status_code == 400
    token = _csrf_token(client)
    valid = client.post(
        "/api/search",
        json={"q": "phone", "source": "demo"},
        headers={precision_app.CSRF_HEADER_NAME: token},
        auto_csrf=False,
    )
    assert valid.status_code == 200
    assert valid.get_json()["search_record_id"]


def test_rendered_post_forms_use_shared_token_field_and_meta_tag():
    client, _user = _client()
    for path in ("/profile", "/watchlist", "/analytics", "/forgot-password"):
        response = client.get(path)
        soup = BeautifulSoup(response.data, "html.parser")
        assert soup.select_one('meta[name="csrf-token"]')
        for form in soup.select('form[method="post"], form[method="POST"]'):
            assert form.select_one('input[name="csrf_token"]'), f"missing token in {path}"


def test_password_reset_request_and_completion_accept_valid_csrf(monkeypatch):
    client, user = _client()
    monkeypatch.setattr(precision_app.EmailService, "send_password_reset", lambda *_args, **_kwargs: "message-id")
    token = _csrf_token(client, "/forgot-password")
    requested = client.post(
        "/forgot-password",
        data={"email": user["email"], "csrf_token": token},
        auto_csrf=False,
    )
    assert requested.status_code == 200
    reset_record = precision_app.repository.latest_password_reset_token(user["_id"])
    assert reset_record is not None
    raw_token = "batch2b-valid-reset-token"
    precision_app.repository.create_password_reset_token(
        user["_id"],
        precision_app.reset_token_hash(raw_token),
        precision_app.utcnow() + timedelta(minutes=30),
    )
    csrf = _csrf_token(client, f"/reset-password/{raw_token}")
    completed = client.post(
        f"/reset-password/{raw_token}",
        data={"csrf_token": csrf, "password": "UpdatedOfflinePassword1!", "confirm_password": "UpdatedOfflinePassword1!"},
        auto_csrf=False,
    )
    assert completed.status_code == 302


def test_no_normal_mutation_endpoint_is_csrf_exempt():
    mutation_rules = []
    for rule in precision_app.app.url_map.iter_rules():
        unsafe = set(rule.methods or ()) - {"GET", "HEAD", "OPTIONS", "TRACE"}
        if unsafe:
            mutation_rules.append(rule.endpoint)
    assert mutation_rules
    assert not hasattr(precision_app.app, "csrf_exempt_endpoints")
    assert "enforce_csrf_protection" in {function.__name__ for function in precision_app.app.before_request_funcs.get(None, [])}


@pytest.mark.parametrize("value", [
    "https://evil.test/path",
    "//evil.test/path",
    "/\\evil.test/path",
    "javascript:alert(1)",
    "%68%74%74%70%73%3A%2F%2Fevil.test",
    "/%2F%2Fevil.test",
])
def test_unsafe_redirect_targets_are_rejected(value):
    with precision_app.app.test_request_context("/"):
        assert precision_app.safe_internal_redirect_target(value, "/dashboard", log_rejection=False) == "/dashboard"


def test_relative_internal_redirect_and_login_next_behavior():
    with precision_app.app.test_request_context("/"):
        assert precision_app.safe_internal_redirect_target("/profile?tab=account", "/dashboard") == "/profile?tab=account"
    client, _user = _client()
    client.post("/logout")
    accepted = client.post(
        "/login",
        data={"email": "batch2b@example.test", "password": PASSWORD, "next": "/profile"},
    )
    assert accepted.headers["Location"].endswith("/profile")
    client.post("/logout")
    rejected = client.post(
        "/login",
        data={"email": "batch2b@example.test", "password": PASSWORD, "next": "https://evil.test"},
    )
    assert rejected.headers["Location"].endswith("/dashboard")


def test_environment_aware_secret_and_cookie_configuration(caplog):
    with pytest.raises(ProductionConfigurationError):
        validate_production_configuration({"APP_ENV": "production", "DEMO_MODE": "false"})
    stable_secret = "stable-production-secret-key-1234567890"
    production = web_security_configuration({
        "APP_ENV": "production",
        "APP_BASE_URL": "https://precision-curator.example.com",
        "DEMO_MODE": "false",
        "FLASK_SECRET_KEY": stable_secret,
        "SESSION_LIFETIME_HOURS": "12",
    })
    assert production["SECRET_KEY"] == stable_secret
    assert production["SESSION_COOKIE_SECURE"] is True
    assert production["SESSION_COOKIE_HTTPONLY"] is True
    assert production["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert production["PERMANENT_SESSION_LIFETIME"] == timedelta(hours=12)
    development = web_security_configuration({"APP_ENV": "development"})
    assert development["SESSION_COOKIE_SECURE"] is False
    assert stable_secret not in caplog.text
    example = Path(".env.example").read_text(encoding="utf-8")
    assert "replace-with-a-random-secret-at-least-32-characters" in example
    assert stable_secret not in example


def test_get_requests_do_not_run_mutation_workflows():
    client, user = _client()
    before = len(precision_app.repository.list_searches(user["_id"], limit=0))
    assert client.get("/search?q=phone&action=search").status_code == 302
    assert client.get("/search-legacy?q=phone").status_code == 200
    assert client.get("/search-mongo?q=phone&action=search").status_code == 200
    assert client.get("/api/search?q=phone&source=demo").status_code == 405
    assert client.get("/compare").status_code == 405
    assert client.get("/test-data/seed-watchlist-snapshots").status_code == 405
    assert client.get("/watchlist/000000000000000000000001/refresh").status_code == 405
    assert client.get("/watchlist/000000000000000000000001/prediction").status_code == 405
    assert len(precision_app.repository.list_searches(user["_id"], limit=0)) == before


def test_normal_search_post_redirect_get_flow_remains_available():
    client, _user = _client()
    response = client.post("/search", data={"q": "phone", "data_source": "mongodb", "action": "search"})
    assert response.status_code == 302
    assert "/search/results/" in response.headers["Location"]
    result = client.get(response.headers["Location"])
    assert result.status_code == 200
