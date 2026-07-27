"""Price-alert evaluation using already-persisted Watchlist snapshots."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


VALID_DIRECTIONS = {"drop", "increase", "either"}


def _utc(value=None):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _price(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result > 0 else None


def _snapshot_time(snapshot):
    value = snapshot.get("collected_at") or snapshot.get("created_at")
    return _utc(value) if isinstance(value, datetime) else datetime.min.replace(tzinfo=timezone.utc)


def _valid_snapshot(snapshot, monitor):
    if not snapshot or _price(snapshot.get("average_price")) is None:
        return False
    if str(snapshot.get("watchlist_id")) != str(monitor.get("_id")):
        return False
    stable_id = str(monitor.get("monitor_id") or monitor.get("_id"))
    if str(snapshot.get("monitor_id") or monitor.get("_id")) not in {stable_id, str(monitor.get("_id"))}:
        return False
    if str(snapshot.get("user_id")) != str(monitor.get("user_id")):
        return False
    return snapshot.get("data_quality") not in {"failed", "empty", "invalid"}


def _audit(callback, event_type, details):
    if callback:
        try:
            callback(event_type, details)
        except Exception:
            pass


def normalize_mail_error(exc):
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "delivery_timeout"
    if isinstance(exc, (ValueError, RuntimeError)):
        return "mail_configuration_error"
    return "mail_delivery_failed"


def evaluate_price_alert(repository, monitor, snapshot, *, mail_service_factory, monitor_url,
                         alerts_enabled=True, now=None, audit=None):
    """Evaluate one snapshot without performing any marketplace retrieval."""
    now = _utc(now)
    if not alerts_enabled or not monitor.get("alert_enabled"):
        return {"status": "disabled", "event_id": None, "change_percent": None}
    direction = monitor.get("alert_direction")
    threshold = _price(monitor.get("alert_threshold_percent"))
    if direction not in VALID_DIRECTIONS or threshold is None or not _valid_snapshot(snapshot, monitor):
        return {"status": "invalid", "event_id": None, "change_percent": None}

    snapshots = [row for row in repository.list_price_snapshots(monitor["_id"], limit=0)
                 if _valid_snapshot(row, monitor)]
    snapshots.sort(key=_snapshot_time)
    current_index = next((index for index, row in enumerate(snapshots)
                          if str(row.get("_id")) == str(snapshot.get("_id"))), None)
    previous = snapshots[current_index - 1] if current_index is not None and current_index > 0 else None
    if previous is None:
        repository.update_watchlist_item(monitor["_id"], {
            "alert_last_evaluated_at": now,
            "alert_last_change_percent": None,
            "alert_last_email_status": "not_required",
        }, user_id=monitor["user_id"])
        _audit(audit, "price_alert_evaluated", {"monitor_id": str(monitor["_id"]), "status": "no_previous_snapshot"})
        return {"status": "not_required", "event_id": None, "change_percent": None}

    current_price = _price(snapshot.get("average_price"))
    previous_price = _price(previous.get("average_price"))
    if current_price is None or previous_price is None:
        return {"status": "invalid", "event_id": None, "change_percent": None}
    change = (((current_price - previous_price) / previous_price) * Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    crossed = ((direction == "drop" and change <= -threshold)
               or (direction == "increase" and change >= threshold)
               or (direction == "either" and abs(change) >= threshold))
    repository.update_watchlist_item(monitor["_id"], {
        "alert_last_evaluated_at": now,
        "alert_last_change_percent": float(change),
        "alert_last_email_status": "not_required" if not crossed else monitor.get("alert_last_email_status"),
    }, user_id=monitor["user_id"])
    _audit(audit, "price_alert_evaluated", {
        "monitor_id": str(monitor["_id"]), "change_percent": float(change),
        "threshold_percent": float(threshold), "status": "triggered" if crossed else "not_required",
    })
    if not crossed:
        return {"status": "not_required", "event_id": None, "change_percent": float(change)}

    last_triggered = monitor.get("alert_last_triggered_at")
    cooldown_hours = max(1, int(monitor.get("alert_cooldown_hours") or 24))
    cooling_down = isinstance(last_triggered, datetime) and _utc(last_triggered) + timedelta(hours=cooldown_hours) > now
    email_requested = bool(monitor.get("alert_email_enabled", True)) and not cooling_down
    event = {
        "monitor_id": monitor.get("monitor_id") or monitor["_id"],
        "watchlist_id": monitor["_id"],
        "owner_user_id": monitor["user_id"],
        "snapshot_id": snapshot["_id"],
        "previous_snapshot_id": previous["_id"],
        "alert_rule_version": int(monitor.get("alert_rule_version") or 0),
        "direction": direction,
        "threshold_percent": float(threshold),
        "previous_price": float(previous_price),
        "current_price": float(current_price),
        "change_percent": float(change),
        "triggered_at": now,
        "email_requested": email_requested,
        "email_status": "suppressed_by_cooldown" if cooling_down else ("not_requested" if not email_requested else "pending"),
        "email_error_code": None,
        "event_status": "suppressed_by_cooldown" if cooling_down else "triggered",
    }
    event_id, created = repository.create_price_alert_event(event)
    if not created:
        _audit(audit, "duplicate_alert_suppressed", {"monitor_id": str(monitor["_id"]), "snapshot_id": str(snapshot["_id"])})
        return {"status": "duplicate", "event_id": event_id, "change_percent": float(change)}
    repository.update_watchlist_item(monitor["_id"], {"alert_last_triggered_at": now}, user_id=monitor["user_id"])
    _audit(audit, "price_alert_triggered", {"monitor_id": str(monitor["_id"]), "event_id": event_id, "change_percent": float(change)})
    if cooling_down:
        _audit(audit, "cooldown_suppression", {"monitor_id": str(monitor["_id"]), "event_id": event_id})
        return {"status": "suppressed_by_cooldown", "event_id": event_id, "change_percent": float(change)}
    if not email_requested:
        return {"status": "triggered", "event_id": event_id, "change_percent": float(change)}

    user = repository.get_user_by_id(monitor["user_id"])
    try:
        delivery = mail_service_factory().send_price_alert(
            user.get("email"), monitor.get("product_label") or monitor.get("keyword") or "Watchlist Monitor",
            direction, float(threshold), float(previous_price), float(current_price), float(change),
            snapshot.get("collected_at") or snapshot.get("created_at"), monitor_url,
        )
        repository.update_price_alert_event(event_id, {"email_status": "sent", "event_status": "email_sent"})
        repository.update_watchlist_item(monitor["_id"], {"alert_last_email_status": "sent"}, user_id=monitor["user_id"])
        _audit(audit, "alert_email_sent", {"monitor_id": str(monitor["_id"]), "event_id": event_id, "delivery_mode": delivery})
        return {"status": "email_sent", "event_id": event_id, "change_percent": float(change)}
    except Exception as exc:
        code = normalize_mail_error(exc)
        repository.update_price_alert_event(event_id, {"email_status": "failed", "email_error_code": code, "event_status": "email_failed"})
        repository.update_watchlist_item(monitor["_id"], {"alert_last_email_status": "failed"}, user_id=monitor["user_id"])
        _audit(audit, "alert_email_failed", {"monitor_id": str(monitor["_id"]), "event_id": event_id, "error_code": code})
        return {"status": "email_failed", "event_id": event_id, "change_percent": float(change), "error_code": code}
