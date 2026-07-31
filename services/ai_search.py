"""OpenAI-assisted public price discovery.

The model is instructed to include only prices it can verify through web-search
source URLs.  Results remain decision support and are never purchasing advice.
"""

from datetime import datetime, timezone
import json
import os
import re


class AISearchError(RuntimeError):
    pass


def _extract_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise AISearchError("The AI response did not contain structured price results.")
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AISearchError("The AI response could not be parsed safely.") from exc
    if not isinstance(value, list):
        raise AISearchError("The AI response had an unexpected format.")
    return value


def _extract_json_object(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise AISearchError("The AI response did not contain a JSON object.")
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AISearchError("The AI response could not be parsed as JSON.") from exc
    if not isinstance(value, dict):
        raise AISearchError("The AI response had an unexpected object format.")
    return value


def search_prices(keyword, limit=10):
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("AI_API_KEY")
    if not api_key:
        raise AISearchError("AI discovery is unavailable because OPENAI_API_KEY or AI_API_KEY is not configured.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AISearchError("AI Web Search requires the OpenAI Python package.") from exc

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    prompt = f"""
Search the public web for up to {limit} currently visible retail listings matching: {keyword!r}.
Never estimate, infer, average, or invent a price. Include an item only when its exact price and
public product/source URL are visible in a web-search source. Return ONLY a JSON array. Each object
must contain: title, price (number), currency, platform, seller, condition, source_url, image_url,
and confidence_level (high, medium, or low). Omit unverifiable items. Do not include prose.
""".strip()
    try:
        response = OpenAI(api_key=api_key).responses.create(
            model=model,
            tools=[{"type": "web_search_preview"}],
            input=prompt,
        )
        raw = response.output_text
        data = _extract_json(raw)
    except AISearchError:
        raise
    except Exception as exc:
        raise AISearchError(f"AI Web Search could not complete ({exc.__class__.__name__}). Please try again later.") from exc

    collected_at = datetime.now(timezone.utc).isoformat()
    results = []
    for row in data[:limit]:
        try:
            price = float(row.get("price"))
        except (TypeError, ValueError):
            continue
        source_url = str(row.get("source_url") or "").strip()
        if not source_url.startswith(("http://", "https://")):
            continue
        results.append({
            "title": str(row.get("title") or "Untitled listing")[:300],
            "price": price,
            "price_numeric": price,
            "currency": str(row.get("currency") or "USD")[:8],
            "platform": str(row.get("platform") or "Web merchant")[:100],
            "seller": str(row.get("seller") or row.get("platform") or "Unknown")[:150],
            "condition": str(row.get("condition") or "Not stated")[:80],
            "item_url": source_url,
            "source_url": source_url,
            "image_url": row.get("image_url"),
            "source_type": "openai_web_search",
            "collected_at": collected_at,
            "confidence_level": row.get("confidence_level") if row.get("confidence_level") in {"high", "medium", "low"} else "medium",
        })
    return results, model, raw[:1000]


def deterministic_summary(keyword, items):
    prices = []
    for item in items:
        try:
            prices.append(float(item.get("price")))
        except (TypeError, ValueError):
            continue
    platforms = sorted({str(item.get("platform") or "Unknown") for item in items})
    if not prices:
        return f"No priced records are available for {keyword}. Try a broader query or another source."
    variation = "limited" if max(prices) - min(prices) < max(1, sum(prices) / len(prices) * .15) else "moderate"
    return (f"{len(prices)} priced records for {keyword} span {len(platforms)} platform(s). "
            f"The observed range is {min(prices):.2f}–{max(prices):.2f}, averaging {sum(prices)/len(prices):.2f}; "
            f"this suggests {variation} cross-platform price variation.")


def summarize_market(keyword, items):
    """Return a backend-generated summary and an explicit generation mode."""
    fallback = deterministic_summary(keyword, items)
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("AI_API_KEY")
    if not api_key:
        return fallback, "deterministic_fallback", None
    try:
        from openai import OpenAI
        compact = [{k: row.get(k) for k in ("platform", "title", "price", "currency", "category", "brand", "availability")} for row in items[:20]]
        response = OpenAI(api_key=api_key).responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            input=("Write a concise two-sentence retail market summary grounded only in these records. "
                   "Do not invent facts or give purchasing advice.\n" + json.dumps(compact, ensure_ascii=False)),
        )
        text = (response.output_text or "").strip()
        return (text or fallback), ("api_generated" if text else "deterministic_fallback"), None
    except Exception as exc:
        return fallback, "deterministic_fallback", exc.__class__.__name__


_MISSING_FACT_VALUES = {"", "not provided", "not stated", "not specified", "not specified by source", "unknown", "n/a", "none"}


def _reported_condition(item):
    value = item.get("condition_display") or item.get("condition")
    text = str(value or "").strip()
    return text if text.casefold() not in _MISSING_FACT_VALUES else None


def _condition_coverage(items):
    total = len(items)
    provided = sum(1 for item in items if _reported_condition(item))
    return provided, total


def rule_based_market_summary(keyword, items):
    prices = []
    for item in items:
        try:
            value = item.get("normalized_price")
            if value is None:
                value = item.get("total_price", item.get("price"))
            prices.append(float(value))
        except (TypeError, ValueError):
            continue
    platforms = {str(item.get("platform") or "Unknown") for item in items}
    if not prices:
        return f"No normalized price records are available for {keyword}. Try a broader query or another source."
    low, high, average = min(prices), max(prices), sum(prices) / len(prices)
    condition_count, record_count = _condition_coverage(items)
    condition_sentence = (f"Condition data is provided for {condition_count} of {record_count} records. " if record_count else "")
    return (f"{len(prices)} records for {keyword} span {len(platforms)} platform(s). "
            f"The lowest normalized price is {low:.2f}, the highest is {high:.2f}, and the average is {average:.2f}. "
            f"The observed price range is {high-low:.2f}. {condition_sentence}"
            "Compare like-for-like variants and seller quality before deciding.")


def ground_market_summary(keyword, summary, items):
    """Replace an AI summary when its condition claim contradicts record coverage."""
    provided, total = _condition_coverage(items)
    if not total or provided / total < 0.8:
        return summary, False
    unsupported_phrases = ("often not provided", "frequently not provided", "mostly not provided", "condition data is limited", "conditions are unavailable")
    for sentence in re.split(r"(?<=[.!?])\s+", str(summary or "")):
        lowered = sentence.casefold()
        if "condition" in lowered and any(phrase in lowered for phrase in unsupported_phrases):
            return rule_based_market_summary(keyword, items), True
    return summary, False


def _call_gemini(api_key, model, prompt, timeout_ms):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout_ms))
    response = client.models.generate_content(model=model, contents=prompt)
    return (response.text or "").strip()


def _gemini_fallback_reason(exc):
    text = f"{exc.__class__.__name__} {exc}".lower()
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 404 or "404" in text or ("model" in text and ("not found" in text or "no longer available" in text)):
        return "model_unavailable"
    if status == 503 or "503" in text or "unavailable" in text:
        return "service_unavailable"
    if status == 429 or "429" in text or "quota" in text or "resource_exhausted" in text:
        return "quota_exceeded"
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text or "deadline" in text:
        return "timeout"
    if "api_key_invalid" in text or "api key not valid" in text or "invalid api key" in text:
        return "invalid_key"
    if "permission" in text or "403" in text:
        return "permission_denied"
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return "sdk_unavailable"
    if isinstance(exc, (AISearchError, json.JSONDecodeError, ValueError)):
        return "invalid_response"
    return "api_error"


def summarize_market_gemini(keyword, items):
    """Generate through Gemini on the backend, with a deterministic local fallback."""
    fallback = rule_based_market_summary(keyword, items)
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    if not api_key:
        return fallback, "rule_based_fallback", "missing_key"
    condition_count, record_count = _condition_coverage(items)
    compact = [{key: row.get(key) for key in ("platform", "title", "product_name", "normalized_price", "price", "currency", "category", "condition_display", "condition", "confidence_level")} for row in items[:25]]
    prompt = ("You are assisting an e-commerce price intelligence system. Write a concise factual market summary in no more than four short sentences. "
              "grounded only in the supplied records. Mention the cheapest option, price spread, platform coverage, "
              "and a limitation only when supported by the supplied counts. Treat condition and availability as separate fields. "
              "Never claim condition is often missing when it is provided for most records. Use plain text only: no Markdown, headings, bullets, or asterisks. "
              "Do not invent prices or expose system configuration.\n"
              f"Query: {keyword}\nCondition coverage: {condition_count} of {record_count} records.\nRule-based statistics: {fallback}\nRecords: {json.dumps(compact, ensure_ascii=False, default=str)}")
    try:
        text = _call_gemini(api_key, model, prompt, int(os.getenv("GEMINI_TIMEOUT_MS", "45000")))
        if not text:
            return fallback, "rule_based_fallback", "empty_response"
        grounded, replaced = ground_market_summary(keyword, text, items)
        if replaced:
            return grounded, "rule_based_fallback", "unsupported_condition_claim"
        return grounded, "gemini_api", None
    except Exception as exc:
        return fallback, "rule_based_fallback", _gemini_fallback_reason(exc)


def predict_price_gemini(product_label, tracking_scope, platform_scope, source_label, snapshots):
    """Return a Gemini prediction payload or a deterministic fallback."""
    fallback = {
        "predicted_average_price": None,
        "predicted_direction": "stable",
        "confidence_level": "low",
        "reason": "Gemini prediction is unavailable.",
    }
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    if not api_key:
        return fallback, "ai_unavailable", "missing_key"

    compact = []
    # Forecasting should be grounded in the most recent observations. The
    # caller supplies chronological history, so retain the newest window.
    for row in snapshots[-20:]:
        compact.append({
            "date": str(row.get("collected_at") or row.get("created_at") or ""),
            "average_price": row.get("average_price"),
            "low": row.get("lowest_price"),
            "high": row.get("highest_price"),
            "record_count": row.get("record_count"),
            "data_quality": row.get("data_quality"),
        })
    prompt = (
        "You are helping a price intelligence system. Return JSON only with keys "
        '"predicted_average_price", "predicted_direction", "confidence_level", and "reason". '
        'Predicted direction must be one of "increase", "decrease", or "stable". '
        "Use only the supplied snapshot summaries and do not invent values.\n"
        f"product_label: {product_label}\n"
        f"tracking_scope: {tracking_scope}\n"
        f"platform_scope: {platform_scope}\n"
        f"source_label: {source_label}\n"
        f"snapshot_count: {len(snapshots)}\n"
        f"snapshots: {json.dumps(compact, ensure_ascii=False, default=str)}"
    )
    try:
        text = _call_gemini(api_key, model, prompt, int(os.getenv("GEMINI_TIMEOUT_MS", "45000")))
        payload = _extract_json_object(text)
        predicted_average_price = float(payload.get("predicted_average_price"))
        predicted_direction = payload.get("predicted_direction")
        confidence_level = payload.get("confidence_level")
        reason = str(payload.get("reason") or "")[:500]
        if predicted_direction not in {"increase", "decrease", "stable"}:
            raise ValueError("Bad predicted direction")
        if confidence_level not in {"low", "medium", "high"}:
            raise ValueError("Bad confidence level")
        return {
            "predicted_average_price": round(predicted_average_price, 2),
            "predicted_direction": predicted_direction,
            "confidence_level": confidence_level,
            "reason": reason or "Gemini prediction returned without explanation.",
        }, "gemini_api", None
    except Exception as exc:
        return fallback, "ai_unavailable", _gemini_fallback_reason(exc)
