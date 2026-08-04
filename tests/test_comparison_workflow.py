import precision_app
from bs4 import BeautifulSoup
from services.database import MongoRepository


def make_client(username="Workflow User"):
    precision_app.app.config.update(TESTING=True, SECRET_KEY="workflow-tests", DEVELOPER_ROLE_PREVIEW=False)
    precision_app.repository = MongoRepository(uri="", database_name="comparison_workflow_tests")
    client = precision_app.app.test_client()
    email = f"{''.join(character.lower() for character in username if character.isalnum())}@example.test"
    password = "OfflineTestPassword1!"
    user = precision_app.repository.create_user(username, email, precision_app.generate_password_hash(password, method="pbkdf2:sha256:1000"), "consumer")
    client.post("/login", data={"email": email, "password": password})
    precision_app.repository.update_user(user["_id"], {"membership_tier": "professional", "plan": "professional"})
    return client, precision_app.repository.get_user_by_id(user["_id"])


def seed_search(user):
    search_id = precision_app.repository.create_search(user["_id"], "iPhone 12", "both", role="consumer", data_mode="api")
    rows = precision_app.repository.save_external_results(search_id, user["_id"], [
        {"title": "Apple iPhone 12 128GB", "platform": "eBay", "price": 679.0, "raw_price_text": "$679.00", "currency": "USD", "condition": "Used", "source_type": "live_ebay", "source_url": "https://example.com/ebay-679", "match_type": "exact_match"},
        {"title": "Apple iPhone 12 monthly offer", "platform": "Walmart", "price": 1.0, "raw_price_text": "$1.00/month", "currency": "USD", "condition": "New", "source_type": "serpapi_walmart", "source_url": "https://example.com/walmart-monthly", "match_type": "exact_match"},
        {"title": "Apple iPhone 12 128GB", "platform": "Walmart", "price": 729.0, "raw_price_text": "$729.00", "currency": "USD", "condition": "New", "source_type": "serpapi_walmart", "source_url": "https://example.com/walmart-729", "match_type": "exact_match"},
    ])
    tokens = [f"external:{row['_id']}" for row in rows]
    precision_app.repository.attach_result_tokens(search_id, tokens)
    precision_app.repository.complete_search(search_id, len(rows))
    return search_id, tokens


def create_comparison(client, search_id, tokens):
    response = client.post("/compare", query_string=[("search_record_id", search_id), *[("result_token", token) for token in tokens]])
    group = precision_app.repository.list_comparison_groups(limit=1)[0]
    return response, group


def test_blank_search_page_does_not_report_unrequested_walmart_status():
    client, _user = make_client("Blank Search Status")
    response = client.get("/search")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "No Walmart matches were found for this query." not in body
    assert "Walmart could not be reached for this search." not in body
    statuses = precision_app._canonical_source_statuses("", "both", precision_app._new_search_diagnostics("", "both"), [])
    assert statuses["ebay"]["status"] == "not_requested"
    assert statuses["walmart"]["status"] == "not_requested"


def test_monthly_offer_stays_visible_but_is_excluded_from_metrics():
    rows = precision_app.annotate_comparison(precision_app.normalize_price_items([
        {"title": "Phone full price", "platform": "eBay", "price": 679, "raw_price_text": "$679.00"},
        {"title": "Phone monthly offer", "platform": "Walmart", "price": 1, "raw_price_text": "$1.00/month"},
        {"title": "Phone full price", "platform": "Walmart", "price": 729, "raw_price_text": "$729.00"},
    ]), "Phone")
    metrics = precision_app.comparison_metrics(rows)
    assert len(rows) == 3
    assert rows[1]["price_type"] == "installment"
    assert rows[1]["analytics_eligible"] is False
    assert rows[1]["normalized_price"] is None
    assert metrics["lowest"] == 679
    assert metrics["average"] == 704
    assert metrics["excluded_count"] == 1
    conclusion = precision_app.comparison_conclusion(rows, metrics)
    assert "lowest comparable full-price" in conclusion
    assert "monthly-payment" in conclusion
    assert "1.00" not in conclusion


def test_analytics_preview_keeps_excluded_rows_out_of_metrics_and_uses_singapore_time():
    rows = precision_app.annotate_comparison(precision_app.normalize_price_items([
        {"title": "Phone full price", "platform": "eBay", "price": 700, "raw_price_text": "$700", "condition": "used", "collected_at": "2026-07-11T15:14:40+00:00"},
        {"title": "Phone plan", "platform": "Walmart", "price": 1, "raw_price_text": "$1/month", "collected_at": "2026-07-11T15:14:40+00:00"},
    ]), "Phone")
    payload = precision_app.build_analytics_payload(rows, query="Phone")
    assert len(payload["records"]) == 2
    assert payload["summary"]["total_records"] == 1
    assert payload["summary"]["average_price"] == 700
    assert payload["records"][0]["condition"] == "Used"
    assert payload["records"][0]["collected_at"] == "11 Jul 2026, 23:14 SGT"
    assert payload["records"][1]["analytics_eligible"] is False


def test_retailer_dashboard_uses_latest_active_monitor_snapshots_only():
    _client, user = make_client("Retailer Snapshot Owner")
    precision_app.repository.create_watchlist_item(user["_id"], {
        "keyword": "Phone", "product_label": "Phone", "status": "active", "tracking_mode": "selected_records",
        "selected_records_snapshot": [],
    })
    monitor = precision_app.repository.list_watchlist_items(user["_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(monitor["_id"], user["_id"], {
        "listing_records": [
            {"title": "Phone 128GB", "platform": "eBay", "price": 650, "raw_price_text": "$650", "source_url": "https://example.com/phone"},
            {"title": "Phone 128GB duplicate", "platform": "eBay", "price": 650, "raw_price_text": "$650", "source_url": "https://example.com/phone"},
        ],
    })
    precision_app.repository.create_search(user["_id"], "Old history should not count", "both", role="retailer")
    portfolio = precision_app.build_retailer_dashboard_view(user["_id"])
    assert portfolio["portfolio_scope"] is True
    assert portfolio["prices"]["listings"] == 1
    assert portfolio["prices"]["lowest"] is None
    view = precision_app.build_retailer_dashboard_view(user["_id"], monitor["_id"])
    assert view["portfolio_scope"] is False
    assert view["prices"]["listings"] == 1
    assert view["prices"]["lowest"] == 650
    assert view["source_kind"] == "watchlist"


def test_retailer_dashboard_builds_actionable_price_trend_metrics():
    _client, user = make_client("Retailer Trend Owner")
    precision_app.repository.create_watchlist_item(user["_id"], {
        "keyword": "Phone", "product_label": "Phone", "status": "active", "tracking_mode": "selected_records",
        "selected_records_snapshot": [],
    })
    monitor = precision_app.repository.list_watchlist_items(user["_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(monitor["_id"], user["_id"], {
        "average_price": 100, "lowest_price": 90, "highest_price": 120, "record_count": 2,
        "collected_at": precision_app.utcnow() - precision_app.timedelta(days=1),
        "listing_records": [
            {"title": "Phone A", "platform": "eBay", "price": 90, "raw_price_text": "$90"},
            {"title": "Phone B", "platform": "Walmart", "price": 110, "raw_price_text": "$110"},
        ],
    })
    precision_app.repository.save_price_snapshot(monitor["_id"], user["_id"], {
        "average_price": 90, "lowest_price": 80, "highest_price": 110, "record_count": 2,
        "collected_at": precision_app.utcnow(),
        "listing_records": [
            {"title": "Phone A", "platform": "eBay", "price": 100, "raw_price_text": "$100"},
            {"title": "Phone B", "platform": "Walmart", "price": 80, "raw_price_text": "$80"},
        ],
    })

    view = precision_app.build_retailer_dashboard_view(user["_id"], monitor["_id"])
    metrics = view["selected_metrics"]
    assert view["trend_chart"]["available"] is True
    assert view["trend_chart"]["average"] == [100.0, 90.0]
    assert metrics["change_amount"] == -10
    assert metrics["change_percentage"] == -10
    assert metrics["trend_state"] == "falling"
    assert metrics["best_platform"] == "Walmart"
    assert metrics["best_price"] == 80
    assert metrics["below_average_percent"] == 11.1
    assert metrics["average_position"] == 33.3
    assert "Walmart" in view["sourcing_insight"] and "11.1%" in view["sourcing_insight"]


def test_retailer_dashboard_renders_portfolio_and_monitor_trend_views():
    client, user = make_client("Retailer Dashboard Render")
    precision_app.repository.update_user(user["_id"], {
        "roles": ["retailer"], "primary_role": "retailer", "active_role": "retailer", "role": "retailer",
    })
    with client.session_transaction() as browser_session:
        browser_session["roles"] = ["retailer"]
        browser_session["active_role"] = "retailer"
        browser_session["role"] = "retailer"
    precision_app.repository.create_watchlist_item(user["_id"], {
        "keyword": "Phone", "product_label": "Phone", "status": "active", "tracking_mode": "selected_records",
        "platform_scope": "eBay, Walmart", "selected_records_snapshot": [],
    })
    monitor = precision_app.repository.list_watchlist_items(user["_id"], limit=1)[0]
    precision_app.repository.save_price_snapshot(monitor["_id"], user["_id"], {
        "average_price": 100, "lowest_price": 90, "highest_price": 110, "record_count": 2,
        "collected_at": precision_app.utcnow(),
        "listing_records": [
            {"title": "Phone A", "platform": "eBay", "price": 90, "raw_price_text": "$90"},
            {"title": "Phone B", "platform": "Walmart", "price": 110, "raw_price_text": "$110"},
        ],
    })

    portfolio = client.get("/dashboard/retailer")
    monitor_view = client.get("/dashboard/retailer", query_string={"monitor_id": monitor["_id"]})
    assert portfolio.status_code == monitor_view.status_code == 200
    assert b"Price monitoring portfolio" in portfolio.data
    assert b"BASELINE" in portfolio.data
    assert b"Price trend" in monitor_view.data
    assert b"Baseline collected" in monitor_view.data
    assert b"Sourcing opportunity" in monitor_view.data


def test_compare_requires_two_and_accepts_practical_selected_sets():
    client, user = make_client()
    search_id, tokens = seed_search(user)
    too_few = client.post("/compare", query_string={"search_record_id": search_id, "result_token": tokens[0]})
    assert too_few.status_code == 302
    response, _group = create_comparison(client, search_id, tokens)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Comparison workspace" in body
    assert "$1.00/month" not in body
    assert "Analyze comparison" in body
    assert "Track this comparison" in body
    assert "Save comparison set" in body
    assert ">Data source<" not in body
    assert all(label in body for label in ("Product", "Platform", "Price", "Condition", "Price quality", "Source link", "Market position"))
    assert "Comparison coverage" in body and "Comparable price range" in body
    assert "Decision support" in body and "Market conclusion" in body
    assert 'rel="noopener noreferrer"' in body


def test_explicit_analysis_scopes_and_ownership():
    client, user = make_client("Analysis Owner")
    search_id, tokens = seed_search(user)
    response = client.post("/analytics/from-search", data={"search_record_id": search_id})
    assert response.status_code == 302
    search_analysis = precision_app.repository.list_analysis_records(user["_id"], limit=1)[0]
    assert search_analysis["scope"] == "all_valid_results"
    assert len(search_analysis["included_result_ids"]) == 2
    assert len(search_analysis["excluded_result_ids"]) == 0
    search_page = client.get(f"/analytics/{search_analysis['_id']}")
    assert search_page.status_code == 200
    assert "Ranked Listing Prices" in search_page.get_data(as_text=True)
    assert "Price Trend Over Time" not in search_page.get_data(as_text=True)
    assert "Search Analytics — All valid results" in search_page.get_data(as_text=True)
    comparison_response, group = create_comparison(client, search_id, tokens)
    assert comparison_response.status_code == 200
    response = client.post(f"/analytics/from-comparison/{group['_id']}")
    assert response.status_code == 302
    comparison_analysis = precision_app.repository.list_analysis_records(user["_id"], limit=1)[0]
    assert comparison_analysis["scope"] == "selected_comparison"
    comparison_page = client.get(f"/analytics/{comparison_analysis['_id']}")
    assert comparison_page.status_code == 200
    assert "Ranked Listing Prices" in comparison_page.get_data(as_text=True)
    assert "Comparison Analytics — Selected records" in comparison_page.get_data(as_text=True)
    other = precision_app.repository.create_user("Different User", "different@example.com", "unused", "consumer", membership_tier="professional")
    with client.session_transaction() as session:
        session["user_id"] = other["_id"]
        session["username"] = other["display_name"]
        session["role"] = "consumer"
        session["active_role"] = "consumer"
    assert client.get(f"/compare/{group['_id']}").status_code == 302
    assert client.get(f"/analytics/{comparison_analysis['_id']}").status_code == 404


def test_track_creates_baseline_without_prediction_and_save_does_not_monitor():
    client, user = make_client("Track Owner")
    search_id, tokens = seed_search(user)
    _response, group = create_comparison(client, search_id, tokens)
    track = client.post("/watchlist/from-compare", data={"comparison_id": group["_id"], "search_record_id": search_id})
    assert track.status_code == 302
    watchlist = precision_app.repository.list_watchlist_items(user["_id"], limit=1)[0]
    assert watchlist["comparison_group_id"] == group["_id"]
    assert precision_app.repository.list_price_snapshots(watchlist["_id"], limit=10)
    assert precision_app.repository.list_predictions(user["_id"], watchlist["_id"], limit=10) == []
    saved = client.post(f"/saved/comparison/{group['_id']}")
    assert saved.status_code == 302
    saved_group = precision_app.repository.get_comparison_group(group["_id"], user["_id"])
    assert saved_group["saved_status"] == "saved"
    assert saved_group["monitoring_enabled"] is False
    client.post("/logout")
    with client.session_transaction() as session:
        session["user_id"] = user["_id"]
        session["username"] = user["display_name"]
        session["role"] = "consumer"
        session["active_role"] = "consumer"
    reopened = client.get(f"/compare/{group['_id']}")
    assert reopened.status_code == 200
    reopened_body = reopened.get_data(as_text=True)
    assert ">Saved</span>" in reopened_body
    assert "Save comparison set" not in reopened_body


def test_search_actions_and_logout_transient_state():
    client, user = make_client("Session Owner")
    search_id, tokens = seed_search(user)
    with client.session_transaction() as session:
        session["current_search_id"] = search_id
        session["selected_result_ids"] = tokens
        session["temporary_comparison_state"] = "temp"
        session["active_analysis_id"] = "analysis"
        session["temporary_filters"] = {"platform": "eBay"}
    client.post("/logout")
    with client.session_transaction() as session:
        for key in ("current_search_id", "selected_result_ids", "temporary_comparison_state", "active_analysis_id", "temporary_filters"):
            assert key not in session
    template = open("templates/source_search_roles.html", encoding="utf-8").read()
    assert "Analyze selected" not in template
    assert "Analyze all comparable results" in template
    assert "Compare selected" in template
    assert "Save selected evidence" in template


def test_search_ui_exposes_bulk_selection_and_separates_recent_history_language():
    search_template = open("templates/source_search_roles.html", encoding="utf-8").read()
    history_template = open("templates/partials/search_history.html", encoding="utf-8").read()
    assert "Select visible" in search_template
    assert "Select filtered" in search_template
    assert "Select all eBay" not in search_template
    assert "result-platform-filter" in search_template
    assert "bulk-action-bar" in search_template
    assert "select-all-visible" in search_template
    assert "results-visible-summary" in search_template
    assert "mt-4 overflow-x-auto app-table-wrap" in search_template
    assert "max-h-[680px]" not in search_template
    assert "Not qualified" in search_template
    assert 'class="refine-filter-grid mt-4"' in search_template
    assert "2xl:grid-cols-7" not in search_template
    assert "Price range" in search_template
    assert "data-facet-popover" in search_template
    assert "data-facet-summary" in search_template
    assert "updateFacetSummary" in search_template
    assert "AI-generated" in search_template
    assert "Generated with Gemini from the displayed records." in search_template
    assert "AI output may be inaccurate" in search_template
    assert "cleanInsightText" in search_template
    assert search_template.index('id="result-context"') < search_template.index('id="ai-panel"') < search_template.index('data-facet-area')
    assert "Key finding" in search_template and "Review before acting" in search_template
    assert 'aria-controls="ai-panel"' in search_template
    assert "Regenerate interpretation" in search_template
    assert "How these results were selected" not in search_template
    assert 'id="ai-freshness"' not in search_template
    assert search_template.count('id="bulk-action-bar"') == 1
    assert search_template.count('id="bulk-selected-label"') == 1
    assert "comparisonMax" not in search_template
    assert "<th class=\"p-3\">Category</th>" not in search_template
    assert "<th class=\"p-3\">Match" not in search_template
    assert "Recent searches" in history_template
    assert "Create monitor" not in history_template


def test_mixed_apple_search_requires_category_before_analytics_or_comparison(monkeypatch):
    client, user = make_client("Ambiguous Apple Owner")
    search_id = precision_app.repository.create_search(user["_id"], "apple", "both", role="consumer", data_mode="api")
    rows = precision_app.repository.save_external_results(search_id, user["_id"], [
        {"title": "Apple iPhone 12 128GB", "platform": "eBay", "price": 500, "condition": "New", "aspects": {"Carrier": "Unlocked"}, "match_type": "exact_match"},
        {"title": "Apple iPhone 13 256GB", "platform": "Walmart", "price": 650, "condition": "Used", "aspects": {"Carrier": "Verizon"}, "match_type": "exact_match"},
        {"title": "Apple MacBook Air M2 8GB Memory 256GB", "platform": "eBay", "price": 900, "condition": "New", "match_type": "exact_match"},
        {"title": "Apple MacBook Pro M3 16GB Memory 512GB", "platform": "Walmart", "price": 1300, "condition": "Used", "match_type": "exact_match"},
        {"title": "Freeze Dried Fuji Apples snack", "platform": "eBay", "price": 400, "condition": "New", "match_type": "exact_match"},
        {"title": "Green Apple Candy", "platform": "Walmart", "price": 350, "condition": "New", "match_type": "exact_match"},
    ])
    tokens = [f"external:{row['_id']}" for row in rows]
    precision_app.repository.attach_result_tokens(search_id, tokens)
    precision_app.repository.complete_search(search_id, len(rows))

    response = client.post(f"/search/results/{search_id}", follow_redirects=True)
    body = response.get_data(as_text=True)
    record = precision_app.repository.get_search(search_id, user["_id"])
    assert response.status_code == 200
    assert "6 matched listings" in body
    assert "Multiple product categories found" in body
    assert "Choose one product category to enable reliable price comparison and analysis." in body
    assert body.index(">Category<") < body.index(">Platform<")
    assert "Lowest comparable price" not in body
    assert "Analyze all comparable results" not in body
    assert 'aria-label="AI Discover: Explain this comparison"' not in body
    assert record["query_intent_status"] in {"ambiguous", "broad"}
    assert record["category_mode"] == "multi_category"
    assert set(record["detected_category_keys"]) == {"smartphones", "laptops", "food_and_grocery"}
    assert {value["category_key"] for value in record["result_categories"].values()} == {"smartphones", "laptops", "food_and_grocery"}

    blocked_analysis = client.post("/analytics/from-search", data={"search_record_id": search_id})
    blocked_compare = client.post("/compare", query_string=[("search_record_id", search_id), ("result_token", tokens[0]), ("result_token", tokens[1])])
    assert blocked_analysis.status_code == 302 and blocked_compare.status_code == 302
    assert precision_app.repository.list_analysis_records(user["_id"], limit=10) == []
    assert precision_app.repository.list_comparison_groups(user["_id"], limit=10) == []

    monkeypatch.setattr(precision_app, "search_ebay_cached", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("category filtering called eBay")))
    monkeypatch.setattr(precision_app, "search_walmart_items", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("category filtering called Walmart")))
    phones = client.get(f"/search/results/{search_id}", query_string={"selected_category_key": "smartphones"})
    phone_body = phones.get_data(as_text=True)
    assert phones.status_code == 200
    assert "2 comparable listings" in phone_body
    assert all(label in phone_body for label in ("Model", "Storage", "Carrier"))
    assert "Analyze all comparable results" in phone_body

    laptops = client.get(f"/search/results/{search_id}", query_string={"selected_category_key": "laptops"})
    laptop_body = laptops.get_data(as_text=True)
    assert laptops.status_code == 200
    assert all(label in laptop_body for label in ("Model", "Processor", "Memory", "Storage"))

    food = client.get(f"/search/results/{search_id}", query_string={"selected_category_key": "food_and_grocery"})
    food_body = food.get_data(as_text=True)
    assert food.status_code == 200
    assert "General comparison mode is active" in food_body
    assert all(label not in food_body for label in (">Model<", ">Storage<", ">Carrier<", ">Processor<", ">Memory<"))


def test_mixed_gucci_product_types_require_selection_and_scope_ai(monkeypatch):
    client, user = make_client("Generic Gucci Owner")
    search_id = precision_app.repository.create_search(user["_id"], "Gucci", "both", role="consumer", data_mode="api")
    source_rows = [
        {"title": "Gucci Bloom Eau de Parfum perfume spray", "platform": "eBay", "price": 110, "condition": "New"},
        {"title": "Gucci Guilty Eau de Toilette fragrance", "platform": "Walmart", "price": 120, "condition": "New"},
        {"title": "Gucci Flora perfume spray", "platform": "eBay", "price": 130, "condition": "New"},
        {"title": "Gucci foundation makeup", "platform": "Walmart", "price": 100, "condition": "New"},
        {"title": "Gucci pressed powder cosmetic", "platform": "eBay", "price": 105, "condition": "New"},
        {"title": "Gucci lipstick makeup", "platform": "Walmart", "price": 115, "condition": "New"},
    ]
    rows = precision_app.repository.save_external_results(search_id, user["_id"], [{**row, "match_type": "exact_match"} for row in source_rows])
    tokens = [f"external:{row['_id']}" for row in rows]
    precision_app.repository.attach_result_tokens(search_id, tokens)
    precision_app.repository.complete_search(search_id, len(rows))

    page = client.post(f"/search/results/{search_id}", follow_redirects=True)
    body = page.get_data(as_text=True)
    record = precision_app.repository.get_search(search_id, user["_id"])
    assert page.status_code == 200
    assert "6 matched listings" in body and "Multiple product types found" in body
    assert "Choose one product type to enable reliable price comparison and analysis." in body
    assert body.index(">Product type<") < body.index(">Platform<")
    assert "Lowest comparable price" not in body and "Analyze all comparable results" not in body
    assert record["comparison_enabled"] is False
    assert record["product_type_counts"] == {"fragrance": 3, "makeup": 3}
    assert client.post("/analytics/from-search", data={"search_record_id": search_id}).status_code == 302
    assert precision_app.repository.list_analysis_records(user["_id"], limit=10) == []

    fragrance_page = client.post(f"/search/results/{search_id}", data={"selected_product_type": "fragrance"}, follow_redirects=True)
    fragrance_body = fragrance_page.get_data(as_text=True)
    assert fragrance_page.status_code == 200
    assert "3 comparable listings in the collected result set" in fragrance_body
    assert "General comparison mode is active" in fragrance_body
    record = precision_app.repository.get_search(search_id, user["_id"])
    assert record["active_filters"]["selected_product_type"] == "fragrance"

    captured = {}
    def fake_summary(_query, items, **_kwargs):
        captured["titles"] = [item["title"] for item in items]
        return "Scoped fragrance insight", "rule_based_fallback", None
    monkeypatch.setattr(precision_app, "summarize_market", fake_summary)
    insight = client.post("/api/ai-discover", json={"search_record_id": search_id})
    assert insight.status_code == 200
    assert len(insight.get_json()["included_result_ids"]) == 3
    assert all("makeup" not in title.lower() and "powder" not in title.lower() and "lipstick" not in title.lower() for title in captured["titles"])


def test_refine_platform_and_condition_override_stored_filters_and_scope_ai(monkeypatch):
    client, user = make_client("Refine Filter Owner")
    search_id = precision_app.repository.create_search(user["_id"], "iPhone 13", "both", role="consumer", data_mode="api")
    source_rows = [
        {"title": "Apple iPhone 13 128GB Blue", "platform": "eBay", "price": 500, "condition": "New"},
        {"title": "Apple iPhone 13 128GB Midnight", "platform": "eBay", "price": 510, "condition": "New"},
        {"title": "Apple iPhone 13 128GB Used", "platform": "eBay", "price": 440, "condition": "Used"},
        {"title": "Apple iPhone 13 128GB Blue", "platform": "Walmart", "price": 520, "condition": "New"},
        {"title": "Apple iPhone 13 128GB Midnight", "platform": "Walmart", "price": 530, "condition": "New"},
        {"title": "Apple iPhone 13 128GB Used", "platform": "Walmart", "price": 450, "condition": "Used"},
    ]
    rows = precision_app.repository.save_external_results(
        search_id,
        user["_id"],
        [{**row, "match_type": "exact_match", "source_type": "marketplace"} for row in source_rows],
    )
    tokens = [f"external:{row['_id']}" for row in rows]
    precision_app.repository.attach_result_tokens(search_id, tokens)
    precision_app.repository.complete_search(search_id, len(rows))

    condition_page = client.post(
        f"/search/results/{search_id}",
        data={"platform": "", "condition": "New", "min_price": "", "max_price": "", "sort": "normalized_price_asc"},
        follow_redirects=True,
    )
    condition_soup = BeautifulSoup(condition_page.data, "html.parser")
    record = precision_app.repository.get_search(search_id, user["_id"])
    assert condition_page.status_code == 200
    assert record["active_filters"]["condition"] == "New"
    assert record["active_filters"]["platform"] == ""
    assert condition_soup.select_one('select[name="condition"] option[selected]').get("value") == "New"

    captured = {}
    def fake_summary(_query, items, **_kwargs):
        captured["items"] = items
        return "Condition-scoped insight", "rule_based_fallback", None

    monkeypatch.setattr(precision_app, "summarize_market", fake_summary)
    insight = client.post("/api/ai-discover", json={"search_record_id": search_id})
    assert insight.status_code == 200
    assert len(captured["items"]) == 4
    assert {item["platform"] for item in captured["items"]} == {"eBay", "Walmart"}
    assert all(item["condition_normalized"] == "New" for item in captured["items"])

    platform_page = client.post(
        f"/search/results/{search_id}",
        data={"platform": "eBay", "condition": "", "min_price": "", "max_price": "", "sort": "normalized_price_asc"},
        follow_redirects=True,
    )
    platform_soup = BeautifulSoup(platform_page.data, "html.parser")
    record = precision_app.repository.get_search(search_id, user["_id"])
    assert platform_page.status_code == 200
    assert record["active_filters"]["platform"] == "eBay"
    assert record["active_filters"]["condition"] == ""
    assert platform_soup.select_one('select[name="platform"] option[selected]').get("value") == "eBay"
    assert {option.get("value") for option in platform_soup.select('select[name="platform"] option')} == {"", "eBay", "Walmart"}


def test_channel_query_remains_unchanged_when_exact_results_are_strong():
    client, user = make_client("Channel Suggestion Owner")
    search_id = precision_app.repository.create_search(user["_id"], "channel", "ebay", role="consumer", data_mode="api")
    rows = precision_app.repository.save_external_results(search_id, user["_id"], [
        {"title": "Two channel audio amplifier", "platform": "eBay", "price": 90, "condition": "New", "match_type": "exact_match"},
        {"title": "Digital audio channel converter", "platform": "eBay", "price": 110, "condition": "New", "match_type": "exact_match"},
    ])
    precision_app.repository.attach_result_tokens(search_id, [f"external:{row['_id']}" for row in rows])
    precision_app.repository.complete_search(search_id, len(rows))
    page = client.get(f"/search/results/{search_id}")
    body = page.get_data(as_text=True)
    record = precision_app.repository.get_search(search_id, user["_id"])
    assert page.status_code == 200
    assert record["keyword"] == "channel"
    assert "Did you mean Chanel?" not in body and "Search for Chanel" not in body
    assert "Two channel audio amplifier" in body


def _mock_phone_rows(platform, count=3):
    return [
        {"title": f"Apple iPhone 15 {128 + index * 128}GB Unlocked", "platform": platform, "price": 700 + index * 25, "condition": "New", "match_type": "exact_match"}
        for index in range(count)
    ]


def test_canonical_source_status_distinguishes_success_no_results_and_failure(monkeypatch):
    client, user = make_client("Canonical Source Owner")
    monkeypatch.setattr(precision_app, "_serpapi_walmart_enabled", lambda: True)
    monkeypatch.setattr(precision_app, "search_ebay_cached", lambda *args, **kwargs: (_mock_phone_rows("eBay"), {"cache_status": "miss"}))

    monkeypatch.setattr(precision_app, "search_serpapi_cached", lambda *args, **kwargs: ([], {"cache_status": "miss", "serpapi_params": {}, "spelling_suggestion": None}))
    no_results = client.post("/search", data={"q": "iPhone 15", "search_scope": "both", "action": "search"}, follow_redirects=True)
    first = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    assert no_results.status_code == 200
    assert first["source_statuses"]["ebay"]["status"] == "success"
    assert first["source_statuses"]["walmart"]["status"] == "no_results"
    assert "No Walmart listings were returned for this search." in no_results.get_data(as_text=True)

    monkeypatch.setattr(precision_app, "search_serpapi_cached", lambda *args, **kwargs: (_ for _ in ()).throw(precision_app.SerpApiError("provider timeout")))
    failed = client.post("/search", data={"q": "iPhone 15", "search_scope": "both", "action": "search"}, follow_redirects=True)
    second = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    assert failed.status_code == 200
    assert second["source_statuses"]["walmart"]["status"] == "timeout"
    assert second["source_statuses"]["walmart"]["raw_count"] == 0
    assert "Walmart is temporarily unavailable. Results shown are limited to other available sources." in failed.get_data(as_text=True)
    assert first["_id"] != second["_id"]

    monkeypatch.setattr(precision_app, "search_serpapi_cached", lambda *args, **kwargs: (_mock_phone_rows("Walmart"), {"cache_status": "miss", "serpapi_params": {}, "spelling_suggestion": None}))
    recovered = client.post("/search", data={"q": "iPhone 15", "search_scope": "both", "action": "search"}, follow_redirects=True)
    third = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    assert recovered.status_code == 200
    assert third["source_statuses"]["walmart"]["status"] == "success"
    assert third["source_statuses"]["walmart"]["retained_count"] > 0
    assert "Walmart listings are included." not in recovered.get_data(as_text=True)


def test_provider_correction_is_user_controlled_and_corrected_rows_are_not_merged(monkeypatch):
    client, user = make_client("Provider Correction Owner")
    monkeypatch.setattr(precision_app, "_serpapi_walmart_enabled", lambda: True)
    monkeypatch.setattr(precision_app, "search_ebay_cached", lambda query, **kwargs: ([
        {"title": f"Two channel audio amplifier {index}", "platform": "eBay", "price": 90 + index, "condition": "New", "match_type": "exact_match"}
        for index in range(3)
    ] if query == "channel" else [
        {"title": f"Chanel perfume fragrance {index}", "platform": "eBay", "price": 100 + index, "condition": "New", "match_type": "exact_match"}
        for index in range(3)
    ], {"cache_status": "miss"}))
    def walmart(query, *args, **kwargs):
        if query == "channel":
            return ([{"title": f"Chanel perfume {index}", "platform": "Walmart", "price": 100 + index} for index in range(3)], {"cache_status": "miss", "serpapi_params": {}, "spelling_suggestion": "Chanel"})
        return ([], {"cache_status": "miss", "serpapi_params": {}, "spelling_suggestion": None})
    monkeypatch.setattr(precision_app, "search_serpapi_cached", walmart)

    original = client.post("/search", data={"q": "channel", "search_scope": "both", "action": "search"}, follow_redirects=True)
    original_record = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    original_body = original.get_data(as_text=True)
    assert original.status_code == 200
    assert original_record["keyword"] == original_record["effective_query"] == "channel"
    assert original_record["source_statuses"]["walmart"]["status"] == "query_correction_suggested"
    assert original_record["source_statuses"]["walmart"]["raw_count"] == 3
    assert original_record["source_statuses"]["walmart"]["retained_count"] == 0
    assert all(item.get("platform") != "Walmart" for item in precision_app.resolve_result_tokens(original_record["result_tokens"]))
    assert "Walmart suggested ‘Chanel’" in original_body
    assert "Also looking for the brand Chanel?" in original_body

    accepted = client.post("/search", data={"q": "Chanel", "search_scope": "both", "action": "search"}, follow_redirects=True)
    accepted_record = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    assert accepted.status_code == 200
    assert accepted_record["_id"] != original_record["_id"]
    assert accepted_record["keyword"] == "Chanel"


def test_running_search_run_does_not_render_final_results():
    client, user = make_client("Pending Search Owner")
    search_id = precision_app.repository.create_search(user["_id"], "iPhone 15", "both", role="consumer", data_mode="api")
    page = client.get(f"/search/results/{search_id}")
    body = page.get_data(as_text=True)
    assert page.status_code == 202
    assert "Collecting marketplace listings" in body
    assert "Lowest comparable price" not in body and "comparable listings in the collected result set" not in body


def test_broad_smartphone_auto_selects_category_and_excludes_accessories():
    client, user = make_client("Broad Smartphone Owner")
    search_id = precision_app.repository.create_search(user["_id"], "smartphone", "both", role="consumer", data_mode="api")
    source_rows = _mock_phone_rows("eBay") + [
        {"title": "Smartphone desk stand holder", "platform": "eBay", "price": 20, "condition": "New", "match_type": "exact_match"},
        {"title": "Smartphone protective case cover", "platform": "Walmart", "price": 15, "condition": "New", "match_type": "exact_match"},
    ]
    rows = precision_app.repository.save_external_results(search_id, user["_id"], source_rows)
    precision_app.repository.attach_result_tokens(search_id, [f"external:{row['_id']}" for row in rows])
    precision_app.repository.complete_search(search_id, len(rows))
    page = client.post(f"/search/results/{search_id}", follow_redirects=True)
    body = page.get_data(as_text=True)
    record = precision_app.repository.get_search(search_id, user["_id"])
    assert page.status_code == 200
    assert record["search_mode"] == "broad_single_category"
    assert record["selected_category_key"] == "smartphones"
    assert record["comparison_enabled"] is True
    assert "3 comparable listings in the collected result set" in body
    assert "Excluded results available for review (2)" in body
    assert "Accessory rather than the requested product" in body
    assert "Comparable full price" not in body
    assert {entry["product_role"] for entry in record["result_categories"].values()} == {"complete_product", "accessory"}


def test_gucci_bloom_perfume_enters_general_coherent_mode():
    client, user = make_client("Coherent Perfume Owner")
    search_id = precision_app.repository.create_search(user["_id"], "Gucci Bloom perfume", "both", role="consumer", data_mode="api")
    rows = precision_app.repository.save_external_results(search_id, user["_id"], [
        {"title": f"Gucci Bloom Eau de Parfum perfume spray {index}", "platform": "eBay" if index % 2 else "Walmart", "price": 100 + index * 5, "condition": "New", "match_type": "exact_match"}
        for index in range(3)
    ])
    precision_app.repository.attach_result_tokens(search_id, [f"external:{row['_id']}" for row in rows])
    precision_app.repository.complete_search(search_id, len(rows))
    page = client.post(f"/search/results/{search_id}", follow_redirects=True)
    record = precision_app.repository.get_search(search_id, user["_id"])
    assert page.status_code == 200
    assert record["search_mode"] == "general_coherent"
    assert record["selected_product_type"] == "fragrance"
    assert record["comparison_enabled"] is True
    assert "General comparison mode is active" in page.get_data(as_text=True)


def test_broad_laptop_query_auto_selects_laptops_mode():
    client, user = make_client("Broad Laptop Owner")
    search_id = precision_app.repository.create_search(user["_id"], "laptop", "ebay", role="consumer", data_mode="api")
    rows = precision_app.repository.save_external_results(search_id, user["_id"], [
        {"title": f"Dell XPS {13 + index} laptop Intel Core i7", "platform": "eBay", "price": 900 + index * 50, "condition": "New", "match_type": "exact_match"}
        for index in range(3)
    ])
    precision_app.repository.attach_result_tokens(search_id, [f"external:{row['_id']}" for row in rows])
    precision_app.repository.complete_search(search_id, len(rows))
    page = client.post(f"/search/results/{search_id}", follow_redirects=True)
    record = precision_app.repository.get_search(search_id, user["_id"])
    assert page.status_code == 200
    assert record["search_mode"] == "broad_single_category"
    assert record["selected_category_key"] == "laptops"
    assert record["comparison_enabled"] is True


def test_ai_discover_is_bound_to_the_current_search_run():
    client, user = make_client("AI Scope Owner")
    search_id, _tokens = seed_search(user)
    response = client.post("/api/ai-discover", json={"search_record_id": search_id, "query": "a different query"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["search_run_id"] == str(search_id)
    assert payload["query"] if "query" in payload else True
    assert payload["comparable_result_count"] == 2
    assert payload["generated_at"].endswith("SGT")


def test_condition_is_canonical_in_normalized_and_analytics_records():
    rows = precision_app.normalize_price_items([
        {"title": "Phone - Very Good", "platform": "eBay", "price": 500, "source_url": "https://example.com/phone"}
    ])
    assert rows[0]["condition_display"] == "Very good"
    assert rows[0]["condition_normalized"] == "Very good"
    assert rows[0]["condition_source"] == "title_inferred"
    payload = precision_app.build_analytics_payload(rows, query="Phone")
    assert payload["records"][0]["condition_display"] == rows[0]["condition_display"]


def test_final_templates_expose_one_monitor_action_and_no_technical_status():
    search_template = open("templates/source_search_roles.html", encoding="utf-8").read()
    watchlist_template = open("templates/watchlist.html", encoding="utf-8").read()
    assert '<th class="p-3">Condition</th>' in search_template
    assert '<th class="p-3">Price quality</th>' not in search_template
    assert '<th class="p-3">Product fit</th>' not in search_template
    assert "Refresh snapshot" not in watchlist_template
    assert "AI unavailable" not in watchlist_template
    for label in ("Collect first snapshot", "Collect now and validate", "Daily Refresh", "Generate forecast"):
        assert label in watchlist_template


def test_canonical_iphone_exact_matching_excludes_air_and_pro_max():
    rows = precision_app.normalize_price_items([
        {"title": "Apple iPhone 17 Pro 256GB Unlocked", "platform": "eBay", "price": 1099},
        {"title": "Apple iPhone 17 Air 256GB", "platform": "eBay", "price": 899},
        {"title": "Apple iPhone 17 Pro Max 256GB", "platform": "Walmart", "price": 1199},
    ])
    accepted, rejected = precision_app._filter_relevant_search_items_with_rejections(rows, "iPhone 17 Pro")
    assert [row["title"] for row in accepted] == ["Apple iPhone 17 Pro 256GB Unlocked"]
    reasons = {row["title"]: row["warning_flags"] for row in rejected}
    assert "edition_conflict_air" in reasons["Apple iPhone 17 Air 256GB"]
    assert "edition_conflict_pro_max" in reasons["Apple iPhone 17 Pro Max 256GB"]
    assert accepted[0]["parsed_model"] == {
        "brand": "Apple", "product_family": "iPhone", "generation": "17",
        "edition": "Pro", "storage": "256GB", "carrier": "Unlocked", "product_type": "device",
    }


def test_marketplaces_share_the_same_canonical_matcher():
    rows = precision_app.normalize_price_items([
        {"title": "Apple iPhone 17 Pro 512GB", "platform": platform, "price": 1000}
        for platform in ("eBay", "Walmart")
    ])
    accepted, rejected = precision_app._filter_relevant_search_items_with_rejections(rows, "iPhone 17 Pro")
    assert not rejected
    assert {row["platform"] for row in accepted} == {"eBay", "Walmart"}
    assert {row["match_type"] for row in accepted} == {"exact_match"}


def test_exact_matching_rejects_plan_and_contract_listings():
    rows = precision_app.normalize_price_items([
        {"title": "Apple iPhone 17 Pro monthly plan", "platform": "eBay", "price": 49},
        {"title": "Apple iPhone 17 Pro contract-only offer", "platform": "Walmart", "price": 99},
        {"title": "Apple iPhone 17 Pro 256GB Unlocked", "platform": "eBay", "price": 1099},
    ])
    accepted, rejected = precision_app._filter_relevant_search_items_with_rejections(rows, "iPhone 17 Pro")
    assert [row["title"] for row in accepted] == ["Apple iPhone 17 Pro 256GB Unlocked"]
    flags = {row["title"]: row["warning_flags"] for row in rejected}
    assert "product_type_plan" in flags["Apple iPhone 17 Pro monthly plan"]
    assert "product_type_plan" in flags["Apple iPhone 17 Pro contract-only offer"]


def test_search_run_prg_has_stable_url_and_restores_exact_state(monkeypatch):
    client, user = make_client("Stable Search Owner")
    monkeypatch.setattr(precision_app, "search_ebay_cached", lambda *args, **kwargs: ([
        {"title": "Apple iPhone 17 Pro 256GB", "platform": "eBay", "price": 1099, "source_url": "https://example.com/pro"},
        {"title": "Apple iPhone 17 Air 256GB", "platform": "eBay", "price": 899, "source_url": "https://example.com/air"},
    ], {"cache_status": "miss"}))
    response = client.post("/search", data={"q": "iPhone 17 Pro", "search_scope": "ebay", "action": "search"})
    assert response.status_code == 302
    assert "/search/results/" in response.location
    page = client.get(response.location)
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Apple iPhone 17 Pro 256GB" in body
    assert "Apple iPhone 17 Air 256GB" not in body
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    assert run["raw_query"] == "iPhone 17 Pro"
    assert run["canonical_query"] == "Apple iPhone 17 Pro"
    assert run["source_diagnostics"]["raw_ebay_count"] == 2
    assert run["source_diagnostics"]["exact_ebay_count"] == 1


def test_zero_exact_results_is_not_source_unavailable(monkeypatch):
    client, user = make_client("No Match Owner")
    monkeypatch.setattr(precision_app, "search_ebay_cached", lambda *args, **kwargs: ([
        {"title": "Apple iPhone 17 Air 256GB", "platform": "eBay", "price": 899}
    ], {"cache_status": "miss"}))
    response = client.post("/search", data={"q": "iPhone 17 Pro", "search_scope": "ebay"}, follow_redirects=True)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "No comparable listings were found for “iPhone 17 Pro”" in body
    assert "eBay source is currently unavailable" not in body
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    assert run["source_diagnostics"]["raw_ebay_count"] == 1
    assert run["source_diagnostics"]["exact_ebay_count"] == 0


def test_empty_multi_source_search_does_not_claim_ebay_results_are_shown(monkeypatch):
    client, user = make_client("Empty Multi Source Owner")
    monkeypatch.setattr(precision_app, "search_ebay_cached", lambda *args, **kwargs: ([
        {"title": "Apple iPhone 17 Air 256GB", "platform": "eBay", "price": 899}
    ], {"cache_status": "miss"}))
    monkeypatch.setattr(precision_app, "_serpapi_walmart_enabled", lambda: False)
    monkeypatch.setattr(precision_app, "_legacy_walmart_scraper_enabled", lambda: False)

    response = client.post(
        "/search",
        data={"q": "iPhone 17 Pro", "search_scope": "both", "action": "search"},
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "0 matched listings" in body
    assert "eBay results are shown" not in body
    assert "No comparable listings were found for “iPhone 17 Pro”" in body
    assert '<section class="mt-5 rounded-xl border border-slate-200 bg-white p-4 shadow-sm" data-facet-area>' not in body
    assert "Price comparison records" not in body

    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    precision_app.repository.update_search_run(run["_id"], {"notice": "eBay results are shown. Walmart source is currently unavailable."})
    restored = client.get(f"/search/results/{run.get('search_run_id') or run['_id']}").get_data(as_text=True)
    assert "eBay results are shown" not in restored
    assert "No comparable listings were found for “iPhone 17 Pro”" in restored


def test_source_api_failure_uses_neutral_empty_state(monkeypatch):
    client, _user = make_client("Failed Source Owner")
    def fail(*args, **kwargs):
        raise RuntimeError("network down")
    monkeypatch.setattr(precision_app, "search_ebay_cached", fail)
    response = client.post("/search", data={"q": "iPhone 17 Pro", "search_scope": "ebay"}, follow_redirects=True)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "No comparable listings were found for “iPhone 17 Pro”" in body
    assert "results are shown" not in body


def test_walmart_diagnostics_keep_raw_and_exact_counts_separate(monkeypatch):
    client, user = make_client("Walmart Count Owner")
    monkeypatch.setattr(precision_app, "_serpapi_walmart_enabled", lambda: True)
    monkeypatch.setattr(precision_app, "search_serpapi_cached", lambda *args, **kwargs: ([
        {"title": "Apple iPhone 17 Pro 256GB", "platform": "Walmart", "price": 1099},
        {"title": "Apple iPhone 17 Air 256GB", "platform": "Walmart", "price": 899},
        {"title": "iPhone 17 Pro protective case", "platform": "Walmart", "price": 29},
    ], {"cache_status": "miss", "serpapi_params": {"q": "iPhone 17 Pro"}}))
    response = client.post("/search", data={"q": "iPhone 17 Pro", "search_scope": "walmart"})
    assert response.status_code == 302
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    diagnostics = run["source_diagnostics"]
    assert diagnostics["raw_walmart_count"] == 3
    assert diagnostics["normalized_walmart_count"] == 3
    assert diagnostics["product_type_eligible_walmart_count"] == 2
    assert diagnostics["exact_walmart_count"] == 1
    assert diagnostics["rejected_count_by_reason"]["incompatible_model"] == 1
    assert diagnostics["rejected_count_by_reason"]["accessory"] == 1


def test_mixed_storage_summary_and_workspace_are_explicit():
    rows = precision_app.normalize_price_items([
        {"title": "Apple iPhone 17 Pro 256GB", "platform": "eBay", "price": 1099},
        {"title": "Apple iPhone 17 Pro 512GB", "platform": "Walmart", "price": 1299},
    ])
    summary = precision_app.calculate_summary(rows)
    assert summary["mixed_storage"] is True
    assert summary["storage_variants"] == ["256GB", "512GB"]
    assert summary["best_platform"] is None


def test_search_summary_uses_category_configuration_and_platform_median_not_single_lowest_listing():
    rows = precision_app.normalize_price_items([
        {"title": "Phone 128GB A", "platform": "eBay", "price": 100, "condition": "New", "attributes": {"storage": "128GB", "carrier": "Unlocked"}},
        {"title": "Phone 128GB B", "platform": "eBay", "price": 300, "condition": "New", "attributes": {"storage": "128GB", "carrier": "Unlocked"}},
        {"title": "Phone 128GB C", "platform": "Walmart", "price": 180, "condition": "New", "attributes": {"storage": "128GB", "carrier": "Unlocked"}},
        {"title": "Phone 128GB D", "platform": "Walmart", "price": 190, "condition": "New", "attributes": {"storage": "128GB", "carrier": "Unlocked"}},
    ])
    summary = precision_app.calculate_summary(rows, category_key="smartphones")
    assert summary["lowest_price"] == 100
    assert summary["best_platform"] == "Walmart"
    assert summary["best_platform_price"] == 185
    assert summary["platform_metric_label"] == "Lowest median-price platform"
    assert summary["platform_metric_detail"] == "Median USD 185.00 · 2 comparable listings"


def test_search_summary_disqualifies_mixed_laptop_configuration_and_condition():
    laptops = precision_app.normalize_price_items([
        {"title": "Laptop 16GB 512GB", "platform": "eBay", "price": 900, "condition": "New", "attributes": {"cpu": "M3", "ram": "16GB", "storage": "512GB", "screen_size": "14 inch"}},
        {"title": "Laptop 32GB 1TB", "platform": "Walmart", "price": 1100, "condition": "New", "attributes": {"cpu": "M3", "ram": "32GB", "storage": "1TB", "screen_size": "14 inch"}},
    ])
    summary = precision_app.calculate_summary(laptops, category_key="laptops")
    assert summary["mixed_configuration"] is True
    assert set(summary["differing_configuration_fields"]) == {"ram", "storage"}
    assert "Multiple hardware configurations" in summary["configuration_notice"]
    assert summary["best_platform"] is None
    assert summary["platform_qualification_reason"] == "To compare platforms fairly, choose one product configuration."

    phones = precision_app.normalize_price_items([
        {"title": "Phone 128GB", "platform": "eBay", "price": 500, "condition": "Used", "attributes": {"storage": "128GB"}},
        {"title": "Phone 128GB", "platform": "Walmart", "price": 600, "condition": "New", "attributes": {"storage": "128GB"}},
    ])
    condition_summary = precision_app.calculate_summary(phones, category_key="smartphones")
    assert condition_summary["mixed_condition"] is True
    assert condition_summary["best_platform"] is None
    assert "choose one product condition" in condition_summary["platform_qualification_reason"]


def test_search_template_has_compact_guidance_ai_icon_and_dynamic_configuration_copy():
    template = open("templates/source_search_roles.html", encoding="utf-8").read()
    assert "Example searches:" not in template
    assert 'id="ai-button"' in template and ">auto_awesome</span>" in template
    assert "Some technical filters are unavailable" not in template
    assert "Filters only include specifications available across enough listings." in template
    assert "summary.scope_refinement_notice" in template
    assert "Choose one storage capacity to compare platforms." not in template


def _seed_multi_storage_comparison(client, user):
    search_id = precision_app.repository.create_search(user["_id"], "iPhone 17 Pro", "both", role="consumer", data_mode="api")
    rows = precision_app.repository.save_external_results(search_id, user["_id"], [
        {"title": "Apple iPhone 17 Pro 256GB eBay", "platform": "eBay", "price": 900, "source_url": "https://example.com/256-ebay", "match_type": "exact_match"},
        {"title": "Apple iPhone 17 Pro 256GB Walmart", "platform": "Walmart", "price": 940, "source_url": "https://example.com/256-walmart", "match_type": "exact_match"},
        {"title": "Apple iPhone 17 Pro 512GB eBay", "platform": "eBay", "price": 1100, "source_url": "https://example.com/512-ebay", "match_type": "exact_match"},
        {"title": "Apple iPhone 17 Pro 512GB Walmart", "platform": "Walmart", "price": 1160, "source_url": "https://example.com/512-walmart", "match_type": "exact_match"},
    ])
    tokens = [f"external:{row['_id']}" for row in rows]
    precision_app.repository.attach_result_tokens(search_id, tokens)
    precision_app.repository.complete_search(search_id, len(rows))
    page, group = create_comparison(client, search_id, tokens)
    return search_id, tokens, page, group


def test_multi_storage_comparison_opens_group_overview_then_recalculates_selected_scope():
    client, user = make_client("Multi Storage Overview")
    _search_id, _tokens, overview, group = _seed_multi_storage_comparison(client, user)
    body = overview.get_data(as_text=True)
    assert overview.status_code == 200
    assert "Choose a storage capacity" in body
    assert body.count("Open comparison") == 2
    assert "All selected listings" in body
    assert "Comparison coverage" not in body
    assert "Market conclusion" not in body
    assert "Comparison actions" not in body
    assert "Compare separately by storage" not in body
    assert "Continue with mixed variants" not in body

    scoped = client.get(f"/compare/{group['_id']}", query_string={"storage": "256GB"})
    scoped_body = scoped.get_data(as_text=True)
    assert scoped.status_code == 200
    assert "256GB comparison" in scoped_body
    assert "Comparison coverage" in scoped_body and "Market conclusion" in scoped_body
    assert "USD 900.00" in scoped_body and "USD 940.00" in scoped_body
    assert "Apple iPhone 17 Pro 512GB eBay" not in scoped_body
    assert 'name="storage_scope" value="256GB"' in scoped_body


def test_multi_storage_analysis_and_monitor_are_server_scoped_to_selected_capacity():
    client, user = make_client("Multi Storage Actions")
    _search_id, _tokens, _overview, group = _seed_multi_storage_comparison(client, user)

    blocked = client.post(f"/analytics/from-comparison/{group['_id']}", follow_redirects=True)
    assert "Choose one storage group before analyzing" in blocked.get_data(as_text=True)
    assert precision_app.repository.list_analysis_records(user["_id"], limit=10) == []

    analysis = client.post(f"/analytics/from-comparison/{group['_id']}", data={"storage_scope": "512GB"})
    assert analysis.status_code == 302
    record = precision_app.repository.list_analysis_records(user["_id"], limit=1)[0]
    assert record["filters"] == {"storage": "512GB"}
    assert len(record["included_result_ids"]) == 2

    blocked_monitor = client.post("/watchlist/from-compare", data={"comparison_id": group["_id"]}, follow_redirects=True)
    assert "Choose one storage group before creating a Monitor" in blocked_monitor.get_data(as_text=True)
    tracked = client.post("/watchlist/from-compare", data={"comparison_id": group["_id"], "storage_scope": "256GB"})
    assert tracked.status_code == 302
    monitor = precision_app.repository.list_watchlist_items(user["_id"], limit=1)[0]
    assert monitor["storage_scope"] == "256GB"
    assert monitor["frozen_scope"]["storage"] == "256GB"
    assert len(monitor["selected_records_snapshot"]) == 2
    assert all("256GB" in row["title"] for row in monitor["selected_records_snapshot"])


def test_repeated_analysis_reuses_signature_without_duplicate_history():
    client, user = make_client("Idempotent Analysis Owner")
    search_id, tokens = seed_search(user)
    _page, group = create_comparison(client, search_id, tokens)
    first = client.post(f"/analytics/from-comparison/{group['_id']}")
    second = client.post(f"/analytics/from-comparison/{group['_id']}")
    assert first.status_code == second.status_code == 302
    analyses = precision_app.repository.list_analysis_records(user["_id"], limit=20)
    assert len(analyses) == 1
    assert first.location == second.location
    client.get(first.location)
    client.get(first.location)
    touched = precision_app.repository.get_analysis_record(analyses[0]["_id"], user["_id"])
    assert touched["view_count"] == 2
    assert touched["last_viewed_at"] is not None


def test_ai_insight_is_persisted_only_for_its_search_run(monkeypatch):
    client, user = make_client("Persisted Insight Owner")
    first_id, _tokens = seed_search(user)
    second_id = precision_app.repository.create_search(user["_id"], "iPhone 17 Pro Max", "ebay", role="consumer")
    monkeypatch.setattr(precision_app, "summarize_market", lambda query, items, **_kwargs: (f"Insight for {query}", "rule", None))
    response = client.post("/api/ai-discover", json={"search_record_id": first_id})
    assert response.status_code == 200
    insight = precision_app.repository.get_ai_insight_for_search(first_id, user["_id"])
    assert insight["search_run_id"] == str(first_id)
    assert insight["query"] == "iPhone 12"
    assert precision_app.repository.get_ai_insight_for_search(second_id, user["_id"]) is None


def test_ai_insight_browser_action_uses_prg(monkeypatch):
    client, user = make_client("Insight PRG Owner")
    search_id, _tokens = seed_search(user)
    monkeypatch.setattr(precision_app, "summarize_market", lambda query, items, **_kwargs: (f"Insight for {query}", "rule", None))
    initial_page = client.get(f"/search/results/{search_id}")
    initial_soup = BeautifulSoup(initial_page.data, "html.parser")
    assert "hidden" in (initial_soup.select_one("#ai-panel").get("class") or [])
    assert initial_soup.select_one("#ai-summary") is not None
    response = client.post(f"/search/results/{search_id}/ai-insight", data={"search_record_id": search_id})
    assert response.status_code == 302
    assert response.location.endswith(f"/search/results/{search_id}")
    page = client.get(response.location)
    assert page.status_code == 200
    assert "Insight for iPhone 12" in page.get_data(as_text=True)
    assert "hidden" not in (BeautifulSoup(page.data, "html.parser").select_one("#ai-panel").get("class") or [])


def test_search_run_id_is_public_route_identity_with_legacy_id_compatibility():
    client, user = make_client("Public Search Run Owner")
    search_id, _tokens = seed_search(user)
    record = precision_app.repository.get_search(search_id, user["_id"])
    public_id = record["search_run_id"]

    assert public_id != str(search_id)
    assert precision_app.repository.get_search(public_id, user["_id"])["_id"] == search_id
    assert client.get(f"/search/results/{public_id}").status_code == 200
    # Previously issued URLs used the database ID; they remain safe to open.
    assert client.get(f"/search/results/{search_id}").status_code == 200


def test_base_search_renders_only_meaningful_search_controls():
    client, _user = make_client("Empty Search State Owner")
    page = client.get("/search")
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Product keyword or model" in body and "Search Sources" in body
    assert "AI Comparison Insight" not in body
    assert "Search again to generate an insight for the updated query." not in body
    assert "dynamic-filters" not in body
    assert "Compare selected" not in body and "Save as evidence" not in body


def test_stale_ai_insight_is_replaced_with_updated_query_message():
    client, user = make_client("Stale Insight Owner")
    search_id, _tokens = seed_search(user)
    record = precision_app.repository.get_search(search_id, user["_id"])
    precision_app.repository.save_ai_insight(user["_id"], {
        "search_run_id": str(search_id),
        "query": "Another product",
        "summary": "Stale insight that must not be displayed",
        "result_ids": [],
    })

    page = client.get(f"/search/results/{record['search_run_id']}")
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Search again to generate an insight for the updated query." in body
    assert "Stale insight that must not be displayed" not in body


def test_analysis_signature_uses_normalized_filters_and_source_data_version():
    client, user = make_client("Analysis Signature Inputs Owner")
    search_id, tokens = seed_search(user)
    record = precision_app.repository.get_search(search_id, user["_id"])
    items = precision_app.resolve_result_tokens(tokens)

    with client:
        client.get(f"/search/results/{record['search_run_id']}")
        first = precision_app._analysis_record_payload(
            "all_valid_results", record, items, tokens,
            analysis_filters={"platform": "eBay", "minimum_price": 500},
        )
        reordered = precision_app._analysis_record_payload(
            "all_valid_results", record, items, tokens,
            analysis_filters={"minimum_price": 500, "platform": "eBay"},
        )
        changed_filters = precision_app._analysis_record_payload(
            "all_valid_results", record, items, tokens,
            analysis_filters={"platform": "Walmart", "minimum_price": 500},
        )
        changed_items = [dict(item, price=(item.get("price") or 0) + 25) for item in items]
        changed_source = precision_app._analysis_record_payload(
            "all_valid_results", record, changed_items, tokens,
            analysis_filters={"platform": "eBay", "minimum_price": 500},
        )

    assert first["filters"] == {"minimum_price": 500, "platform": "eBay"}
    assert first["analysis_signature"] == reordered["analysis_signature"]
    assert first["analysis_signature"] != changed_filters["analysis_signature"]
    assert first["source_data_version"] != changed_source["source_data_version"]
    assert first["analysis_signature"] != changed_source["analysis_signature"]


def test_changed_source_data_allows_a_new_comparison_analysis():
    client, user = make_client("Refresh Analysis Source Owner")
    search_id, tokens = seed_search(user)
    _page, group = create_comparison(client, search_id, tokens)
    client.post(f"/analytics/from-comparison/{group['_id']}")
    changed_records = [dict(item) for item in group["selected_records"]]
    changed_records[0]["price"] = float(changed_records[0]["price"]) + 25
    precision_app.repository.update_comparison_group(group["_id"], {"selected_records": changed_records})

    page = client.get(f"/compare/{group['_id']}")
    assert page.status_code == 200
    assert "Analyze comparison" in page.get_data(as_text=True)


def test_comparison_actions_precede_the_selected_product_table():
    client, user = make_client("Comparison Action Placement")
    search_id, tokens = seed_search(user)
    page, _group = create_comparison(client, search_id, tokens)
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "Back to search results" in body
    assert body.index('id="comparison-actions"') < body.index('id="comparison-table"')
    assert body.count("Comparison actions") == 1


def test_selected_monitor_has_one_scoped_export_menu():
    client, user = make_client("Scoped Monitor Export")
    monitor_id, _ = precision_app.repository.create_watchlist_item(user["_id"], {
        "keyword": "Phone", "product_label": "Phone", "tracking_mode": "search_scope", "source_label": "live",
    })
    page = client.get("/watchlist", query_string={"item_id": monitor_id})
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert body.count("Monitor report") == 1
    assert "Export watchlist report" not in body and "Export selected monitor report" not in body
    assert f"monitor_id={monitor_id}" in body


def test_export_menus_use_clean_labels_and_saved_evidence_hides_empty_actions():
    client, user = make_client("Clean Export Labels")
    monitor_id, _ = precision_app.repository.create_watchlist_item(user["_id"], {
        "keyword": "Phone", "product_label": "Phone", "tracking_mode": "search_scope", "source_label": "live",
    })
    watchlist = client.get("/watchlist", query_string={"item_id": monitor_id}).get_data(as_text=True)
    assert "Ã¢" not in watchlist and "â€”" not in watchlist
    for label in ("Export Monitor report", "Export Snapshot history CSV", "Export Forecast validation CSV", "Export Trend chart PNG"):
        assert label in watchlist
    saved = client.get("/saved").get_data(as_text=True)
    assert saved.count('<button type="button" class="app-export-summary"') == 1
    assert "Saved status" not in saved and "Return to search results" not in saved
    assert 'id="delete-selected-evidence" class="hidden' in saved
    assert 'data-saved-panel="evidence"' in saved and 'data-saved-panel="comparisons"' in saved
    assert "getElementById('evidence-search')" in saved and "getElementById('evidence-platform-filter')" in saved
    assert "getElementById('select-visible-evidence')" in saved and "getElementById('evidence-visible-count')" in saved
