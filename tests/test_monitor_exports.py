import csv
import io
from datetime import datetime, timedelta, timezone

import precision_app
from services.database import MongoRepository


def _client_and_monitor():
    precision_app.app.config.update(TESTING=True, SECRET_KEY="monitor-export-tests")
    precision_app.repository = MongoRepository(uri="", database_name="test_monitor_exports")
    client = precision_app.app.test_client()
    password = "OfflineTestPassword1!"
    user = precision_app.repository.create_user("Export User", "export@example.test", precision_app.generate_password_hash(password, method="pbkdf2:sha256:1000"), "consumer")
    client.post("/login", data={"email": user["email"], "password": password})
    precision_app.repository.update_user(user["_id"], {"membership_tier": "professional", "plan": "professional"})
    monitor_id, _ = precision_app.repository.create_watchlist_item(user["_id"], {
        "keyword": "selected monitor", "product_label": "Selected Monitor", "platform_scope": "Test Market", "source_label": "mock",
    })
    return client, user, precision_app.repository.get_watchlist_item(monitor_id, user["_id"])


def _rows(response):
    return list(csv.reader(io.StringIO(response.data.decode("utf-8"))))


def test_snapshot_csv_is_one_selected_monitor_history_table():
    client, user, monitor = _client_and_monitor()
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for index in range(10):
        precision_app.repository.save_price_snapshot(monitor["_id"], user["_id"], {
            "average_price": 100 + index, "lowest_price": 90 + index, "highest_price": 110 + index,
            "record_count": 3, "platform_scope": "Test Market", "source_label": "mock", "data_quality": "good",
            "collected_at": base + timedelta(days=index),
        })
    other_id, _ = precision_app.repository.create_watchlist_item(user["_id"], {"keyword": "other", "product_label": "Other"})
    precision_app.repository.save_price_snapshot(other_id, user["_id"], {"average_price": 999, "collected_at": base})

    rows = _rows(client.get("/watchlist/export/snapshots.csv", query_string={"monitor_id": monitor["_id"]}))
    assert len(rows) == 11
    assert rows[0][0] == "Monitor Name"
    assert rows[0][10] == "Forecast Context"
    assert all(row[0] == "Selected Monitor" for row in rows[1:])
    assert rows[1][3] == "2026-07-01 08:00:00 SGT"
    assert rows[-1][4:7] == ["109.00", "99.00", "119.00"]
    assert rows[1][10] == "Initial baseline · 1 snapshot"
    assert rows[2][10] == "Moving average · 2 snapshots"
    assert rows[3][10] == "Trend-ready · 3 snapshots"
    assert rows[-1][10] == "Trend-ready · 10 snapshots"


def test_forecast_context_matches_the_actual_baseline_methods():
    assert precision_app._forecast_context_presentation(1)["key"] == "persistence"
    assert precision_app._forecast_context_presentation(2)["key"] == "moving_average"
    assert precision_app._forecast_context_presentation(3)["key"] == "trend"
    assert precision_app._forecast_context_presentation(0)["key"] == "ineligible"


def test_watchlist_presents_forecast_context_instead_of_good_quality():
    client, user, monitor = _client_and_monitor()
    precision_app.repository.save_price_snapshot(
        monitor["_id"],
        user["_id"],
        {
            "average_price": 100,
            "lowest_price": 95,
            "highest_price": 105,
            "record_count": 2,
            "source_label": "mock",
            "data_quality": "good",
            "collected_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
        },
    )

    body = client.get(
        "/watchlist", query_string={"item_id": monitor["_id"]}
    ).get_data(as_text=True)

    assert "Forecast context" in body
    assert "Initial baseline · 1 snapshot" in body
    assert "support a linear trend" in body
    assert "Scroll horizontally to view more columns." not in body
    assert ">Quality<" not in body
    assert ">good<" not in body


def test_validation_csv_requires_distinct_later_snapshot_and_marks_fallback():
    client, user, monitor = _client_and_monitor()
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    baseline_id = precision_app.repository.save_price_snapshot(monitor["_id"], user["_id"], {"average_price": 100, "collected_at": base})
    validation_id = precision_app.repository.save_price_snapshot(monitor["_id"], user["_id"], {"average_price": 110, "collected_at": base + timedelta(days=2)})
    prediction_id = precision_app.repository.save_prediction(user["_id"], monitor["_id"], {
        "prediction_id": "valid-cycle", "monitor_id": monitor["monitor_id"], "status": "evaluated",
        "baseline_snapshot_id": baseline_id, "actual_snapshot_id": validation_id,
        "prediction_created_at": base + timedelta(days=1), "evaluated_at": base + timedelta(days=2),
        "baseline_predicted_average_price": 110, "actual_average_price": 110,
        "baseline_absolute_error": 0, "baseline_percentage_error": 0, "ai_predicted_average_price": None,
    })
    precision_app.repository.save_prediction(user["_id"], monitor["_id"], {
        "prediction_id": "legacy-cycle", "monitor_id": monitor["monitor_id"], "status": "evaluated",
        "baseline_snapshot_id": baseline_id, "actual_snapshot_id": baseline_id,
        "prediction_created_at": base - timedelta(days=1), "baseline_predicted_average_price": 100,
    })

    rows = _rows(client.get("/watchlist/export/validation.csv", query_string={"monitor_id": monitor["_id"]}))
    assert len(rows) == 2 and rows[0][0] == "Monitor Name"
    assert rows[1][2] == "valid-cycle"
    assert rows[1][3] != rows[1][4]
    assert rows[1][8] == "" and rows[1][16:18] == ["Fallback", "Yes"]
    assert rows[1][10] == "0.00"  # zero is retained only for this valid, distinct cycle
    assert str(prediction_id)


def test_monitor_report_embeds_both_printable_charts_with_empty_states():
    client, user, monitor = _client_and_monitor()
    report = client.get("/watchlist/report", query_string={"monitor_id": monitor["_id"]})
    body = report.data.decode("utf-8")
    assert report.status_code == 200
    assert "Average Price Trend chart" in body
    assert "Forecast Comparison chart" in body
    assert "Not available" in body


def test_new_monitor_persists_search_category_metadata():
    client, user, _monitor = _client_and_monitor()
    record = {
        "_id": "search-category",
        "keyword": "iPhone 15",
        "selected_category_key": "smartphones",
        "detected_category_key": "smartphones",
        "detected_category_display": "Smartphones",
        "selected_source": "all",
    }
    items = [{
        "title": "Apple iPhone 15",
        "platform": "eBay",
        "price": 399,
        "total_price": 399,
        "currency": "USD",
        "analytics_eligible": True,
        "category": "Marketplace",
    }]
    payload = precision_app._watchlist_item_payload_from_search(record, items)
    assert payload["category"] == "Smartphones"
    assert payload["category_key"] == "smartphones"
    assert payload["category_display"] == "Smartphones"
    assert payload["frozen_scope"]["category_display"] == "Smartphones"

    monitor_id, _ = precision_app.repository.create_watchlist_item(user["_id"], payload)
    stored = precision_app.repository.get_watchlist_item(monitor_id, user["_id"])
    assert stored["category_key"] == "smartphones"
    assert stored["category_display"] == "Smartphones"


def test_legacy_monitor_category_falls_back_to_linked_search_for_report_refresh_and_export(monkeypatch):
    client, user, _monitor = _client_and_monitor()
    search_id = precision_app.repository.create_search(
        user["_id"], "iPhone 15", "all", role="consumer", data_mode="live"
    )
    precision_app.repository.update_search_run(search_id, {
        "selected_category_key": "smartphones",
        "detected_category_key": "smartphones",
        "detected_category_display": "Smartphones",
    })
    monitor_id, _ = precision_app.repository.create_watchlist_item(user["_id"], {
        "keyword": "iPhone 15",
        "product_label": "iPhone 15",
        "platform_scope": "eBay",
        "category": "Marketplace",
        "search_record_id": search_id,
    })
    monitor = precision_app.repository.get_watchlist_item(monitor_id, user["_id"])
    precision_app.repository.save_price_snapshot(
        monitor_id,
        user["_id"],
        {
            "average_price": 399,
            "lowest_price": 399,
            "highest_price": 399,
            "record_count": 1,
            "collected_at": datetime.now(timezone.utc),
        },
    )

    report = client.get("/watchlist/report", query_string={"monitor_id": monitor_id})
    assert report.status_code == 200
    assert "<b>Category</b><p>Smartphones</p>" in report.get_data(as_text=True)

    snapshot_rows = _rows(client.get(
        "/watchlist/export/snapshots.csv", query_string={"monitor_id": monitor_id}
    ))
    assert snapshot_rows[0][-1] == "Category"
    assert snapshot_rows[1][-1] == "Smartphones"

    captured = {}

    def fake_ebay(query, *, category, limit, refresh_live):
        captured.update(query=query, category=category, limit=limit, refresh_live=refresh_live)
        return [], {"source_type": "mock"}

    monkeypatch.setattr(precision_app, "search_ebay_cached", fake_ebay)
    precision_app._retrieve_monitor_provider_records(monitor)
    assert captured["category"] == "Smartphones"
