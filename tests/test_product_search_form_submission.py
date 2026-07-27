from pathlib import Path

from bs4 import BeautifulSoup
from werkzeug.security import generate_password_hash

import precision_app
from services.database import MongoRepository


ROOT = Path(__file__).resolve().parents[1]


def _make_client():
    precision_app.app.config.update(
        TESTING=True,
        SECRET_KEY="product-search-form-tests",
        DEVELOPER_ROLE_PREVIEW=False,
        RUNTIME_ENVIRONMENT="production",
    )
    precision_app.repository = MongoRepository(uri="", database_name="product_search_form_tests")
    user = precision_app.repository.create_user(
        "Search Form User",
        "search-form@example.test",
        generate_password_hash("OfflinePassword1!"),
        "consumer",
    )
    client = precision_app.app.test_client()
    client.post("/login", data={"email": user["email"], "password": "OfflinePassword1!"})
    return client, user


def _rendered_search_form_payload(client, *, query, scope):
    """Serialize the rendered form's successful controls instead of hand-building POST data."""
    page = client.get("/search")
    assert page.status_code == 200
    form = BeautifulSoup(page.data, "html.parser").select_one("#source-search-form")
    assert form is not None

    query_control = form.select_one('input[name="q"]')
    scope_control = form.select_one('select[name="search_scope"]')
    assert query_control is not None and query_control.has_attr("required")
    assert scope_control is not None
    query_control["value"] = query
    for option in scope_control.select("option"):
        if option.get("value") == scope:
            option["selected"] = ""
        else:
            option.attrs.pop("selected", None)

    payload = {}
    for control in form.select("input[name], select[name], textarea[name]"):
        if control.has_attr("disabled"):
            continue
        name = control["name"]
        if control.name == "select":
            selected = control.select_one("option[selected]") or control.select_one("option")
            payload[name] = selected.get("value", selected.get_text()) if selected else ""
            continue
        input_type = control.get("type", "text").lower()
        if input_type in {"checkbox", "radio"} and not control.has_attr("checked"):
            continue
        payload[name] = control.get("value", control.get_text())
    return form, payload


def test_rendered_product_search_form_preserves_keyword_scope_and_csrf(monkeypatch):
    client, user = _make_client()
    form, payload = _rendered_search_form_payload(
        client,
        query="iPhone 17 Pro",
        scope="ebay",
    )

    assert form.get("method", "").lower() == "post"
    assert form.get("action") == "/search"
    assert payload["q"] == "iPhone 17 Pro"
    assert payload["search_scope"] == "ebay"
    assert payload["action"] == "search"
    assert payload["csrf_token"]

    provider_queries = []

    def offline_ebay(query, **_kwargs):
        provider_queries.append(query)
        return ([{
            "title": "Apple iPhone 17 Pro 256GB",
            "platform": "eBay",
            "price": 1099,
            "source_url": "https://example.test/iphone-17-pro",
        }], {"cache_status": "miss"})

    monkeypatch.setattr(precision_app, "search_ebay_cached", offline_ebay)
    response = client.post(
        form["action"],
        data=payload,
        auto_csrf=False,
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.request.path.startswith("/search/results/")
    assert provider_queries
    assert "Enter a product keyword or model before searching." not in body
    assert "Apple iPhone 17 Pro 256GB" in body
    restored_form = BeautifulSoup(response.data, "html.parser").select_one("#source-search-form")
    assert restored_form.select_one('input[name="q"]')["value"] == "iPhone 17 Pro"
    assert not restored_form.select_one('input[name="q"]').has_attr("disabled")
    assert not restored_form.select_one('select[name="search_scope"]').has_attr("disabled")
    assert not restored_form.select_one('button[type="submit"]').has_attr("disabled")
    run = precision_app.repository.list_searches(user["_id"], limit=1)[0]
    assert run["raw_query"] == "iPhone 17 Pro"
    assert run["selected_source"] == "ebay"
    assert run["result_count"] == 1


def test_loading_handler_disables_only_submit_controls_and_blocks_duplicates():
    source = (ROOT / "templates" / "source_search_roles.html").read_text(encoding="utf-8-sig")
    handler = source.split("function beginSourceSearch(form) {", 1)[1].split("\n}", 1)[0]

    assert "form.dataset.submitting === 'true'" in handler
    assert "form.dataset.submitting = 'true'" in handler
    assert "button.disabled = true" in handler
    assert "loading.classList.remove('hidden')" in handler
    assert "input:not([type=\"hidden\"]), select" not in handler
    assert "control.disabled = true" not in handler
    assert 'name="q" type="search" required' in source
    assert 'name="search_scope"' in source
    assert "Searching marketplace sources&hellip;" in source
    assert "{% if show_search_diagnostics %}" in source


def test_empty_search_keeps_existing_server_validation_message():
    client, _user = _make_client()
    form, payload = _rendered_search_form_payload(client, query="", scope="ebay")
    response = client.post(
        form["action"],
        data=payload,
        auto_csrf=False,
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Enter a product keyword or model before searching." in response.get_data(as_text=True)
