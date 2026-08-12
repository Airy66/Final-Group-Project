"""Cache-key and normalization helpers for SerpApi marketplace search."""

from datetime import datetime, timezone
import hashlib
import json
import os
import re
from urllib.parse import urlparse

import requests

from services.platforms import canonical_marketplace_platform


class SerpApiError(RuntimeError):
    SAFE_MESSAGES = {
        "authentication_error": "Marketplace provider credentials are not configured.",
        "provider_connection_error": "Marketplace provider connection failed.",
        "provider_timeout": "Marketplace provider request timed out.",
    }

    def __init__(self, code, *, safe_message=None):
        self.code = str(code or "provider_error")
        self.safe_message = safe_message or self.SAFE_MESSAGES.get(
            self.code,
            "Marketplace provider request failed.",
        )
        super().__init__(self.code)


SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
SERPAPI_NORMALIZATION_VERSION = "2"


def normalize_query(value):
    return " ".join(str(value or "").strip().lower().split())


def normalize_cache_value(value):
    if value is None:
        return ""
    return str(value).strip().lower()


def build_serpapi_cache_key(params):
    relevant = {
        "provider": "serpapi",
        "engine": normalize_cache_value(params.get("engine")),
        "platform": normalize_cache_value(params.get("platform")),
        "q": normalize_query(params.get("q")),
        "category": normalize_cache_value(params.get("category")),
        "min_price": normalize_cache_value(params.get("min_price")),
        "max_price": normalize_cache_value(params.get("max_price")),
        "sort": normalize_cache_value(params.get("sort")),
        "location": normalize_cache_value(params.get("location")),
        "country": normalize_cache_value(params.get("country")),
        "currency": normalize_cache_value(params.get("currency")),
        "store_id": normalize_cache_value(params.get("store_id")),
        "limit": normalize_cache_value(params.get("limit")),
        "normalization_version": normalize_cache_value(params.get("normalization_version") or SERPAPI_NORMALIZATION_VERSION),
    }
    payload = json.dumps(relevant, sort_keys=True, separators=(",", ":"))
    return "serpapi:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def engine_for_platform(platform):
    platform = normalize_cache_value(platform)
    if platform == "ebay":
        return os.getenv("SERPAPI_EBAY_ENGINE", "ebay")
    if platform == "walmart":
        return os.getenv("SERPAPI_WALMART_ENGINE", "google_shopping")
    if platform in {"google_shopping", "shopping"}:
        return "google_shopping"
    return platform or "google_shopping"


def provider_sort_for_platform(platform, sort):
    """Keep Walmart collection relevance-based; the application sorts locally."""
    value = normalize_cache_value(sort)
    return "" if canonical_marketplace_platform(platform) == "Walmart" else value


def _safe_url(value):
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
    except Exception:
        return ""
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _first_present(row, keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _nested(row, path, default=None):
    current = row
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current in (None, ""):
            return default
    return current


def _parse_price(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "")
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _candidate_rows(payload, *, platform="", engine=""):
    if canonical_marketplace_platform(platform) == "Walmart" and normalize_cache_value(engine) == "walmart":
        value = payload.get("organic_results")
        return list(value) if isinstance(value, list) else []
    keys = (
        "shopping_results",
        "organic_results",
        "items_results",
        "inline_shopping_results",
        "product_results",
        "ebay_results",
        "walmart_results",
    )
    rows = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(value)
    return rows


def normalize_serpapi_records(payload, *, platform, query, collected_at=None, limit=20, source_type="serpapi_live", diagnostics=None, engine=""):
    collected_at = collected_at or datetime.now(timezone.utc).isoformat()
    canonical_platform = canonical_marketplace_platform(platform)
    platform_key = "walmart" if canonical_platform == "Walmart" else ("ebay" if canonical_platform == "eBay" else normalize_cache_value(platform))
    response_engine = normalize_cache_value(
        engine or (payload.get("search_parameters") or {}).get("engine")
    )
    if not response_engine and platform_key == "walmart":
        response_engine = "walmart"
    if (
        platform_key == "walmart"
        and response_engine in {"google_shopping", "google_shopping_light"}
        and not isinstance(payload.get("shopping_results"), list)
        and isinstance(payload.get("organic_results"), list)
    ):
        # Keep stored legacy Walmart fixtures/responses parseable. A real
        # Google Shopping response uses shopping_results.
        response_engine = "walmart"
    candidate_rows = _candidate_rows(payload, platform=platform_key, engine=response_engine)
    walmart_shopping_mode = platform_key == "walmart" and response_engine in {"google_shopping", "google_shopping_light"}
    rejection_counts = {}

    def reject(reason):
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    results = []
    parsed_count = 0
    for row in candidate_rows:
        if not isinstance(row, dict):
            reject("unsupported_record_shape")
            continue
        parsed_count += 1
        if walmart_shopping_mode:
            merchant = str(_first_present(row, ("source", "seller", "seller_name", "merchant"), "")).strip()
            merchant_url = str(_first_present(row, ("link", "product_link", "url"), "")).lower()
            if not (merchant.casefold().startswith("walmart") or "walmart.com/" in merchant_url):
                reject("non_walmart_merchant")
                continue
        primary_offer = row.get("primary_offer") if isinstance(row.get("primary_offer"), dict) else {}
        installment = row.get("installment") if isinstance(row.get("installment"), dict) else None
        raw_price = _first_present(row, ("extracted_price", "price", "price_str"))
        if raw_price in (None, ""):
            raw_price = primary_offer.get("offer_price")
        if raw_price in (None, ""):
            raw_price = primary_offer.get("price")
        price = _parse_price(raw_price)
        if platform_key == "walmart" and not str(_first_present(row, ("title", "name", "product_title"), "")).strip():
            reject("missing_title")
            continue
        if raw_price in (None, ""):
            reject("missing_price")
            continue
        if price is None or (platform_key == "walmart" and price <= 0):
            reject("invalid_price")
            continue
        title = str(_first_present(row, ("title", "name", "product_title"), "Untitled listing"))[:300]
        raw_source_url = _first_present(row, ("product_page_url", "link", "product_link", "serpapi_link", "url"))
        source_url = _safe_url(raw_source_url)
        if platform_key == "walmart" and not source_url:
            reject("missing_source_url")
            continue
        currency = primary_offer.get("currency") or _first_present(row, ("currency",), "USD")
        seller_keys = ("seller", "seller_name", "source", "merchant") if platform_key == "walmart" else ("seller", "source", "merchant")
        normalized = {
            "title": title,
            "product_name": title,
            "query": query,
            "price": price,
            "price_numeric": price,
            "normalized_price": price,
            "currency": str(currency or "USD")[:8],
            "platform": canonical_platform if canonical_platform in {"eBay", "Walmart"} else "Google Shopping",
            "seller": str(_first_present(row, seller_keys, ""))[:150],
            "condition": str(_first_present(row, ("condition", "second_hand_condition", "availability"), "Not stated"))[:120],
            "availability": str(_first_present(row, ("availability", "condition", "second_hand_condition"), "Not stated"))[:120],
            "category": str(_first_present(row, ("category",), "Marketplace"))[:120],
            "item_url": source_url,
            "source_url": source_url,
            "image_url": _first_present(row, ("thumbnail", "image", "image_url")),
            "rating": _first_present(row, ("rating", "reviews_rating", "review_rating")),
            "review_count": _first_present(row, ("reviews", "reviews_count", "review_count")),
            "source_type": source_type,
            "collected_at": collected_at,
            "confidence_level": "medium",
            "confidence": 0.82,
            "data_source_label": source_type,
            "serpapi_position": row.get("position"),
        }
        provider_price_text = str(_first_present(row, ("price", "price_str"), raw_price) or "").strip()
        is_installment = bool(installment) or bool(re.search(r"(?:/\s*(?:mo|month)|per\s+month|monthly)", provider_price_text, re.IGNORECASE))
        normalized["raw_price_text"] = provider_price_text if is_installment else f"{str(currency or 'USD')[:8]} {price:.2f}"
        if is_installment:
            normalized.update(
                price_type="installment",
                billing_period="monthly",
                installment_period=(installment or {}).get("period"),
            )
        if len(results) < int(limit or 20):
            results.append(normalized)
    if diagnostics is not None:
        diagnostics.update({
            "record_field": (
                "organic_results"
                if platform_key == "walmart" and response_engine == "walmart"
                else ("shopping_results" if walmart_shopping_mode else "configured_result_fields")
            ),
            "raw_result_count": len(candidate_rows),
            "parsed_result_count": parsed_count,
            "normalized_result_count": len(results),
            "excluded_result_count": sum(rejection_counts.values()),
            "rejection_counts": rejection_counts,
        })
    return results


def call_serpapi_marketplace(query, *, platform, category="", min_price=None, max_price=None, sort="", location="", country="", store_id="", limit=20, no_cache=False):
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        raise SerpApiError("authentication_error")
    engine = engine_for_platform(platform)
    request_query = " ".join(str(query or "").strip().split())
    normalized_query = normalize_query(query)
    if not normalized_query:
        raise SerpApiError("Enter a product keyword or model before searching.")
    params = {
        "api_key": api_key,
        "engine": engine,
    }
    if platform == "walmart" or engine == "walmart":
        if engine == "walmart":
            params["query"] = request_query or normalized_query
        else:
            params["q"] = f"{request_query or normalized_query} Walmart"
    else:
        params["q"] = request_query or normalized_query
    if location:
        params["location"] = location
    if country:
        params["gl"] = country
    if (platform == "walmart" or engine == "walmart") and store_id:
        params["store_id"] = str(store_id).strip()
    provider_sort = provider_sort_for_platform(platform, sort)
    if provider_sort:
        params["sort"] = provider_sort
    if category:
        params["category"] = category
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price
    params["no_cache"] = "true" if no_cache else "false"
    try:
        response = requests.get(
            SERPAPI_ENDPOINT,
            params=params,
            timeout=int(os.getenv("SERPAPI_TIMEOUT_SECONDS", "20")),
        )
    except requests.Timeout as exc:
        raise SerpApiError("provider_timeout") from exc
    except requests.RequestException as exc:
        # requests exceptions may contain the fully rendered URL, including
        # the API key. Persist only a stable, non-sensitive error code.
        raise SerpApiError("provider_connection_error") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise SerpApiError("SerpApi returned a non-JSON response.") from exc
    if response.status_code >= 400 or payload.get("error"):
        raise SerpApiError(str(payload.get("error") or f"SerpApi HTTP {response.status_code}"))
    collected_at = datetime.now(timezone.utc).isoformat()
    parser_diagnostics = {}
    try:
        records = normalize_serpapi_records(
            payload,
            platform=platform,
            query=normalized_query,
            collected_at=collected_at,
            limit=limit,
            source_type="serpapi_live",
            diagnostics=parser_diagnostics,
            engine=engine,
        )
    except Exception as exc:
        raise SerpApiError("normalization_failed") from exc
    search_information = payload.get("search_information") or {}
    spelling_suggestion = search_information.get("spelling_fix") or search_information.get("spelling_suggestion") or payload.get("spelling_fix")
    return {
        "provider": "serpapi",
        "engine": engine,
        "platform": platform,
        "request_params": {k: v for k, v in params.items() if k != "api_key"},
        "raw_response": payload,
        "normalized_records": records,
        "parser_diagnostics": parser_diagnostics,
        "collected_at": collected_at,
        "spelling_suggestion": spelling_suggestion,
    }


def serpapi_account():
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        raise SerpApiError("authentication_error")
    response = requests.get("https://serpapi.com/account.json", params={"api_key": api_key}, timeout=15)
    try:
        payload = response.json()
    except ValueError as exc:
        raise SerpApiError("SerpApi account endpoint returned a non-JSON response.") from exc
    if response.status_code >= 400 or payload.get("error"):
        raise SerpApiError(str(payload.get("error") or f"SerpApi HTTP {response.status_code}"))
    return payload
