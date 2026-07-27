import csv
import io
from datetime import timedelta

from bs4 import BeautifulSoup

import precision_app
from services.database import MongoRepository, utcnow


PASSWORD = "OfflineForecastCycle1!"


def _setup(monkeypatch):
    repository = MongoRepository(uri="", database_name="forecast_traceability", allow_memory_fallback=True)
    monkeypatch.setattr(precision_app, "repository", repository)
    monkeypatch.setitem(precision_app.app.config, "TESTING", True)
    user = repository.create_user(
        "Forecast Owner", "forecast.owner@example.test",
        precision_app.generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000"),
        "consumer", membership_tier="professional", plan="professional",
    )
    client = precision_app.app.test_client()
    client.post("/login", data={"email": user["email"], "password": PASSWORD})
    monitor_id, _ = repository.create_watchlist_item(user["_id"], {
        "keyword": "Traceable Phone", "product_label": "Traceable Phone",
        "tracking_mode": "search_scope", "platform_scope": "eBay",
    })
    monitor = repository.get_watchlist_item(monitor_id, user["_id"])
    return client, repository, user, monitor


def _snapshot(repository, monitor, price, when):
    snapshot_id = repository.save_price_snapshot(monitor["_id"], monitor["user_id"], {
        "average_price": price, "lowest_price": price - 2, "highest_price": price + 2,
        "record_count": 3, "source_label": "mock", "data_quality": "good", "collected_at": when,
    })
    return next(row for row in repository.list_price_snapshots(monitor["_id"], limit=0) if row["_id"] == snapshot_id)


def test_cycles_are_distinct_immutable_and_latest_valid_snapshot_is_new_baseline(monkeypatch):
    client, repository, user, monitor = _setup(monkeypatch)
    values = iter((111.0, 123.0))
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *_args, **_kwargs: ({
        "predicted_average_price": next(values), "predicted_direction": "rising",
        "confidence_level": "medium", "reason": "offline",
    }, "mocked", None))
    base_time = utcnow() - timedelta(days=2)
    first_snapshot = _snapshot(repository, monitor, 100, base_time)
    assert client.post(f"/watchlist/{monitor['_id']}/prediction").status_code == 302
    first_cycle = repository.list_predictions(user["_id"], monitor["_id"], limit=10)[0]
    assert first_cycle["baseline_snapshot_id"] == first_snapshot["_id"]
    assert first_cycle["baseline_snapshot_at"] == first_snapshot["collected_at"]
    assert first_cycle["baseline_average_price"] == 100
    assert first_cycle["baseline_snapshot_observed_price"] == 100
    assert first_cycle["benchmark_method"] == "last_value"
    assert first_cycle["benchmark_predicted_price"] == 100
    assert first_cycle["forecast_id"] and first_cycle["owner_user_id"] == user["_id"]

    validation = _snapshot(repository, monitor, 108, utcnow() + timedelta(minutes=1))
    precision_app._evaluate_pending_monitor_forecast(monitor, validation)
    validated_before = repository.list_predictions(user["_id"], monitor["_id"], limit=10)[0]
    assert validated_before["cycle_status"] == "validated"
    assert validated_before["validation_snapshot_id"] == validation["_id"]
    validated_page = client.get("/watchlist", query_string={"item_id": monitor["_id"]}).get_data(as_text=True)
    assert "Generate next forecast" in validated_page

    latest = _snapshot(repository, monitor, 120, utcnow() + timedelta(minutes=2))
    client.get("/watchlist", query_string={"item_id": monitor["_id"], "forecast_id": first_cycle["forecast_id"]})
    client.post(f"/watchlist/{monitor['_id']}/prediction")
    cycles = repository.list_predictions(user["_id"], monitor["_id"], limit=10)
    assert len(cycles) == 2
    newest, preserved = cycles
    assert newest["baseline_snapshot_id"] == latest["_id"]
    assert newest["baseline_average_price"] == 120
    assert preserved["_id"] == validated_before["_id"]
    assert preserved["validation_snapshot_id"] == validated_before["validation_snapshot_id"]
    assert preserved["actual_average_price"] == 108


def test_benchmark_method_semantics_distinguish_persistence_from_other_models():
    assert precision_app._baseline_prediction([100]) == (100, "last_value")
    assert precision_app._baseline_prediction([100, 124]) == (112, "moving_average")
    trend_value, trend_method = precision_app._baseline_prediction([90, 95, 100])
    assert (trend_value, trend_method) == (110, "linear_trend")

    persistence = precision_app._forecast_cycle_view({
        "forecast_id": "persistence", "status": "pending_actual", "baseline_method": "last_value",
        "baseline_average_price": 100, "baseline_predicted_average_price": 100,
    }, [])
    assert persistence["benchmark_predicted_price"] == persistence["baseline_snapshot_observed_price"] == 100
    assert persistence["benchmark_method_label"] == "Persistence benchmark"
    assert "unchanged" in persistence["benchmark_explanation"]

    trend = precision_app._forecast_cycle_view({
        "forecast_id": "trend", "status": "pending_actual", "baseline_method": "linear_trend",
        "baseline_average_price": 100, "baseline_predicted_average_price": 112,
    }, [])
    assert trend["benchmark_predicted_price"] == 112 and trend["baseline_snapshot_observed_price"] == 100
    assert trend["benchmark_method_label"] == "Linear-trend benchmark"
    assert "Extrapolates" in trend["benchmark_explanation"]


def test_pending_cycle_cannot_be_replaced_by_repeated_generation(monkeypatch):
    client, repository, user, monitor = _setup(monkeypatch)
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *_args, **_kwargs: ({
        "predicted_average_price": 105, "predicted_direction": "stable", "confidence_level": "medium", "reason": "offline",
    }, "mocked", None))
    _snapshot(repository, monitor, 100, utcnow() - timedelta(hours=1))
    client.post(f"/watchlist/{monitor['_id']}/prediction")
    assert "Forecast pending" in client.get("/watchlist", query_string={"item_id": monitor["_id"]}).get_data(as_text=True)
    repeated = client.post(f"/watchlist/{monitor['_id']}/prediction", follow_redirects=True)
    assert len(repository.list_predictions(user["_id"], monitor["_id"], limit=10)) == 1
    assert "already waiting for validation" in repeated.get_data(as_text=True)


def test_selector_selected_cycle_summary_chart_timestamps_and_history(monkeypatch):
    client, repository, user, monitor = _setup(monkeypatch)
    base = utcnow() - timedelta(days=3)
    baseline_one = _snapshot(repository, monitor, 100, base)
    validation_one = _snapshot(repository, monitor, 104, base + timedelta(days=1))
    baseline_two = _snapshot(repository, monitor, 200, base + timedelta(days=2))
    validation_two = _snapshot(repository, monitor, 196, base + timedelta(days=3))
    first_id = repository.save_prediction(user["_id"], monitor["_id"], {
        "forecast_id": "cycle-one-forecast", "prediction_id": "cycle-one-forecast",
        "monitor_id": monitor["monitor_id"], "forecast_schema_version": 1,
        "status": "evaluated", "cycle_status": "validated", "prediction_created_at": base + timedelta(hours=1), "created_at": base + timedelta(hours=1),
        "baseline_snapshot_id": baseline_one["_id"], "baseline_snapshot_at": baseline_one["collected_at"],
        "baseline_average_price": 100, "baseline_predicted_average_price": 112,
        "baseline_method": "linear_trend", "ai_predicted_average_price": 105,
        "actual_average_price": 104, "observed_average_price": 104,
        "validation_snapshot_id": validation_one["_id"], "actual_snapshot_id": validation_one["_id"], "validation_snapshot_at": base + timedelta(days=1),
        "evaluated_at": base + timedelta(days=1, minutes=5),
        "baseline_absolute_error": 8, "baseline_percentage_error": 7.69,
        "ai_absolute_error": 1, "ai_percentage_error": 0.96,
    })
    repository.save_prediction(user["_id"], monitor["_id"], {
        "forecast_id": "cycle-two-forecast", "prediction_id": "cycle-two-forecast",
        "monitor_id": monitor["monitor_id"], "forecast_schema_version": 1,
        "status": "evaluated", "cycle_status": "validated", "prediction_created_at": base + timedelta(days=2, minutes=1), "created_at": base + timedelta(days=2, minutes=1),
        "baseline_snapshot_id": baseline_two["_id"], "baseline_snapshot_at": base + timedelta(days=2),
        "baseline_average_price": 200, "baseline_predicted_average_price": 190,
        "baseline_method": "moving_average", "ai_predicted_average_price": 196,
        "actual_average_price": 196, "observed_average_price": 196,
        "validation_snapshot_id": validation_two["_id"], "actual_snapshot_id": validation_two["_id"],
        "validation_snapshot_at": base + timedelta(days=3), "evaluated_at": base + timedelta(days=3, minutes=5),
        "baseline_absolute_error": 6, "baseline_percentage_error": 3.06,
        "ai_absolute_error": 0, "ai_percentage_error": 0,
    })
    selected = client.get("/watchlist", query_string={"item_id": monitor["_id"], "forecast_id": "cycle-one-forecast"})
    body = selected.get_data(as_text=True)
    soup = BeautifulSoup(body, "html.parser")
    options = soup.select('select[name="forecast_id"] option')
    assert len(options) == 2 and any("Validated" in option.text for option in options)
    assert soup.select_one('option[value="cycle-one-forecast"]').has_attr("selected")
    for label in ("Source Snapshot", "Predictions", "Validation", "Forecast evaluation", "Forecast History"):
        assert label in body
    summary = soup.select_one("[data-forecast-summary]").get_text(" ", strip=True)
    evaluation = soup.select_one("[data-forecast-evaluation]").get_text(" ", strip=True)
    chart_card = soup.select_one("[data-forecast-validation-chart]").get_text(" ", strip=True)
    history = soup.select_one("details[data-forecast-history]")
    assert history and not history.has_attr("open")
    assert all(value in summary for value in ("USD 100.00", "USD 112.00", "USD 105.00", "USD 104.00", "7.69%", "0.96%", "Validated"))
    selected_timestamps = [
        precision_app.format_sgt_datetime(base),
        precision_app.format_sgt_datetime(base + timedelta(hours=1)),
        precision_app.format_sgt_datetime(base + timedelta(days=1)),
    ]
    assert all(timestamp in summary for timestamp in selected_timestamps)
    assert all(value in evaluation for value in ("USD 112.00", "USD 105.00", "USD 104.00", "USD 8.00", "USD 1.00", "7.69%", "0.96%", "Linear-trend benchmark")), evaluation
    assert "Benchmark prediction, AI-assisted forecast and observed market average" in chart_card
    assert "cycle-one-forecast" not in summary
    assert "cycle-one-forecast" not in evaluation
    first_record = next(row for row in repository.list_predictions(user["_id"], monitor["_id"], limit=10) if row.get("prediction_id") == "cycle-one-forecast")
    selected_cycle = precision_app._forecast_cycle_view(first_record, repository.list_price_snapshots(monitor["_id"], limit=100))
    chart = precision_app._forecast_cycle_chart_data(selected_cycle)
    assert selected_cycle["baseline_snapshot_observed_price"] == 100
    assert selected_cycle["benchmark_predicted_price"] == 112
    assert selected_cycle["ai_predicted_price"] == 105
    assert selected_cycle["validation_observed_price"] == 104
    assert selected_cycle["benchmark_absolute_error"] == 8
    assert selected_cycle["benchmark_error_percent"] == 7.69
    assert selected_cycle["ai_absolute_error"] == 1
    assert selected_cycle["ai_error_percent"] == 0.96
    assert chart["labels"] == ["Benchmark prediction", "AI-assisted forecast", "Observed market average"]
    assert chart["values"] == [112.0, 105.0, 104.0]
    assert chart["values"][0] != selected_cycle["baseline_snapshot_observed_price"]

    report_link = soup.select_one('a[href*="/watchlist/report"]')["href"]
    csv_link = soup.select_one('a[href*="/watchlist/export/validation.csv"]')["href"]
    assert "forecast_id=cycle-one-forecast" in report_link and "forecast_id=cycle-one-forecast" in csv_link
    report = client.get(report_link)
    report_body = report.get_data(as_text=True)
    report_soup = BeautifulSoup(report.data, "html.parser")
    report_section = report_soup.find("h2", string="Forecast and validation").parent.get_text(" ", strip=True)
    assert "Selected Forecast Cycle" in report_section
    assert all(value in report_section for value in ("USD 112.00", "USD 105.00", "USD 104.00", "7.69%", "0.96%", "Linear-trend benchmark"))
    assert all(timestamp in report_section for timestamp in selected_timestamps)
    assert "USD 190.00" not in report_section and "USD 196.00" not in report_section
    export_rows = list(csv.reader(io.StringIO(client.get(csv_link).get_data(as_text=True))))
    assert len(export_rows) == 2
    assert export_rows[1][2] == "cycle-one-forecast"
    assert export_rows[1][7:14] == ["112.00", "105.00", "104.00", "8.00", "1.00", "7.69%", "0.96%"]
    assert export_rows[1][5] == precision_app.singapore_time_string(base + timedelta(hours=1))
    assert export_rows[1][20:22] == [precision_app.singapore_time_string(base), precision_app.singapore_time_string(base + timedelta(days=1))]

    latest = client.get("/watchlist", query_string={"item_id": monitor["_id"], "forecast_id": "cycle-two-forecast"})
    latest_soup = BeautifulSoup(latest.data, "html.parser")
    latest_summary = latest_soup.select_one("[data-forecast-summary]").get_text(" ", strip=True)
    latest_evaluation = latest_soup.select_one("[data-forecast-evaluation]").get_text(" ", strip=True)
    assert all(value in latest_summary for value in ("USD 200.00", "USD 190.00", "USD 196.00", "3.06%", "0.00%", "Validated"))
    assert all(value in latest_evaluation for value in ("USD 190.00", "USD 196.00", "3.06%", "0.00%", "Historical moving-average benchmark"))
    assert "USD 112.00" not in latest_summary and "0.96%" not in latest_evaluation
    latest_report = client.get("/watchlist/report", query_string={"monitor_id": monitor["_id"]})
    latest_section = BeautifulSoup(latest_report.data, "html.parser").find("h2", string="Forecast and validation").parent.get_text(" ", strip=True)
    assert "Latest Forecast Cycle" in latest_section and "USD 190.00" in latest_section and "USD 112.00" not in latest_section
    assert client.get("/watchlist/report", query_string={"monitor_id": monitor["_id"], "forecast_id": "not-owned"}).status_code == 404
    assert client.get("/watchlist/export/validation.csv", query_string={"monitor_id": monitor["_id"], "forecast_id": "not-owned"}).status_code == 404
    assert str(first_id)


def test_pending_chart_has_no_observed_value_and_legacy_fields_are_safe(monkeypatch):
    _client, repository, user, monitor = _setup(monkeypatch)
    baseline = _snapshot(repository, monitor, 90, utcnow() - timedelta(hours=2))
    pending = precision_app._forecast_cycle_view({
        "_id": "pending-cycle", "prediction_id": "pending-cycle", "user_id": user["_id"],
        "watchlist_id": monitor["_id"], "status": "pending_actual", "baseline_snapshot_id": baseline["_id"],
        "baseline_predicted_average_price": 95, "prediction_created_at": utcnow() - timedelta(hours=1),
    }, [baseline])
    chart = precision_app._forecast_cycle_chart_data(pending)
    assert chart["labels"] == ["Benchmark prediction", "AI-assisted forecast"]
    assert "Observed market average" not in chart["labels"]
    assert "Observation pending" in chart["status_text"]
    legacy = precision_app._forecast_cycle_view({"_id": "legacy", "user_id": user["_id"], "status": "evaluated"}, [])
    assert legacy["forecast_method"] == "Not available in this legacy forecast record"
    assert legacy["baseline_snapshot_at_display"] is None


def test_action_hierarchy_alert_compaction_responsive_and_security(monkeypatch):
    client, repository, user, monitor = _setup(monkeypatch)
    page = client.get("/watchlist", query_string={"item_id": monitor["_id"]})
    soup = BeautifulSoup(page.data, "html.parser")
    body = page.get_data(as_text=True)
    for label in ("Collect latest snapshot", "Generate forecast", "View snapshots", "Export Monitor report", "Export Snapshot history CSV", "Export Forecast validation CSV", "Export Trend chart PNG"):
        assert label in body
    export_menu = soup.select_one("[data-export-menu]")
    assert "Archive" not in export_menu.get_text() and "Delete" not in export_menu.get_text()
    action_row = soup.select_one(".watchlist-action-row")
    assert not action_row.select('form[action*="auto-refresh"]')
    assert soup.select_one('[aria-label="Daily Refresh status"] form[action*="auto-refresh"]')
    status_grid = soup.select_one("[data-monitor-status-grid]")
    assert status_grid and "lg:grid-cols-2" in status_grid.get("class", [])
    assert len(status_grid.select(":scope > section")) == 2
    alert_modal = soup.select_one("dialog[data-alert-modal]")
    assert alert_modal and not alert_modal.has_attr("open")
    assert soup.select_one('[data-alert-modal-open][aria-controls="price-alert-modal"]')
    email_input = alert_modal.select_one('input[type="email"][readonly]')
    assert email_input and email_input.get("value") == user["email"] and not email_input.get("name")
    assert alert_modal.select_one('input[name="alert_email_enabled"][role="switch"]')
    assert alert_modal.select_one("[data-alert-modal-close]")
    assert "Cancel" in alert_modal.get_text() and "Save alert" in alert_modal.get_text()
    assert "max-sm:flex-col" in str(action_row) and "max-sm:w-full" in str(action_row)
    assert client.post(f"/watchlist/{monitor['_id']}/prediction", auto_csrf=False).status_code == 400
    other = repository.create_user("Other", "other.forecast@example.test", "unused", "consumer", membership_tier="professional")
    other_monitor_id, _ = repository.create_watchlist_item(other["_id"], {"keyword": "Private", "product_label": "Private"})
    assert client.post(f"/watchlist/{other_monitor_id}/prediction").status_code == 404
