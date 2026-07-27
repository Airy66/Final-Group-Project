import csv
import io

import precision_app
from services.database import MongoRepository


def _client(username="AuditUser"):
    precision_app.app.config.update(TESTING=True, SECRET_KEY="audit-export-tests")
    precision_app.repository = MongoRepository(uri="", database_name="test_audit_exports")
    client = precision_app.app.test_client()
    password = "OfflineTestPassword1!"
    email = f"{''.join(character.lower() for character in username if character.isalnum())}@example.test"
    user = precision_app.repository.create_user(username, email, precision_app.generate_password_hash(password, method="pbkdf2:sha256:1000"), "researcher")
    client.post("/login", data={"email": email, "password": password})
    precision_app.repository.update_user(user["_id"], {"membership_tier": "professional", "plan": "professional"})
    return client, precision_app.repository.get_user_by_display_name(username)


def _csv_rows(response):
    return list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))


def _seed_audit_scope(user):
    repo = precision_app.repository
    search_id = repo.create_search(user["_id"], "iPhone 15", "both", role="researcher", workspace="researcher")
    repo.update_search_run(search_id, {
        "source_statuses": {
            "ebay": {"status": "success", "retained_count": 3},
            "walmart": {"status": "query_correction_suggested", "retained_count": 0},
        },
        "comparison_enabled": True,
        "result_categories": {
            "one": {"product_role": "complete_product"},
            "two": {"product_role": "complete_product"},
        },
    })
    repo.complete_search(search_id, 3, "partial_success")
    repo.save_evidence(user["_id"], search_id, {
        "title": "Apple iPhone 15 128GB", "platform": "eBay", "source_type": "live_ebay",
        "confidence_level": "high", "analytics_eligible": True, "role": "researcher",
    })
    repo.log_ai_search(user["_id"], "iPhone 15", "gemini-test", "Grounded summary", "Offline response")
    repo.log_ai_activity(user["_id"], "iPhone 15", "gemini-test", "Gemini", 2)
    repo.log_event(user["_id"], "records_collected", "researcher", user["display_name"], {
        "query": "iPhone 15", "search_scope": "both", "record_count": 3,
    })
    repo.log_event(user["_id"], "search_started", "researcher", user["display_name"], {
        "query": "ordinary search must not be AI activity", "search_scope": "both",
    })


def test_source_audit_csv_has_one_header_distinct_scope_status_and_all_record_types():
    client, user = _client()
    _seed_audit_scope(user)
    response = client.get("/audit/export/source-audit.csv")
    rows = _csv_rows(response)
    assert response.status_code == 200
    assert rows[0] == [
        "Audit ID", "Record Type", "Query / Product", "User", "Role", "Workspace", "Source Scope",
        "eBay Status", "Walmart Status", "Matched Records", "Comparable Records", "Action / Event",
        "Status", "Timestamp (SGT)", "Details",
    ]
    assert sum(row == rows[0] for row in rows) == 1
    by_type = {row[1] for row in rows[1:]}
    assert {"Search", "Saved evidence", "AI analysis", "Source event"}.issubset(by_type)
    search = next(row for row in rows[1:] if row[1] == "Search")
    assert search[6] == "eBay and Walmart"
    assert search[7] == "Success"
    assert search[8] == "Query correction suggested"
    assert search[12] == "Partial"
    assert "Multi-source" not in search
    assert all(row[13].endswith(" SGT") for row in rows[1:] if row[13])
    assert "source-audit-" in response.headers["Content-Disposition"]


def test_ai_activity_csv_uses_ai_schema_and_excludes_ordinary_search_events():
    client, user = _client("AIExportUser")
    _seed_audit_scope(user)
    response = client.get("/audit/export/ai-activity-log.csv")
    rows = _csv_rows(response)
    assert response.status_code == 200
    assert rows[0][:6] == ["Event ID", "Operation", "User", "Role", "Workspace", "Query / Analysis"]
    assert "AI Provider" in rows[0] and "Duration (ms)" in rows[0]
    assert "Record Type" not in rows[0] and "Source Scope" not in rows[0]
    assert len(rows) == 2
    assert all("ordinary search must not be AI activity" not in row for row in rows[1:])
    assert all(row[12].endswith(" SGT") and row[13].endswith(" SGT") for row in rows[1:])


def test_empty_ai_activity_csv_contains_header_only():
    client, _user = _client("EmptyAIUser")
    rows = _csv_rows(client.get("/audit/export/ai-activity-log.csv"))
    assert len(rows) == 1
    assert rows[0][0:2] == ["Event ID", "Operation"]


def test_general_activity_csv_uses_general_activity_schema():
    client, user = _client("GeneralLogUser")
    precision_app.repository.log_event(user["_id"], "role_switch", "researcher", user["display_name"], {"role": "researcher"})
    response = client.get("/audit/export/activity-log.csv")
    rows = _csv_rows(response)
    assert response.status_code == 200
    assert rows[0] == ["Action", "User", "Role", "Workspace", "Detail", "Time (SGT)", "Status"]
    assert "Record type" not in rows[0]
    assert all(row[5].endswith(" SGT") for row in rows[1:] if row[5])
    assert all(row[6] in {"Completed", "Failed", "No results", "Recorded"} for row in rows[1:])


def test_general_activity_csv_uses_events_without_synthetic_search_duplicates():
    client, user = _client("CleanGeneralLogUser")
    _seed_audit_scope(user)
    rows = _csv_rows(client.get("/audit/export/activity-log.csv"))
    assert not any("mixed" in cell.lower() for row in rows[1:] for cell in row)
    assert not any(row[0] == "Search" for row in rows[1:])
    collected = [row for row in rows[1:] if row[0] == "Records Collected"]
    assert len(collected) == 1
    assert "Retained 3 record(s)" in collected[0][4]
    assert collected[0][1] == user["display_name"]
    assert collected[0][6] == "Completed"


def test_printable_source_audit_report_is_meaningful_and_navigation_free():
    client, user = _client("ReportAuditUser")
    _seed_audit_scope(user)
    response = client.get("/audit/report", query_string={"record_type": "all"})
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    for text in (
        "Report information", "Audit summary", "Marketplace source summary", "Evidence timeline",
        "Provenance and limitations", "iPhone 15", "eBay and Walmart", "Partial",
        "eBay official marketplace API", "Walmart external / third-party marketplace retrieval service",
        "Print / Save as PDF",
    ):
        assert text in body
    assert "<th>Field</th>" not in body
    assert "Search | Multi-source" not in body
    assert "<aside" not in body and "Activity Logs</a>" not in body
    assert "@media print" in body and ".sidebar, .topbar" in body and "thead { display: table-header-group; }" in body


def test_source_audit_exports_respect_record_type_filter_without_errors():
    client, user = _client("FilteredAuditUser")
    _seed_audit_scope(user)
    csv_rows = _csv_rows(client.get("/audit/export/source-audit.csv", query_string={"record_type": "evidence"}))
    assert len(csv_rows) == 2
    assert csv_rows[1][1] == "Saved evidence"
    report = client.get("/audit/report", query_string={"record_type": "evidence"})
    assert report.status_code == 200
    body = report.get_data(as_text=True)
    assert "Current filtered view" in body and "Type: Evidence" in body
