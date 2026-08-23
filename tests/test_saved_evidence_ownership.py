import csv
import io

import pytest
from werkzeug.security import generate_password_hash

import precision_app
from services.database import MongoRepository


PASSWORD = "OfflineTestPassword1!"


def _setup_client():
    precision_app.app.config.update(
        TESTING=True,
        SECRET_KEY="saved-evidence-ownership-tests",
        RUNTIME_ENVIRONMENT="test",
    )
    precision_app.repository = MongoRepository(uri="", database_name="saved_evidence_ownership")
    return precision_app.app.test_client()


def _create_user(name, role, tier="professional"):
    email = f"{name.lower().replace(' ', '.')}@example.test"
    return precision_app.repository.create_user(
        name,
        email,
        generate_password_hash(PASSWORD, method="pbkdf2:sha256:1000"),
        role,
        roles=[role],
        active_role=role,
        plan=tier,
        membership_tier=tier,
    )


def _login(client, user):
    return client.post("/login", data={"email": user["email"], "password": PASSWORD})


def _seed_evidence(user, label, platform="eBay"):
    search_id = precision_app.repository.create_search(
        user["_id"], label, "both", role=user["role"], data_mode="live"
    )
    precision_app.repository.complete_search(search_id, 1)
    evidence_id, created = precision_app.repository.save_evidence(user["_id"], search_id, {
        "query": label,
        "title": f"{label} private listing",
        "product_name": f"{label} private listing",
        "platform": platform,
        "price": 499.0,
        "normalized_price": 499.0,
        "total_price": 499.0,
        "currency": "USD",
        "condition": "Used",
        "condition_display": "Used",
        "analytics_eligible": True,
        "source_type": "marketplace",
        "source_url": "https://example.test/listing",
    })
    assert created
    return search_id, evidence_id


def _csv_rows(response):
    return list(csv.reader(io.StringIO(response.get_data(as_text=True))))


def test_researcher_saved_workspace_exports_and_analytics_are_owner_scoped():
    client = _setup_client()
    owner = _create_user("Researcher Owner", "researcher")
    other = _create_user("Researcher Other", "researcher")
    owner_search, owner_evidence = _seed_evidence(owner, "owner-evidence")
    other_search, other_evidence = _seed_evidence(other, "other-evidence", platform="Walmart")
    research_payload = {
        "selected_records": [],
        "analysis_summary": {
            "lowest_price": 499,
            "average_price": 499,
            "highest_price": 499,
            "potential_saving": 0,
            "best_platform": "eBay",
        },
        "insight_mode": "Rule-based insight",
        "ai_price_insight": "Owner-scoped research insight.",
    }
    owner_research = precision_app.repository.save_research(owner["_id"], {**research_payload, "query": "owner-package"})
    other_research = precision_app.repository.save_research(other["_id"], {**research_payload, "query": "other-package"})

    _login(client, owner)
    saved_page = client.get("/saved").get_data(as_text=True)
    assert "owner-evidence private listing" in saved_page
    assert "other-evidence private listing" not in saved_page

    assert client.get(f"/saved/research/{owner_research}").status_code == 200
    assert client.get(f"/saved/research/{other_research}").status_code == 404

    own_csv = client.get("/saved/export/evidence.csv", query_string={"evidence_id": owner_evidence})
    other_csv = client.get("/saved/export/evidence.csv", query_string={"evidence_id": other_evidence})
    assert any("owner-evidence private listing" in cell for row in _csv_rows(own_csv) for cell in row)
    assert not any("other-evidence private listing" in cell for row in _csv_rows(other_csv) for cell in row)

    report = client.get("/saved/report").get_data(as_text=True)
    assert "owner-evidence private listing" in report
    assert "other-evidence private listing" not in report
    assert client.get(f"/analytics/{owner_search}?mode=saved").status_code == 200
    assert client.get(f"/analytics/{other_search}?mode=saved").status_code == 404
    assert client.get(f"/analytics/{other_search}/export/results.csv?mode=saved").status_code == 404


def test_researcher_delete_and_bulk_delete_reject_foreign_evidence_ids():
    client = _setup_client()
    owner = _create_user("Delete Owner", "researcher")
    other = _create_user("Delete Other", "researcher")
    owner_search, owner_evidence = _seed_evidence(owner, "owner-delete")
    _other_search, other_evidence = _seed_evidence(other, "other-delete")
    _login(client, owner)

    client.post(f"/saved/evidence/{other_evidence}/delete")
    assert precision_app.repository.get_evidence(other_evidence, other["_id"]) is not None

    client.post("/saved/evidence/delete", data={"evidence_id": [owner_evidence, other_evidence]})
    assert precision_app.repository.get_evidence(owner_evidence, owner["_id"]) is None
    assert precision_app.repository.get_evidence(other_evidence, other["_id"]) is not None

    own_second_id, created = precision_app.repository.save_evidence(owner["_id"], owner_search, {
        "title": "owner second private listing", "platform": "Walmart", "normalized_price": 510,
    })
    assert created
    client.post(f"/saved/evidence/{own_second_id}/delete")
    assert precision_app.repository.get_evidence(own_second_id, owner["_id"]) is None


@pytest.mark.parametrize("role", ["consumer", "retailer"])
def test_normal_users_only_list_and_delete_their_own_evidence(role):
    client = _setup_client()
    owner = _create_user(f"{role} Owner", role)
    other = _create_user(f"{role} Other", role)
    _owner_search, owner_evidence = _seed_evidence(owner, f"{role}-owner")
    _other_search, other_evidence = _seed_evidence(other, f"{role}-other")
    _login(client, owner)

    page = client.get("/saved").get_data(as_text=True)
    assert f"{role}-owner private listing" in page
    assert f"{role}-other private listing" not in page
    client.post(f"/saved/evidence/{other_evidence}/delete")
    assert precision_app.repository.get_evidence(other_evidence, other["_id"]) is not None
    client.post(f"/saved/evidence/{owner_evidence}/delete")
    assert precision_app.repository.get_evidence(owner_evidence, owner["_id"]) is None


def test_administrator_retains_global_saved_evidence_access():
    client = _setup_client()
    administrator = _create_user("Evidence Administrator", "administrator")
    first = _create_user("Admin Scope First", "consumer")
    second = _create_user("Admin Scope Second", "researcher")
    _first_search, first_evidence = _seed_evidence(first, "admin-first")
    _second_search, second_evidence = _seed_evidence(second, "admin-second", platform="Walmart")
    _login(client, administrator)

    page = client.get("/saved").get_data(as_text=True)
    assert "admin-first private listing" in page
    assert "admin-second private listing" in page
    selected_csv = client.get(
        "/saved/export/evidence.csv",
        query_string=[("evidence_id", first_evidence), ("evidence_id", second_evidence)],
    ).get_data(as_text=True)
    assert "admin-first private listing" in selected_csv
    assert "admin-second private listing" in selected_csv

    client.post(f"/saved/evidence/{second_evidence}/delete")
    assert precision_app.repository.get_evidence(second_evidence, second["_id"]) is None
