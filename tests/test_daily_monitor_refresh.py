from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

from bs4 import BeautifulSoup

import precision_app
from services.database import MongoRepository
from tools import refresh_due_monitors


PASSWORD = "OfflineMonitorPassword1!"


def _setup(monkeypatch, name="Monitor Owner"):
    repository = MongoRepository(uri="", database_name="test_daily_monitor_refresh", allow_memory_fallback=True)
    monkeypatch.setattr(precision_app, "repository", repository)
    for key, value in {
        "TESTING": True,
        "SECRET_KEY": "daily-monitor-refresh-test-secret",
        "MONITOR_DAILY_REFRESH_ENABLED": True,
        "MONITOR_SCHEDULED_RUN_LIMIT": 5,
        "MONITOR_REFRESH_TIMEZONE": "Asia/Singapore",
        "MONITOR_DAILY_REFRESH_HOUR": 8,
        "MONITOR_TEST_PROVIDER_CALLS": True,
    }.items():
        monkeypatch.setitem(precision_app.app.config, key, value)
    user = repository.create_user(
        name,
        f"{name.lower().replace(' ', '.')}@example.test",
        precision_app.generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000"),
        "consumer",
        membership_tier="professional",
        plan="professional",
    )
    client = precision_app.app.test_client()
    client.post("/login", data={"email": user["email"], "password": PASSWORD})
    return client, repository, user


def _records(price=100.0, include_walmart=True):
    rows = [
        {"title": "Precision Phone 256GB", "platform": "eBay", "price": price, "shipping": 0, "condition": "New"},
        {"title": "Precision Phone 256GB", "platform": "eBay", "price": price + 10, "shipping": 0, "condition": "New"},
    ]
    if include_walmart:
        rows.append({"title": "Precision Phone 256GB", "platform": "Walmart", "price": price + 5, "shipping": 0, "condition": "New"})
    return rows


def _monitor(repository, user, label="Precision Phone", status="active"):
    monitor_id, _ = repository.create_watchlist_item(user["_id"], {
        "keyword": "Precision Phone 256GB",
        "product_label": label,
        "tracking_mode": "search_scope",
        "platform_scope": "eBay, Walmart",
        "category": "Smartphones",
        "status": status,
        "initial_records_snapshot": _records(),
        "frozen_scope": {"keyword": "Precision Phone 256GB", "category": "Smartphones", "platform_scope": "eBay, Walmart"},
    })
    return repository.get_watchlist_item(monitor_id, user["_id"])


def _provider_result(price=100.0, partial=False):
    statuses = {
        "ebay": {"status": "success", "record_count": 2, "source_type": "mock_ebay"},
        "walmart": {"status": "failed" if partial else "success", "record_count": 0 if partial else 1, "error_code": "walmart_unavailable" if partial else None, "source_type": "mock_walmart"},
    }
    return _records(price, include_walmart=not partial), statuses


def _enable(repository, monitor, now=None):
    now = now or datetime.now(timezone.utc)
    return repository.enable_watchlist_auto_refresh(monitor["_id"], monitor["user_id"], now - timedelta(minutes=1))


def test_existing_and_new_monitors_default_to_daily_refresh_off(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    new_monitor = _monitor(repository, user)
    assert new_monitor["auto_refresh_enabled"] is False
    assert new_monitor["last_refresh_status"] == "never"
    raw = repository._memory["watchlist_items"][0]
    for field in ("auto_refresh_enabled", "last_refresh_status", "next_refresh_at"):
        raw.pop(field, None)
    legacy = repository.get_watchlist_item(new_monitor["_id"], user["_id"])
    assert legacy["auto_refresh_enabled"] is False
    assert legacy["last_refresh_status"] == "never"


def test_one_automatic_monitor_per_owner_is_enforced_server_side(monkeypatch):
    client, repository, user = _setup(monkeypatch)
    first = _monitor(repository, user, "First Monitor")
    second = _monitor(repository, user, "Second Monitor")
    enabled = client.post(f"/watchlist/{first['_id']}/auto-refresh/enable")
    assert enabled.status_code == 302
    blocked = client.post(f"/watchlist/{second['_id']}/auto-refresh/enable", follow_redirects=True)
    assert "Pause the current automatic monitor before enabling another one." in blocked.get_data(as_text=True)
    assert "First Monitor" in blocked.get_data(as_text=True)
    assert repository.get_watchlist_item(first["_id"], user["_id"])["auto_refresh_enabled"] is True
    assert repository.get_watchlist_item(second["_id"], user["_id"])["auto_refresh_enabled"] is False

    other = repository.create_user("Other Owner", "other.owner@example.test", "unused", "consumer", membership_tier="professional")
    other_monitor = _monitor(repository, other, "Other Monitor")
    ok, current, reason = repository.enable_watchlist_auto_refresh(other_monitor["_id"], other["_id"], datetime.now(timezone.utc))
    assert ok is True and current is None and reason is None


def test_pause_archive_delete_and_restore_are_never_scheduled(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    now = datetime.now(timezone.utc)
    paused = _monitor(repository, user, "Paused")
    _enable(repository, paused, now)
    repository.pause_watchlist_auto_refresh(paused["_id"], user["_id"])
    assert precision_app.due_monitor_candidates(now=now, monitor_id=paused["_id"]) == []

    archived = _monitor(repository, user, "Archived")
    _enable(repository, archived, now)
    repository.archive_watchlist_item(archived["_id"], user["_id"])
    assert precision_app.due_monitor_candidates(now=now, monitor_id=archived["_id"]) == []
    repository.restore_watchlist_item(archived["_id"], user["_id"])
    restored = repository.get_watchlist_item(archived["_id"], user["_id"])
    assert restored["auto_refresh_enabled"] is False and restored["next_refresh_at"] is None

    deleted = _monitor(repository, user, "Deleted")
    repository.delete_watchlist_item(deleted["_id"], user["_id"])
    assert precision_app.due_monitor_candidates(now=now, monitor_id=deleted["_id"], force=True) == []


def test_due_success_creates_snapshot_and_updates_daily_metadata(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    now = datetime(2026, 7, 16, 1, 0, tzinfo=timezone.utc)
    _enable(repository, monitor, now)
    calls = []
    monkeypatch.setattr(precision_app, "_retrieve_monitor_provider_records", lambda item: (calls.append(item["_id"]) or _provider_result()))
    result = precision_app.refresh_monitor(monitor["_id"], user["_id"], trigger="scheduled", now=now)
    assert result["status"] == "success" and len(calls) == 1
    assert repository.count_price_snapshots(monitor["_id"]) == 1
    updated = repository.get_watchlist_item(monitor["_id"], user["_id"])
    assert updated["last_refresh_status"] == "success"
    assert updated["last_refresh_trigger"] == "scheduled"
    assert updated["last_scheduled_refresh_date"] == "2026-07-16"
    assert updated["next_refresh_at"] > now


def test_not_due_is_skipped_before_provider_calls(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    repository.enable_watchlist_auto_refresh(monitor["_id"], user["_id"], future)
    monkeypatch.setattr(precision_app, "_retrieve_monitor_provider_records", lambda _item: (_ for _ in ()).throw(AssertionError("provider called")))
    result = precision_app.refresh_monitor(monitor["_id"], user["_id"], trigger="scheduled")
    assert result == {"status": "skipped", "error_code": "not_due", "snapshot_id": None}
    assert repository.count_price_snapshots(monitor["_id"]) == 0


def test_same_sgt_day_duplicate_and_concurrent_worker_create_one_snapshot(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    now = datetime.now(timezone.utc)
    _enable(repository, monitor, now)
    barrier = Barrier(2)
    calls = []

    def provider(item):
        calls.append(item["_id"])
        return _provider_result()

    monkeypatch.setattr(precision_app, "_retrieve_monitor_provider_records", provider)
    results = []

    def worker():
        barrier.wait()
        results.append(precision_app.refresh_monitor(monitor["_id"], user["_id"], trigger="scheduled", force=True, scheduled_date="2026-07-16", now=now))

    threads = [Thread(target=worker), Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(result["status"] for result in results) == ["skipped", "success"]
    assert len(calls) == 1
    assert repository.count_price_snapshots(monitor["_id"]) == 1


def test_partial_refresh_creates_quality_snapshot(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    _enable(repository, monitor)
    monkeypatch.setattr(precision_app, "_retrieve_monitor_provider_records", lambda _item: _provider_result(partial=True))
    result = precision_app.refresh_monitor(monitor["_id"], user["_id"], trigger="scheduled", force=True, scheduled_date="2026-07-17")
    assert result["status"] == "partial"
    snapshot = repository.latest_price_snapshot(monitor["_id"])
    assert snapshot["data_quality"] == "partial"
    assert snapshot["source_statuses"]["walmart"]["status"] == "failed"


def test_complete_failure_creates_no_zero_snapshot_and_preserves_history(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    old_id = repository.save_price_snapshot(monitor["_id"], user["_id"], {"average_price": 99.0, "lowest_price": 90.0, "highest_price": 110.0, "record_count": 3, "collected_at": datetime(2026, 7, 1, tzinfo=timezone.utc)})
    _enable(repository, monitor)
    monkeypatch.setattr(precision_app, "_retrieve_monitor_provider_records", lambda _item: ([], {"ebay": {"status": "failed", "error_code": "ebay_unavailable"}, "walmart": {"status": "failed", "error_code": "walmart_unavailable"}}))
    result = precision_app.refresh_monitor(monitor["_id"], user["_id"], trigger="scheduled", force=True, scheduled_date="2026-07-18")
    assert result["status"] == "failed" and result["snapshot_id"] is None
    snapshots = repository.list_price_snapshots(monitor["_id"], limit=10)
    assert len(snapshots) == 1 and snapshots[0]["_id"] == old_id and snapshots[0]["average_price"] == 99.0


def test_manual_and_scheduled_paths_use_shared_refresh_service(monkeypatch):
    client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    calls = []
    monkeypatch.setattr(precision_app, "refresh_monitor", lambda monitor_id, owner_id=None, trigger="manual", **kwargs: (calls.append((monitor_id, owner_id, trigger)) or {"status": "success", "snapshot_id": "offline"}))
    response = client.post(f"/watchlist/{monitor['_id']}/refresh")
    assert response.status_code == 302 and calls == [(monitor["_id"], user["_id"], "manual")]

    monkeypatch.setattr(precision_app, "due_monitor_candidates", lambda **kwargs: [monitor])
    assert refresh_due_monitors.run(["--force", "--limit", "1", "--scheduled-date", "2026-07-19"]) == 0
    assert calls[-1] == (monitor["_id"], user["_id"], "scheduled")


def test_dry_run_and_cli_limit_and_monitor_selection_are_safe(monkeypatch, capsys):
    _client, repository, user = _setup(monkeypatch)
    first = _monitor(repository, user, "First")
    second = _monitor(repository, user, "Second")
    candidates = [first, second]
    monkeypatch.setattr(precision_app, "due_monitor_candidates", lambda monitor_id=None, force=False: [row for row in candidates if monitor_id is None or row["_id"] == monitor_id])
    monkeypatch.setattr(precision_app, "refresh_monitor", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry-run mutated")))
    assert refresh_due_monitors.run(["--dry-run", "--monitor-id", second["_id"]]) == 0
    output = capsys.readouterr().out
    assert second["_id"] in output and first["_id"] not in output and "no provider calls or database mutation" in output

    calls = []
    monkeypatch.setattr(precision_app, "refresh_monitor", lambda monitor_id, *_args, **_kwargs: (calls.append(monitor_id) or {"status": "success"}))
    assert refresh_due_monitors.run(["--force", "--limit", "1"]) == 0
    assert calls == [first["_id"]]


def test_later_snapshot_validates_pending_forecast_without_generating_ai(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    baseline_id = repository.save_price_snapshot(monitor["_id"], user["_id"], {"average_price": 100.0, "lowest_price": 90.0, "highest_price": 110.0, "record_count": 3, "collected_at": datetime(2026, 7, 1, tzinfo=timezone.utc)})
    prediction_id = repository.save_prediction(user["_id"], monitor["_id"], {
        "status": "pending_actual",
        "prediction_created_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
        "baseline_snapshot_id": baseline_id,
        "baseline_predicted_average_price": 105.0,
        "ai_predicted_average_price": None,
    })
    _enable(repository, monitor)
    monkeypatch.setattr(precision_app, "predict_price_gemini", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AI forecast generated")))
    monkeypatch.setattr(precision_app, "_retrieve_monitor_provider_records", lambda _item: _provider_result(price=110.0))
    result = precision_app.refresh_monitor(monitor["_id"], user["_id"], trigger="scheduled", force=True, scheduled_date="2026-07-20")
    assert result["status"] == "success"
    prediction = next(row for row in repository.list_predictions(user["_id"], monitor["_id"], limit=10) if row["_id"] == prediction_id)
    assert prediction["status"] == "evaluated" and prediction["actual_snapshot_id"] != baseline_id


def test_watchlist_ui_status_actions_loading_and_archived_controls(monkeypatch):
    client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    page = client.get("/watchlist", query_string={"item_id": monitor["_id"]})
    body = page.get_data(as_text=True)
    for label in ("Daily Refresh", "Last refreshed", "Next refresh", "Last status", "Collect latest snapshot", "Enable", "View snapshots", "Export"):
        assert label in body
    assert "No automatic monitor is active." in body and "Enable daily refresh on one Monitor." in body
    assert "data-monitor-action" in body and "form.dataset.submitting === 'true'" in body
    assert "max-sm:flex-col" in body

    client.post(f"/watchlist/{monitor['_id']}/auto-refresh/enable")
    client.post(f"/watchlist/{monitor['_id']}/auto-refresh/pause")
    paused = client.get("/watchlist", query_string={"item_id": monitor["_id"]}).get_data(as_text=True)
    assert "Resume" in paused

    repository.archive_watchlist_item(monitor["_id"], user["_id"])
    archived = client.get("/watchlist", query_string={"archived": "1", "item_id": monitor["_id"]}).get_data(as_text=True)
    assert "Restore to Active" in archived
    assert "Enable daily refresh" not in archived and "Resume daily refresh" not in archived


def test_enable_pause_require_authentication_ownership_and_csrf(monkeypatch):
    client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    client.post("/logout")
    assert client.post(f"/watchlist/{monitor['_id']}/auto-refresh/enable").status_code == 302
    client.post("/login", data={"email": user["email"], "password": PASSWORD})
    missing = client.post(f"/watchlist/{monitor['_id']}/auto-refresh/enable", auto_csrf=False)
    assert missing.status_code == 400
    page = client.get("/watchlist")
    token = BeautifulSoup(page.data, "html.parser").select_one('meta[name="csrf-token"]')["content"]
    valid = client.post(f"/watchlist/{monitor['_id']}/auto-refresh/enable", data={"csrf_token": token}, auto_csrf=False)
    assert valid.status_code == 302

    other = repository.create_user("Route Other", "route.other@example.test", "unused", "consumer", membership_tier="professional")
    other_monitor = _monitor(repository, other, "Owned Elsewhere")
    assert client.post(f"/watchlist/{other_monitor['_id']}/auto-refresh/enable").status_code == 404
