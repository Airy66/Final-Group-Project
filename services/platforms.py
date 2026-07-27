"""Canonical marketplace identities shared by providers and view-model filters."""

import re


def canonical_marketplace_platform(value, source_type=None):
    """Return one stable display identity for supported marketplace aliases."""
    raw_value = str(value or "").strip()
    platform_key = re.sub(r"[^a-z0-9]+", "_", raw_value.casefold()).strip("_")
    source_key = re.sub(r"[^a-z0-9]+", "_", str(source_type or "").casefold()).strip("_")
    if platform_key in {"walmart", "walmart_marketplace", "walmart_com"} or "walmart" in source_key:
        return "Walmart"
    if platform_key in {"ebay", "ebay_marketplace", "ebay_com"} or "ebay" in source_key:
        return "eBay"
    return raw_value or "Unknown"
