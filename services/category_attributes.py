"""Category-aware listing normalization and local Search Run facets."""

from collections import Counter
from dataclasses import dataclass
import re

from services.platforms import canonical_marketplace_platform


CATEGORY_PROFILES = {
    "smartphones": {"display_name": "Smartphones", "facets": ["brand", "model", "storage", "carrier", "color", "condition"]},
    "laptops": {"display_name": "Laptops", "facets": ["brand", "model", "cpu", "ram", "storage", "screen_size", "gpu", "condition"]},
    "headphones": {"display_name": "Headphones", "facets": ["brand", "model", "headphone_type", "wireless", "noise_cancelling", "color", "condition"]},
    "cameras": {"display_name": "Cameras", "facets": ["brand", "model", "camera_type", "lens_mount", "kit_type", "condition"]},
    "food_and_grocery": {"display_name": "Food and groceries", "facets": ["brand", "condition"]},
    "generic": {"display_name": "General products", "facets": ["product_type", "brand", "condition"]},
}

FACET_COVERAGE_THRESHOLDS = {
    "default": 0.30,
    "product_type": 0.0,
    "brand": 0.50,
    "model": 0.40,
    "cpu": 0.30,
    "ram": 0.30,
    "storage": 0.30,
    "screen_size": 0.30,
    "condition": 0.0,
}

PRODUCT_TYPE_LABELS = {
    "fragrance": "Fragrance",
    "makeup": "Makeup",
    "audio_equipment": "Audio equipment",
    "fashion": "Fashion",
    "other_products": "Other products",
}
SUPPORTED_ENHANCED_CATEGORIES = {"smartphones", "laptops", "headphones", "cameras"}

# Shared canonical mapping used for source category names/IDs from every
# marketplace. Exact IDs can be added here without changing classification or
# facet code; names are also matched deterministically below.
SOURCE_CATEGORY_MAP = {
    "9355": "smartphones",       # eBay Cell Phones & Smartphones
    "177": "laptops",            # eBay Laptops & Netbooks
    "112529": "headphones",      # eBay Headphones
    "31388": "cameras",          # eBay Digital Cameras
    "976759": "food_and_grocery",# Walmart Food
}

FACET_LABELS = {"product_type": "Product type", "brand": "Brand", "model": "Model", "storage": "Storage", "carrier": "Carrier", "color": "Color", "condition": "Condition", "cpu": "Processor", "ram": "Memory", "screen_size": "Screen size", "gpu": "Graphics", "headphone_type": "Headphone type", "wireless": "Wireless", "noise_cancelling": "Noise cancelling", "camera_type": "Camera type", "lens_mount": "Lens mount", "kit_type": "Kit type"}
ACCESSORY_RE = re.compile(r"\b(case|cover|strap|charger|charging cable|replacement cushion|ear cushion|replacement cable|lens only|camera lens)\b", re.I)


@dataclass(frozen=True)
class CategoryDetectionResult:
    category_key: str
    category_display: str
    category_source: str
    category_confidence: float


def _text(*values):
    return " ".join(str(value or "") for value in values).lower()


def _aspect_text(aspects):
    if isinstance(aspects, dict):
        return _text(*aspects.keys(), *aspects.values())
    if isinstance(aspects, list):
        return _text(*aspects)
    return _text(aspects)


def _brand(text):
    product_family_brands = (
        (r"\b(iphone|ipad|airpods?|macbook|apple\s+watch)\b", "Apple"),
        (r"\bgalaxy\s+[a-z0-9]", "Samsung"),
        (r"\bpixel(?:\s+phone)?\s*\d", "Google"),
    )
    for pattern, brand in product_family_brands:
        if re.search(pattern, str(text or ""), re.I):
            return brand
    for name in ("Apple", "Samsung", "Google", "Dell", "Lenovo", "HP", "ASUS", "Acer", "Sony", "Bose", "Canon", "Nikon", "Fujifilm", "Panasonic", "Gucci", "Chanel"):
        if re.search(rf"\b{re.escape(name.lower())}\b", text.lower()):
            return name
    return None


def detect_product_category(query, title, source_category=None, source_aspects=None):
    title_text = _text(title)
    source = _text(source_category)
    if ACCESSORY_RE.search(title_text):
        return CategoryDetectionResult("generic", "General products", "accessory_guard", 0.2)
    rules = {
        "smartphones": r"\b(iphone|galaxy\s+[sz]|pixel\s*(phone)?|smartphone|mobile phone)\b",
        "laptops": r"\b(macbook|dell xps|thinkpad|laptop|notebook computer|chromebook)\b",
        "headphones": r"\b(headphones?|headset|earbuds?|airpods|wh-1000|wf-1000|noise[ -]?cancell?ing)\b",
        "cameras": r"\b(canon eos|nikon z|sony alpha|mirrorless camera|dslr|digital camera)\b",
        "food_and_grocery": r"\b(food|grocery|groceries|candy|snack|fruit|dried|freeze dried|fresh produce|apple chips?)\b",
    }
    mapped = SOURCE_CATEGORY_MAP.get(str(source_category or "").strip())
    if mapped:
        return CategoryDetectionResult(mapped, CATEGORY_PROFILES[mapped]["display_name"], "source_category", 0.98)
    for key, pattern in rules.items():
        if re.search(pattern, source):
            return CategoryDetectionResult(key, CATEGORY_PROFILES[key]["display_name"], "source_category", 0.95)
    for key, pattern in rules.items():
        if re.search(pattern, _aspect_text(source_aspects)):
            return CategoryDetectionResult(key, CATEGORY_PROFILES[key]["display_name"], "source_aspects", 0.85)
    # Result classification is deliberately independent of query intent. A
    # broad query such as "apple" must not make every returned row an Apple
    # device. The query is used only when a source returned no usable title.
    classification_text = title_text or _text(query)
    for key, pattern in rules.items():
        if re.search(pattern, classification_text):
            return CategoryDetectionResult(key, CATEGORY_PROFILES[key]["display_name"], "title", 0.7)
    return CategoryDetectionResult("generic", "General products", "generic_fallback", 0.0)


def classify_query_intent(query):
    """Classify query breadth without forcing a result category."""
    text = re.sub(r"[^a-z0-9]+", " ", str(query or "").lower()).strip()
    tokens = text.split()
    ambiguous_brands = {"apple", "galaxy", "canon", "sony", "samsung", "google", "dell", "lenovo", "hp", "asus", "acer", "bose", "nikon", "fujifilm", "panasonic"}
    if text in ambiguous_brands:
        return "ambiguous"
    detected = detect_product_category(text, text)
    if detected.category_key == "generic" or len(tokens) <= 1:
        return "broad"
    return "specific"


def classify_product_role(title, source_category=None, source_aspects=None, price_text=None):
    text = _text(title, source_category, _aspect_text(source_aspects), price_text)
    if re.search(r"\b(per month|/month|monthly payment|installment|finance(?:d|ing)?)\b", text):
        return {"role": "installment", "source": "price_or_title", "confidence": 0.98}
    # A warranty mentioned as an attribute of a complete device (for example
    # "iPhone 16 ... 12 month Apple warranty") is not itself a service-plan
    # listing. Only explicit warranty products or coverage offers belong here.
    if re.search(r"\b(service plan|protection plan|extended warranty|warranty (?:plan|coverage|service|only|for)|subscription|activation|phone plan|data plan)\b", text):
        return {"role": "service_or_plan", "source": "title", "confidence": 0.95}
    if re.search(r"\b(replacement|repair part|spare part|screen assembly|motherboard|logic board|ear cushion|replacement cable|battery only)\b", text):
        return {"role": "replacement_part", "source": "title", "confidence": 0.92}
    if re.search(r"\b(case|cover|stand|holder|mount|strap|charger|charging cable|selfie microphone|selfie mic|vr viewer|screen protector|camera bag|lens cap)\b", text):
        return {"role": "accessory", "source": "title", "confidence": 0.94}
    if not str(title or "").strip():
        return {"role": "uncertain", "source": "missing_title", "confidence": 0.0}
    return {"role": "complete_product", "source": "default_complete_product", "confidence": 0.75}


def category_scope(items, selected_category_key=None, query_intent_status=None):
    """Return the deterministic persisted comparison scope for a result set."""
    counts = Counter(
        item.get("category_key") or "generic"
        for item in items
        if item.get("product_role") == "complete_product"
    )
    total = sum(counts.values())
    material_counts = Counter({key: count for key, count in counts.items() if count >= 3 or (total and count / total >= 0.15)})
    # Low-confidence generic fallbacks should not split an otherwise coherent,
    # specific product search. Explicit food/unsupported mappings remain
    # distinct canonical categories and still create a multi-category scope.
    if query_intent_status == "specific" and any(key != "generic" for key in material_counts):
        material_counts.pop("generic", None)
    selected = selected_category_key if selected_category_key in counts else None
    keys = sorted(material_counts, key=lambda key: (-material_counts[key], CATEGORY_PROFILES.get(key, CATEGORY_PROFILES["generic"])["display_name"]))
    multi = len(keys) > 1 and not selected
    return {
        "detected_category_keys": keys,
        "category_counts": dict(counts),
        "selected_category_key": selected,
        "category_mode": "multi_category" if multi else "single_category",
        "comparison_enabled": not multi,
    }


def detect_generic_product_type(title, source_category=None, source_aspects=None):
    """Return a broad deterministic type for unsupported/general products."""
    source_text = _text(source_category, _aspect_text(source_aspects))
    title_text = _text(title)
    rules = {
        "fragrance": r"\b(fragrance|perfume|parfum|eau de (?:parfum|toilette)|cologne|body spray)\b",
        "makeup": r"\b(makeup|cosmetic|foundation|pressed powder|face powder|concealer|lipstick|mascara|eyeshadow|blush)\b",
        "audio_equipment": r"\b(amplifier|audio converter|dac|mixer|receiver|audio interface|channel strip|preamplifier)\b",
        "fashion": r"\b(handbag|shoulder bag|tote bag|backpack|wallet|clothing|shirt|dress|jacket|coat|shoes?|sneakers?)\b",
    }
    for evidence, confidence in ((source_text, 0.9), (title_text, 0.75)):
        for key, pattern in rules.items():
            if re.search(pattern, evidence):
                return {"key": key, "display": PRODUCT_TYPE_LABELS[key], "confidence": confidence}
    return {"key": "other_products", "display": PRODUCT_TYPE_LABELS["other_products"], "confidence": 0.0}


def product_type_scope(items, selected_product_type=None):
    counts = Counter(
        (item.get("attributes") or {}).get("product_type")
        for item in items
        if item.get("product_role") == "complete_product" and (item.get("attributes") or {}).get("product_type") not in {None, ""}
    )
    total = sum(counts.values())
    material_counts = Counter({key: count for key, count in counts.items() if count >= 3 or (total and count / total >= 0.15)})
    selected = selected_product_type if selected_product_type in counts else None
    keys = sorted(material_counts, key=lambda key: (-material_counts[key], PRODUCT_TYPE_LABELS.get(key, key)))
    multi = len(keys) > 1 and not selected
    return {
        "detected_product_types": keys,
        "product_type_counts": dict(counts),
        "selected_product_type": selected,
        "multi_product_type": multi,
        "facet_coverage": {
            "eligible_record_count": len(items),
            "populated_record_count": sum(counts.values()),
            "coverage_ratio": (sum(counts.values()) / len(items)) if items else 0.0,
            "distinct_value_count": len(counts),
        },
    }


def _aspect_value(aspects, *keys):
    if not isinstance(aspects, dict):
        return None
    for key, value in aspects.items():
        if str(key).lower().replace(" ", "_") in keys and value not in (None, ""):
            normalized = str(value).strip()
            if normalized.casefold() not in {"not stated", "not specified", "unknown", "n/a", "none", "unbranded"}:
                return normalized
    return None


def _capacity(value, ram=False):
    match = re.search(r"\b(1|2|4|8|16|32|64|128|256|512|1024|2048|4096)\s*(gb|g|tb)\b", str(value or ""), re.I)
    if not match:
        return None
    amount, unit = match.groups()
    if unit.lower() == "tb":
        return f"{int(amount)}TB"
    if amount == "1024" and not ram:
        return "1TB"
    if amount == "2048" and not ram:
        return "2TB"
    if amount == "4096" and not ram:
        return "4TB"
    return f"{amount}GB"


def _first(pattern, text, flags=re.I):
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def extract_smartphone_attributes(title, aspects):
    text = _text(title)
    storage = _capacity(_aspect_value(aspects, "storage", "capacity") or text)
    carrier = _aspect_value(aspects, "carrier", "network")
    if not carrier:
        carrier = next((name for name in ("Unlocked", "AT&T", "Verizon", "T-Mobile") if name.lower() in text), None)
    model = _aspect_value(aspects, "model") or _first(r"\b((?:apple\s+)?iphone\s+\d+(?:\s+(?:pro max|pro|plus|air|se))?|galaxy\s+[sz]\s*\w+|pixel\s+\w+)\b", title)
    return {key: value for key, value in {"brand": _aspect_value(aspects, "brand") or _brand(title), "model": model, "storage": storage, "carrier": carrier or "Not specified", "color": _aspect_value(aspects, "color")}.items() if value}


def extract_laptop_attributes(title, aspects):
    text = _text(title)
    cpu = _aspect_value(aspects, "cpu", "processor") or _first(r"\b((?:intel\s+core\s+(?:ultra\s+)?i?[3579]|amd\s+ryzen\s+[3579]|(?:apple\s+)?m[1-4]))\b", title)
    ram = _capacity(_aspect_value(aspects, "ram", "memory") or _first(r"\b((?:8|16|32|64|128)\s*gb)\s*(?:ram|memory)\b", text) or "", ram=True)
    storage = _capacity(_aspect_value(aspects, "storage", "ssd") or _first(r"\b((?:256|512)\s*gb|(?:1|2|4)\s*tb)\b", text) or "")
    screen = _aspect_value(aspects, "screen_size", "screen") or _first(r"\b(13|14|15\.6|16|17)[ -]?inch\b", text)
    if screen:
        screen = screen.replace(" ", "") + "-inch"
    gpu = _aspect_value(aspects, "gpu", "graphics") or _first(r"\b((?:nvidia|geforce|radeon|intel iris)[\w\s-]*)", title)
    model = _aspect_value(aspects, "model") or _first(r"\b((?:macbook(?:\s+(?:air|pro))?|dell\s+xps\s*\d+|thinkpad\s*\w+))\b", title)
    return {key: value for key, value in {"brand": _aspect_value(aspects, "brand") or _brand(title), "model": model, "cpu": cpu, "ram": ram, "storage": storage, "screen_size": screen, "gpu": gpu}.items() if value}


def extract_headphone_attributes(title, aspects):
    text = _text(title)
    kind = _aspect_value(aspects, "headphone_type", "type") or ("Earbuds" if re.search(r"\b(earbuds?|airpods)\b", text) else "Over-ear" if re.search(r"\bover[ -]?ear\b", text) else "In-ear" if "in-ear" in text else "Headset" if "headset" in text else None)
    wireless = _aspect_value(aspects, "wireless") or ("Yes" if re.search(r"\b(wireless|bluetooth)\b", text) else "No" if "wired" in text else "Not specified")
    nc = _aspect_value(aspects, "noise_cancelling", "noise_canceling") or ("Yes" if re.search(r"noise[ -]?cancell?ing|\banc\b", text) else "Not specified")
    return {key: value for key, value in {"brand": _aspect_value(aspects, "brand") or _brand(title), "model": _aspect_value(aspects, "model") or _first(r"\b((?:sony\s+)?(?:wh|wf)-?1000\w+|airpods\s*(?:pro)?\s*\d*)\b", title), "headphone_type": kind or "Not specified", "wireless": "Yes" if str(wireless).lower() in {"yes", "true", "wireless"} else "No" if str(wireless).lower() in {"no", "false", "wired"} else "Not specified", "noise_cancelling": "Yes" if str(nc).lower() in {"yes", "true", "anc"} else "No" if str(nc).lower() in {"no", "false"} else "Not specified", "color": _aspect_value(aspects, "color")}.items() if value}


def extract_camera_attributes(title, aspects):
    text = _text(title)
    kind = _aspect_value(aspects, "camera_type", "type") or ("Mirrorless" if "mirrorless" in text or re.search(r"\b(eos r|nikon z|alpha)\b", text) else "DSLR" if "dslr" in text else "Compact" if "compact" in text else None)
    kit = _aspect_value(aspects, "kit_type", "kit") or ("Body only" if re.search(r"\bbody only\b", text) else "Multi-lens kit" if len(re.findall(r"\b\d+\s*mm\b", text)) > 1 else "Lens kit" if re.search(r"\b(with|kit)\b.*\b(lens|\d+\s*mm)\b", text) else "Not specified")
    return {key: value for key, value in {"brand": _aspect_value(aspects, "brand") or _brand(title), "model": _aspect_value(aspects, "model") or _first(r"\b((?:canon\s+)?eos\s+\w+|nikon\s+z\s*\w+|sony\s+alpha\s*\w*)\b", title), "camera_type": kind or "Not specified", "lens_mount": _aspect_value(aspects, "lens_mount", "mount"), "kit_type": kit}.items() if value}


def extract_generic_attributes(title, aspects):
    return {"brand": _aspect_value(aspects, "brand") or _brand(title)} if (_aspect_value(aspects, "brand") or _brand(title)) else {}


def extract_category_attributes(title, source_aspects, category_key):
    extractors = {"smartphones": extract_smartphone_attributes, "laptops": extract_laptop_attributes, "headphones": extract_headphone_attributes, "cameras": extract_camera_attributes}
    return extractors.get(category_key, extract_generic_attributes)(title or "", source_aspects or {})


def enrich_listing_category(row, query=""):
    detected = detect_product_category(query, row.get("title") or row.get("product_name"), row.get("category") or row.get("source_category"), row.get("aspects") or row.get("source_aspects"))
    attributes = dict(row.get("attributes") or {})
    attributes.update({key: value for key, value in extract_category_attributes(row.get("title") or row.get("product_name"), row.get("aspects") or row.get("source_aspects"), detected.category_key).items() if value})
    if detected.category_key == "generic":
        product_type = detect_generic_product_type(row.get("title") or row.get("product_name"), row.get("category") or row.get("source_category"), row.get("aspects") or row.get("source_aspects"))
        attributes.update(product_type=product_type["key"], product_type_display=product_type["display"])
    role = classify_product_role(row.get("title") or row.get("product_name"), row.get("category") or row.get("source_category"), row.get("aspects") or row.get("source_aspects"), row.get("raw_price_text") or row.get("price_text"))
    row.update(category_key=detected.category_key, category_display=detected.category_display, category_source=detected.category_source, category_confidence=detected.category_confidence, product_type_key=attributes.get("product_type"), product_type_display=attributes.get("product_type_display"), product_role=role["role"], classification_confidence=min(1.0, max(detected.category_confidence, role["confidence"])), classification_source=f"{detected.category_source}+{role['source']}", brand=attributes.get("brand") or row.get("brand"), attributes=attributes)
    return row


def _sort_value(value):
    match = re.match(r"^(\d+(?:\.\d+)?)\s*(GB|TB|inch)?$", str(value), re.I)
    if not match:
        return (1, str(value).lower())
    number, unit = match.groups()
    multiplier = 1024 if (unit or "").lower() == "tb" else 1
    return (0, float(number) * multiplier)


def canonical_facet_value(key, value):
    """Return the stable comparison value used by a facet filter.

    Marketplace titles frequently repeat the manufacturer in the model field
    (for example, ``Apple iPhone 14 Pro`` versus ``iPhone 14 Pro``).  Keep the
    source wording for display while using one canonical model identity for
    counting and filtering.
    """
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if key != "model":
        return text
    text = re.sub(r"^(apple|samsung|google|sony|canon|nikon|dell|lenovo|hp|asus)\s+", "", text, flags=re.I)
    return text.casefold()


def generate_available_facets(items, category_key, selected_facets=None):
    profile = CATEGORY_PROFILES.get(category_key, CATEGORY_PROFILES["generic"])
    selected_facets = selected_facets or {}
    facets = {}
    for key in profile["facets"]:
        facet_items = items
        if key == "model" and selected_facets.get("brand"):
            selected_brands = selected_facets["brand"] if isinstance(selected_facets["brand"], list) else [selected_facets["brand"]]
            facet_items = [item for item in items if canonical_facet_value("brand", item.get("brand")) in selected_brands]
        values = []
        for item in facet_items:
            attributes = item.get("attributes") or {}
            value = (item.get("condition_display") or item.get("condition")) if key == "condition" else item.get("brand") if key == "brand" else attributes.get("product_type_display") if key == "product_type" else attributes.get(key)
            if value and str(value).strip().casefold() not in {"not stated", "not specified", "unknown", "n/a", "none", "unbranded"}:
                values.append((canonical_facet_value(key, value), str(value)))
        counts = Counter(canonical for canonical, _display in values)
        eligible_count = len(facet_items)
        populated_count = len(values)
        coverage_ratio = populated_count / eligible_count if eligible_count else 0.0
        threshold = FACET_COVERAGE_THRESHOLDS.get(key, FACET_COVERAGE_THRESHOLDS["default"])
        if len(counts) < 2 or coverage_ratio < threshold:
            continue
        displays = {}
        for canonical, display in values:
            displays.setdefault(canonical, display)
        facets[key] = {"label": FACET_LABELS.get(key, key.replace("_", " ").title()), "type": "multi_select", "eligible_record_count": eligible_count, "populated_record_count": populated_count, "coverage_ratio": coverage_ratio, "distinct_value_count": len(counts), "values": [{"value": displays[value], "filter_value": value, "count": count} for value, count in sorted(counts.items(), key=lambda pair: _sort_value(displays[pair[0]]))]}
    return facets


def apply_category_filters(items, selected_facets, min_price=None, max_price=None, platform=None, condition=None):
    selected_facets = selected_facets or {}
    canonical_platform = canonical_marketplace_platform(platform) if platform else None
    def selected_values(key):
        value = selected_facets.get(key, [])
        return value if isinstance(value, list) else [value]
    kept = []
    for item in items:
        if canonical_platform and canonical_marketplace_platform(item.get("platform"), item.get("source_type")) != canonical_platform:
            continue
        if condition and item.get("condition_display") != condition:
            continue
        price = item.get("normalized_price")
        if min_price is not None and (price is None or price < min_price):
            continue
        if max_price is not None and (price is None or price > max_price):
            continue
        attributes = item.get("attributes") or {}
        if any(canonical_facet_value(key, item.get("brand") if key == "brand" else item.get("condition_display") if key == "condition" else attributes.get("product_type_display") if key == "product_type" else attributes.get(key)) not in values for key in selected_facets for values in [selected_values(key)] if values):
            continue
        kept.append(item)
    return kept
