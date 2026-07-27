import precision_app
from services.database import MongoRepository


def test_authenticated_shell_and_watchlist_header_are_compact():
    precision_app.app.config.update(TESTING=True, SECRET_KEY="shell-ui-tests")
    precision_app.repository = MongoRepository(uri="", database_name="test_shell_ui")
    client = precision_app.app.test_client()
    password = "OfflineTestPassword1!"
    user = precision_app.repository.create_user("Shell User", "shell@example.test", precision_app.generate_password_hash(password, method="pbkdf2:sha256:1000"), "consumer")
    client.post("/login", data={"email": user["email"], "password": password})
    precision_app.repository.update_user(user["_id"], {"membership_tier": "professional", "plan": "professional"})
    precision_app.repository.create_watchlist_item(user["_id"], {"keyword": "phone", "product_label": "Phone monitor", "source_label": "mock"})
    body = client.get("/watchlist").data.decode("utf-8")
    assert "sidebar-brand" in body
    assert "Price Watchlist" in body
    assert "Monitor saved products and comparison groups over time." in body
    assert "Active monitors" in body
    assert "Tracked products and saved comparison groups for long-term price monitoring." not in body
    assert "class=\"app-brand\"" not in body
    assert "workspace-sidebar" in body and "app-header" in body
    assert body.count('alt="Precision Curator logo"') == 1
    assert 'class="sidebar-brand-fallback" aria-hidden="true" hidden' in body
    assert '>Collapse</span>' not in body
    assert 'aria-label="Collapse sidebar"' in body
    assert "header-utilities ml-auto" in body
    assert "Auto-validates after next snapshot" not in body
    assert "watchlist-action-row" in body
    assert "xl:grid-cols-[19rem_minmax(0,1fr)]" in body
    assert "xl:sticky xl:top-20" in body
    assert "Archive monitor" in body and "Delete monitor" in body
    assert "Active" in body and "Archived" in body
    assert 'class="app-page-header__content"' in body
    assert 'class="app-header-inner flex w-full items-center justify-between gap-4"' in body
    assert "app-header-inner flex w-full items-center justify-between gap-4 py-1" not in body
    assert body.count("\ufeff") == 1
    assert "position:fixed;inset:0 0 auto 0" in body


def test_shared_headers_and_analysis_toolbar_render_offline():
    precision_app.app.config.update(TESTING=True, SECRET_KEY="shell-ui-tests", DEMO_TOOLS_ENABLED=False)
    precision_app.repository = MongoRepository(uri="", database_name="test_shared_headers")
    client = precision_app.app.test_client()
    password = "OfflineTestPassword1!"
    user = precision_app.repository.create_user("Research UI", "research@example.test", precision_app.generate_password_hash(password, method="pbkdf2:sha256:1000"), "researcher")
    client.post("/login", data={"email": user["email"], "password": password})
    precision_app.repository.update_user(user["_id"], {"membership_tier": "professional", "plan": "professional"})
    precision_app.repository.create_analysis_record(user["_id"], {
        "query": "Phone market",
        "scope": "selected_comparison",
        "comparable_result_count": 3,
        "platforms": ["eBay", "Walmart"],
    })

    logs = client.get("/logs").data.decode("utf-8")
    audit = client.get("/audit").data.decode("utf-8")
    analytics = client.get("/analytics").data.decode("utf-8")

    for body in (logs, audit):
        assert 'class="app-page-header__content"' in body
        assert 'class="app-page-header__actions"' in body
        assert body.count("\ufeff") == 1
    assert "Workflow events" in logs and "Search user or event detail" in logs
    assert "Evidence records and provenance" in audit
    assert 'id="audit-search"' in audit
    assert 'id="audit-detail-dialog"' in audit
    assert "detailDialog.showModal()" in audit
    assert "No matching audit records" in audit
    assert "Search by product or query" in analytics
    assert "All analyses" in analytics and "Comparison analyses" in analytics
    assert "Newest first" in analytics and "Recently viewed" in analytics
    assert "Open analysis" in analytics and "Lifecycle actions for Phone market" in analytics
    assert "Delete analysis" in analytics


def test_public_home_header_starts_at_viewport_top():
    precision_app.app.config.update(TESTING=True, SECRET_KEY="shell-ui-tests")
    precision_app.repository = MongoRepository(uri="", database_name="test_public_shell")
    body = precision_app.app.test_client().get("/").data.decode("utf-8")
    assert 'class="public-header z-40 border-b"' in body
    assert 'class="public-main"' in body
    assert "position:fixed;inset:0 0 auto 0;height:4rem" in body
    assert "public-main{padding-top:0}" in body
    assert "public-header sticky" not in body
