from bs4 import BeautifulSoup
import pytest
import requests

import precision_app
from services import serpapi_search
from services.database import MongoRepository


pytestmark = pytest.mark.usefixtures("walmart_offline_test_config")


def _make_client(username="Walmart Pipeline User"):
    precision_app.app.config.update(
        TESTING=True,
        SECRET_KEY="walmart-pipeline-tests",
        DEVELOPER_ROLE_PREVIEW=False,
        RUNTIME_ENVIRONMENT="production",
    )
    precision_app.repository = MongoRepository(uri="", database_name="walmart_pipeline_tests")
    email = f"{''.join(character.lower() for character in username if character.isalnum())}@example.test"
    password = "OfflinePassword1!"
    user = precision_app.repository.create_user(
        username,
        email,
        precision_app.generate_password_hash(password, method="pbkdf2:sha256:1000"),
        "researcher",
    )
    client = precision_app.app.test_client()
    client.post("/login", data={"email": email, "password": password})
    precision_app.repository.update_user(user["_id"], {"membership_tier": "professional", "plan": "professional"})
    return client, precision_app.repository.get_user_by_id(user["_id"])


def _walmart_payload(*, include_valid=True):
    rows = []
    if include_valid:
        rows.append({
            "us_item_id": "1001",
            "title": "Apple iPhone 15 128GB Unlocked",
            "primary_offer": {"offer_price": 729.00},
            "product_page_url": "https://www.walmart.com/ip/iphone-15/1001",
            "seller_name": "Walmart.com",
        })
    rows.extend([
        {
            "us_item_id": "1002",
            "title": "Apple iPhone 15 Protective Case",
            "primary_offer": {"offer_price": 19.00},
            "product_page_url": "https://www.walmart.com/ip/iphone-15-case/1002",
        },
        {
            "us_item_id": "1003",
            "title": "Apple iPhone 15 monthly installment plan",
            "primary_offer": {"offer_price": 29.99},
            "product_page_url": "https://www.walmart.com/ip/iphone-15-plan/1003",
        },
        {
            "us_item_id": "1004",
            "title": "Apple iPhone 15 Pro 256GB",
            "primary_offer": {"offer_price": 899.00},
            "product_page_url": "https://www.walmart.com/ip/iphone-15-pro/1004",
        },
        {
            "us_item_id": "1005",
            "title": "Apple iPhone 15 malformed price",
            "primary_offer": {"offer_price": "not available"},
            "product_page_url": "javascript:alert('unsafe')",
        },
    ])
    return {
        "search_metadata": {"status": "Success"},
        "organic_results": rows,
    }


def _iphone_14_pro_payload():
    return {
        "search_metadata": {"status": "Success"},
        "organic_results": [
            {
                "us_item_id": "1401",
                "title": "Apple iPhone 14 Pro 128GB Unlocked",
                "primary_offer": {"offer_price": 799.00},
                "product_page_url": "https://www.walmart.com/ip/iphone-14-pro-128/1401",
                "seller_name": "Walmart.com",
            },
            {
                "us_item_id": "1402",
                "title": "Apple iPhone 14 Pro 256GB - Very Good",
                "primary_offer": {"offer_price": 699.00},
                "product_page_url": "https://www.walmart.com/ip/iphone-14-pro-256/1402",
            },
            {
                "us_item_id": "1403",
                "title": "iPhone 14 Pro Protective Case",
                "primary_offer": {"offer_price": 19.00},
                "product_page_url": "https://www.walmart.com/ip/iphone-14-pro-case/1403",
            },
            {
                "us_item_id": "1404",
                "title": "Apple iPhone 14 Pro from $25 per month",
                "primary_offer": {"offer_price": 25.00},
                "product_page_url": "https://www.walmart.com/ip/iphone-14-pro-plan/1404",
            },
            {
                "us_item_id": "1405",
                "title": "Apple iPhone 14 Pro Max 256GB",
                "primary_offer": {"offer_price": 849.00},
                "product_page_url": "https://www.walmart.com/ip/iphone-14-pro-max/1405",
            },
            {
                "us_item_id": "1406",
                "title": "Apple iPhone 14 Pro malformed price",
                "primary_offer": {"offer_price": "not available"},
                "product_page_url": "https://www.walmart.com/ip/iphone-14-pro-invalid/1406",
            },
        ],
    }


def _iphone_11_payload(*, include_valid=True):
    rows = []
    if include_valid:
        rows.extend([
            {
                "us_item_id": "1101",
                "title": "Apple iPhone 11 128GB Unlocked",
                "primary_offer": {"offer_price": 399.00},
                "product_page_url": "https://www.walmart.com/ip/iphone-11-128/1101",
            },
            {
                "us_item_id": "1102",
                "title": "Refurbished Apple iPhone 11 64GB",
                "primary_offer": {"offer_price": 299.00},
                "product_page_url": "https://www.walmart.com/ip/iphone-11-64/1102",
            },
        ])
    rows.extend([
        {
            "us_item_id": "1103",
            "title": "iPhone 11 Protective Case",
            "primary_offer": {"offer_price": 19.00},
            "product_page_url": "https://www.walmart.com/ip/iphone-11-case/1103",
        },
        {
            "us_item_id": "1104",
            "title": "Apple iPhone 11 from $25 per month",
            "primary_offer": {"offer_price": 25.00},
            "product_page_url": "https://www.walmart.com/ip/iphone-11-plan/1104",
        },
        {
            "us_item_id": "1105",
            "title": "Apple iPhone 11 Pro 64GB",
            "primary_offer": {"offer_price": 449.00},
            "product_page_url": "https://www.walmart.com/ip/iphone-11-pro/1105",
        },
        {
            "us_item_id": "1106",
            "title": "Apple iPhone 11 malformed price",
            "primary_offer": {"offer_price": "not available"},
            "product_page_url": "https://www.walmart.com/ip/iphone-11-invalid/1106",
        },
    ])
    return {"search_metadata": {"status": "Success"}, "organic_results": rows}


def _zero_price_iphone_16_search_payload():
    return {
        "search_metadata": {"status": "Success"},
        "organic_results": [
            {
                "product_id": "IPHONE16-TMO",
                "title": "T-Mobile iPhone 16 128GB Ultramarine",
                "primary_offer": {"offer_price": 0, "min_price": 0},
                "product_page_url": "https://www.walmart.com/ip/iphone-16-tmo/1601",
            },
            {
                "product_id": "IPHONE16-USED",
                "title": "Pre-Owned Apple iPhone 16 - AT&T - 128GB Teal (Fair)",
                "primary_offer": {"offer_price": 0, "min_price": 0},
                "product_page_url": "https://www.walmart.com/ip/iphone-16-used/1602",
            },
            {
                "product_id": "IPHONE16-VERIZON",
                "title": "Verizon Apple iPhone 16 128GB Black",
                "primary_offer": {"offer_price": 0, "min_price": 0},
                "product_page_url": "https://www.walmart.com/ip/iphone-16-verizon/1605",
            },
            {
                "product_id": "IPHONE16-PRO",
                "title": "AT&T iPhone 16 Pro 128GB Black Titanium",
                "primary_offer": {"offer_price": 0, "min_price": 0},
                "product_page_url": "https://www.walmart.com/ip/iphone-16-pro/1603",
            },
            {
                "product_id": "IPHONE16-CASE",
                "title": "iPhone 16 Protective Case",
                "primary_offer": {"offer_price": 0, "min_price": 0},
                "product_page_url": "https://www.walmart.com/ip/iphone-16-case/1604",
            },
        ],
    }


def _google_shopping_walmart_payload():
    return {
        "search_parameters": {"engine": "google_shopping", "q": "iPhone 16 Walmart"},
        "shopping_results": [
            {
                "position": 1,
                "title": "Apple iPhone 16 128GB Unlocked",
                "source": "Walmart",
                "price": "$799.00",
                "extracted_price": 799.00,
                "product_link": "https://www.google.com/shopping/product/walmart-iphone-16",
            },
            {
                "position": 2,
                "title": "Pre-Owned Apple iPhone 16 128GB Teal",
                "source": "Walmart - Marketplace Seller",
                "price": "$599.00",
                "extracted_price": 599.00,
                "second_hand_condition": "used",
                "product_link": "https://www.google.com/shopping/product/walmart-iphone-16-used",
            },
            {
                "position": 3,
                "title": "Verizon Apple iPhone 16 128GB Black",
                "source": "Walmart",
                "price": "$779.00",
                "extracted_price": 779.00,
                "product_link": "https://www.google.com/shopping/product/walmart-iphone-16-verizon",
            },
            {
                "position": 4,
                "title": "AT&T iPhone 16 Pro 128GB Black Titanium",
                "source": "Walmart",
                "price": "$999.00",
                "extracted_price": 999.00,
                "product_link": "https://www.google.com/shopping/product/walmart-iphone-16-pro",
            },
            {
                "position": 5,
                "title": "iPhone 16 Protective Case",
                "source": "Walmart",
                "price": "$19.00",
                "extracted_price": 19.00,
                "product_link": "https://www.google.com/shopping/product/walmart-iphone-16-case",
            },
        ],
    }


class _MockSerpApiResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _install_offline_sources(monkeypatch, walmart_payload, *, ebay_title="Apple iPhone 15 128GB Unlocked"):
    monkeypatch.setattr(precision_app, "_serpapi_walmart_enabled", lambda: True)
    monkeypatch.setattr(precision_app, "search_ebay_cached", lambda *_args, **_kwargs: ([
        {
            "title": ebay_title,
            "platform": "eBay",
            "price": 749,
            "source_url": "https://www.ebay.com/itm/offline-fixture",
        }
    ], {"cache_status": "miss"}))
    monkeypatch.setattr(
        serpapi_search.requests,
        "get",
        lambda *_args, **_kwargs: _MockSerpApiResponse(walmart_payload),
    )


def _search(client):
    return client.post(
        "/search",
        data={"q": "iPhone 15", "search_scope": "both", "action": "search"},
        follow_redirects=True,
    )


def _search_iphone_14_pro(client):
    return client.post(
        "/search",
        data={"q": "iPhone 14 Pro", "search_scope": "both", "action": "search"},
        follow_redirects=True,
    )


def _search_iphone_11(client, **extra):
    return client.post(
        "/search",
        data={"q": "iPhone 11", "search_scope": "both", "action": "search", **extra},
        follow_redirects=True,
    )


def test_iphone_11_runtime_fixture_reaches_comparable_search_run_and_visible_ids(monkeypatch):
    client, user = _make_client("Walmart iPhone 11 Runtime")
    _install_offline_sources(
        monkeypatch,
        _iphone_11_payload(),
        ebay_title="Apple iPhone 11 128GB Unlocked",
    )

    response = _search_iphone_11(client)
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    diagnostics = run["source_diagnostics"]
    soup = BeautifulSoup(response.data, "html.parser")
    walmart_rows = soup.select('.result-row[data-platform="Walmart"]')
    walmart_ids = {row.get("data-token") for row in walmart_rows}
    displayed_platforms = [row.get("data-platform") for row in soup.select(".result-row")]

    assert response.status_code == 200
    assert diagnostics["raw_walmart_count"] == 6
    assert diagnostics["parsed_walmart_count"] == 6
    assert diagnostics["normalized_walmart_count"] == 5
    assert diagnostics["walmart_valid_price_count"] == 4
    assert diagnostics["exact_walmart_count"] == 2
    assert diagnostics["excluded_walmart_count"] == 4
    assert diagnostics["comparable_walmart_count"] == 2
    assert diagnostics["displayed_walmart_count"] == 2
    assert diagnostics["walmart_rejected_count_by_reason"] == {
        "invalid_price": 1,
        "accessory": 1,
        "installment_or_plan": 1,
        "incompatible_model": 1,
    }
    assert run["source_statuses"]["walmart"]["status"] == "success"
    assert len(walmart_rows) == displayed_platforms.count("Walmart") == 2
    assert displayed_platforms.count("eBay") == 1
    assert walmart_ids <= set(run["result_ids"])
    assert walmart_ids <= set(run["comparable_result_ids"])
    assert walmart_ids <= set(run["visible_result_ids"])
    assert "Walmart returned listings, but none met the current comparable-product criteria." not in response.get_data(as_text=True)


def test_new_search_run_does_not_reuse_stale_ebay_only_visible_ids(monkeypatch):
    client, user = _make_client("Walmart Fresh Run IDs")
    monkeypatch.setattr(precision_app, "_serpapi_walmart_enabled", lambda: True)
    monkeypatch.setattr(precision_app, "search_ebay_cached", lambda *_args, **_kwargs: ([
        {
            "title": "Apple iPhone 11 128GB Unlocked",
            "platform": "eBay",
            "price": 419,
            "source_url": "https://www.ebay.com/itm/iphone-11-fresh-run",
        }
    ], {"cache_status": "miss"}))
    payloads = iter((_iphone_11_payload(include_valid=False), _iphone_11_payload()))
    monkeypatch.setattr(
        serpapi_search.requests,
        "get",
        lambda *_args, **_kwargs: _MockSerpApiResponse(next(payloads)),
    )

    first_response = _search_iphone_11(client)
    first_run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    second_response = _search_iphone_11(client, refresh_live="1")
    second_run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    second_walmart_rows = BeautifulSoup(second_response.data, "html.parser").select('.result-row[data-platform="Walmart"]')
    second_walmart_ids = {row.get("data-token") for row in second_walmart_rows}

    assert "Walmart returned listings, but none met the current comparable-product criteria." in first_response.get_data(as_text=True)
    assert first_run["source_diagnostics"]["displayed_walmart_count"] == 0
    assert first_run["search_run_id"] != second_run["search_run_id"]
    assert set(first_run["visible_result_ids"]).isdisjoint(second_run["visible_result_ids"])
    assert second_run["source_diagnostics"]["search_run_mode"] == "new_collection"
    assert second_run["source_diagnostics"]["walmart_provider_response_mode"] == "new_collection"
    assert second_run["source_diagnostics"]["result_id_source"] == "newly_persisted"
    assert second_walmart_ids <= set(second_run["result_ids"])
    assert second_walmart_ids <= set(second_run["comparable_result_ids"])
    assert second_walmart_ids <= set(second_run["visible_result_ids"])
    assert len(second_walmart_rows) == 2


def test_provider_cache_persists_but_each_search_gets_new_run_and_result_ids(monkeypatch):
    client, user = _make_client("Walmart Cache New Search Run")
    monkeypatch.setattr(precision_app, "_serpapi_walmart_enabled", lambda: True)
    monkeypatch.setattr(precision_app, "search_ebay_cached", lambda *_args, **_kwargs: ([
        {
            "title": "Apple iPhone 11 128GB Unlocked",
            "platform": "eBay",
            "price": 419,
            "source_url": "https://www.ebay.com/itm/iphone-11-cache-run",
        }
    ], {"cache_status": "miss"}))
    provider_calls = []

    def fake_provider(*_args, **_kwargs):
        provider_calls.append(True)
        return _MockSerpApiResponse(_iphone_11_payload())

    monkeypatch.setattr(serpapi_search.requests, "get", fake_provider)

    _search_iphone_11(client)
    first_run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    second_response = _search_iphone_11(client)
    second_run = precision_app.repository.list_searches(user["_id"], limit=1)[0]

    assert len(provider_calls) == 1
    assert first_run["search_run_id"] != second_run["search_run_id"]
    assert set(first_run["result_ids"]).isdisjoint(second_run["result_ids"])
    assert second_run["source_diagnostics"]["search_run_mode"] == "new_collection"
    assert second_run["source_diagnostics"]["walmart_provider_response_mode"] == "cached_provider_response"
    assert second_run["source_diagnostics"]["result_id_source"] == "newly_persisted"
    runtime_summary = second_run["source_diagnostics"]["runtime_pipeline_summary"]
    assert runtime_summary["search_run_mode"] == "new_collection"
    assert runtime_summary["provider_response_mode"] == "cached_provider_response"
    assert len(BeautifulSoup(second_response.data, "html.parser").select('.result-row[data-platform="Walmart"]')) == 2


def test_priced_walmart_search_uses_one_provider_call_then_filters_locally(monkeypatch):
    client, user = _make_client("Walmart Single Search Call")
    monkeypatch.setattr(precision_app, "_serpapi_walmart_enabled", lambda: True)
    monkeypatch.setattr(precision_app, "search_ebay_cached", lambda *_args, **_kwargs: ([
        {
            "title": "Apple iPhone 16 128GB Unlocked",
            "platform": "eBay",
            "price": 829,
            "source_url": "https://www.ebay.com/itm/iphone-16-detail-test",
        }
    ], {"cache_status": "miss"}))
    provider_calls = []
    payload = _google_shopping_walmart_payload()

    def fake_provider(_url, *, params, timeout):
        provider_calls.append(dict(params))
        assert params.get("engine") == "google_shopping"
        assert params.get("q") == "iPhone 16 Walmart"
        assert "query" not in params
        assert "sort" not in params
        assert "store_id" not in params
        return _MockSerpApiResponse(payload)

    monkeypatch.setattr(serpapi_search.requests, "get", fake_provider)

    response = client.post(
        "/search",
        data={"q": "iPhone 16", "search_scope": "both", "action": "search"},
        follow_redirects=True,
    )
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    diagnostics = run["source_diagnostics"]
    walmart_rows = BeautifulSoup(response.data, "html.parser").select('.result-row[data-platform="Walmart"]')

    assert response.status_code == 200
    assert len(provider_calls) == 1
    assert diagnostics["raw_walmart_count"] == 5
    assert diagnostics["parsed_walmart_count"] == 5
    assert diagnostics["normalized_walmart_count"] == 5
    assert diagnostics["walmart_valid_price_count"] == 5
    assert diagnostics["exact_walmart_count"] == 3
    assert diagnostics["comparable_walmart_count"] == 3
    assert diagnostics["displayed_walmart_count"] == 3
    assert len(walmart_rows) == 3
    assert "Walmart returned listings, but none met the current comparable-product criteria." not in response.get_data(as_text=True)


def test_iphone_14_pro_fixture_retains_two_walmart_rows_and_binds_the_same_ids(monkeypatch):
    client, user = _make_client("Walmart iPhone 14 Pro")
    _install_offline_sources(
        monkeypatch,
        _iphone_14_pro_payload(),
        ebay_title="Apple iPhone 14 Pro 128GB Unlocked",
    )

    response = _search_iphone_14_pro(client)
    body = response.get_data(as_text=True)
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    diagnostics = run["source_diagnostics"]
    walmart_status = run["source_statuses"]["walmart"]
    soup = BeautifulSoup(response.data, "html.parser")
    walmart_rows = soup.select('.result-row[data-platform="Walmart"]')
    walmart_titles = [row.select_one("td:nth-of-type(3) p").get_text(" ", strip=True) for row in walmart_rows]
    walmart_ids = {row.get("data-token") for row in walmart_rows}
    all_displayed_platforms = [row.get("data-platform") for row in soup.select(".result-row")]

    parsed_fixture = serpapi_search.normalize_serpapi_records(
        _iphone_14_pro_payload(),
        platform="walmart",
        query="iPhone 14 Pro",
        diagnostics={},
    )
    normalized_fixture = precision_app.normalize_price_items(parsed_fixture)
    direct_comparable, _direct_rejected = precision_app._filter_relevant_search_items_with_rejections(
        normalized_fixture,
        "iPhone 14 Pro",
    )
    direct_titles = {row["title"] for row in direct_comparable if row.get("analytics_eligible")}

    assert response.status_code == 200
    assert diagnostics["raw_walmart_count"] == 6
    assert diagnostics["parsed_walmart_count"] == 6
    assert diagnostics["normalized_walmart_count"] == 5
    assert diagnostics["walmart_valid_price_count"] == 4
    assert diagnostics["excluded_walmart_count"] == 4
    assert diagnostics["comparable_walmart_count"] == 2
    assert diagnostics["displayed_walmart_count"] == 2
    assert diagnostics["walmart_rejected_count_by_reason"] == {
        "invalid_price": 1,
        "accessory": 1,
        "installment_or_plan": 1,
        "incompatible_model": 1,
    }
    assert walmart_status["status"] == "success"
    assert walmart_status["comparable_count"] == walmart_status["displayed_count"] == 2
    assert len(walmart_rows) == 2
    assert set(walmart_titles) == direct_titles == {
        "Apple iPhone 14 Pro 128GB Unlocked",
        "Apple iPhone 14 Pro 256GB - Very Good",
    }
    assert all_displayed_platforms.count("Walmart") == 2
    assert all_displayed_platforms.count("eBay") == 1
    assert "Walmart returned listings, but none met the current comparable-product criteria." not in body
    assert "Only eBay has live matched records" not in body
    assert diagnostics["walmart_pipeline_summary"] == {
        "provider_status": "success",
        "raw_result_count": 6,
        "parsed_result_count": 6,
        "normalized_result_count": 5,
        "valid_price_result_count": 4,
        "model_matched_result_count": 2,
        "excluded_result_count": 4,
        "excluded_count_by_reason": {
            "invalid_price": 1,
            "accessory": 1,
            "installment_or_plan": 1,
            "incompatible_model": 1,
        },
        "comparable_result_count": 2,
        "displayed_result_count": 2,
    }

    assert walmart_ids
    assert walmart_ids <= set(run["retained_result_ids"])
    assert walmart_ids <= set(run["filtered_result_ids"])
    assert walmart_ids <= set(run["visible_result_ids"])
    assert run["retained_result_ids"] == diagnostics["retained_result_ids"]
    assert run["filtered_result_ids"] == diagnostics["filtered_result_ids"]
    assert run["visible_result_ids"] == diagnostics["visible_result_ids"]


def test_iphone_14_pro_matcher_accepts_storage_carrier_and_condition_but_rejects_other_models():
    accepted_titles = [
        "Apple iPhone 14 Pro 128GB Unlocked",
        "Apple iPhone 14 Pro 256GB AT&T",
        "Refurbished Apple iPhone 14 Pro 512GB",
    ]
    rejected_titles = [
        "Apple iPhone 14 128GB",
        "Apple iPhone 14 Plus 128GB",
        "Apple iPhone 14 Pro Max 256GB",
        "Apple iPhone 15 Pro 128GB",
    ]
    rows = precision_app.normalize_price_items([
        {
            "title": title,
            "platform": "walmart_marketplace",
            "source_type": "serpapi_walmart",
            "price": 700 + index,
            "source_url": f"https://www.walmart.com/ip/offline/{index}",
        }
        for index, title in enumerate(accepted_titles + rejected_titles, start=1)
    ])

    accepted, rejected = precision_app._filter_relevant_search_items_with_rejections(rows, "iPhone 14 Pro")

    assert [row["title"] for row in accepted] == accepted_titles
    assert [row["title"] for row in rejected] == rejected_titles
    assert all(row["match_type"] == "exact_match" for row in accepted)
    assert all(row["match_type"] != "exact_match" for row in rejected)
    assert "edition_conflict_base" in rejected[0]["warning_flags"]
    assert "edition_conflict_plus" in rejected[1]["warning_flags"]
    assert "edition_conflict_pro_max" in rejected[2]["warning_flags"]
    assert "missing_model" in rejected[3]["warning_flags"]


def test_walmart_platform_aliases_share_one_canonical_identity():
    rows = precision_app.normalize_price_items([
        {
            "title": f"Apple iPhone 14 Pro 128GB alias {index}",
            "platform": alias,
            "price": 799,
        }
        for index, alias in enumerate(("Walmart", "walmart", "walmart_marketplace", "walmart.com"))
    ])
    source_only = precision_app.normalize_price_items([{
        "title": "Apple iPhone 14 Pro source-only alias",
        "platform": "",
        "source_type": "serpapi_walmart",
        "price": 799,
    }])

    assert [row["platform"] for row in rows] == ["Walmart"] * 4
    assert source_only[0]["platform"] == "Walmart"
    assert len(precision_app.apply_category_filters(rows, {}, platform="walmart")) == 4

    provider_alias = serpapi_search.normalize_serpapi_records(
        {"organic_results": [{
            "title": "Apple iPhone 14 Pro provider alias",
            "primary_offer": {"offer_price": 799},
            "product_page_url": "https://www.walmart.com/ip/provider-alias/1",
        }]},
        platform="walmart.com",
        query="iPhone 14 Pro",
    )
    assert provider_alias[0]["platform"] == "Walmart"


def test_walmart_aliases_are_canonical_before_status_counts_and_view_binding(monkeypatch):
    client, user = _make_client("Walmart Alias Binding")
    alias_rows = [
        {
            "title": "Apple iPhone 14 Pro 128GB Unlocked",
            "platform": "walmart_marketplace",
            "source_type": "serpapi_walmart",
            "price": 799,
            "source_url": "https://www.walmart.com/ip/alias/1",
        },
        {
            "title": "Apple iPhone 14 Pro 256GB AT&T",
            "platform": "walmart.com",
            "source_type": "walmart_api",
            "price": 829,
            "source_url": "https://www.walmart.com/ip/alias/2",
        },
    ]
    monkeypatch.setattr(precision_app, "_serpapi_walmart_enabled", lambda: True)
    monkeypatch.setattr(
        precision_app,
        "search_serpapi_cached",
        lambda *_args, **_kwargs: (
            alias_rows,
            {
                "cache_status": "hit",
                "parser_diagnostics": {
                    "record_field": "organic_results",
                    "raw_result_count": 2,
                    "parsed_result_count": 2,
                    "normalized_result_count": 2,
                    "excluded_result_count": 0,
                    "rejection_counts": {},
                },
            },
        ),
    )

    response = client.post(
        "/search",
        data={"q": "iPhone 14 Pro", "search_scope": "walmart", "action": "search"},
        follow_redirects=True,
    )
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    walmart_rows = BeautifulSoup(response.data, "html.parser").select('.result-row[data-platform="Walmart"]')

    assert response.status_code == 200
    assert run["source_statuses"]["walmart"]["status"] == "success"
    assert run["source_statuses"]["walmart"]["comparable_count"] == 2
    assert run["source_diagnostics"]["final_walmart_count"] == 2
    assert run["source_diagnostics"]["displayed_walmart_count"] == 2
    assert len(walmart_rows) == 2
    assert {row.get("data-token") for row in walmart_rows} <= set(run["visible_result_ids"])


def test_walmart_cache_hit_preserves_parser_counts_without_a_provider_call(monkeypatch):
    _client, _user = _make_client("Walmart Cached Parser Counts")
    query = "iPhone 14 Pro"
    params = precision_app._serpapi_cache_params(query, "walmart", limit=20)
    parser_diagnostics = {
        "record_field": "organic_results",
        "raw_result_count": 1,
        "parsed_result_count": 1,
        "normalized_result_count": 1,
        "excluded_result_count": 0,
        "rejection_counts": {},
    }
    precision_app.repository.save_search_cache(params["cache_key"], {
        "provider": "serpapi",
        "platform": "walmart",
        "normalized_query": precision_app.normalize_serpapi_query(query),
        "params": params,
        "normalized_records": [{
            "title": "Apple iPhone 14 Pro 128GB Unlocked",
            "platform": "walmart_marketplace",
            "price": 799,
            "source_url": "https://www.walmart.com/ip/cached/1",
        }],
        "live_source_type": "serpapi_walmart",
        "collected_at": precision_app.utcnow(),
        "expires_at": precision_app.utcnow() + precision_app.timedelta(hours=1),
        "parser_diagnostics": parser_diagnostics,
    })
    monkeypatch.setattr(
        precision_app,
        "call_serpapi_marketplace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache hit must not call provider")),
    )

    rows, metadata = precision_app.search_serpapi_cached(query, "walmart", limit=20)

    assert metadata["cache_status"] == "hit"
    assert metadata["parser_diagnostics"] == parser_diagnostics
    assert [row["platform"] for row in rows] == ["Walmart"]


def test_walmart_organic_results_flow_to_table_exclusions_kpis_and_audit(monkeypatch):
    client, user = _make_client()
    _install_offline_sources(monkeypatch, _walmart_payload())

    response = _search(client)
    body = response.get_data(as_text=True)
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    diagnostics = run["source_diagnostics"]
    status = run["source_statuses"]["walmart"]
    displayed_rows = BeautifulSoup(response.data, "html.parser").select(".result-row")
    displayed_platforms = [row.get("data-platform") for row in displayed_rows]

    assert response.status_code == 200
    assert "Apple iPhone 15 128GB Unlocked" in body
    assert "Walmart listings are included." not in body
    assert status["status"] == "success"
    assert status["raw_count"] == diagnostics["raw_walmart_count"] == 5
    assert status["parsed_count"] == diagnostics["parsed_walmart_count"] == 5
    assert status["normalized_count"] == diagnostics["normalized_walmart_count"] == 4
    assert status["excluded_count"] == diagnostics["excluded_walmart_count"] == 4
    assert status["comparable_count"] == diagnostics["comparable_walmart_count"] == 1
    assert status["displayed_count"] == diagnostics["displayed_walmart_count"] == 1
    assert diagnostics["walmart_response_field"] == "organic_results"
    assert diagnostics["walmart_rejected_count_by_reason"] == {
        "invalid_price": 1,
        "accessory": 1,
        "installment_or_plan": 1,
        "incompatible_model": 1,
    }
    assert displayed_platforms.count("Walmart") == 1
    assert displayed_platforms.count("eBay") == 1
    assert run["result_count"] == len(displayed_rows)

    assert "Accessory rather than the requested product" in body
    assert "Installment or service-plan offer" in body
    assert "Different product model or variant" in body
    assert "javascript:alert" not in body
    assert all(row["platform"] == "Walmart" for row in run["excluded_search_records"])

    audit = next(
        row for row in precision_app.build_audit_records(user["_id"])
        if row["audit_id"] == str(run.get("search_run_id") or run["_id"])
    )
    assert audit["walmart_status"] == "Success"
    for text in ("raw: 5", "parsed: 5", "normalized: 4", "excluded: 4", "comparable: 1", "displayed: 1"):
        assert text in audit["detail"]


def test_walmart_parser_uses_documented_organic_results_and_rejects_unsafe_shapes():
    diagnostics = {}
    payload = {
        "organic_results": [{
            "title": "Apple iPhone 15 128GB",
            "primary_offer": {"offer_price": 729},
            "product_page_url": "https://www.walmart.com/ip/iphone-15/1001",
        }],
        "shopping_results": [{
            "title": "Obsolete-field record must not be merged",
            "price": 1,
            "link": "https://example.test/obsolete",
        }],
    }
    records = serpapi_search.normalize_serpapi_records(
        payload,
        platform="walmart",
        query="iphone 15",
        diagnostics=diagnostics,
    )

    assert [row["title"] for row in records] == ["Apple iPhone 15 128GB"]
    assert records[0]["price"] == 729
    assert records[0]["source_url"].startswith("https://www.walmart.com/")
    assert diagnostics == {
        "record_field": "organic_results",
        "raw_result_count": 1,
        "parsed_result_count": 1,
        "normalized_result_count": 1,
        "excluded_result_count": 0,
        "rejection_counts": {},
    }

    rejected_diagnostics = {}
    rejected = serpapi_search.normalize_serpapi_records(
        {"organic_results": [
            "unsupported",
            {"title": "", "primary_offer": {"offer_price": 10}, "product_page_url": "https://www.walmart.com/ip/x/1"},
            {"title": "Missing price", "product_page_url": "https://www.walmart.com/ip/x/2"},
            {"title": "Bad price", "primary_offer": {"offer_price": "n/a"}, "product_page_url": "https://www.walmart.com/ip/x/3"},
            {"title": "Unsafe URL", "primary_offer": {"offer_price": 10}, "product_page_url": "javascript:alert(1)"},
        ]},
        platform="walmart",
        query="iphone 15",
        diagnostics=rejected_diagnostics,
    )
    assert rejected == []
    assert rejected_diagnostics["rejection_counts"] == {
        "unsupported_record_shape": 1,
        "missing_title": 1,
        "missing_price": 1,
        "invalid_price": 1,
        "missing_source_url": 1,
    }


def test_walmart_runtime_uses_one_google_shopping_call_without_store_or_provider_sort(monkeypatch):
    captured = {}

    def fake_get(_url, *, params, timeout):
        captured.update(params)
        return _MockSerpApiResponse(_iphone_11_payload())

    monkeypatch.setattr(serpapi_search.requests, "get", fake_get)
    result = serpapi_search.call_serpapi_marketplace(
        "iPhone 11",
        platform="walmart",
        sort="normalized_price_asc",
        limit=12,
    )
    cache_params = precision_app._serpapi_cache_params(
        "iPhone 11",
        "walmart",
        sort="normalized_price_asc",
        limit=12,
    )
    old_shape = dict(cache_params)
    old_shape["sort"] = "normalized_price_asc"
    old_shape.pop("cache_key", None)
    different_store_shape = dict(cache_params)
    different_store_shape["store_id"] = "1101"
    different_store_shape.pop("cache_key", None)

    assert captured["engine"] == "google_shopping"
    assert captured["q"] == "iPhone 11 Walmart"
    assert "query" not in captured
    assert "sort" not in captured
    assert "sort" not in result["request_params"]
    assert cache_params["sort"] == ""
    assert cache_params["store_id"] == ""
    assert cache_params["cache_key"] != serpapi_search.build_serpapi_cache_key(old_shape)
    assert cache_params["cache_key"] != serpapi_search.build_serpapi_cache_key(different_store_shape)


def test_google_shopping_walmart_parser_keeps_only_walmart_merchants_with_prices():
    payload = _google_shopping_walmart_payload()
    payload["shopping_results"].append({
        "position": 6,
        "title": "Apple iPhone 16 128GB from another retailer",
        "source": "Target",
        "price": "$749.00",
        "extracted_price": 749.00,
        "product_link": "https://www.google.com/shopping/product/target-iphone-16",
    })
    diagnostics = {}

    records = serpapi_search.normalize_serpapi_records(
        payload,
        platform="walmart",
        query="iPhone 16",
        engine="google_shopping",
        diagnostics=diagnostics,
    )

    assert len(records) == 5
    assert {row["platform"] for row in records} == {"Walmart"}
    assert {row["seller"] for row in records} == {"Walmart", "Walmart - Marketplace Seller"}
    assert all(row["price"] > 0 for row in records)
    assert records[0]["raw_price_text"] == "USD 799.00"
    assert diagnostics["record_field"] == "shopping_results"
    assert diagnostics["raw_result_count"] == 6
    assert diagnostics["rejection_counts"] == {"non_walmart_merchant": 1}


def test_google_shopping_installment_metadata_is_preserved_and_excluded_from_analytics():
    payload = {
        "shopping_results": [{
            "position": 1,
            "title": "Apple iPhone 16",
            "source": "Walmart",
            "price": "$17.48/mo",
            "extracted_price": 17.48,
            "installment": {"price": "$17.48/mo", "extracted_price": 17.48, "period": 36},
            "product_link": "https://www.google.com/shopping/product/walmart-iphone-16-plan",
        }],
    }

    parsed = serpapi_search.normalize_serpapi_records(
        payload, platform="walmart", query="iPhone 16", engine="google_shopping",
    )
    normalized = precision_app.normalize_price_items(parsed)

    assert parsed[0]["raw_price_text"] == "$17.48/mo"
    assert parsed[0]["price_type"] == "installment"
    assert normalized[0]["analytics_eligible"] is False
    assert normalized[0]["total_price"] is None


def test_plain_numeric_walmart_raw_price_is_formatted_with_currency():
    rows = precision_app.normalize_price_items([{
        "title": "Apple iPhone 16 128GB Unlocked",
        "platform": "Walmart",
        "price": 799,
        "raw_price_text": "799.0",
        "currency": "USD",
    }])

    assert rows[0]["raw_price_text"] == "USD 799.00"


def test_serpapi_connection_failure_does_not_expose_rendered_request_url(monkeypatch):
    monkeypatch.setattr(
        serpapi_search.requests,
        "get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            requests.exceptions.ProxyError(
                "failed https://serpapi.com/search.json?api_key=must-not-leak"
            )
        ),
    )

    try:
        serpapi_search.call_serpapi_marketplace(
            "iPhone 16",
            platform="walmart",
        )
    except serpapi_search.SerpApiError as exc:
        assert str(exc) == "provider_connection_error"
        assert "api_key" not in str(exc)
        assert "must-not-leak" not in str(exc)
    else:
        raise AssertionError("Expected a safe SerpApiError")


def test_missing_serpapi_credential_is_normalized_without_calling_provider(monkeypatch):
    client, user = _make_client("Walmart Missing Credential")
    provider_calls = []
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setattr(
        precision_app,
        "search_ebay_cached",
        lambda *_args, **_kwargs: ([], {"cache_status": "miss"}),
    )
    monkeypatch.setattr(
        serpapi_search.requests,
        "get",
        lambda *_args, **_kwargs: provider_calls.append(True),
    )

    _search(client)
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    status = run["source_statuses"]["walmart"]

    assert provider_calls == []
    assert status["status"] == status["status_code"] == "authentication_error"
    assert status["error_code"] == "authentication_error"
    assert status["safe_message"] == "Marketplace provider credentials are not configured."
    assert "SERPAPI_API_KEY is not configured" not in str(status)


def test_provider_connection_failure_is_distinct_from_missing_credentials(monkeypatch):
    client, user = _make_client("Walmart Connection Failure")
    provider_calls = []
    monkeypatch.setattr(precision_app, "_serpapi_walmart_enabled", lambda: True)
    monkeypatch.setattr(
        precision_app,
        "search_ebay_cached",
        lambda *_args, **_kwargs: ([], {"cache_status": "miss"}),
    )

    def fail_provider(*_args, **_kwargs):
        provider_calls.append(True)
        raise requests.exceptions.ConnectionError("offline provider failure")

    monkeypatch.setattr(serpapi_search.requests, "get", fail_provider)

    _search(client)
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    status = run["source_statuses"]["walmart"]

    assert provider_calls == [True]
    assert status["status"] == status["status_code"] == "provider_connection_error"
    assert status["error_code"] == "provider_connection_error"
    assert status["safe_message"] == "Marketplace provider connection failed."


def test_zero_walmart_offer_price_is_invalid_not_missing():
    diagnostics = {}

    records = serpapi_search.normalize_serpapi_records(
        {"organic_results": [{
            "title": "Apple iPhone 11 128GB Unlocked",
            "primary_offer": {"offer_price": 0, "min_price": 0},
            "product_page_url": "https://www.walmart.com/ip/zero-price/1",
        }]},
        platform="walmart",
        query="iPhone 11",
        diagnostics=diagnostics,
    )

    assert records == []
    assert diagnostics["rejection_counts"] == {"invalid_price": 1}


def test_walmart_success_with_zero_raw_results_is_not_unavailable(monkeypatch):
    client, user = _make_client("Walmart No Raw")
    _install_offline_sources(monkeypatch, {"search_metadata": {"status": "Success"}, "organic_results": []})

    response = _search(client)
    body = response.get_data(as_text=True)
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]

    assert run["source_statuses"]["walmart"]["status"] == "no_results"
    assert "No Walmart listings were returned for this search." in body
    assert "Walmart is temporarily unavailable" not in body
    assert body.count("No Walmart listings were returned for this search.") == 1


def test_walmart_success_with_only_rejected_records_is_not_unavailable(monkeypatch):
    client, user = _make_client("Walmart No Comparable")
    _install_offline_sources(monkeypatch, _walmart_payload(include_valid=False))

    response = _search(client)
    body = response.get_data(as_text=True)
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    walmart = run["source_statuses"]["walmart"]

    assert walmart["status"] == "success_no_comparable_results"
    assert walmart["raw_count"] == 4
    assert walmart["normalized_count"] == 3
    assert walmart["excluded_count"] == 4
    assert walmart["comparable_count"] == walmart["displayed_count"] == 0
    assert "Walmart returned listings, but none met the current comparable-product criteria." in body
    assert "Walmart is temporarily unavailable" not in body
    assert body.count("Walmart returned listings, but none met the current comparable-product criteria.") == 1


def test_walmart_timeout_and_authentication_failure_have_one_safe_message(monkeypatch):
    for username, error, expected_status in (
        ("Walmart Timeout", "provider timeout", "timeout"),
        ("Walmart Authentication", "Invalid API key", "authentication_error"),
    ):
        client, user = _make_client(username)
        monkeypatch.setattr(precision_app, "_serpapi_walmart_enabled", lambda: True)
        monkeypatch.setattr(precision_app, "search_ebay_cached", lambda *_args, **_kwargs: ([
            {"title": "Apple iPhone 15 128GB", "platform": "eBay", "price": 749}
        ], {"cache_status": "miss"}))
        monkeypatch.setattr(
            precision_app,
            "search_serpapi_cached",
            lambda *_args, _error=error, **_kwargs: (_ for _ in ()).throw(precision_app.SerpApiError(_error)),
        )

        response = _search(client)
        body = response.get_data(as_text=True)
        run = precision_app.repository.list_searches(user["_id"], limit=1)[0]

        assert run["source_statuses"]["walmart"]["status"] == expected_status
        message = "Walmart is temporarily unavailable. Results shown are limited to other available sources."
        assert body.count(message) == 1
        assert "No Walmart listings were returned" not in body
        assert "none met the current comparable-product criteria" not in body
        assert "Invalid API key" not in body


def test_ebay_only_search_behavior_and_walmart_not_requested_remain_unchanged(monkeypatch):
    client, user = _make_client("eBay Unchanged")
    monkeypatch.setattr(precision_app, "search_ebay_cached", lambda *_args, **_kwargs: ([
        {
            "title": "Apple iPhone 15 128GB Unlocked",
            "platform": "eBay",
            "price": 749,
            "source_url": "https://www.ebay.com/itm/iphone-15",
        }
    ], {"cache_status": "miss"}))
    monkeypatch.setattr(
        precision_app,
        "search_serpapi_cached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Walmart must not be called")),
    )

    response = client.post(
        "/search",
        data={"q": "iPhone 15", "search_scope": "ebay", "action": "search"},
        follow_redirects=True,
    )
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]

    assert response.status_code == 200
    assert "Apple iPhone 15 128GB Unlocked" in response.get_data(as_text=True)
    assert run["source_statuses"]["ebay"]["status"] == "success"
    assert run["source_statuses"]["walmart"]["status"] == "not_requested"
