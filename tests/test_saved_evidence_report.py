from datetime import datetime, timezone

import precision_app
from services.database import MongoRepository


PASSWORD = "OfflineTestPassword1!"


def _setup(name="Evidence Reporter"):
    precision_app.app.config.update(TESTING=True, SECRET_KEY="saved-evidence-report-tests")
    precision_app.repository = MongoRepository(uri="", database_name="test_saved_evidence_report")
    client = precision_app.app.test_client()
    email = f"{name.lower().replace(' ', '.')}@example.test"
    user = precision_app.repository.create_user(
        name,
        email,
        precision_app.generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000"),
        "consumer",
        membership_tier="premium",
    )
    client.post("/login", data={"email": email, "password": PASSWORD})
    search_id = precision_app.repository.create_search(
        user["_id"], "iPhone evidence", "all", role="consumer", data_mode="live"
    )
    return client, user, search_id


def test_saved_evidence_report_renders_complete_account_scoped_records():
    client, user, search_id = _setup()
    collected_at = datetime(2026, 7, 27, 11, 30, tzinfo=timezone.utc)
    for index in range(21):
        precision_app.repository.save_evidence(user["_id"], search_id, {
            "query": "iPhone evidence",
            "title": f"Evidence phone {index}",
            "product_name": f"Evidence phone {index}",
            "platform": "eBay" if index % 2 == 0 else "Walmart",
            "price": 300 + index,
            "normalized_price": 300 + index,
            "currency": "USD",
            "condition": "Used",
            "condition_display": "Used",
            "source_type": "live_ebay" if index % 2 == 0 else "serpapi_walmart",
            "source_url": f"https://example.test/listing/{index}",
            "collected_at": collected_at,
        })

    other = precision_app.repository.create_user(
        "Other Evidence Owner",
        "other.evidence@example.test",
        precision_app.generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000"),
        "consumer",
        membership_tier="premium",
    )
    other_search = precision_app.repository.create_search(
        other["_id"], "private evidence", "ebay", role="consumer", data_mode="live"
    )
    precision_app.repository.save_evidence(other["_id"], other_search, {
        "query": "private evidence",
        "title": "Other account private listing",
        "platform": "eBay",
        "normalized_price": 1,
    })

    response = client.get("/saved/report")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Saved evidence report" in body
    for heading in ("Query", "Platform", "Product", "Price", "Condition", "Source", "Collected", "Evidence link"):
        assert f">{heading}<" in body
    assert "Evidence phone 0" in body and "Evidence phone 20" in body
    assert "USD 300.00" in body
    assert "eBay Browse API" in body and "Walmart via SerpAPI" in body
    assert "27 Jul 2026, 19:30 SGT" in body
    assert "Open listing" in body
    assert "21 included" in body
    assert "Other account private listing" not in body
    assert ">Field<" not in body and ">Value<" not in body


def test_saved_evidence_report_has_clear_empty_state():
    client, _user, _search_id = _setup("Empty Evidence Reporter")
    response = client.get("/saved/report")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "No saved evidence records" in body
    assert "0 included" in body
