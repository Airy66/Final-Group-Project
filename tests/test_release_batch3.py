import ast
from pathlib import Path
import re

import pytest

import app as compatibility_entry
import precision_app
from services.runtime_config import ProductionConfigurationError, validate_production_configuration


ROOT = Path(__file__).resolve().parents[1]


def test_canonical_and_compatibility_entries_resolve_to_one_app():
    assert compatibility_entry.app is precision_app.app
    assert precision_app.app.import_name == "precision_app"


@pytest.mark.parametrize("filename", ["app.py", "precision_app.py"])
def test_importable_entries_do_not_start_a_development_server(filename):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"))
    run_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
    ]
    assert run_calls == []


def test_compatibility_entry_creates_no_duplicate_flask_app():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "Flask(" not in source
    assert "requests" not in source
    assert "from precision_app import app" in source


def test_health_is_public_minimal_and_makes_no_provider_calls(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("health check attempted an external call")

    monkeypatch.setattr(precision_app.requests, "get", forbidden)
    monkeypatch.setattr(precision_app.requests, "post", forbidden)
    monkeypatch.setattr(precision_app, "call_serpapi_marketplace", forbidden)
    monkeypatch.setattr(precision_app, "search_prices", forbidden)
    client = precision_app.app.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "service": "precision-curator"}


def test_production_configuration_remains_fail_closed():
    with pytest.raises(ProductionConfigurationError):
        validate_production_configuration({
            "APP_ENV": "production",
            "DEMO_MODE": "false",
        })
    with pytest.raises(ProductionConfigurationError):
        validate_production_configuration({
            "APP_ENV": "production",
            "FLASK_SECRET_KEY": "x" * 40,
            "DEMO_MODE": "true",
        })
    with pytest.raises(ProductionConfigurationError):
        validate_production_configuration({
            "APP_ENV": "production",
            "FLASK_SECRET_KEY": "x" * 40,
            "DEMO_MODE": "false",
            "DEMO_TOOLS_ENABLED": "true",
        })


def test_legacy_search_get_routes_cannot_mutate():
    rules = {rule.rule: set(rule.methods or ()) for rule in precision_app.app.url_map.iter_rules()}
    assert "GET" in rules["/search-legacy"]
    assert "POST" in rules["/search-legacy"]
    assert "GET" in rules["/search-mongo"]
    assert "POST" in rules["/search-mongo"]
    source = (ROOT / "precision_app.py").read_text(encoding="utf-8")
    assert 'params = request.form if request.method == "POST" else {}' in source


def test_literal_template_endpoints_all_exist():
    endpoints = {rule.endpoint for rule in precision_app.app.url_map.iter_rules()}
    missing = set()
    pattern = re.compile(r"url_for\(\s*['\"]([^'\"]+)['\"]")
    for template in (ROOT / "templates").glob("*.html"):
        for endpoint in pattern.findall(template.read_text(encoding="utf-8-sig")):
            if endpoint not in endpoints:
                missing.add((template.name, endpoint))
    assert not missing


def _requirement_names():
    names = set()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#") or value.startswith("-"):
            continue
        names.add(re.split(r"[<>=!~\[]", value, maxsplit=1)[0].lower())
    return names


def test_required_production_dependencies_are_declared():
    requirements = _requirement_names()
    assert {
        "flask", "gunicorn", "openpyxl", "pymongo", "python-dotenv",
        "requests", "google-genai", "matplotlib", "pandas", "werkzeug",
    } <= requirements
    assert "asyncio" not in requirements


def test_environment_template_contains_placeholders_not_credentials():
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    for key in (
        "EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "SERPAPI_API_KEY",
        "GEMINI_API_KEY", "OPENAI_API_KEY", "BREVO_API_KEY", "BREVO_SENDER_EMAIL",
        "PRECISION_ADMIN_PASSWORD",
    ):
        assert values[key] == ""
    assert values["FLASK_SECRET_KEY"].startswith("replace-with-")
    assert values["MONGO_URI"].startswith("mongodb+srv://")
    assert values["MONGO_DATABASE"] == "precision_curator_production"


def test_readme_documents_canonical_commands_and_release_boundaries():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "precision_app:app" in readme
    assert "gunicorn precision_app:app --workers 2 --timeout 120" in readme
    assert "python -m pytest tests src/tests -q" in readme
    assert "python -m tools.create_admin" in readme
    assert "MongoDB Atlas" in readme
    assert "ephemeral" in readme


def test_runtime_generated_files_are_ignored_and_source_assets_retained():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (
        "static/charts/*", "static/uploads/avatars/*", "login_rendered.html",
        "results.csv", "test_*.csv", "htmlcov/", ".pytest_cache/",
    ):
        assert entry in ignore
    assert (ROOT / "static/charts/.gitkeep").exists()
    assert (ROOT / "static/uploads/avatars/.gitkeep").exists()


def test_render_blueprint_uses_canonical_entry_and_health_path():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "gunicorn precision_app:app" in blueprint
    assert "healthCheckPath: /health" in blueprint
    assert "DEMO_MODE" in blueprint
    assert "MONGO_URI" in blueprint
    assert "MONGO_DATABASE" in blueprint
    assert "value: \"false\"" in blueprint


def test_tracked_tree_contains_no_removed_hardcoded_credential():
    assert not (ROOT / "ebay_test.py").exists()
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "PRD-" not in app_source
