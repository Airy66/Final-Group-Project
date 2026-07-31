from pathlib import Path

import precision_app
from services.database import MongoRepository
from services.mail_service import PasswordResetMailService
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]


def _template(name):
    return (ROOT / "templates" / name).read_text(encoding="utf-8-sig")


def test_sidebar_feature_badge_has_its_own_accessible_column():
    base = _template("product_base.html")
    assert "grid-template-columns:28px minmax(0,1fr) auto" in base
    assert ".app-nav-item .sidebar-link-text{min-width:0;overflow:hidden" in base
    assert 'title="Source Audit"' in base
    assert '<span class="sidebar-link-text">Source Audit</span>' in base
    assert 'title="Requires Professional plan" aria-label="Requires Professional plan">PRO</span>' in base
    assert 'title="Requires Premium plan" aria-label="Requires Premium plan">PREM</span>' in base
    assert ".sidebar-plan-marker--premium{" in base
    assert ".sidebar-plan-marker--professional{" in base
    assert "sidebar-access-lock" not in base
    assert '<span class="sidebar-link-text">Activity Logs</span>' in base
    assert 'aria-current="page"' in base
    assert 'body[data-sidebar-collapsed="true"] .sidebar-badge' in base


def test_locked_feature_pages_have_distinct_value_previews_and_one_primary_upgrade_cta():
    expected_features = {
        "saved_research": ("bookmarks", "Evidence library"),
        "watchlist": ("monitoring", "Price history"),
        "analytics_dashboard": ("analytics", "Price distribution"),
        "source_audit": ("policy", "Source provenance"),
        "logs": ("receipt_long", "AI traceability"),
    }
    headlines = set()
    for feature, (icon, capability) in expected_features.items():
        view = precision_app.LOCKED_FEATURE_VIEWS[feature]
        assert view["icon"] == icon
        assert len(view["capabilities"]) == 3
        assert capability in {item["title"] for item in view["capabilities"]}
        headlines.add(view["headline"])
    assert len(headlines) == len(expected_features)

    template = _template("locked_feature.html")
    assert template.count("{{ cta_label }}") == 1
    assert "What this unlocks" in template
    assert "Compare all plans" in template
    assert "Continue with Product Search" in template


def test_all_locked_feature_pages_use_the_same_dense_workspace_width():
    base = _template("product_base.html")
    assert "'analytics_compatibility','logs','administrator_dashboard'" in base
    assert "or feature is defined" in base


def test_responsive_shell_has_explicit_wide_and_mobile_policies():
    base = _template("product_base.html")
    assert ".workspace-content{max-width:90rem}" in base
    assert ".workspace-content--dense{max-width:108rem}" in base
    assert "@media (min-width: 768px)" in base
    assert "@media (max-width: 767px)" in base
    assert "@media (max-width: 640px)" in base
    assert ".workspace-sidebar{position:fixed" in base
    assert ".workspace-sidebar{position:relative" in base
    assert ".workspace-main{width:100%" in base
    assert "max-width:calc(100vw - 1.5rem)" in base


def test_search_loading_is_generic_and_duplicate_safe_by_default(monkeypatch):
    source = _template("source_search_roles.html")
    assert "Searching marketplace sources&hellip;" in source
    assert "Collecting and preparing comparable listings. This may take a few seconds." in source
    assert "form.dataset.submitting === 'true'" in source
    assert "form.setAttribute('aria-busy', 'true')" in source
    assert "button.disabled = true" in source
    assert "input:not([type=\"hidden\"]), select" not in source
    assert "{% if show_search_diagnostics %}" in source

    monkeypatch.setitem(precision_app.app.config, "RUNTIME_ENVIRONMENT", "production")
    monkeypatch.setattr(precision_app, "SHOW_SEARCH_DIAGNOSTICS", True)
    assert precision_app._search_ui_diagnostics_enabled() is False
    monkeypatch.setitem(precision_app.app.config, "RUNTIME_ENVIRONMENT", "development")
    assert precision_app._search_ui_diagnostics_enabled() is True
    monkeypatch.setattr(precision_app, "SHOW_SEARCH_DIAGNOSTICS", False)
    assert precision_app._search_ui_diagnostics_enabled() is False


def test_rendered_normal_search_hides_provider_stages(monkeypatch):
    monkeypatch.setattr(precision_app, "SHOW_SEARCH_DIAGNOSTICS", False)
    monkeypatch.setitem(precision_app.app.config, "TESTING", True)
    monkeypatch.setitem(precision_app.app.config, "SECRET_KEY", "final-ui-search")
    monkeypatch.setitem(precision_app.app.config, "RUNTIME_ENVIRONMENT", "production")
    precision_app.repository = MongoRepository(uri="", database_name="final_ui_search")
    user = precision_app.repository.create_user(
        "UI Search",
        "ui-search@example.test",
        generate_password_hash("OfflinePassword1!"),
        "consumer",
    )
    client = precision_app.app.test_client()
    client.post("/login", data={"email": user["email"], "password": "OfflinePassword1!"})
    body = client.get("/search").get_data(as_text=True)
    assert "Searching marketplace sources" in body
    assert "Searching eBay and Walmart" not in body
    assert "Normalizing provider responses" not in body
    assert 'title="Source Audit"' in body
    assert '<span class="sidebar-link-text">Source Audit</span>' in body


def test_export_menus_exclude_lifecycle_actions():
    for name in ("saved_packages.html", "audit.html", "analytics_dashboard.html", "watchlist.html"):
        source = _template(name)
        menu_fragments = source.split("data-export-menu")
        for fragment in menu_fragments[1:]:
            menu = fragment.split("</div>", 1)[0]
            assert ">Archive<" not in menu
            assert ">Delete<" not in menu
    watchlist = _template("watchlist.html")
    assert 'aria-label="Monitor lifecycle actions"' in watchlist
    assert ">Archive</button>" in watchlist and ">Delete</button>" in watchlist


def test_export_triggers_share_one_visual_contract():
    for name in ("saved_packages.html", "audit.html", "analytics_dashboard.html", "watchlist.html", "logs_complete.html"):
        source = _template(name)
        assert "app-export-summary" in source
        assert "download" in source
        assert "expand_more" in source
    base = _template("product_base.html")
    assert ".app-export-button,.app-export-summary" in base
    assert "background:#2563eb" in base
    assert ".app-export-button,.app-export-summary" in base


def test_account_and_more_menus_are_isolated_from_export_styles():
    base = _template("product_base.html")
    analyses = _template("analytics_index.html")
    watchlist = _template("watchlist.html")
    assert ".app-dropdown > summary:not(.app-export-summary)" not in base
    assert ".app-dropdown>.user-menu-summary" in base
    assert ".app-dropdown>.app-overflow-summary" in base
    assert 'class="app-overflow-summary"' in analyses
    assert 'id="analysis-list" class="mt-5 overflow-visible' in analyses
    assert "app-compact-action" in watchlist
    assert "data-view-alert-history" in watchlist


def test_dashboard_zero_listing_monitor_has_honest_state():
    source = _template("dashboard_retailer_saas.html")
    assert "{% if listing_count %}" in source
    assert "Needs refresh" in source
    assert "display_sgt_datetime" in source
    assert "Price trend" in source
    assert "Baseline collected" in source
    assert "Observed range" in source
    assert "Sourcing opportunity" in source
    assert "data-values=" in source


def test_comparable_price_curve_uses_rank_not_time_and_sorts_prices():
    rows = [
        {"_selection_token": "external:c", "title": "C", "price": 300, "normalized_price": 300, "analytics_eligible": True, "platform": "eBay"},
        {"_selection_token": "external:a", "title": "A", "price": 100, "normalized_price": 100, "analytics_eligible": True, "platform": "eBay"},
        {"_selection_token": "external:b", "title": "B", "price": 200, "normalized_price": 200, "analytics_eligible": True, "platform": "Walmart"},
    ]
    payload = precision_app.build_analytics_payload(rows, query="Phone", analysis_scope="frozen analysis")
    curve = payload["comparable_price_curve"]
    assert curve["available"] is True
    assert curve["prices"] == [100.0, 200.0, 300.0]
    assert curve["labels"] == [1, 2, 3]
    assert curve["record_ids"] == ["external:a", "external:b", "external:c"]
    assert curve["x_axis"] == "Listing rank"
    assert "time" not in curve["x_axis"].lower()

    insufficient = precision_app.build_analytics_payload(rows[:1], query="Phone")
    assert insufficient["comparable_price_curve"]["available"] is False


def test_analytics_chart_families_and_scope_copy_are_truthful():
    source = _template("analytics_dashboard.html")
    assert "Comparable Price Curve" in source
    assert "Comparable listing prices ordered from lowest to highest." in source
    assert "Listing rank" in source
    assert "Price Trend" not in source
    assert "type: 'pie'" in source and "radius: ['55%', '78%']" in source
    assert "Price distribution histogram" in source
    assert "Lollipop" in source and "type: 'scatter'" in source
    assert "renderPriceCurve();" in source
    assert "renderPreview();" in source


def test_watchlist_status_cards_editor_and_action_hierarchy():
    source = _template("watchlist.html")
    assert 'data-status-card="daily-refresh"' in source
    assert 'data-status-card="price-alert"' in source
    assert "<dialog id=\"price-alert-modal\"" in source
    assert "<details id=\"price-alert-modal\"" not in source
    assert "Collect first snapshot" in source and "Collect now and validate" in source
    assert "Generate next forecast" in source and "Generate forecast" in source
    assert "View snapshots" in source and ">Export " in source
    assert "Average Price Trend" in source and "Snapshot history over time." in source
    for label in ("Benchmark prediction", "AI-assisted forecast", "Observed market average", "Benchmark error", "AI forecast error"):
        assert label in source


def test_unified_email_templates_keep_plain_text_and_persisted_plan_content():
    service = PasswordResetMailService(mode="console")
    reset_text = service._text_body("https://app.test/reset", 30)
    reset_html = service._html_body("https://app.test/reset", 30)
    welcome_text = service._welcome_text_body("Person", "https://app.test/login", "professional")
    welcome_html = service._welcome_html_body("Person", "https://app.test/login", "professional")
    alert_text, alert_html = service._price_alert_bodies(
        "Monitor", "drop", 5, 100, 90, -10, None, "https://app.test/watchlist", False
    )
    test_text, test_html = service._price_alert_bodies(
        "Monitor", "either", 5, None, None, None, None, "https://app.test/watchlist", True
    )
    for plain, html in (
        (reset_text, reset_html),
        (welcome_text, welcome_html),
        (alert_text, alert_html),
        (test_text, test_html),
    ):
        assert plain and html
        assert "Precision Curator" in html
        assert "max-width:600px" in html
        assert "<img" not in html.lower()
    assert "Your current plan: Professional" in welcome_text + welcome_html
    assert "Source Audit" in welcome_text + welcome_html
    assert "This is a test notification. No marketplace threshold was triggered." in test_text + test_html


def test_registration_passes_membership_from_persisted_user_record():
    source = (ROOT / "precision_app.py").read_text(encoding="utf-8-sig")
    start = source.index("send_registration_welcome(")
    call = source[start:start + 500]
    assert 'user.get("membership_tier")' in call
    assert "request.form" not in call
