import csv
import io

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

import precision_app
from services.database import MongoRepository
from services.runtime_config import ProductionConfigurationError, validate_production_configuration
from tools.create_admin import bootstrap_admin


TEST_PASSWORD = "OfflineTestPassword1!"
DEMO_ROUTES = (
    ("get", "/demo"),
    ("get", "/test-data"),
    ("post", "/test-data/seed-watchlist-snapshots"),
    ("post", "/test-data/seed-watchlist-validation"),
    ("post", "/test-data/delete"),
    ("post", "/test-data/reset"),
)


def _setup_client():
    precision_app.app.config.update(
        TESTING=True,
        SECRET_KEY="security-batch-one-tests",
        RUNTIME_ENVIRONMENT="test",
        DEMO_TOOLS_ENABLED=False,
        DEMO_MEMBERSHIP_UPGRADE_ENABLED=False,
    )
    precision_app.repository = MongoRepository(uri="", database_name="security_batch_one")
    return precision_app.app.test_client()


def _create_user(name, role="consumer", tier="professional"):
    email = f"{''.join(character.lower() for character in name if character.isalnum())}@example.test"
    user = precision_app.repository.create_user(
        name,
        email,
        generate_password_hash(TEST_PASSWORD, method="pbkdf2:sha256:1000"),
        role,
        roles=[role],
        active_role=role,
        plan=tier,
        membership_tier=tier,
    )
    return user


def _login(client, user):
    return client.post("/login", data={"email": user["email"], "password": TEST_PASSWORD})


def _csv_rows(response):
    return list(csv.reader(io.StringIO(response.get_data(as_text=True))))


def test_login_rejects_empty_username_role_and_administrator_only_payloads():
    client = _setup_client()
    before = len(precision_app.repository.list_users(limit=0))
    payloads = (
        {},
        {"email": "", "password": ""},
        {"username": "Mock User", "role": "consumer"},
        {"username": "Backdoor Admin", "role": "administrator"},
    )
    for payload in payloads:
        response = client.post("/login", data=payload)
        assert response.status_code == 200
        assert b"Invalid email or password" in response.data
        with client.session_transaction() as session:
            assert "user_id" not in session
    assert len(precision_app.repository.list_users(limit=0)) == before


def test_invalid_credentials_do_not_create_user_and_valid_password_login_still_works():
    client = _setup_client()
    response = client.post("/login", data={"email": "missing@example.test", "password": "WrongPassword1!", "role": "administrator"})
    assert response.status_code == 200
    assert precision_app.repository.get_user_by_email("missing@example.test") is None
    user = _create_user("Normal Login", tier="basic")
    assert _login(client, user).status_code == 302
    with client.session_transaction() as session:
        assert session["user_id"] == user["_id"]
        assert session["role"] == "consumer"


def test_normal_startup_state_has_no_default_administrator_and_seed_does_not_reset_password():
    _setup_client()
    assert precision_app.repository.get_user_by_email("admin@precision.local") is None
    original_hash = generate_password_hash(TEST_PASSWORD, method="pbkdf2:sha256:1000")
    user = precision_app.repository.create_user(
        "Administrator",
        "secure-admin@example.test",
        original_hash,
        "administrator",
        roles=["administrator"],
        active_role="administrator",
        plan="professional",
        membership_tier="professional",
    )
    precision_app.repository.seed_admin_user("Administrator", user["email"], "DifferentPassword2!", reset_password=False)
    unchanged = precision_app.repository.get_user_by_email(user["email"])
    assert unchanged["password_hash"] == original_hash
    assert check_password_hash(unchanged["password_hash"], TEST_PASSWORD)


def test_explicit_admin_bootstrap_hashes_password_and_requires_reset_for_overwrite(monkeypatch):
    repository = MongoRepository(uri="", database_name="bootstrap_tests")
    monkeypatch.setattr(
        "tools.create_admin.generate_password_hash",
        lambda value: generate_password_hash(value, method="pbkdf2:sha256:1000"),
    )
    user, action = bootstrap_admin(repository, "Admin@Example.Test", "UniqueAdminPassword1!")
    assert action == "created"
    assert user["email_lower"] == "admin@example.test"
    assert user["password_hash"] != "UniqueAdminPassword1!"
    assert check_password_hash(user["password_hash"], "UniqueAdminPassword1!")
    with pytest.raises(ValueError, match="already exists"):
        bootstrap_admin(repository, user["email"], "AnotherAdminPassword2!")
    reset_user, action = bootstrap_admin(repository, user["email"], "AnotherAdminPassword2!", reset_password=True)
    assert action == "reset"
    assert check_password_hash(reset_user["password_hash"], "AnotherAdminPassword2!")


def test_non_admin_audit_page_and_csv_are_owner_scoped():
    client = _setup_client()
    owner = _create_user("Audit Owner", "consumer")
    other = _create_user("Audit Other", "consumer")
    precision_app.repository.create_search(owner["_id"], "owner-private-query", "ebay", role="consumer")
    precision_app.repository.create_search(other["_id"], "other-private-query", "ebay", role="consumer")
    precision_app.repository.log_event(owner["_id"], "search_started", "consumer", owner["display_name"], {"query": "owner-event"})
    precision_app.repository.log_event(other["_id"], "search_started", "consumer", other["display_name"], {"query": "other-event"})
    _login(client, owner)
    page = client.get("/audit").get_data(as_text=True)
    assert "owner-private-query" in page
    assert "other-private-query" not in page
    csv_text = client.get("/audit/export/source-audit.csv").get_data(as_text=True)
    assert "owner-private-query" in csv_text
    assert "other-private-query" not in csv_text


def test_researcher_logs_are_owner_scoped_and_retailer_cannot_read_logs():
    client = _setup_client()
    researcher = _create_user("Research Owner", "researcher")
    other = _create_user("Research Other", "researcher")
    precision_app.repository.log_event(researcher["_id"], "search_started", "researcher", researcher["display_name"], {"query": "research-owner-event"})
    precision_app.repository.log_event(other["_id"], "search_started", "researcher", other["display_name"], {"query": "research-other-event"})
    _login(client, researcher)
    logs = client.get("/logs").get_data(as_text=True)
    assert "research-owner-event" in logs
    assert "research-other-event" not in logs
    activity_csv = client.get("/audit/export/activity-log.csv").get_data(as_text=True)
    assert "research-owner-event" in activity_csv
    assert "research-other-event" not in activity_csv
    client.post("/logout")
    retailer = _create_user("Retail Log User", "retailer")
    _login(client, retailer)
    retailer_logs = client.get("/logs").get_data(as_text=True)
    assert "research-other-event" not in retailer_logs


def test_researcher_dashboard_metrics_and_activity_are_owner_scoped():
    client = _setup_client()
    researcher = _create_user("Dashboard Research Owner", "researcher")
    other = _create_user("Dashboard Research Other", "researcher")
    owner_search = precision_app.repository.create_search(
        researcher["_id"], "owner-dashboard-query", "ebay", role="researcher"
    )
    other_search = precision_app.repository.create_search(
        other["_id"], "other-dashboard-query", "ebay", role="researcher"
    )
    precision_app.repository.save_evidence(
        researcher["_id"],
        owner_search,
        {"title": "Owner evidence title", "platform": "eBay", "price": 499, "currency": "USD"},
    )
    precision_app.repository.save_evidence(
        other["_id"],
        other_search,
        {"title": "Other evidence title", "platform": "Walmart", "price": 599, "currency": "USD"},
    )
    precision_app.repository.save_research(researcher["_id"], {"title": "Owner research package"})
    precision_app.repository.save_research(other["_id"], {"title": "Other research package"})
    precision_app.repository.log_ai_search(
        researcher["_id"], "owner-ai-dashboard", "offline-model", "Owner summary", "Owner response"
    )
    precision_app.repository.log_ai_search(
        other["_id"], "other-ai-dashboard", "offline-model", "Other summary", "Other response"
    )
    precision_app.repository.log_event(
        researcher["_id"], "search_started", "researcher", researcher["display_name"], {"query": "owner-dashboard-event"}
    )
    precision_app.repository.log_event(
        other["_id"], "api_call_failed", "researcher", other["display_name"], {"query": "other-dashboard-event"}
    )

    _login(client, researcher)
    response = client.get("/dashboard/researcher")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Owner evidence title" in body
    assert "owner-ai-dashboard" in body
    assert "owner-dashboard-event" in body
    assert "Other evidence title" not in body
    assert "other-ai-dashboard" not in body
    assert "other-dashboard-event" not in body
    view = precision_app.build_researcher_dashboard_view(researcher["_id"])
    assert view["snapshot"]["search_records"] == 1
    assert view["snapshot"]["evidence_records"] == 1
    assert view["snapshot"]["research_records"] == 1
    assert view["source_warning_count"] == 0


def test_administrator_audit_scope_is_global():
    client = _setup_client()
    administrator = _create_user("Global Admin", "administrator")
    other = _create_user("Global Other", "consumer")
    precision_app.repository.create_search(other["_id"], "administrator-visible-query", "ebay", role="consumer")
    _login(client, administrator)
    response = client.get("/audit")
    assert response.status_code == 200
    assert b"administrator-visible-query" in response.data


def test_non_admin_ai_activity_export_is_owner_scoped():
    client = _setup_client()
    owner = _create_user("AI Owner", "consumer")
    other = _create_user("AI Other", "consumer")
    precision_app.repository.log_ai_activity(owner["_id"], "owner-ai-query", "offline-model", "Offline", 2)
    precision_app.repository.log_ai_activity(other["_id"], "other-ai-query", "offline-model", "Offline", 2)
    precision_app.repository.log_ai_search(owner["_id"], "owner-summary-query", "offline-model", "prompt", "response")
    precision_app.repository.log_ai_search(other["_id"], "other-summary-query", "offline-model", "prompt", "response")
    _login(client, owner)
    export = client.get("/audit/export/ai-activity-log.csv").get_data(as_text=True)
    assert "owner-ai-query" in export
    assert "owner-summary-query" in export
    assert "other-ai-query" not in export
    assert "other-summary-query" not in export


def test_membership_upgrade_requires_explicit_non_production_demo_policy():
    client = _setup_client()
    user = _create_user("Basic Member", tier="basic")
    _login(client, user)
    assert client.post("/membership/upgrade", data={"membership_tier": "professional"}).status_code == 403
    assert precision_app.repository.get_user_by_id(user["_id"])["membership_tier"] == "basic"
    precision_app.app.config.update(DEMO_MEMBERSHIP_UPGRADE_ENABLED=True, RUNTIME_ENVIRONMENT="production")
    assert client.post("/membership/upgrade", data={"membership_tier": "professional"}).status_code == 403
    precision_app.app.config.update(RUNTIME_ENVIRONMENT="test")
    assert client.post("/membership/upgrade", data={"membership_tier": "premium"}).status_code == 302
    assert precision_app.repository.get_user_by_id(user["_id"])["membership_tier"] == "premium"


def test_all_demo_routes_are_blocked_without_flag_and_cannot_mutate():
    client = _setup_client()
    administrator = _create_user("Demo Admin", "administrator")
    _login(client, administrator)
    before = {
        "testing": len(precision_app.repository.list_testing_records(limit=0)),
        "watchlists": len(precision_app.repository.list_watchlist_items(user_id=administrator["_id"], limit=0)),
        "snapshots": len(precision_app.repository._memory["price_snapshots"]),
        "predictions": len(precision_app.repository.list_predictions(administrator["_id"], limit=0)),
    }
    for method, path in DEMO_ROUTES:
        response = getattr(client, method)(path)
        assert response.status_code == 404
    after = {
        "testing": len(precision_app.repository.list_testing_records(limit=0)),
        "watchlists": len(precision_app.repository.list_watchlist_items(user_id=administrator["_id"], limit=0)),
        "snapshots": len(precision_app.repository._memory["price_snapshots"]),
        "predictions": len(precision_app.repository.list_predictions(administrator["_id"], limit=0)),
    }
    assert after == before


def test_demo_get_routes_do_not_seed_and_demo_mutations_require_approved_operator():
    client = _setup_client()
    precision_app.app.config["DEMO_TOOLS_ENABLED"] = True
    consumer = _create_user("Demo Consumer", "consumer")
    _login(client, consumer)
    assert client.get("/demo").status_code == 404
    assert precision_app.repository.list_testing_records(limit=0) == []
    client.post("/logout")
    administrator = _create_user("Approved Demo Admin", "administrator")
    _login(client, administrator)
    assert client.get("/demo").status_code == 200
    assert client.get("/test-data").status_code == 200
    assert precision_app.repository.list_testing_records(limit=0) == []


def test_production_rejects_missing_mongodb_and_memory_fallback(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_MODE", "false")
    with pytest.raises(RuntimeError, match="MONGO_URI is required"):
        MongoRepository(uri="")
    with pytest.raises(ProductionConfigurationError, match="cannot be enabled"):
        MongoRepository(uri="", allow_memory_fallback=True)
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("APP_ENV", "test")
    repository = MongoRepository(uri="", allow_memory_fallback=True)
    assert repository.mode == "memory_fallback"


def test_production_startup_validation_rejects_unsafe_flags_and_missing_secret():
    with pytest.raises(ProductionConfigurationError) as error:
        validate_production_configuration({
            "APP_ENV": "production",
            "FLASK_SECRET_KEY": "",
            "DEMO_LOGIN_ENABLED": "true",
            "DEMO_TOOLS_ENABLED": "true",
            "DEMO_MEMBERSHIP_UPGRADE_ENABLED": "true",
            "DEMO_MODE": "true",
            "PRECISION_ADMIN_PASSWORD": "Admin123!",
        })
    message = str(error.value)
    assert "FLASK_SECRET_KEY" in message
    assert "DEMO_LOGIN_ENABLED" in message
    assert "DEMO_TOOLS_ENABLED" in message
    assert "DEMO_MEMBERSHIP_UPGRADE_ENABLED" in message
    assert "DEMO_MODE" in message
    assert "default administrator password" in message
