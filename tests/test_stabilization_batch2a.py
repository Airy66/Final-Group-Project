import ast
import csv
import io
from datetime import datetime, timezone
from pathlib import Path

import precision_app
from services.database import MongoRepository


CSV_ROUTE_FUNCTIONS = {
    "saved_export_evidence_csv",
    "export_watchlist_snapshots_csv",
    "export_watchlist_validation_csv",
    "export_audit_csv",
    "export_ai_activity_log_csv",
    "export_activity_log_csv",
    "analytics_export_results_csv",
}


def test_csv_formula_injection_sanitizer_handles_all_dangerous_prefixes():
    dangerous = ["=1+1", "+SUM(A1:A2)", "-2+3", "@cmd", "  =HYPERLINK(\"https://bad.test\")", "\t+1"]
    for value in dangerous:
        assert precision_app.sanitize_csv_cell(value) == "'" + value
    assert precision_app.sanitize_csv_cell("ordinary text") == "ordinary text"
    assert precision_app.sanitize_csv_cell(42) == 42
    assert precision_app.sanitize_csv_cell(-42.5) == -42.5


def test_csv_response_applies_sanitizer_without_changing_numeric_cells():
    response = precision_app.csv_response([["label", "value"], ["=2+2", -12.5]], "safe.csv")
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    assert rows == [["label", "value"], ["'=2+2", "-12.5"]]


def test_every_server_csv_export_route_uses_the_central_response_helper():
    tree = ast.parse(Path(precision_app.__file__).read_text(encoding="utf-8"))
    found = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name not in CSV_ROUTE_FUNCTIONS:
            continue
        found.add(node.name)
        assert any(
            isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "csv_response"
            for call in ast.walk(node)
        ), f"{node.name} bypasses the central CSV sanitizer"
    assert found == CSV_ROUTE_FUNCTIONS


def test_analytics_csv_route_neutralizes_user_controlled_text():
    precision_app.app.config.update(TESTING=True, SECRET_KEY="batch2a-csv-route")
    precision_app.repository = MongoRepository(uri="", database_name="batch2a_csv_route")
    password = "OfflineTestPassword1!"
    user = precision_app.repository.create_user(
        "CSV Analyst",
        "csv-analyst@example.test",
        precision_app.generate_password_hash(password, method="pbkdf2:sha256:1000"),
        "consumer",
    )
    user_id = user["_id"]
    precision_app.repository.update_user(user_id, {"membership_tier": "professional", "plan": "professional"})
    search_id = precision_app.repository.create_search(user_id, "safe query", "mock", role="consumer", data_mode="mock")
    stored = precision_app.repository.save_external_results(search_id, user_id, [{
        "title": "Safe phone listing",
        "platform": "eBay",
        "price": 10,
        "currency": "USD",
        "condition": "New",
        "category_display": "Phone",
        "seller": "+malicious-seller",
        "source_url": "https://example.test/item",
        "source_type": "live_ebay",
        "collected_at": datetime.now(timezone.utc),
    }])
    precision_app.repository.attach_result_tokens(search_id, [f"external:{stored[0]['_id']}"])
    precision_app.repository.complete_search(search_id, 1)

    client = precision_app.app.test_client()
    client.post("/login", data={"email": "csv-analyst@example.test", "password": password})
    response = client.get(f"/analytics/{search_id}/export/results.csv")
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    assert response.status_code == 200
    assert rows[1][9] == "'+malicious-seller"


def test_deleted_analysis_releases_signature_and_can_be_recreated():
    repo = MongoRepository(uri="", database_name="batch2a_analysis_recreate")
    user_id = repo.create_user("Analyst", "analyst@example.test", "hash", "consumer")["_id"]
    payload = {
        "search_record_id": None,
        "scope": "all_valid_results",
        "query": "phone",
        "analysis_signature": "same-signature",
        "generated_at": datetime.now(timezone.utc),
    }
    original_id = repo.create_analysis_record(user_id, payload)
    assert repo.delete_analysis_record(original_id, user_id)

    deleted = next(row for row in repo._memory["analytics_reports"] if row["_id"] == original_id)
    assert "analysis_signature" not in deleted
    assert deleted["deleted_analysis_signature"] == "same-signature"
    assert repo.find_analysis_by_signature(user_id, "same-signature") is None

    replacement_id = repo.create_analysis_record(user_id, payload)
    replacement = repo.get_analysis_record(replacement_id, user_id)
    assert replacement["analysis_signature"] == "same-signature"
    assert replacement["status"] == "active"


def test_recreation_retires_legacy_deleted_signature_without_cross_user_changes():
    repo = MongoRepository(uri="", database_name="batch2a_legacy_analysis")
    owner_id = repo.create_user("Owner", "owner@example.test", "hash", "consumer")["_id"]
    other_id = repo.create_user("Other", "other@example.test", "hash", "consumer")["_id"]
    payload = {"scope": "all_valid_results", "query": "tablet", "analysis_signature": "legacy-signature"}

    legacy_id = repo.create_analysis_record(owner_id, payload)
    repo.update_analysis_record(legacy_id, {"status": "deleted"})
    other_analysis_id = repo.create_analysis_record(other_id, payload)

    replacement_id = repo.create_analysis_record(owner_id, payload)
    legacy = next(row for row in repo._memory["analytics_reports"] if row["_id"] == legacy_id)
    other = next(row for row in repo._memory["analytics_reports"] if row["_id"] == other_analysis_id)
    assert legacy["deleted_analysis_signature"] == "legacy-signature"
    assert "analysis_signature" not in legacy
    assert other["analysis_signature"] == "legacy-signature"
    assert repo.get_analysis_record(replacement_id, owner_id)["analysis_signature"] == "legacy-signature"
