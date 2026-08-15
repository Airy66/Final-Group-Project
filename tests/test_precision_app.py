import csv
import io
import json
from datetime import datetime
from decimal import Decimal

import precision_app
from services.database import MongoRepository
from services import ai_search
from bs4 import BeautifulSoup
from bson import ObjectId


def client():
    precision_app.app.config.update(
        TESTING=True,
        SECRET_KEY="test-only",
        DEVELOPER_ROLE_PREVIEW=False,
        DEMO_TOOLS_ENABLED=False,
        DEMO_MEMBERSHIP_UPGRADE_ENABLED=False,
        REGISTRATION_WELCOME_EMAIL_ENABLED=False,
        RUNTIME_ENVIRONMENT="test",
    )
    # Keep this legacy integration module deterministic and fully offline.
    # Individual provider tests monkeypatch their provider functions directly.
    precision_app.MOCK_SEARCH_MODE = True
    precision_app.SERPAPI_MARKETPLACE_ENABLED = False
    precision_app.WALMART_BROWSER_FALLBACK = False
    precision_app.SHOW_DEMO_FALLBACK = False
    precision_app.repository = MongoRepository(uri="", database_name="test_precision_curator")
    return precision_app.app.test_client()


def login(test_client, username="Alex", role="consumer", membership_tier=None):
    email_local = "".join(character.lower() for character in username if character.isalnum()) or "user"
    email = f"{email_local}@example.test"
    password = "OfflineTestPassword1!"
    user = precision_app.repository.get_user_by_email(email)
    if not user:
        user = precision_app.repository.create_user(
            username,
            email,
            precision_app.generate_password_hash(password, method="pbkdf2:sha256:1000"),
            role,
            roles=[role],
            active_role=role,
        )
    else:
        assigned_roles = list(user.get("roles") or [user.get("role") or "consumer"])
        if role not in assigned_roles:
            assigned_roles.append(role)
        precision_app.repository.update_user(user["_id"], {"roles": assigned_roles, "active_role": role, "role": role})
        user = precision_app.repository.get_user_by_id(user["_id"])
    if membership_tier:
        precision_app.repository.update_user(user["_id"], {"membership_tier": membership_tier, "plan": membership_tier})
    return test_client.post("/login", data={"email": email, "password": password})


def test_new_user_defaults_to_basic_membership():
    repo = MongoRepository(uri="", database_name="test_precision_curator_defaults")
    user = repo.create_user("NewUser", "new@example.com", "hash", "consumer")
    assert user["membership_tier"] == "basic"
    assert user["plan"] == "basic"


def test_academic_membership_defaults_safely_to_basic():
    repo = MongoRepository(uri="", database_name="test_precision_curator_academic")
    user = repo.create_user("Legacy", "legacy@example.com", "hash", "consumer", plan="academic", membership_tier="academic")
    assert user["membership_tier"] == "basic"
    assert user["plan"] == "basic"


def test_membership_access_matrix_and_locked_controls():
    consumer_basic = {"role": "consumer", "active_role": "consumer", "membership_tier": "basic"}
    consumer_premium = {"role": "consumer", "active_role": "consumer", "membership_tier": "premium"}
    consumer_professional = {"role": "consumer", "active_role": "consumer", "membership_tier": "professional"}
    retailer_basic = {"role": "retailer", "active_role": "retailer", "membership_tier": "basic"}
    retailer_premium = {"role": "retailer", "active_role": "retailer", "membership_tier": "premium"}
    retailer_professional = {"role": "retailer", "active_role": "retailer", "membership_tier": "professional"}
    researcher_basic = {"role": "researcher", "active_role": "researcher", "membership_tier": "basic"}
    researcher_professional = {"role": "researcher", "active_role": "researcher", "membership_tier": "professional"}
    admin = {"role": "administrator", "active_role": "administrator", "membership_tier": "professional"}

    assert precision_app.can_access(consumer_basic, "product_search")
    assert not precision_app.can_access(consumer_basic, "watchlist")
    assert precision_app.can_access(consumer_premium, "watchlist")
    assert precision_app.can_access(consumer_premium, "prediction")
    assert precision_app.can_access(consumer_premium, "prediction_validation")
    assert precision_app.can_access(consumer_professional, "prediction_validation")
    assert not precision_app.can_access(retailer_basic, "export_report")
    assert precision_app.can_access(retailer_premium, "analytics_dashboard")
    assert precision_app.can_access(retailer_professional, "prediction_validation")
    assert not precision_app.can_access(researcher_basic, "source_audit")
    assert not precision_app.can_access(consumer_premium, "source_audit")
    assert precision_app.can_access(researcher_professional, "export_report")
    assert precision_app.can_access(admin, "research_package")
    assert precision_app.membership_rank("basic") == 1
    assert precision_app.membership_rank("premium") == 2
    assert precision_app.membership_rank("professional") == 3


def test_security_invalid_object_ids_return_friendly_not_found():
    test_client = client()
    login(test_client, membership_tier="premium")
    for path in ["/analytics/not-a-valid-id", "/analytics/not-a-valid-id/export/results.csv"]:
        response = test_client.get(path)
        assert response.status_code == 404
        assert b"That record or page could not be found." in response.data


def test_security_invalid_source_url_is_not_clickable():
    test_client = client()
    login(test_client, username="UnsafeUrl", membership_tier="premium")
    user = precision_app.repository.get_user_by_display_name("UnsafeUrl")
    search_id = precision_app.repository.create_search(user["_id"], "unsafe url phone", "mongodb", role="consumer", data_mode="mongodb")
    precision_app.repository.save_products(search_id, user["_id"], [{
        "title": "Unsafe URL listing",
        "platform": "Test Market",
        "price": 199.0,
        "currency": "USD",
        "seller": "Synthetic seller",
        "condition": "New",
        "source_type": "Test Source",
        "source_url": "javascript:alert(1)",
        "confidence_level": "medium",
    }])
    precision_app.repository.complete_search(search_id, 1)
    page = test_client.get(f"/analytics/{search_id}")
    body = page.data.decode("utf-8", errors="ignore")
    assert page.status_code == 200
    assert "javascript:alert" not in body
    assert "Unavailable" in body


def test_analytics_title_falls_back_for_suspicious_query(monkeypatch):
    test_client = client()
    login(test_client, username="Analyst", membership_tier="premium")
    user = precision_app.repository.get_user_by_display_name("Analyst")
    search_id = precision_app.repository.create_search(user["_id"], "iPhone 14", "mongodb", role="consumer", data_mode="mongodb")
    precision_app.repository.save_products(search_id, user["_id"], [{
        "title": "Sample listing",
        "platform": "Test Market",
        "price": 199.0,
        "currency": "USD",
        "seller": "Seller",
        "condition": "New",
        "source_type": "Test Source",
        "source_url": "https://example.com",
    }])
    precision_app.repository.complete_search(search_id, 1)
    if precision_app.repository.db is None:
        precision_app.repository._memory["search_records"][0]["keyword"] = {"$gt": ""}
    else:
        precision_app.repository.db.search_records.update_one({"_id": ObjectId(search_id)}, {"$set": {"keyword": {"$gt": ""}}})
    page = test_client.get(f"/analytics/{search_id}")
    assert page.status_code == 200
    body = page.data.decode("utf-8", errors="ignore")
    assert "{$gt" not in body
    assert "Search analytics" in body


def test_analytics_title_filters_string_operator_query():
    test_client = client()
    login(test_client, username="Analyst2", membership_tier="premium")
    user = precision_app.repository.get_user_by_display_name("Analyst2")
    search_id = precision_app.repository.create_search(user["_id"], '{"$ne": null}', "mongodb", role="consumer", data_mode="mongodb")
    precision_app.repository.save_products(search_id, user["_id"], [{
        "title": "Sample listing",
        "platform": "Test Market",
        "price": 199.0,
        "currency": "USD",
        "seller": "Seller",
        "condition": "New",
        "source_type": "Test Source",
        "source_url": "https://example.com",
    }])
    precision_app.repository.complete_search(search_id, 1)
    page = test_client.get(f"/analytics/{search_id}")
    assert page.status_code == 200
    body = page.data.decode("utf-8", errors="ignore")
    assert '{"$ne": null}' not in body
    assert "Filtered search result" in body


def test_validation_special_character_search_does_not_crash():
    test_client = client()
    login(test_client, membership_tier="premium")
    response = test_client.post("/search", follow_redirects=True, query_string={"q": "<script>$ne</script> phone", "data_source": "mongodb", "action": "search"})
    assert response.status_code == 200
    body = response.data.decode("utf-8", errors="ignore")
    assert "The search term contains unsupported characters" in body
    assert precision_app.repository.list_searches(limit=1) == []


def test_invalid_search_operator_is_rejected_without_record_creation():
    test_client = client()
    login(test_client, membership_tier="premium")
    response = test_client.post("/search", follow_redirects=True, query_string={"q": '{"$gt": ""}', "data_source": "mongodb", "action": "search"})
    assert response.status_code == 200
    body = response.data.decode("utf-8", errors="ignore")
    assert "The search term contains unsupported characters" in body
    assert precision_app.repository.list_searches(limit=1) == []
    audit_logs = precision_app.repository.list_audit_logs(limit=5)
    assert any(row.get("event_type") == "blocked_search_input" for row in audit_logs)


def test_blocked_xss_search_does_not_enter_history():
    test_client = client()
    login(test_client, membership_tier="premium")
    response = test_client.post("/search", follow_redirects=True, query_string={"q": "<script>alert(1)</script> phone", "data_source": "all", "action": "search"})
    assert response.status_code == 200
    body = response.data.decode("utf-8", errors="ignore")
    assert "The search term contains unsupported characters" in body
    assert precision_app.repository.list_searches(limit=1) == []
    audit_logs = precision_app.repository.list_audit_logs(limit=5)
    assert any(row.get("event_type") == "blocked_search_input" for row in audit_logs)


def test_cleanup_search_history_removes_security_payloads_and_demo_sources():
    repo = precision_app.repository
    user = repo.get_user_by_display_name("Alex")
    repo.create_search(user["_id"], "iPhone 14", "ebay", role="consumer", data_mode="api")
    repo.create_search(user["_id"], '{"$gt": ""}', "mongodb", role="consumer", data_mode="mongodb")
    repo.create_search(user["_id"], "Mock product", "demo", role="consumer", data_mode="synthetic")
    result = precision_app.cleanup_search_history(user["_id"])
    remaining = repo.list_searches(user["_id"], limit=10)
    assert result["removed"] >= 2
    assert all("iPhone 14" in row["keyword"] for row in remaining)


def test_homepage_and_register_show_three_membership_plans():
    test_client = client()
    home = test_client.get("/")
    assert home.status_code == 200
    home_body = home.data.decode("utf-8", errors="ignore")
    assert "Basic" in home_body and "Premium" in home_body and "Professional" in home_body
    assert "Academic" not in home_body
    assert "Recommended" in home_body
    assert "precision-curator-logo.png" in home_body
    assert 'href="/#membership"' in home_body
    assert 'id="features"' in home_body
    assert 'href="/#features"' in home_body
    assert 'href="/#roles"' in home_body and 'href="/#faq"' in home_body
    assert "Price evidence," in home_body and "curated with precision." in home_body
    assert "Explore sample workspace" in home_body
    assert "What is Precision Curator?" in home_body
    assert "Different market decisions need different starting points." in home_body
    assert 'id="product-difference"' not in home_body
    assert "Typical price search" not in home_body
    assert "Evidence model" not in home_body
    assert "Connected services" not in home_body
    assert "MongoDB Atlas" not in home_body
    assert "Everything needed to turn listings into a confident decision." not in home_body
    assert "Illustrative monitored price trend" not in home_body
    assert 'data-product-panel=' not in home_body
    assert "Reliable by design" in home_body
    assert "Saved across sessions" in home_body
    assert "Built into the product" in home_body
    assert "Frequently asked questions" in home_body
    assert "app-button-primary" in home_body and "app-card-hover" in home_body
    assert "From the first search to a decision you can revisit." in home_body
    assert all(step in home_body for step in ("Search", "Compare", "Save", "Monitor", "Validate"))
    assert "Research package export" in home_body
    assert "Can I use multiple workspace roles on one account?" in home_body
    assert "Each account has exactly one assigned role" in home_body
    assert "Can my assigned role be changed later?" in home_body
    assert "The new role replaces the existing role" in home_body
    assert "Live workspace preview" not in home_body
    assert "12 sources scanned" not in home_body
    assert "Representative feedback" not in home_body
    assert "Maya Chen" not in home_body and "Daniel Lim" not in home_body and "Aisha Tan" not in home_body

    register = test_client.get("/register?plan=premium")
    assert register.status_code == 200
    register_body = register.data.decode("utf-8", errors="ignore")
    assert 'name="membership_tier" value="premium"' in register_body
    assert "app-choice-input" in register_body
    assert 'name="role" value="administrator"' not in register_body
    assert "/ month" in register_body and "Source Audit" in register_body
    assert "Choose your workspace" in register_body
    assert "Use at least 8 characters." in register_body
    assert "Choose your workspace." in register_body
    assert "Each account has one workspace designed around how you use the product." in register_body
    assert "Choose an access level." in register_body
    assert "Demo pricing · no payment collected" in register_body
    assert "Administrator accounts are assigned internally." not in register_body
    assert "Administrators can update role assignments" not in register_body
    assert "For simple product discovery and essential price comparison." in register_body
    assert "For saved workflows, watchlist tracking, analytics, and AI-assisted forecasting." in register_body
    assert "For advanced research, source audit, provenance, activity logs, and exportable reports." in register_body
    assert "data-membership-plan-list" in register_body
    assert "data-plan-row" in register_body
    assert "[grid-template-columns:repeat(auto-fit,minmax(180px,1fr))]" not in register_body
    assert "plan-card-header" not in register_body
    assert "plan-price" not in register_body
    assert "absolute" not in register_body[register_body.find("Membership plan"):]
    professional = test_client.get("/register?plan=professional")
    professional_body = professional.data.decode("utf-8", errors="ignore")
    assert 'name="membership_tier" value="professional"' in professional_body
    assert "is-selected" in professional_body


def test_register_rejects_short_and_mismatched_passwords():
    test_client = client()
    short = test_client.post(
        "/register",
        data={
            "display_name": "Short Password",
            "email": "short-password@example.com",
            "password": "short",
            "confirm_password": "short",
            "role": "consumer",
            "membership_tier": "basic",
        },
    )
    assert short.status_code == 200
    assert b"Password must be at least 8 characters long." in short.data
    assert precision_app.repository.get_user_by_display_name("Short Password") is None

    mismatch = test_client.post(
        "/register",
        data={
            "display_name": "Mismatch Password",
            "email": "mismatch-password@example.com",
            "password": "Password123",
            "confirm_password": "Password456",
            "role": "consumer",
            "membership_tier": "premium",
        },
    )
    assert mismatch.status_code == 200
    assert b"Passwords do not match." in mismatch.data
    assert precision_app.repository.get_user_by_display_name("Mismatch Password") is None


def test_register_submits_premium_and_professional_memberships():
    test_client = client()
    premium = test_client.post(
        "/register",
        data={
            "display_name": "Premium Signup",
            "email": "premium-signup@example.com",
            "password": "Password123",
            "confirm_password": "Password123",
            "role": "retailer",
            "membership_tier": "premium",
        },
    )
    assert premium.status_code == 302
    premium_user = precision_app.repository.get_user_by_display_name("Premium Signup")
    assert premium_user["membership_tier"] == "premium"
    assert premium_user["primary_role"] == "retailer"
    assert premium_user["roles"] == ["retailer"]
    assert premium_user["active_role"] == "retailer"

    professional = test_client.post(
        "/register",
        data={
            "display_name": "Professional Signup",
            "email": "professional-signup@example.com",
            "password": "Password123",
            "confirm_password": "Password123",
            "role": "researcher",
            "membership_tier": "professional",
        },
    )
    assert professional.status_code == 302
    professional_user = precision_app.repository.get_user_by_display_name("Professional Signup")
    assert professional_user["membership_tier"] == "professional"
    assert professional_user["primary_role"] == "researcher"
    assert professional_user["roles"] == ["researcher"]
    assert professional_user["active_role"] == "researcher"


def test_admin_accounts_do_not_show_membership_controls():
    test_client = client()
    login(test_client, username="AdminUser", role="administrator", membership_tier="professional")
    page = test_client.get("/dashboard/administrator")
    assert page.status_code == 200
    body = page.data.decode("utf-8", errors="ignore")
    assert "Academic" not in body
    assert "System access" in body
    assert "System administrator access" in body
    assert "Membership plan" not in body
    assert "membership_tier" not in body
    assert "Service health" in body and "Recent activity" in body
    assert "Recent system activity" not in body and "Activity review snapshot" not in body
    assert 'colspan="6"' in body
    assert "max-h-[720px]" in body

    membership_page = test_client.get("/membership", follow_redirects=False)
    assert membership_page.status_code == 302
    assert "/dashboard/administrator" in membership_page.headers["Location"]

    profile_page = test_client.get("/profile")
    profile_body = profile_page.get_data(as_text=True)
    assert "System access" in profile_body
    assert "Administrator account" in profile_body
    assert "System administrator" in profile_body
    assert "Membership summary" not in profile_body
    assert "Professional Plan" not in profile_body
    assert "Upgrade / manage plan" not in profile_body


def test_administrator_can_archive_mixed_evidence_history_records():
    test_client = client()
    login(test_client, username="ArchiveAdmin", role="administrator", membership_tier="basic")
    user = precision_app.repository.get_user_by_display_name("ArchiveAdmin")
    search_id = precision_app.repository.create_search(user["_id"], "archive-this-search", "ebay", status="completed")
    ai_id = precision_app.repository.log_ai_activity(user["_id"], "archive-this-ai-log", "test-model", "records", 1)
    audit_id = precision_app.repository.log_event(user["_id"], "search_started", "administrator", "ArchiveAdmin", {"query": "archive-this-event"})

    audit_page = test_client.get("/audit").get_data(as_text=True)
    logs_page = test_client.get("/logs").get_data(as_text=True)
    assert 'id="audit-archive-form"' in audit_page and 'name="record_ref"' in audit_page
    assert 'id="log-archive-form"' in logs_page and 'name="log_id"' in logs_page

    response = test_client.post(
        "/audit/archive",
        data={"record_ref": [f"search_records:{search_id}", f"ai_search_logs:{ai_id}", f"audit_logs:{audit_id}"]},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert all(row.get("keyword") != "archive-this-search" for row in precision_app.repository.list_searches(limit=0))
    assert all(row.get("keyword") != "archive-this-ai-log" for row in precision_app.repository.list_ai_logs(limit=0))
    assert all(str(row.get("_id")) != str(audit_id) for row in precision_app.repository.list_audit_logs(limit=0))


def test_logs_membership_access():
    test_client = client()
    login(test_client, username="ResearcherBasicLogs", role="researcher", membership_tier="basic")
    basic = test_client.get("/logs")
    assert basic.status_code == 200
    assert b"Keep a chronological record of research actions." in basic.data
    assert b"AI traceability" in basic.data
    assert b"Requires Professional" in basic.data

    test_client.post("/logout")
    login(test_client, username="ResearcherPremiumLogs", role="researcher", membership_tier="premium")
    premium = test_client.get("/logs")
    assert premium.status_code == 200
    assert b"Keep a chronological record of research actions." in premium.data
    assert b"Upgrade to Professional" in premium.data

    test_client.post("/logout")
    login(test_client, username="ResearcherProLogs", role="researcher", membership_tier="professional")
    professional = test_client.get("/logs")
    assert professional.status_code == 200
    assert b'href="/audit/export/activity-log.csv"' in professional.data
    assert b'<span>Export</span>' in professional.data

    test_client.post("/logout")
    login(test_client, username="AdminLogs", role="administrator", membership_tier="basic")
    admin = test_client.get("/logs")
    assert admin.status_code == 200
    assert b'href="/audit/export/activity-log.csv"' in admin.data
    assert b'<span>Export</span>' in admin.data


def test_activity_log_categories_match_event_semantics():
    assert precision_app._log_category("search_started") == "Search records"
    assert precision_app._log_category("records_collected") == "Search records"
    assert precision_app._log_category("prediction_created") == "Workflow events"
    assert precision_app._log_category("watchlist_created") == "Workflow events"
    assert precision_app._log_category("comparison_generated") == "Workflow events"
    assert precision_app._log_category("logout") == "Admin actions"
    assert precision_app._log_category("ai_discover") == "AI logs"
    assert precision_app._log_category("api_call_failed", "fallback applied") == "Errors / warnings"


def test_membership_page_clarifies_account_plan_and_single_assigned_workspace():
    test_client = client()
    login(test_client, username="UpgradeBasic", role="consumer", membership_tier="basic")
    page = test_client.get("/membership")
    body = page.data.decode("utf-8", errors="ignore")
    soup = BeautifulSoup(page.data, "html.parser")
    assert page.status_code == 200
    assert "Membership and access" in body
    assert "Review the features available under each membership plan. Membership changes are managed by the administrator in this academic prototype." in body
    assert "Current plan" in body
    assert soup.select_one("[data-current-membership]").get_text(" ", strip=True) == "Current: Basic"
    assert "Prototype pricing for demonstration only. No live billing is processed." in body
    assert "Your assigned workspace" in body
    assert soup.select_one("[data-current-workspace-role]").get_text(" ", strip=True) == "Consumer"
    assert "Each account has one assigned workspace role." in body
    assert "Workspace roles are not combined." in body
    assert "A role change replaces the current role" in body
    assert "Membership is account-wide" in body
    assert "managed separately from the workspace role" in body
    assert "Contact administrator" in body
    assert "No role change is applied from this page." in body
    assert body.count("Administrator managed") == 2
    assert not soup.select('form[action="/membership/upgrade"]')
    assert not soup.select('[name="role"], [name="roles"], [multiple], input[type="checkbox"]')
    for purchase_copy in ("Buy", "Subscribe", "Upgrade now", "Checkout"):
        assert purchase_copy not in body

    blocked = test_client.post("/membership/upgrade", data={"membership_tier": "professional"})
    assert blocked.status_code == 403
    user = precision_app.repository.get_user_by_display_name("UpgradeBasic")
    assert user["membership_tier"] == "basic"
    assert user["active_role"] == user["role"] == "consumer"

    precision_app.app.config["DEMO_MEMBERSHIP_UPGRADE_ENABLED"] = True
    still_noninteractive = test_client.get("/membership")
    assert b'form action="/membership/upgrade"' not in still_noninteractive.data
    upgraded = test_client.post(
        "/membership/upgrade",
        data={"membership_tier": "premium", "role": "researcher", "roles": ["researcher", "retailer"]},
    )
    assert upgraded.status_code == 302
    user = precision_app.repository.get_user_by_display_name("UpgradeBasic")
    assert user["membership_tier"] == "premium"
    assert user["plan"] == "premium"
    assert user["active_role"] == user["role"] == "consumer"
    assert user["roles"] == ["consumer"]


def test_membership_page_uses_persisted_membership_and_role_not_query_values():
    test_client = client()
    login(test_client, username="PersistedWorkspace", role="retailer", membership_tier="premium")

    page = test_client.get("/membership?membership_tier=professional&role=researcher&roles=consumer")
    soup = BeautifulSoup(page.data, "html.parser")
    user = precision_app.repository.get_user_by_display_name("PersistedWorkspace")

    assert page.status_code == 200
    assert soup.select_one("[data-current-membership]").get_text(" ", strip=True) == "Current: Premium"
    assert soup.select_one("[data-current-workspace-role]").get_text(" ", strip=True) == "Retailer / Reseller"
    assert user["membership_tier"] == "premium"
    assert user["active_role"] == user["role"] == "retailer"
    assert user["roles"] == ["retailer"]


def test_membership_pages_do_not_return_500():
    test_client = client()
    for path in ["/", "/sample-analysis", "/register"]:
        response = test_client.get(path)
        assert response.status_code < 500
    login(test_client, username="SmokeBasic", role="consumer", membership_tier="basic")
    for path in ["/dashboard", "/membership", "/watchlist", "/audit", "/logs"]:
        response = test_client.get(path)
        assert response.status_code < 500


def test_public_sample_uses_scoped_median_comparison():
    response = client().get("/sample-analysis")
    body = response.data.decode("utf-8", errors="ignore")

    assert response.status_code == 200
    assert "Walmart has the lower median price in this sample." in body
    assert "USD 689.00" in body
    assert "Median price by marketplace" in body
    assert "Balanced sample: three records per marketplace." in body
    assert "Illustrative dataset." in body
    assert "not live marketplace listings" in body
    assert "How the sample analysis flows" not in body
    assert "Lowest observed platform" not in body
    assert "Platform average comparison" not in body


def test_public_routes_and_login_role_redirect():
    test_client = client()
    assert test_client.get("/").status_code == 200
    assert test_client.get("/demo").status_code == 302
    response = login(test_client, role="retailer")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert test_client.get("/dashboard/retailer").status_code == 200
    assert test_client.get("/demo").status_code == 404
    assert test_client.get("/dashboard/consumer").status_code == 403


def test_dashboard_fallbacks_redirect_to_login():
    test_client = client()
    assert test_client.get("/dashboard/consumer").status_code == 302
    response = test_client.get("/dashboard/not-a-role")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_demo_search_is_saved_and_analytics_is_user_scoped(monkeypatch):
    test_client = client()
    monkeypatch.setattr(precision_app, "create_chart", lambda _df, _id, name: f"{name}.png")
    login(test_client, username="One", role="consumer", membership_tier="premium")
    response = test_client.post("/search?q=laptop&data_source=mongodb&action=search", follow_redirects=True)
    assert response.status_code == 200
    records = precision_app.repository.list_searches(limit=10)
    assert len(records) == 1
    record = records[0]
    assert record["selected_source"] == "memory_fallback"
    assert record["result_count"] > 0
    assert test_client.get(f"/analytics/{record['_id']}").status_code == 200

    test_client.post("/logout")
    login(test_client, username="Two", role="consumer", membership_tier="premium")
    assert test_client.get(f"/analytics/{record['_id']}").status_code == 404


def test_missing_ai_key_is_readable(monkeypatch):
    test_client = client()
    login(test_client, membership_tier="premium")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    empty = test_client.post("/api/ai-discover", json={"query": "headphones"})
    assert empty.status_code == 400
    assert b"Please search sources first before using AI Discover." in empty.data
    test_client.post("/search?q=headphones&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    response = test_client.post("/api/ai-discover", json={"query": "headphones", "search_record_id": record["_id"]})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["generation_mode"] == "scope_guidance"
    assert payload["summary_source"] == "Decision guidance"
    assert payload["decision_status"] == "needs_refinement"
    assert payload["platform_counts"]
    assert "Decision readiness" in payload["summary"]
    assert "Recommended next step" in payload["summary"]
    activity = precision_app.repository.list_activity_logs(limit=1)[0]
    assert activity["summary_source"] == "Decision guidance"
    assert activity["model"] == "gemini-3.1-flash-lite"


def test_gemini_success_and_failure_modes(monkeypatch):
    items = [
        {"platform": "eBay", "normalized_price": 100.0},
        {"platform": "Amazon", "normalized_price": 140.0},
        {"platform": "Shopee", "normalized_price": 160.0},
    ]
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-not-real")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    captured = {}
    def success(api_key, model, prompt, timeout_ms):
        captured.update(model=model, timeout_ms=timeout_ms, prompt=prompt)
        return "Gemini generated market summary."
    monkeypatch.setattr(ai_search, "_call_gemini", success)
    summary, mode, reason = ai_search.summarize_market_gemini("phone", items)
    assert summary == "Gemini generated market summary."
    assert mode == "gemini_api" and reason is None
    assert captured["model"] == "gemini-2.5-flash-lite" and captured["timeout_ms"] == 45000

    failures = [
        (RuntimeError("503 UNAVAILABLE"), "service_unavailable"),
        (TimeoutError("request timed out"), "timeout"),
        (RuntimeError("429 quota exceeded"), "quota_exceeded"),
        (RuntimeError("404 model no longer available"), "model_unavailable"),
        (RuntimeError("unexpected API failure"), "api_error"),
    ]
    for error, expected_reason in failures:
        def fail(*_args, _error=error, **_kwargs):
            raise _error
        monkeypatch.setattr(ai_search, "_call_gemini", fail)
        summary, mode, reason = ai_search.summarize_market_gemini("phone", items)
        assert mode == "rule_based_fallback" and reason == expected_reason
        for expected in ("3 records", "100.00", "160.00", "133.33", "60.00", "3 platform"):
            assert expected in summary


def test_unqualified_scope_can_receive_a_guarded_ai_interpretation(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-not-real")
    captured = {}

    def guarded_interpretation(_api_key, _model, prompt, _timeout_ms):
        captured["prompt"] = prompt
        return (
            "Decision readiness\nCurrent-scope insight available; marketplace recommendation not qualified.\n\n"
            "Key interpretation\nThe visible prices support a within-marketplace reading without establishing a marketplace winner.\n\n"
            "Recommended next step\nReview seller and source evidence, then add matching marketplace coverage only if comparison is needed."
        )

    monkeypatch.setattr(ai_search, "_call_gemini", guarded_interpretation)
    context = {
        "qualified": False,
        "mixed_configuration": True,
        "mixed_condition": True,
        "best_platform": None,
    }
    summary, mode, reason = ai_search.summarize_market_gemini("phone", [], decision_context=context)

    assert mode == "gemini_api"
    assert reason is None
    assert "Current-scope insight available" in summary
    assert "within-marketplace reading" in summary
    assert "not qualified for a marketplace winner" in captured["prompt"]
    assert not any(character.isdigit() for character in summary)


def test_single_platform_scope_provides_useful_reading_without_claiming_a_winner():
    summary = ai_search.decision_support_summary({
        "qualified": False,
        "platform_counts": {"eBay": 2},
        "mixed_configuration": False,
        "mixed_condition": False,
        "price_pattern": "wide",
    })

    assert "Current-scope insight available" in summary
    assert "Prices vary substantially" in summary
    assert "within-marketplace reading" in summary
    assert "no cross-marketplace price conclusion" in summary
    assert "marketplace winner" in summary


def test_decision_guidance_prioritizes_platform_sample_imbalance():
    summary = ai_search.decision_support_summary({
        "qualified": False,
        "platform_counts": {"eBay": 4, "Walmart": 1},
        "mixed_configuration": False,
        "mixed_condition": True,
    })
    assert "current marketplace sample is not balanced" in summary
    assert "eBay: 4" in summary and "Walmart: 1" in summary
    assert "Collect another comparable record for Walmart" in summary
    assert "choose one product condition" in summary
    assert "regenerate the interpretation" in summary


def test_qualified_ai_explanation_rejects_numeric_kpi_restatement(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-not-real")
    monkeypatch.setattr(
        ai_search,
        "_call_gemini",
        lambda *_args, **_kwargs: (
            "Decision readiness\nReady for comparison.\n\n"
            "Key interpretation\nWalmart is lower at 255.25.\n\n"
            "Recommended next step\nReview the evidence."
        ),
    )
    context = {
        "qualified": True,
        "best_platform": "Walmart",
        "robust_platform_sample": True,
    }
    summary, mode, reason = ai_search.summarize_market_gemini("phone", [], decision_context=context)

    assert mode == "rule_based_fallback"
    assert reason == "unstructured_or_numeric_restatement"
    assert "Walmart has the lower typical price" in summary
    assert "255.25" not in summary


def test_qualified_comparison_without_ai_key_uses_decision_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    context = {
        "qualified": True,
        "best_platform": "Walmart",
        "robust_platform_sample": True,
    }
    summary, mode, reason = ai_search.summarize_market_gemini("phone", [], decision_context=context)

    assert mode == "rule_based_fallback"
    assert reason == "missing_key"
    assert "Walmart has the lower typical price" in summary
    assert "Decision readiness" in summary


def test_qualified_ai_explanation_accepts_structured_decision_support(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-not-real")
    generated = (
        "Decision readiness\nReady for a like-for-like comparison.\n\n"
        "Key interpretation\nThe highlighted marketplace has a consistent lower typical price.\n\n"
        "Recommended next step\nReview seller and shipping evidence before deciding."
    )
    monkeypatch.setattr(ai_search, "_call_gemini", lambda *_args, **_kwargs: generated)
    context = {
        "qualified": True,
        "best_platform": "Walmart",
        "robust_platform_sample": True,
    }
    summary, mode, reason = ai_search.summarize_market_gemini("phone", [], decision_context=context)

    assert summary == generated
    assert mode == "gemini_api"
    assert reason is None


def test_gemini_prediction_uses_the_most_recent_twenty_snapshots(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-not-real")
    captured = {}

    def success(_api_key, _model, prompt, _timeout_ms):
        captured["prompt"] = prompt
        return '{"predicted_average_price": 125, "predicted_direction": "increase", "confidence_level": "medium", "reason": "Recent observations"}'

    monkeypatch.setattr(ai_search, "_call_gemini", success)
    snapshots = [
        {"collected_at": f"2026-07-{index:02d}", "average_price": index, "lowest_price": index, "highest_price": index, "record_count": 3, "data_quality": "good"}
        for index in range(1, 26)
    ]

    payload, mode, reason = ai_search.predict_price_gemini("Phone", "search_scope", "all", "live", snapshots)

    assert mode == "gemini_api" and reason is None and payload["predicted_average_price"] == 125
    prompt_snapshots = json.loads(captured["prompt"].split("snapshots: ", 1)[1])
    assert [row["average_price"] for row in prompt_snapshots] == list(range(6, 26))


def test_gemini_condition_claim_is_checked_against_visible_records(monkeypatch):
    items = [
        {"platform": "eBay", "normalized_price": 100.0 + index, "condition_display": "Used"}
        for index in range(4)
    ] + [{"platform": "Walmart", "normalized_price": 110.0, "condition_display": "Not specified by source"}]
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-not-real")
    captured = {}

    def unsupported_claim(_api_key, _model, prompt, _timeout_ms):
        captured["prompt"] = prompt
        return "Prices vary across two platforms. Availability and specific product condition are often not provided."

    monkeypatch.setattr(ai_search, "_call_gemini", unsupported_claim)
    summary, mode, reason = ai_search.summarize_market_gemini("iPhone 13", items)
    assert mode == "rule_based_fallback"
    assert reason == "unsupported_condition_claim"
    assert "Condition data is provided for 4 of 5 records." in summary
    assert "often not provided" not in summary
    assert "Condition coverage: 4 of 5 records." in captured["prompt"]
    assert '"condition_display": "Used"' in captured["prompt"]


def test_role_switch_saved_evidence_compare_and_admin_pages():
    test_client = client()
    login(test_client, role="consumer", membership_tier="premium")
    assert test_client.get("/dashboard/consumer").status_code == 200
    response = test_client.post("/search?q=headphones&data_source=mongodb&action=search", follow_redirects=True)
    assert response.status_code == 200
    record = precision_app.repository.list_searches(limit=1)[0]
    tokens = record["result_tokens"]
    saved = test_client.post("/saved/results", data={"search_record_id": record["_id"], "result_token": [tokens[0]]})
    assert saved.status_code == 302
    assert b"Saved Research" in test_client.get("/saved").data
    comparison = test_client.post("/compare", query_string=[("search_record_id", record["_id"]), ("result_token", tokens[0]), ("result_token", tokens[1])])
    assert comparison.status_code == 200
    assert b"Comparable price spread" in comparison.data
    analytics = test_client.get(f"/analytics/{record['_id']}")
    assert analytics.status_code == 200
    assert b"Price range" in analytics.data
    assert test_client.post("/role-switch", data={"role": "researcher"}).status_code == 302
    test_client.post("/logout")
    login(test_client, role="researcher", membership_tier="professional")
    assert test_client.get("/dashboard/researcher").status_code == 200
    assert test_client.get("/audit").status_code == 200
    assert test_client.get("/logs").status_code == 200


def test_backend_api_uses_labeled_synthetic_fallback(monkeypatch):
    test_client = client()
    login(test_client)
    monkeypatch.setattr(precision_app, "CLIENT_ID", None)
    response = test_client.post("/api/search?q=laptop&source=ebay")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["requested_source"] == "ebay"
    assert payload["actual_source"] == "demo"
    assert payload["data_mode"] == "synthetic"


def test_complete_mongo_style_search_compare_and_evidence_flow():
    test_client = client()
    login(test_client, membership_tier="premium")
    login_page = precision_app.app.test_client().get("/login")
    assert b"Enter workspace" in login_page.data
    response = test_client.post("/search?q=iPhone%2017%20Pro%20256GB&data_source=mongodb&sort=platform&action=search", follow_redirects=True)
    assert response.status_code == 200
    for label in (b"Product Price Search & Comparison", b"Search Sources", b"Explain this comparison", b"Compare selected", b"Save selected evidence"):
        assert label in response.data
    assert b"Normalized price" not in response.data
    assert b"MONGODB_URI" not in response.data
    record = precision_app.repository.list_searches(limit=1)[0]
    products = precision_app.resolve_result_tokens(record["result_tokens"])
    assert len(products) == 7
    annotated = precision_app.annotate_comparison(precision_app.normalize_price_items(products), "iPhone 17 Pro 256GB")
    assert sum(bool(item.get("is_outlier")) for item in annotated) == 1
    assert any(item.get("relevance_warning") for item in annotated)
    selected_tokens = record["result_tokens"][:2]
    comparison = test_client.post("/compare", query_string=[("search_record_id", record["_id"]), ("result_token", selected_tokens[0]), ("result_token", selected_tokens[1])])
    assert comparison.status_code == 200 and b"Comparable price spread" in comparison.data
    saved = test_client.post("/saved/results", data={"search_record_id": record["_id"], "result_token": selected_tokens})
    assert saved.status_code == 302
    assert len(precision_app.repository.list_evidence(limit=10)) == 2
    assert test_client.get(f"/analytics/{record['_id']}").status_code == 200
    event_types = {row["event_type"] for row in precision_app.repository.list_audit_logs(limit=20)}
    assert {"login", "search_started", "records_collected", "comparison_generated", "save_evidence"}.issubset(event_types)


def test_production_role_menus_and_developer_preview():
    test_client = client()
    login(test_client, role="consumer", membership_tier="premium")
    consumer = test_client.get("/dashboard/consumer").data
    assert b"Product Search" in consumer and b"Saved Research" in consumer
    assert b"Developer role preview" not in consumer
    assert b"Source Audit" in consumer and b">Analytics<" in consumer and b"sidebar-badge" in consumer
    assert b"truncate" not in consumer
    assert b"app-nav-item" in consumer
    assert b"grid-template-columns:28px minmax(0,1fr) auto" in consumer
    assert b"Premium Plan" in consumer and b"Workspace" in consumer and b"Account" in consumer
    assert b"precision-curator-logo.png" in consumer
    assert b"height:3.25rem" in consumer
    assert b"Test Data" not in consumer
    assert b"Find a price you can trace." in consumer
    assert b"Recently saved evidence" in consumer
    assert b"Product search" in consumer
    assert b"Both sources" in consumer and b"eBay" in consumer and b"Walmart" in consumer
    test_client.post("/logout")
    login(test_client, role="retailer")
    retailer = test_client.get("/dashboard/retailer").data
    assert b"Monitor marketplace prices." in retailer
    assert b"Price monitoring portfolio" in retailer
    assert b"Price drops" in retailer and b"Needs attention" in retailer
    assert b"Create monitor" in retailer
    assert b"Active monitors" in retailer and b"New baselines" in retailer
    test_client.post("/logout")
    login(test_client, role="researcher")
    admin = test_client.get("/dashboard/researcher").data
    for label in (b"Build traceable findings.", b"Research pipeline", b"Recent evidence", b"Source reliability", b"Recent AI analyses", b"Research activity"):
        assert label in admin
    assert b"Product result database" not in admin
    assert b"Testing records" not in admin
    assert b"Activity Logs" in admin
    assert b"Users" not in admin
    assert b"Developer role preview" not in admin
    assert test_client.post("/role-switch", data={"role": "consumer"}).status_code == 302


def test_single_role_workspace_policy_and_admin_role_replacement():
    test_client = client()
    login(test_client, username="Multi", role="consumer")
    user = precision_app.repository.get_user_by_display_name("Multi")
    precision_app.repository.update_user(user["_id"], {"roles": ["consumer", "researcher"], "active_role": "consumer", "role": "consumer", "plan": "basic"})
    test_client.post("/logout")
    login(test_client, username="Multi", role="consumer")
    dashboard = test_client.get("/dashboard/consumer")
    assert dashboard.status_code == 200
    assert b"Workspace" in dashboard.data
    response = test_client.post("/role-switch", data={"role": "researcher"})
    assert response.status_code == 302
    updated = precision_app.repository.get_user_by_display_name("Multi")
    assert updated["active_role"] == "consumer"
    assert updated["roles"] == ["consumer"]
    assert updated["_id"] == user["_id"]
    assert test_client.post("/role-switch", data={"role": "administrator"}).status_code == 302
    test_client.post("/logout")

    login(test_client, role="administrator")
    target = precision_app.repository.get_user_by_display_name("Multi")
    response = test_client.post(
        f"/dashboard/administrator/users/{target['_id']}",
        data={"workspace_role": "researcher", "plan": "professional", "account_status": "active"},
    )
    assert response.status_code == 302
    updated = precision_app.repository.get_user_by_id(target["_id"])
    assert updated["roles"] == ["researcher"]
    assert updated["active_role"] == "researcher"
    assert updated["plan"] == "professional"
    admin_page = test_client.get("/dashboard/administrator").data.decode("utf-8", errors="ignore")
    assert "Membership plan" in admin_page
    assert "Roles cannot be combined" in admin_page
    assert 'type="checkbox" name="roles"' not in admin_page
    response = test_client.post(
        f"/dashboard/administrator/users/{target['_id']}",
        data={"workspace_role": "consumer", "plan": "basic", "account_status": "active"},
    )
    assert response.status_code == 302
    updated = precision_app.repository.get_user_by_id(target["_id"])
    assert updated["roles"] == ["consumer"]
    assert updated["active_role"] == "consumer"


def test_explicit_search_dynamic_platforms_and_role_tables():
    test_client = client()
    login(test_client, role="consumer", membership_tier="premium")
    pending = test_client.get("/search?q=iphone17&data_source=mongodb")
    assert pending.status_code == 200
    assert precision_app.repository.list_searches(limit=10) == []
    assert b'name="result_token"' not in pending.data

    ebay_controls = test_client.get("/search?data_source=ebay")
    scope_options = BeautifulSoup(ebay_controls.data, "html.parser").select("#platform-filter option")
    options = [option.get_text(strip=True) for option in scope_options]
    assert options == ["Both platforms", "eBay", "Walmart"]
    assert [option.get("value") for option in scope_options] == ["both", "ebay", "walmart"]
    assert next(option for option in scope_options if option.has_attr("selected"))["value"] == "ebay"

    consumer = test_client.post("/search?q=iphone17&data_source=mongodb&action=search", follow_redirects=True)
    assert b"Condition" in consumer.data
    assert b"Price gap / market position" not in consumer.data and b"Evidence status" not in consumer.data
    soup = BeautifulSoup(consumer.data, "html.parser")
    assert soup.select_one("#compare-button").has_attr("disabled")
    assert soup.select_one("#save-button").has_attr("disabled")

    test_client.post("/logout")
    login(test_client, role="retailer")
    retailer = test_client.post("/search?q=iphone17&data_source=mongodb&action=search", follow_redirects=True)
    assert b"Condition" in retailer.data and b"Inclusion status" not in retailer.data and b"Variant review" not in retailer.data


def test_watchlist_and_analytics_membership_gates(monkeypatch):
    test_client = client()
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 100.0, "predicted_direction": "stable", "confidence_level": "medium", "reason": "mocked"}, "gemini_api", None))
    login(test_client, username="BasicConsumer", role="consumer")
    user = precision_app.repository.get_user_by_display_name("BasicConsumer")
    precision_app.repository.update_user(user["_id"], {"membership_tier": "basic", "plan": "basic"})
    test_client.post("/search?q=basicwatch&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    watchlist_locked = test_client.get("/watchlist")
    assert watchlist_locked.status_code == 200
    assert b"See how a market moves after the first search." in watchlist_locked.data
    assert b"Price history" in watchlist_locked.data
    assert b"Requires Premium" in watchlist_locked.data
    assert b"material-symbols-outlined" in watchlist_locked.data
    assert b"Upgrade to Premium" in watchlist_locked.data
    assert watchlist_locked.data.count(b"Upgrade to Premium") == 1
    assert b"/membership" in watchlist_locked.data
    analytics_locked = test_client.get(f"/analytics/{record['_id']}")
    assert analytics_locked.status_code == 200
    assert b"Move from listings to a comparable market view." in analytics_locked.data
    assert b"Price distribution" in analytics_locked.data
    save_blocked = test_client.post("/saved/results", data={"search_record_id": record["_id"], "result_token": record.get("result_tokens", [""])[0] if record.get("result_tokens") else ""})
    assert save_blocked.status_code == 302

    test_client.post("/logout")
    login(test_client, username="PremiumConsumer", role="consumer")
    user = precision_app.repository.get_user_by_display_name("PremiumConsumer")
    precision_app.repository.update_user(user["_id"], {"membership_tier": "premium", "plan": "premium"})
    test_client.post("/search?q=premiumwatch&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    item_id, _ = precision_app.repository.create_watchlist_item(user["_id"], {"keyword": "premium", "product_label": "premium", "tracking_mode": "search_scope", "source_label": "live"})
    precision_app.repository.save_price_snapshot(item_id, user["_id"], {"average_price": 100.0, "lowest_price": 95.0, "highest_price": 105.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    assert test_client.get("/watchlist", query_string={"item_id": item_id}).status_code == 200
    assert test_client.get(f"/analytics/{record['_id']}").status_code == 200
    audit_locked = test_client.get("/audit")
    assert audit_locked.status_code == 200
    assert b"Review collection outcomes, source coverage, evidence events, and failures" in audit_locked.data
    assert b"Requires Professional" in audit_locked.data
    assert b"Upgrade to Professional" in audit_locked.data
    assert b"Source provenance" in audit_locked.data
    assert b"/membership" in audit_locked.data

    test_client.post("/logout")
    login(test_client, username="ProConsumer", role="consumer")
    user = precision_app.repository.get_user_by_display_name("ProConsumer")
    precision_app.repository.update_user(user["_id"], {"membership_tier": "professional", "plan": "professional"})
    test_client.post("/search?q=prowatch&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    item_id, _ = precision_app.repository.create_watchlist_item(user["_id"], {"keyword": "pro", "product_label": "pro", "tracking_mode": "search_scope", "source_label": "live"})
    precision_app.repository.save_price_snapshot(item_id, user["_id"], {"average_price": 100.0, "lowest_price": 95.0, "highest_price": 105.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    precision_app.repository.save_price_snapshot(item_id, user["_id"], {"average_price": 105.0, "lowest_price": 100.0, "highest_price": 110.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 2, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{item_id}/prediction")
    assert test_client.get("/watchlist", query_string={"item_id": item_id}).status_code == 200
    assert test_client.get("/audit").status_code == 200
    assert b"Evidence records and provenance" in test_client.get("/audit").data

    test_client.post("/logout")
    login(test_client, role="researcher")
    researcher = test_client.post("/search?q=iphone17&data_source=mongodb&action=search", follow_redirects=True)
    assert b"Evidence status" not in researcher.data and b"Condition" in researcher.data and b"Variant review" not in researcher.data
    assert b"Source link" in researcher.data
    assert b"Open source" not in researcher.data
    assert b"Researcher / Admin" not in researcher.data


def test_search_table_uses_open_listing_and_sgt(monkeypatch):
    test_client = client()
    login(test_client, role="consumer", membership_tier="premium")
    response = test_client.post("/search?q=iPhone%2017%20Pro%20256GB&data_source=mongodb&action=search", follow_redirects=True)
    assert response.status_code == 200
    body = response.data.decode("utf-8", errors="ignore")
    assert "Open source" not in body
    assert "See source" not in body
    assert "Show technical fields" not in body


def test_saved_records_drive_dashboard_and_analytics(monkeypatch):
    test_client = client()
    monkeypatch.setattr(precision_app, "create_chart", lambda _df, _id, name: f"{name}.png")
    login(test_client, username="Consumer", role="consumer", membership_tier="premium")
    response = test_client.post("/search?q=tablet&data_source=mongodb&action=search", follow_redirects=True)
    assert response.status_code == 200
    record = precision_app.repository.list_searches(limit=1)[0]
    token = record["result_tokens"][0]
    saved = test_client.post("/saved/results", data={"search_record_id": record["_id"], "result_token": [token]})
    assert saved.status_code == 302
    dashboard = test_client.get("/dashboard/consumer")
    assert dashboard.status_code == 200
    assert b"Market overview based on saved and collected records." in dashboard.data or b"No saved market records found. Demo records are shown for preview." in dashboard.data
    assert test_client.get("/saved").status_code == 200
    analytics = test_client.get(f"/analytics/{record['_id']}")
    assert analytics.status_code == 200
    analytics_body = analytics.data.decode("utf-8", errors="ignore")
    for label in ("Search Analytics -", "Total Records", "Ranked Listing Prices", "Price distribution histogram", "Average vs median by platform", "Platform share", "Condition / quality breakdown", "Platform x condition composition"):
        assert label in analytics_body
    for chart_id in ("trend-chart", "histogram-chart", "platform-comparison-chart", "platform-share-chart", "condition-chart", "stacked-chart"):
        assert chart_id in analytics_body
    for dynamic_id in ("summary-total-records", "summary-platforms-covered", "summary-average-price", "summary-price-range", "insight-recommendation", "preview-visible-count"):
        assert dynamic_id in analytics_body
    assert "data-preview-row" in analytics_body
    assert "renderPreview" in analytics_body


def test_analytics_print_report_route_loads_for_professional_user():
    test_client = client()
    login(test_client, username="Reporter", role="consumer", membership_tier="professional")
    response = test_client.post("/search?q=reporting&data_source=mongodb&action=search", follow_redirects=True)
    assert response.status_code == 200
    record = precision_app.repository.list_searches(limit=1)[0]
    report = test_client.get(f"/analytics/{record['_id']}/report")
    assert report.status_code == 200
    body = report.data.decode("utf-8", errors="ignore")
    assert "Analytics summary report" in body


def test_analytics_export_csv_includes_metadata_and_record_fields():
    test_client = client()
    login(test_client, username="CsvReporter", role="consumer", membership_tier="premium")
    response = test_client.post("/search?q=metadata-phone&data_source=mongodb&action=search", follow_redirects=True)
    assert response.status_code == 200
    record = precision_app.repository.list_searches(limit=1)[0]
    export = test_client.get(f"/analytics/{record['_id']}/export/results.csv")
    assert export.status_code == 200
    rows = list(csv.reader(io.StringIO(export.data.decode("utf-8", errors="ignore"))))
    assert rows[0] == ["Analysis ID", "Query", "Platform", "Product Title", "Observed Price", "Currency", "Normalized Price", "Condition", "Category", "Seller", "Collected At (SGT)", "Record Source", "Source URL", "Analysis Included", "Exclusion Reason", "Product Configuration"]
    assert rows[1][1] == "metadata-phone"
    assert rows[1][13] == "Yes"


def test_analytics_export_report_post_renders_printable_report():
    test_client = client()
    login(test_client, username="PostReporter", role="consumer", membership_tier="professional")
    response = test_client.post("/search?q=printable&data_source=mongodb&action=search", follow_redirects=True)
    assert response.status_code == 200
    record = precision_app.repository.list_searches(limit=1)[0]
    report = test_client.post(
        f"/analytics/{record['_id']}/export/report",
        data={
            "metadata": json.dumps({
                "keyword": "printable",
                "role": "consumer",
                "membership": "professional",
                "filters": {"platform": "All", "category": "All", "condition": "All"},
            }),
            "summary": json.dumps({
                "totalRecords": 1,
                "platformsCovered": 1,
                "averagePrice": 10,
                "medianPrice": 10,
                "priceRange": 0,
                "topPlatform": "eBay",
                "bestVisibleOption": "eBay",
                "marketPattern": "A focused result set is ready for review.",
            }),
            "records": json.dumps([{
                "title": "Sample listing",
                "platform": "eBay",
                "price": 10,
                "currency": "USD",
                "condition": "New",
                "category": "Phone",
                "url": "https://example.com",
            }]),
            "charts": json.dumps({}),
        },
    )
    assert report.status_code == 200
    body = report.data.decode("utf-8", errors="ignore")
    assert "Precision Curator Analytics Report" in body
    assert "Insight summary" in body
    assert "Analyzed records" in body
    assert "Chart images are not available. Use Charts (PNG) for presentation images." in body


def test_demo_and_fallback_labels_are_distinct():
    test_client = client()
    login(test_client)
    record = precision_app._label_record_source({"platform": "Demo", "title": "Demo", "source_type": "synthetic_demo"}, "demo")
    assert record["data_source_label"] == "demo"
    record = precision_app._label_record_source({"platform": "Local", "title": "Local", "source_type": "local_cache"}, "fallback")
    assert record["data_source_label"] == "fallback"


def test_save_selected_evidence_reports_exclusions(monkeypatch):
    test_client = client()
    login(test_client, role="consumer", membership_tier="premium")
    test_client.post("/search?q=phone&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    products = precision_app.resolve_result_tokens(record["result_tokens"])
    products[1]["is_accessory"] = True
    monkeypatch.setattr(precision_app, "resolve_result_tokens", lambda _tokens: products)
    response = test_client.post("/saved/results", data={"search_record_id": record["_id"], "result_token": record["result_tokens"][:3]})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/saved#saved-evidence-records")
    saved_page = test_client.get("/saved")
    assert b"#saved-evidence-records" not in saved_page.data


def test_delete_saved_evidence_redirects_to_anchor():
    test_client = client()
    login(test_client, role="consumer", membership_tier="premium")
    test_client.post("/search?q=tablet&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    product = precision_app.normalize_price_items(precision_app.resolve_result_tokens(record["result_tokens"]))[0]
    user_id = precision_app.repository.list_users(limit=1)[0]["_id"]
    precision_app.repository.save_evidence(user_id, record["_id"], product)
    evidence = precision_app.repository.list_evidence(limit=1)[0]
    response = test_client.post(f"/saved/evidence/{evidence['_id']}/delete")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/saved#saved-evidence-records")


def test_watchlist_creation_refresh_and_archive(monkeypatch):
    test_client = client()
    monkeypatch.setattr(precision_app, "create_chart", lambda _df, _id, name: f"{name}.png")
    login(test_client, role="consumer", membership_tier="premium")
    test_client.post("/search?q=watchable&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    response = test_client.post("/watchlist/from-search", data={"search_record_id": record["_id"], "tracking_mode": "search_scope"})
    assert response.status_code == 302
    watchlist_items = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=10)
    assert watchlist_items
    item = watchlist_items[0]
    assert item["tracking_mode"] == "search_scope"
    assert item["record_scope"] == "all_current_search_results"
    watchlist_page = test_client.get("/watchlist")
    assert watchlist_page.status_code == 200
    assert b"Watchlist" in watchlist_page.data
    refresh = test_client.post(f"/watchlist/{item['_id']}/refresh")
    assert refresh.status_code == 302
    snapshots = precision_app.repository.list_price_snapshots(item["_id"], limit=10)
    assert len(snapshots) == 2
    assert snapshots[0]["average_price"] is not None
    assert snapshots[0]["created_at"] > snapshots[1]["created_at"]
    archive = test_client.post(f"/watchlist/{item['_id']}/archive")
    assert archive.status_code == 302
    active_items = precision_app.repository.list_watchlist_items(user_id=record["user_id"], include_archived=False, limit=10)
    assert not active_items
    archived_page = test_client.get("/watchlist?archived=1")
    assert archived_page.status_code == 200
    assert b"Archived monitors" in archived_page.data
    assert b"Restore to Active" in archived_page.data
    restore = test_client.post(f"/watchlist/{item['_id']}/restore")
    assert restore.status_code == 302
    restored_items = precision_app.repository.list_watchlist_items(user_id=record["user_id"], include_archived=False, limit=10)
    assert len(restored_items) == 1
    assert restored_items[0]["status"] == "active"


def test_compare_can_track_selected_records(monkeypatch):
    test_client = client()
    monkeypatch.setattr(precision_app, "create_chart", lambda _df, _id, name: f"{name}.png")
    login(test_client, role="consumer", membership_tier="premium")
    test_client.post("/search?q=trackable&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    tokens = record["result_tokens"][:2]
    response = test_client.post("/compare", query_string=[("search_record_id", record["_id"]), ("result_token", tokens[0]), ("result_token", tokens[1])])
    assert response.status_code == 200
    response = test_client.post("/watchlist/from-compare", data={"search_record_id": record["_id"], "result_token": tokens})
    assert response.status_code == 302
    items = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=10)
    assert items
    item = items[0]
    assert item["tracking_mode"] == "selected_records"
    assert item["record_scope"] == "selected_comparison_records"
    snapshots = precision_app.repository.list_price_snapshots(item["_id"], limit=10)
    assert snapshots[0]["record_count"] == 2
    test_client.post(f"/watchlist/{item['_id']}/refresh")
    refreshed = precision_app.repository.list_price_snapshots(item["_id"], limit=10)[0]
    assert refreshed["record_count"] == 2


def test_sgt_formatter_and_watchlist_item_selection():
    dt = precision_app.datetime(2026, 7, 2, 2, 33, tzinfo=precision_app.timezone.utc)
    assert precision_app.format_sgt_datetime(dt) == "02 Jul 2026, 10:33 SGT"
    items = [
        {"_id": "a", "updated_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)},
        {"_id": "b", "updated_at": precision_app.datetime(2026, 7, 2, tzinfo=precision_app.timezone.utc)},
    ]
    selected = precision_app._watchlist_selected_item(items)
    assert selected["_id"] == "b"
    selected = precision_app._watchlist_selected_item(items, selected_id="a")
    assert selected["_id"] == "a"


def test_prediction_baseline_and_gemini_validation(monkeypatch):
    test_client = client()
    login(test_client, username="Pred", role="researcher", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 108.0, "predicted_direction": "increase", "confidence_level": "high", "reason": "mocked"}, "gemini_api", None))
    test_client.post("/search?q=monitor&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": record["_id"], "tracking_mode": "search_scope"})
    item = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 100.0, "lowest_price": 95.0, "highest_price": 105.0, "record_count": 3, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 110.0, "lowest_price": 105.0, "highest_price": 115.0, "record_count": 3, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 2, tzinfo=precision_app.timezone.utc)})
    response = test_client.post(f"/watchlist/{item['_id']}/prediction")
    assert response.status_code == 302
    prediction = precision_app.repository.list_predictions(user_id=item["user_id"], watchlist_id=item["_id"], limit=1)[0]
    assert prediction["baseline_predicted_average_price"] is not None
    assert prediction["ai_predicted_average_price"] == 108.0
    assert prediction["ai_predicted_direction"] == "increase"
    assert prediction["ai_confidence_level"] == "high"
    assert prediction["status"] == "pending_actual"


def test_prediction_evaluates_with_future_snapshot_and_metrics(monkeypatch):
    test_client = client()
    login(test_client, username="Eval", role="consumer", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 120.0, "predicted_direction": "stable", "confidence_level": "medium", "reason": "mocked"}, "gemini_api", None))
    test_client.post("/search?q=keyboard&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": record["_id"], "tracking_mode": "search_scope"})
    item = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 100.0, "lowest_price": 90.0, "highest_price": 110.0, "record_count": 3, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{item['_id']}/prediction")
    prediction = precision_app.repository.list_predictions(user_id=item["user_id"], watchlist_id=item["_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 130.0, "lowest_price": 125.0, "highest_price": 135.0, "record_count": 3, "source_label": "live", "data_quality": "good", "collected_at": prediction["prediction_created_at"] + precision_app.timedelta(hours=13)})
    response = test_client.post(f"/watchlist/{item['_id']}/prediction/evaluate")
    assert response.status_code == 302
    prediction = precision_app.repository.list_predictions(user_id=item["user_id"], watchlist_id=item["_id"], limit=1)[0]
    assert prediction["status"] == "evaluated"
    assert prediction["actual_average_price"] == 130.0
    assert prediction["baseline_absolute_error"] is not None
    assert prediction["ai_absolute_error"] is not None
    assert prediction["baseline_mape"] is not None
    assert prediction["ai_mape"] is not None


def test_prediction_user_isolation_and_tracking_mode_scope(monkeypatch):
    test_client = client()
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 90.0, "predicted_direction": "decrease", "confidence_level": "low", "reason": "mocked"}, "gemini_api", None))
    login(test_client, username="A", role="consumer", membership_tier="professional")
    test_client.post("/search?q=scope&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": record["_id"], "tracking_mode": "search_scope"})
    search_item = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(search_item["_id"], search_item["user_id"], {"average_price": 100.0, "lowest_price": 95.0, "highest_price": 105.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{search_item['_id']}/prediction")
    test_client.post("/logout")
    login(test_client, username="B", role="consumer")
    assert precision_app.repository.list_predictions(user_id=precision_app.repository.get_user_by_display_name("B")["_id"], limit=10) == []


def test_prediction_evaluation_update_payload_strips_id(monkeypatch):
    test_client = client()
    login(test_client, username="Strip", role="consumer", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 100.0, "predicted_direction": "stable", "confidence_level": "medium", "reason": "mocked"}, "gemini_api", None))
    test_client.post("/search?q=strip&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": record["_id"], "tracking_mode": "search_scope"})
    item = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 90.0, "lowest_price": 85.0, "highest_price": 95.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{item['_id']}/prediction")
    prediction = precision_app.repository.list_predictions(user_id=item["user_id"], watchlist_id=item["_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 95.0, "lowest_price": 90.0, "highest_price": 100.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": prediction["prediction_created_at"] + precision_app.timedelta(hours=13)})
    captured = {}
    original_update = precision_app.repository.update_prediction
    def capture_update(prediction_id, updates):
        captured["updates"] = updates
        return original_update(prediction_id, updates)
    monkeypatch.setattr(precision_app.repository, "update_prediction", capture_update)
    response = test_client.post(f"/watchlist/{item['_id']}/prediction/evaluate")
    assert response.status_code == 302
    assert "_id" not in captured["updates"]
    assert captured["updates"]["status"] == "evaluated"


def test_watchlist_chart_states_and_serializable_data(monkeypatch):
    test_client = client()
    login(test_client, username="Chart", role="consumer", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 100.0, "predicted_direction": "stable", "confidence_level": "medium", "reason": "mocked"}, "gemini_api", None))
    user = precision_app.repository.get_user_by_display_name("Chart")
    item_id, _created = precision_app.repository.create_watchlist_item(user["_id"], {"keyword": "chart", "product_label": "chart", "tracking_mode": "search_scope", "source_label": "live"})
    item = precision_app.repository.get_watchlist_item(item_id, user["_id"])
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 100.0, "lowest_price": 95.0, "highest_price": 105.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    page = test_client.get("/watchlist", query_string={"item_id": item["_id"]})
    assert page.status_code == 200
    body = page.data.decode("utf-8", errors="ignore")
    assert "More history is needed for a trend." in body
    assert "ObjectId" not in body

    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 110.0, "lowest_price": 105.0, "highest_price": 115.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 2, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{item['_id']}/prediction")
    page = test_client.get("/watchlist", query_string={"item_id": item["_id"]})
    body = page.data.decode("utf-8", errors="ignore")
    assert "Average Price Trend" in body
    assert "watchlist-trend-echart" in body
    assert "watchlist-validation-echart" in body
    assert "static/vendor/echarts.min.js" in body
    assert "Forecast awaiting validation" in body
    assert "Collect now and validate" in body


def test_watchlist_hides_debug_marker_and_renders_chart_metadata(monkeypatch):
    test_client = client()
    login(test_client, username="Marker", role="consumer", membership_tier="professional")
    user = precision_app.repository.get_user_by_display_name("Marker")
    item_id, _created = precision_app.repository.create_watchlist_item(user["_id"], {"keyword": "marker", "product_label": "marker", "tracking_mode": "search_scope", "source_label": "live"})
    item = precision_app.repository.get_watchlist_item(item_id, user["_id"])
    for day, average in enumerate([100.0, 102.0], start=1):
        precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": average, "lowest_price": average - 5, "highest_price": average + 5, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, day, tzinfo=precision_app.timezone.utc)})
    page = test_client.get("/watchlist", query_string={"item_id": item["_id"]})
    body = page.data.decode("utf-8", errors="ignore")
    assert "WATCHLIST ACTIVE TEMPLATE CHECK" not in body
    assert "Trend chart data loaded:" not in body
    assert "Rising trend: latest average is higher than the first recorded snapshot." in body


def test_watchlist_validation_debug_panel_uses_active_template(monkeypatch):
    test_client = client()
    login(test_client, username="ValidationDebug", role="consumer", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 100.0, "predicted_direction": "stable", "confidence_level": "medium", "reason": "mocked"}, "gemini_api", None))
    user = precision_app.repository.get_user_by_display_name("ValidationDebug")
    item_id, _created = precision_app.repository.create_watchlist_item(user["_id"], {"keyword": "validation", "product_label": "validation", "tracking_mode": "search_scope", "source_label": "live"})
    item = precision_app.repository.get_watchlist_item(item_id, user["_id"])
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 100.0, "lowest_price": 95.0, "highest_price": 105.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 110.0, "lowest_price": 105.0, "highest_price": 115.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 2, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{item['_id']}/prediction")
    test_client.post(f"/watchlist/{item['_id']}/prediction/evaluate")

    page = test_client.get("/watchlist", query_string={"item_id": item["_id"], "debug_validation": "1"})
    body = page.data.decode("utf-8", errors="ignore")
    assert "Validation diagnostics" in body
    assert "selected_watchlist_id:" in body
    assert "pending_prediction_status:" in body
    assert "snapshots_after_prediction_count:" in body
    assert "actual_snapshot_selected:" in body
    assert "validation_chart.available:" in body
    assert "validation_chart.pending:" in body
    assert "reason_validation_not_available:" in body
    assert "last_evaluation_error:" in body


def test_debug_charts_render_both_canvases(monkeypatch):
    test_client = client()
    login(test_client, username="DebugCanvas", role="consumer", membership_tier="professional")
    user = precision_app.repository.get_user_by_display_name("DebugCanvas")
    item_id, _created = precision_app.repository.create_watchlist_item(user["_id"], {"keyword": "debugcanvas", "product_label": "debugcanvas", "tracking_mode": "search_scope", "source_label": "live"})
    item = precision_app.repository.get_watchlist_item(item_id, user["_id"])
    for day, average in enumerate([135.14, 138.60, 141.50, 139.80, 143.72], start=1):
        precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": average, "lowest_price": average - 5, "highest_price": average + 5, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, day, tzinfo=precision_app.timezone.utc)})
    page = test_client.get("/watchlist", query_string={"item_id": item["_id"], "debug_charts": "1"})
    body = page.data.decode("utf-8", errors="ignore")
    assert 'id="debugLineChart"' in body
    assert 'id="debugBarChart"' in body
    assert "Chart diagnostics" in body


def test_evaluated_prediction_makes_validation_chart_available(monkeypatch):
    test_client = client()
    login(test_client, username="EvalChart", role="consumer", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 100.0, "predicted_direction": "stable", "confidence_level": "medium", "reason": "mocked"}, "gemini_api", None))
    user = precision_app.repository.get_user_by_display_name("EvalChart")
    item_id, _created = precision_app.repository.create_watchlist_item(user["_id"], {"keyword": "evalchart", "product_label": "evalchart", "tracking_mode": "search_scope", "source_label": "live"})
    item = precision_app.repository.get_watchlist_item(item_id, user["_id"])
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 100.0, "lowest_price": 95.0, "highest_price": 105.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 110.0, "lowest_price": 105.0, "highest_price": 115.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 2, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{item['_id']}/prediction")
    test_client.post(f"/watchlist/{item['_id']}/prediction/evaluate")
    page = test_client.get("/watchlist", query_string={"item_id": item["_id"], "debug_validation": "1"})
    body = page.data.decode("utf-8", errors="ignore")
    assert "Validation diagnostics" in body
    assert "reason_validation_not_available:" in body
    assert "validation_chart.available:" in body


def test_prediction_validation_chart_has_reason_when_unavailable():
    chart = precision_app._prediction_validation_chart_data({"status": "pending_actual", "baseline_predicted_average_price": 100.0})
    assert chart["available"] is False
    assert chart["reason"] in {"status_not_evaluated", "baseline_missing", "actual_missing", "no_prediction"}


def test_flat_watchlist_trend_chart_data_is_available_and_padded():
    snapshots = [
        {"average_price": 100.0, "lowest_price": 100.0, "highest_price": 100.0, "record_count": 2, "source_label": "live", "data_quality": "good", "created_at": precision_app.datetime(2026, 7, 2, tzinfo=precision_app.timezone.utc)},
        {"average_price": 100.0, "lowest_price": 100.0, "highest_price": 100.0, "record_count": 3, "source_label": "live", "data_quality": "good", "created_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)},
        {"average_price": 100.0, "lowest_price": 100.0, "highest_price": 100.0, "record_count": 4, "source_label": "demo", "data_quality": "limited", "created_at": precision_app.datetime(2026, 7, 3, tzinfo=precision_app.timezone.utc)},
        {"average_price": 100.0, "lowest_price": 100.0, "highest_price": 100.0, "record_count": 5, "source_label": "demo", "data_quality": "limited", "created_at": precision_app.datetime(2026, 7, 4, tzinfo=precision_app.timezone.utc)},
    ]
    chart = precision_app._watchlist_trend_chart_data(snapshots)
    assert chart["available"] is True
    assert chart["stable"] is True
    assert chart["snapshot_count"] == 4
    assert chart["average"] == [100.0, 100.0, 100.0, 100.0]
    assert chart["y_min"] < 100.0 < chart["y_max"]
    assert chart["counts"] == [3, 2, 4, 5]
    assert chart["tooltip_rows"][0]["label"]
    assert chart["tooltip_rows"][0]["source"] == "live"
    assert chart["trend_summary"]["movement"] == "stable"
    assert chart["trend_summary"]["change_amount"] == 0.0
    json.dumps(chart)


def test_prediction_validation_chart_states():
    pending = precision_app._prediction_validation_chart_data(
        {
            "status": "pending_actual",
            "baseline_predicted_average_price": 100.0,
            "ai_predicted_average_price": None,
            "actual_average_price": None,
        }
    )
    assert pending["available"] is False
    assert pending["pending"] is True
    assert pending["status_text"] == "Actual validation is pending."
    assert pending["labels"] == ["Baseline forecast"]
    assert pending["values"] == [100.0]
    assert pending["error_level"] == "pending"
    assert pending["tooltip_rows"][0]["label"] == "Baseline forecast"

    evaluated = precision_app._prediction_validation_chart_data(
        {
            "status": "evaluated",
            "baseline_predicted_average_price": 100.0,
            "ai_predicted_average_price": 105.0,
            "actual_average_price": 102.0,
        }
    )
    assert evaluated["available"] is True
    assert evaluated["pending"] is False
    assert evaluated["status_text"] == "Predicted vs actual average price."
    assert evaluated["labels"] == ["Baseline forecast", "Gemini forecast", "Actual observed average"]
    assert evaluated["values"] == [100.0, 105.0, 102.0]
    assert evaluated["error_level"] in {"matched", "low", "moderate", "high"}
    assert evaluated["tooltip_rows"][0]["label"] == "Baseline forecast"

    ai_unavailable = precision_app._prediction_validation_chart_data(
        {
            "status": "evaluated",
            "baseline_predicted_average_price": 100.0,
            "ai_predicted_average_price": None,
            "actual_average_price": 101.0,
        }
    )
    assert ai_unavailable["available"] is True
    assert ai_unavailable["labels"] == ["Baseline forecast", "Actual observed average"]
    assert ai_unavailable["values"] == [100.0, 101.0]
    assert ai_unavailable["tooltip_rows"][0]["label"] == "Baseline forecast"
    json.dumps(pending)
    json.dumps(evaluated)
    json.dumps(ai_unavailable)


def test_make_json_safe_converts_mongo_and_numeric_types():
    payload = {
        "_id": ObjectId(),
        "created_at": datetime(2026, 7, 3, 10, 30),
        "price": Decimal("12.50"),
        "nested": [{"item_id": ObjectId(), "when": datetime(2026, 7, 3, 11, 0)}],
    }
    safe = precision_app.make_json_safe(payload)
    assert isinstance(safe["_id"], str)
    assert safe["created_at"] == "2026-07-03T10:30:00"
    assert safe["price"] == 12.5
    assert isinstance(safe["nested"][0]["item_id"], str)
    assert safe["nested"][0]["when"] == "2026-07-03T11:00:00"
    json.dumps(safe)


def test_seed_demo_validation_helper_creates_evaluated_baseline_prediction():
    test_client = client()
    login(test_client, username="SeedVal", role="consumer", membership_tier="professional")
    user = precision_app.repository.get_user_by_display_name("SeedVal")
    result = precision_app.seed_demo_prediction_validation(user["_id"])
    assert result["watchlist_id"]
    predictions = precision_app.repository.list_predictions(user["_id"], result["watchlist_id"], limit=10)
    assert predictions
    assert predictions[0]["status"] == "evaluated"
    assert predictions[0]["baseline_absolute_error"] is not None


def test_watchlist_trend_summary_movements():
    def chart_for(values):
        snapshots = [
            {"average_price": value, "lowest_price": value - 1, "highest_price": value + 1, "record_count": 2, "source_label": "live", "data_quality": "good", "created_at": precision_app.datetime(2026, 7, index + 1, tzinfo=precision_app.timezone.utc)}
            for index, value in enumerate(values)
        ]
        return precision_app._watchlist_trend_chart_data(snapshots)["trend_summary"]["movement"]

    assert chart_for([100.0, 110.0]) == "rising"
    assert chart_for([110.0, 100.0]) == "falling"
    assert chart_for([100.0, 110.0, 100.0]) == "mixed"


def test_flat_watchlist_trend_renders_for_same_average_prices(monkeypatch):
    test_client = client()
    login(test_client, username="FlatTrend", role="consumer", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 100.0, "predicted_direction": "stable", "confidence_level": "medium", "reason": "mocked"}, "gemini_api", None))
    user = precision_app.repository.get_user_by_display_name("FlatTrend")
    item_id, _created = precision_app.repository.create_watchlist_item(user["_id"], {"keyword": "flat", "product_label": "flat", "tracking_mode": "search_scope", "source_label": "live"})
    item = precision_app.repository.get_watchlist_item(item_id, user["_id"])
    for day in range(1, 5):
        precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 100.0, "lowest_price": 100.0, "highest_price": 100.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, day, tzinfo=precision_app.timezone.utc)})
    page = test_client.get("/watchlist", query_string={"item_id": item["_id"]})
    body = page.data.decode("utf-8", errors="ignore")
    assert "Stable price" in body
    assert "Stable trend: average price stayed unchanged across recent snapshots." in body
    assert "More snapshots are needed to display a trend." not in body


def test_consumer_watchlist_uses_refresh_first_workflow(monkeypatch):
    test_client = client()
    login(test_client, username="ConsumerFlow", role="consumer", membership_tier="professional")
    test_client.post("/search?q=consumerflow&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": record["_id"], "tracking_mode": "search_scope"})
    item = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=1)[0]
    page = test_client.get("/watchlist", query_string={"item_id": item["_id"]})
    body = page.data.decode("utf-8", errors="ignore")
    assert "Collect latest prices" in body
    assert "Enable daily refresh" in body
    assert "Generate AI prediction" not in body
    assert "Evaluate with latest snapshot" not in body


def test_refresh_snapshot_validates_pending_forecast_and_preserves_selection(monkeypatch):
    test_client = client()
    login(test_client, username="RefreshUX", role="consumer", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 100.0, "predicted_direction": "stable", "confidence_level": "medium", "reason": "mocked"}, "gemini_api", None))
    test_client.post("/search?q=refreshux&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": record["_id"], "tracking_mode": "search_scope"})
    item = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 100.0, "lowest_price": 95.0, "highest_price": 105.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{item['_id']}/prediction")
    response = test_client.post(f"/watchlist/{item['_id']}/refresh")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/watchlist?item_id={item['_id']}")
    predictions = precision_app.repository.list_predictions(record["user_id"], item["_id"], limit=10)
    assert predictions[0]["status"] == "evaluated"
    page = test_client.get(response.headers["Location"])
    body = page.data.decode("utf-8", errors="ignore")
    assert "Snapshot refreshed." in body or "Forecast validated against the latest snapshot." in body
    snapshot_total = len(precision_app.repository.list_price_snapshots(item["_id"], limit=50))
    assert f"{snapshot_total} snapshots" in body


def test_watchlist_debug_charts_are_toggled_by_query_param(monkeypatch):
    test_client = client()
    login(test_client, username="DebugCharts", role="consumer", membership_tier="professional")
    user = precision_app.repository.get_user_by_display_name("DebugCharts")
    item_id, _created = precision_app.repository.create_watchlist_item(user["_id"], {"keyword": "debug", "product_label": "debug", "tracking_mode": "search_scope", "source_label": "live"})
    item = precision_app.repository.get_watchlist_item(item_id, user["_id"])
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 100.0, "lowest_price": 95.0, "highest_price": 105.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 110.0, "lowest_price": 105.0, "highest_price": 115.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 2, tzinfo=precision_app.timezone.utc)})
    page = test_client.get("/watchlist", query_string={"item_id": item["_id"], "debug_charts": "1"})
    body = page.data.decode("utf-8", errors="ignore")
    assert "WATCHLIST SVG CHART MODE ACTIVE" in body
    assert "trend_points_count:" in body
    assert "validation_chart_available:" in body

    normal = test_client.get("/watchlist", query_string={"item_id": item["_id"]})
    normal_body = normal.data.decode("utf-8", errors="ignore")
    assert "Chart diagnostics" not in normal_body


def test_refresh_does_not_evaluate_same_or_older_snapshot(monkeypatch):
    test_client = client()
    login(test_client, username="Older", role="consumer", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 100.0, "predicted_direction": "stable", "confidence_level": "medium", "reason": "mocked"}, "gemini_api", None))
    test_client.post("/search?q=older&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": record["_id"], "tracking_mode": "search_scope"})
    item = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 100.0, "lowest_price": 95.0, "highest_price": 105.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 2, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{item['_id']}/prediction")
    prediction = precision_app.repository.list_predictions(record["user_id"], item["_id"], limit=10)[0]
    precision_app.repository.update_prediction(prediction["_id"], {"prediction_created_at": precision_app.utcnow() + precision_app.timedelta(days=10)})
    predictions = precision_app.repository.list_predictions(record["user_id"], item["_id"], limit=10)
    assert predictions[0]["status"] == "pending_actual"
    response = test_client.post(f"/watchlist/{item['_id']}/refresh")
    assert response.status_code == 302
    predictions_after = precision_app.repository.list_predictions(record["user_id"], item["_id"], limit=10)
    assert predictions_after[0]["status"] == "pending_actual"



def test_seed_demo_snapshots_script_creates_demo_item_when_needed(monkeypatch):
    from tools import seed_demo_snapshots as seed_script

    test_client = client()
    login(test_client, username="ScriptSeed", role="consumer", membership_tier="professional")
    monkeypatch.setattr(seed_script, "repository", precision_app.repository)
    monkeypatch.setattr(seed_script, "seed_demo_snapshots", precision_app.seed_demo_snapshots)
    monkeypatch.setattr("sys.argv", ["seed_demo_snapshots.py", "--user", "ScriptSeed"])
    seed_script.main()
    user = precision_app.repository.get_user_by_display_name("ScriptSeed")
    items = precision_app.repository.list_watchlist_items(user_id=user["_id"], limit=10)
    demo_items = [item for item in items if item.get("source_label") == "demo"]
    assert len(demo_items) == 1
    snapshots = precision_app.repository.list_price_snapshots(demo_items[0]["_id"], limit=10)
    assert len(snapshots) == 5
    assert all(snap["source_label"] == "demo" for snap in snapshots)


def test_demo_snapshot_seeding_creates_labelled_history():
    test_client = client()
    login(test_client, username="DemoSeed", role="consumer", membership_tier="professional")
    test_client.post("/search?q=demotrack&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": record["_id"], "tracking_mode": "search_scope"})
    item = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=1)[0]
    created = precision_app.seed_demo_snapshots([item["_id"]], user_id=item["user_id"])
    assert len(created) == 5
    snapshots = precision_app.repository.list_price_snapshots(item["_id"], limit=10)
    assert len(snapshots) == 6
    demo_snapshots = [snap for snap in snapshots if snap["source_label"] == "demo"]
    assert len(demo_snapshots) == 5
    assert all(snap["data_quality"] in {"demo", "limited"} for snap in demo_snapshots)
    assert all("presentation" in snap.get("notes", "").lower() for snap in demo_snapshots)


def test_test_data_route_seeds_demo_watchlist_snapshots_for_current_user(monkeypatch):
    test_client = client()
    precision_app.app.config["DEMO_TOOLS_ENABLED"] = True
    login(test_client, username="RouteSeed", role="administrator", membership_tier="professional")
    response = test_client.post("/test-data/seed-watchlist-snapshots")
    assert response.status_code == 302
    assert "/watchlist?item_id=" in response.headers["Location"]
    user = precision_app.repository.get_user_by_display_name("RouteSeed")
    items = precision_app.repository.list_watchlist_items(user_id=user["_id"], limit=10)
    demo_items = [item for item in items if item.get("source_label") == "demo"]
    assert len(demo_items) == 1
    snapshots = precision_app.repository.list_price_snapshots(demo_items[0]["_id"], limit=10)
    assert len(snapshots) == 5
    assert all(snap["source_label"] == "demo" for snap in snapshots)


def test_test_data_route_seeds_demo_prediction_validation(monkeypatch):
    test_client = client()
    precision_app.app.config["DEMO_TOOLS_ENABLED"] = True
    login(test_client, username="DemoValidation", role="administrator", membership_tier="professional")
    response = test_client.post("/test-data/seed-watchlist-validation")
    assert response.status_code == 302
    assert "/watchlist?item_id=" in response.headers["Location"]
    user = precision_app.repository.get_user_by_display_name("DemoValidation")
    items = precision_app.repository.list_watchlist_items(user_id=user["_id"], limit=10)
    demo_items = [item for item in items if item.get("source_label") == "demo"]
    assert demo_items
    snapshots = precision_app.repository.list_price_snapshots(demo_items[0]["_id"], limit=10)
    assert len(snapshots) >= 4
    predictions = precision_app.repository.list_predictions(user["_id"], demo_items[0]["_id"], limit=10)
    assert predictions
    assert predictions[0]["status"] == "evaluated"
    assert predictions[0]["baseline_absolute_error"] is not None


def test_one_snapshot_prediction_stays_pending_and_hides_actual_metrics(monkeypatch):
    test_client = client()
    login(test_client, username="Pending", role="consumer", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 100.0, "predicted_direction": "stable", "confidence_level": "medium", "reason": "mocked"}, "gemini_api", None))
    test_client.post("/search?q=pending&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": record["_id"], "tracking_mode": "search_scope"})
    item = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 90.0, "lowest_price": 85.0, "highest_price": 95.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{item['_id']}/prediction")
    page = test_client.get("/watchlist", query_string={"item_id": item["_id"]})
    body = page.data.decode("utf-8", errors="ignore")
    assert "Actual validation is pending." in body
    assert "Observation pending until a later eligible snapshot is collected." in body
    assert "watchlist-validation-echart" in body


def test_prediction_chart_appears_after_evaluation(monkeypatch):
    test_client = client()
    login(test_client, username="EvalChart", role="consumer", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 100.0, "predicted_direction": "increase", "confidence_level": "high", "reason": "mocked"}, "gemini_api", None))
    test_client.post("/search?q=evalchart&data_source=mongodb&action=search", follow_redirects=True)
    record = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": record["_id"], "tracking_mode": "search_scope"})
    item = precision_app.repository.list_watchlist_items(user_id=record["user_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 90.0, "lowest_price": 85.0, "highest_price": 95.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{item['_id']}/prediction")
    precision_app.repository.save_price_snapshot(item["_id"], item["user_id"], {"average_price": 105.0, "lowest_price": 100.0, "highest_price": 110.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 2, tzinfo=precision_app.timezone.utc)})
    test_client.post(f"/watchlist/{item['_id']}/prediction/evaluate")
    page = test_client.get("/watchlist", query_string={"item_id": item["_id"]})
    body = page.data.decode("utf-8", errors="ignore")
    assert "Forecast vs observed" in body
    assert "Benchmark prediction" in body and "AI-assisted forecast" in body
    assert "Benchmark and AI forecast errors compare this selected Forecast Cycle's predictions with its later validation Snapshot." in body
    assert ">ECharts<" not in body
    assert "watchlist-validation-echart" in body


def test_watchlist_redirects_preserve_selected_item(monkeypatch):
    test_client = client()
    login(test_client, username="Keep", role="consumer", membership_tier="professional")
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *args, **kwargs: ({"predicted_average_price": 100.0, "predicted_direction": "stable", "confidence_level": "medium", "reason": "mocked"}, "gemini_api", None))
    test_client.post("/search?q=keepa&data_source=mongodb&action=search", follow_redirects=True)
    first = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": first["_id"], "tracking_mode": "search_scope"})
    first_item = precision_app.repository.list_watchlist_items(user_id=first["user_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(first_item["_id"], first_item["user_id"], {"average_price": 100.0, "lowest_price": 95.0, "highest_price": 105.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 1, tzinfo=precision_app.timezone.utc)})
    test_client.post("/search?q=keepb&data_source=mongodb&action=search", follow_redirects=True)
    second = precision_app.repository.list_searches(limit=1)[0]
    test_client.post("/watchlist/from-search", data={"search_record_id": second["_id"], "tracking_mode": "search_scope"})
    items = precision_app.repository.list_watchlist_items(user_id=first["user_id"], limit=10)
    second_item = items[0] if str(items[0]["_id"]) != str(first_item["_id"]) else items[1]
    precision_app.repository.save_price_snapshot(second_item["_id"], second_item["user_id"], {"average_price": 120.0, "lowest_price": 115.0, "highest_price": 125.0, "record_count": 2, "source_label": "live", "data_quality": "good", "collected_at": precision_app.datetime(2026, 7, 2, tzinfo=precision_app.timezone.utc)})
    response = test_client.post(f"/watchlist/{second_item['_id']}/refresh")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/watchlist?item_id={second_item['_id']}")
    response = test_client.post(f"/watchlist/{second_item['_id']}/prediction")
    assert response.status_code == 302
    response = test_client.post(f"/watchlist/{second_item['_id']}/prediction/evaluate")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/watchlist?item_id={second_item['_id']}")
