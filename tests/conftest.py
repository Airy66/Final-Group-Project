"""Shared deterministic configuration for the offline test suite."""

import os

import pytest


TEST_SERPAPI_API_KEY = "test-serpapi-key-not-real"

# These values are established before test modules import precision_app, so
# application startup never reads the developer's real .env during pytest.
os.environ["APP_ENV"] = "testing"
os.environ["PRECISION_TESTING"] = "true"
os.environ["SERPAPI_API_KEY"] = TEST_SERPAPI_API_KEY


@pytest.fixture
def walmart_offline_test_config(monkeypatch):
    """Provide fake credentials while failing closed on an unmocked HTTP call."""
    from services import serpapi_search

    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("SERPAPI_API_KEY", TEST_SERPAPI_API_KEY)
    monkeypatch.setenv("SERPAPI_WALMART_ENGINE", "google_shopping")

    def reject_unmocked_provider_call(*_args, **_kwargs):
        raise AssertionError("Walmart tests must mock the SerpAPI HTTP boundary")

    monkeypatch.setattr(
        serpapi_search.requests,
        "get",
        reject_unmocked_provider_call,
    )
