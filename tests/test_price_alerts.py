from datetime import datetime, timedelta, timezone
from threading import Barrier, Thread

from bs4 import BeautifulSoup

import precision_app
from services.database import MongoRepository
from services.email_service import EmailService
from services.price_alerts import evaluate_price_alert


PASSWORD = "OfflineAlertPassword1!"


class CapturingMail:
    sent = []
    fail = False

    def send_price_alert(self, *args):
        if self.fail:
            raise TimeoutError("provider detail must stay private")
        self.sent.append(args)
        return "mocked"


def _setup(monkeypatch, tier="premium"):
    repository = MongoRepository(uri="", database_name="test_price_alerts", allow_memory_fallback=True)
    monkeypatch.setattr(precision_app, "repository", repository)
    monkeypatch.setitem(precision_app.app.config, "TESTING", True)
    monkeypatch.setitem(precision_app.app.config, "PRICE_ALERTS_ENABLED", True)
    monkeypatch.setitem(precision_app.app.config, "MONITOR_TEST_PROVIDER_CALLS", False)
    user = repository.create_user("Alert Owner", "alert.owner@example.test", precision_app.generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000"), "consumer", membership_tier=tier, plan=tier)
    client = precision_app.app.test_client()
    client.post("/login", data={"email": user["email"], "password": PASSWORD})
    CapturingMail.sent = []
    CapturingMail.fail = False
    return client, repository, user


def _monitor(repository, user, label="Alert Monitor"):
    monitor_id, _ = repository.create_watchlist_item(user["_id"], {
        "keyword": label, "product_label": label, "tracking_mode": "search_scope",
        "platform_scope": "eBay", "initial_records_snapshot": [
            {"title": label, "platform": "eBay", "price": 100, "shipping": 0, "condition": "New"},
        ],
    })
    return repository.get_watchlist_item(monitor_id, user["_id"])


def _configure(repository, monitor, direction="drop", threshold=5, **extra):
    repository.update_watchlist_item(monitor["_id"], {
        "alert_enabled": True, "alert_direction": direction,
        "alert_threshold_percent": threshold, "alert_email_enabled": True,
        "alert_cooldown_hours": 24, "alert_rule_version": 1, **extra,
    }, user_id=monitor["user_id"])
    return repository.get_watchlist_item(monitor["_id"], monitor["user_id"])


def _snapshot(repository, monitor, price, when=None, **extra):
    sid = repository.save_price_snapshot(monitor["_id"], monitor["user_id"], {
        "average_price": price, "lowest_price": price, "highest_price": price,
        "record_count": 3, "data_quality": "good", "collected_at": when or datetime.now(timezone.utc), **extra,
    })
    return next(row for row in repository.list_price_snapshots(monitor["_id"], limit=0) if row["_id"] == sid)


def _evaluate(repository, monitor, snapshot, now=None):
    return evaluate_price_alert(repository, monitor, snapshot, mail_service_factory=CapturingMail,
                                monitor_url="https://app.example.test/watchlist", now=now)


def test_monitor_defaults_and_valid_settings_route(monkeypatch):
    client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    assert monitor["alert_enabled"] is False and monitor["alert_last_evaluated_at"] is None
    raw = repository._memory["watchlist_items"][0]
    for key in ("alert_enabled", "alert_direction", "alert_cooldown_hours"):
        raw.pop(key, None)
    legacy = repository.get_watchlist_item(monitor["_id"], user["_id"])
    assert legacy["alert_enabled"] is False and legacy["alert_direction"] == "drop"
    response = client.post(f"/watchlist/{monitor['_id']}/price-alert/settings", data={
        "alert_threshold_percent": "7.5", "alert_direction": "either", "alert_email_enabled": "on",
    })
    assert response.status_code == 302
    saved = repository.get_watchlist_item(monitor["_id"], user["_id"])
    assert saved["alert_threshold_percent"] == 7.5 and saved["alert_direction"] == "either"


def test_settings_validation_ownership_archived_membership_and_csrf(monkeypatch):
    client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    for threshold, direction in (("0.99", "drop"), ("50.01", "drop"), ("5", "sideways")):
        client.post(f"/watchlist/{monitor['_id']}/price-alert/settings", data={"alert_threshold_percent": threshold, "alert_direction": direction})
        assert repository.get_watchlist_item(monitor["_id"], user["_id"])["alert_threshold_percent"] is None
    assert client.post(f"/watchlist/{monitor['_id']}/price-alert/enable", auto_csrf=False).status_code == 400
    other = repository.create_user("Other", "other.alert@example.test", "unused", "consumer", membership_tier="premium")
    other_monitor = _monitor(repository, other, "Private")
    assert client.post(f"/watchlist/{other_monitor['_id']}/price-alert/enable").status_code == 404
    repository.archive_watchlist_item(monitor["_id"], user["_id"])
    client.post(f"/watchlist/{monitor['_id']}/price-alert/enable")
    assert repository.get_watchlist_item(monitor["_id"], user["_id"])["alert_enabled"] is False
    basic_client, _basic_repo, basic_user = _setup(monkeypatch, tier="basic")
    basic_monitor = _monitor(_basic_repo, basic_user)
    assert basic_client.post(f"/watchlist/{basic_monitor['_id']}/price-alert/enable").status_code == 302
    assert _basic_repo.get_watchlist_item(basic_monitor["_id"], basic_user["_id"])["alert_enabled"] is False


def test_first_snapshot_and_invalid_prices_do_not_trigger(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    monitor = _configure(repository, _monitor(repository, user))
    first = _snapshot(repository, monitor, 100)
    assert _evaluate(repository, monitor, first)["status"] == "not_required"
    assert repository.list_price_alert_events(user["_id"], monitor["monitor_id"]) == []
    invalid = _snapshot(repository, monitor, 0, when=datetime.now(timezone.utc) + timedelta(minutes=1))
    assert _evaluate(repository, monitor, invalid)["status"] == "invalid"


def test_incomplete_or_incomparable_snapshots_are_not_evaluated(monkeypatch):
    cases = (
        ({"data_quality": "partial"}, "partial_source_coverage"),
        ({"record_count": 1}, "insufficient_comparable_records"),
        ({"record_count": 6}, "record_count_changed_substantially"),
        ({"alert_scope_signature": "different-scope"}, "comparison_scope_changed"),
    )
    for current_fields, expected_reason in cases:
        _client, repository, user = _setup(monkeypatch)
        monitor = _configure(repository, _monitor(repository, user))
        base = datetime(2026, 7, 1, tzinfo=timezone.utc)
        baseline = _snapshot(repository, monitor, 100, base, alert_scope_signature="stable-scope")
        _evaluate(repository, monitor, baseline, base)
        monitor = repository.get_watchlist_item(monitor["_id"], user["_id"])
        snapshot_fields = {"alert_scope_signature": "stable-scope", **current_fields}
        current = _snapshot(repository, monitor, 80, base + timedelta(hours=1), **snapshot_fields)
        result = _evaluate(repository, monitor, current, base + timedelta(hours=1))
        saved = repository.get_watchlist_item(monitor["_id"], user["_id"])
        assert result == {
            "status": "not_evaluated", "event_id": None,
            "change_percent": None, "reason": expected_reason,
        }
        assert saved["alert_last_evaluation_status"] == "not_evaluated"
        assert saved["alert_last_evaluation_reason"] == expected_reason
        assert saved["alert_last_change_percent"] is None
        assert repository.list_price_alert_events(user["_id"], monitor["monitor_id"]) == []


def test_alert_resumes_after_two_snapshots_share_the_new_sample_shape(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    monitor = _configure(repository, _monitor(repository, user), direction="drop", threshold=5)
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    baseline = _snapshot(repository, monitor, 100, base, record_count=3)
    _evaluate(repository, monitor, baseline, base)
    shifted = _snapshot(repository, monitor, 95, base + timedelta(hours=1), record_count=6)
    assert _evaluate(repository, monitor, shifted, base + timedelta(hours=1))["status"] == "not_evaluated"
    monitor = repository.get_watchlist_item(monitor["_id"], user["_id"])
    comparable = _snapshot(repository, monitor, 85, base + timedelta(hours=2), record_count=6)
    result = _evaluate(repository, monitor, comparable, base + timedelta(hours=2))
    assert result["status"] == "email_sent"
    event = repository.list_price_alert_events(user["_id"], monitor["monitor_id"])[0]
    assert event["previous_snapshot_id"] == shifted["_id"]


def test_drop_increase_either_and_below_threshold_calculation(monkeypatch):
    for direction, current, expected in (("drop", 90, True), ("increase", 110, True), ("either", 94, True), ("drop", 98, False)):
        _client, repository, user = _setup(monkeypatch)
        monitor = _configure(repository, _monitor(repository, user), direction=direction, threshold=5)
        base = datetime(2026, 7, 1, tzinfo=timezone.utc)
        first = _snapshot(repository, monitor, 100, base)
        _evaluate(repository, monitor, first, base)
        monitor = repository.get_watchlist_item(monitor["_id"], user["_id"])
        second = _snapshot(repository, monitor, current, base + timedelta(hours=1))
        result = _evaluate(repository, monitor, second, base + timedelta(hours=1))
        assert (result["status"] == "email_sent") is expected
        if expected:
            assert result["change_percent"] == current - 100
        else:
            assert result["status"] == "not_required" and repository.list_price_alert_events(user["_id"], monitor["monitor_id"]) == []


def test_different_monitor_never_supplies_previous_snapshot(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    first_monitor = _configure(repository, _monitor(repository, user, "First"))
    second_monitor = _configure(repository, _monitor(repository, user, "Second"))
    _snapshot(repository, first_monitor, 200)
    current = _snapshot(repository, second_monitor, 100)
    assert _evaluate(repository, second_monitor, current)["status"] == "not_required"


def test_duplicate_and_concurrent_evaluation_send_one_email(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    monitor = _configure(repository, _monitor(repository, user))
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    _snapshot(repository, monitor, 100, base)
    current = _snapshot(repository, monitor, 90, base + timedelta(hours=1))
    barrier = Barrier(2)
    results = []
    def worker():
        barrier.wait()
        results.append(_evaluate(repository, monitor, current, base + timedelta(hours=1)))
    threads = [Thread(target=worker), Thread(target=worker)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert len(CapturingMail.sent) == 1
    assert sorted(row["status"] for row in results) == ["duplicate", "email_sent"]
    assert len(repository.list_price_alert_events(user["_id"], monitor["monitor_id"])) == 1


def test_cooldown_records_suppressed_event_without_email(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    monitor = _configure(repository, _monitor(repository, user), direction="drop", alert_last_triggered_at=base)
    _snapshot(repository, monitor, 100, base + timedelta(hours=1))
    current = _snapshot(repository, monitor, 90, base + timedelta(hours=2))
    result = _evaluate(repository, monitor, current, base + timedelta(hours=2))
    events = repository.list_price_alert_events(user["_id"], monitor["monitor_id"])
    assert result["status"] == "suppressed_by_cooldown" and len(events) == 1
    assert events[0]["email_status"] == "suppressed_by_cooldown" and CapturingMail.sent == []


def test_email_failure_preserves_snapshot_and_refresh_success(monkeypatch):
    _client, repository, user = _setup(monkeypatch)
    monitor = _configure(repository, _monitor(repository, user), direction="drop")
    _snapshot(repository, monitor, 100, datetime(2026, 7, 1, tzinfo=timezone.utc))
    CapturingMail.fail = True
    monkeypatch.setattr(precision_app, "EmailService", CapturingMail)
    monkeypatch.setattr(precision_app, "_offline_monitor_records", lambda _monitor: ([
        {"title": "Alert Monitor", "platform": "eBay", "price": 90, "shipping": 0, "condition": "New"},
        {"title": "Alert Monitor", "platform": "eBay", "price": 90, "shipping": 0, "condition": "New"},
    ], {"ebay": {"status": "success", "record_count": 2}}))
    result = precision_app.refresh_monitor(monitor["_id"], user["_id"], trigger="manual", now=datetime(2026, 7, 2, tzinfo=timezone.utc))
    assert result["status"] == "success" and repository.count_price_snapshots(monitor["_id"]) == 2
    events = repository.list_price_alert_events(user["_id"], monitor["monitor_id"])
    assert events[0]["email_status"] == "failed" and events[0]["email_error_code"] == "delivery_timeout"


def test_test_email_is_labelled_and_creates_no_snapshot_event_or_provider_call(monkeypatch):
    client, repository, user = _setup(monkeypatch)
    monitor = _monitor(repository, user)
    captured = {}
    def send(_self, recipient, monitor_name, monitor_url):
        captured.update(recipient=recipient, monitor_name=monitor_name, monitor_url=monitor_url)
        return "mocked"
    monkeypatch.setattr(EmailService, "send_price_alert_test", send)
    monkeypatch.setattr(precision_app, "_retrieve_monitor_provider_records", lambda *_: (_ for _ in ()).throw(AssertionError("provider called")))
    response = client.post(f"/watchlist/{monitor['_id']}/price-alert/test", follow_redirects=True)
    assert response.status_code == 200 and captured["recipient"] == user["email"]
    assert "No marketplace threshold was triggered" in response.get_data(as_text=True)
    assert repository.count_price_snapshots(monitor["_id"]) == 0
    assert repository.list_price_alert_events(user["_id"], monitor["monitor_id"]) == []
    text, html = EmailService(enabled=False)._price_alert_bodies("Monitor", "either", 5, None, None, None, datetime.now(timezone.utc), "https://app.test", True)
    assert "This is a test notification. No marketplace threshold was triggered." in text + html
    assert text.count("This is a test notification. No marketplace threshold was triggered.") == 1
    assert html.count("This is a test notification. No marketplace threshold was triggered.") == 1
    assert "Price alert test" in text and "Price alert test" in html
    assert "Price changes by 5% or more in either direction" in text + html
    assert "Test sent" in text + html
    assert "Future triggered alerts will include the previous price, current price and percentage change." in text + html
    assert "Not applicable" not in text + html
    assert "Previous comparable price" not in text + html
    assert "Current comparable price" not in text + html
    assert "Snapshot time" not in text + html


def test_email_content_ui_and_history_are_scoped(monkeypatch):
    client, repository, user = _setup(monkeypatch)
    monitor = _configure(repository, _monitor(repository, user), direction="either")
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    _snapshot(repository, monitor, 100, base)
    current = _snapshot(repository, monitor, 90, base + timedelta(hours=1))
    _evaluate(repository, monitor, current, base + timedelta(hours=1))
    text, html = EmailService(enabled=False)._price_alert_bodies("Monitor", "drop", 5, 100, 90, -10, base, "https://app.test", False)
    content = text + html
    assert "Previous comparable price" in content and "Current comparable price" in content and "-10.00%" in content
    assert "Price decrease detected" in text and "Price decrease detected" in html
    assert "Triggered threshold" in content and "Price drops by 5% or more" in content
    assert "Snapshot time" in content
    assert "password" not in content.lower() and "mongodb" not in content.lower() and "api key" not in content.lower()
    page = client.get("/watchlist", query_string={"item_id": monitor["_id"]})
    body = page.get_data(as_text=True)
    for value in ("Price Alert", "Last checked", "Latest change", "Send test email", "Alert history", "USD 100.00", "USD 90.00"):
        assert value in body
    other = repository.create_user("Other", "history.other@example.test", "unused", "consumer", membership_tier="premium")
    other_monitor = _configure(repository, _monitor(repository, other, "Private history"))
    _snapshot(repository, other_monitor, 200, base)
    other_current = _snapshot(repository, other_monitor, 180, base + timedelta(hours=1))
    _evaluate(repository, other_monitor, other_current, base + timedelta(hours=1))
    assert "Private history" not in client.get("/watchlist", query_string={"item_id": monitor["_id"]}).get_data(as_text=True)


def test_not_evaluated_reason_is_visible_in_watchlist(monkeypatch):
    client, repository, user = _setup(monkeypatch)
    monitor = _configure(repository, _monitor(repository, user))
    repository.update_watchlist_item(monitor["_id"], {
        "alert_last_evaluated_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
        "alert_last_email_status": "not_evaluated",
        "alert_last_evaluation_status": "not_evaluated",
        "alert_last_evaluation_reason": "partial_source_coverage",
    }, user_id=user["_id"])
    page = client.get("/watchlist", query_string={"item_id": monitor["_id"]})
    body = page.get_data(as_text=True)
    assert "Not evaluated because one or more marketplace sources were unavailable." in body
    assert "Threshold not reached" not in body


def test_below_threshold_ui_neutral_status(monkeypatch):
    client, repository, user = _setup(monkeypatch)
    monitor = _configure(repository, _monitor(repository, user), threshold=5)
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    _snapshot(repository, monitor, 100, base)
    current = _snapshot(repository, monitor, 98.2, base + timedelta(hours=1))
    _evaluate(repository, monitor, current, base + timedelta(hours=1))
    page = client.get("/watchlist", query_string={"item_id": monitor["_id"]}).get_data(as_text=True)
    assert "Evaluated: -1.80%. Alert threshold not reached." in page
