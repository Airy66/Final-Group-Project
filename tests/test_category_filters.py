from services.category_attributes import (
    apply_category_filters,
    category_scope,
    classify_query_intent,
    detect_product_category,
    enrich_listing_category,
    extract_category_attributes,
    generate_available_facets,
    product_type_scope,
)


def test_category_detection_for_supported_products_and_generic_fallback():
    assert detect_product_category("iPhone 17 Pro", "Apple iPhone 17 Pro").category_key == "smartphones"
    assert detect_product_category("Dell XPS 15", "Dell XPS 15 laptop").category_key == "laptops"
    assert detect_product_category("Sony WH-1000XM5", "Sony WH-1000XM5 headphones").category_key == "headphones"
    assert detect_product_category("Canon EOS R6", "Canon EOS R6 mirrorless camera").category_key == "cameras"
    assert detect_product_category("garden trowel", "steel garden trowel").category_key == "generic"


def test_accessory_guards_prevent_false_product_categories():
    assert detect_product_category("iPhone 17 Pro", "iPhone 17 Pro phone case").category_key == "generic"
    assert detect_product_category("Canon EOS R6", "Canon EOS camera strap").category_key == "generic"
    assert detect_product_category("Dell XPS", "Dell laptop charger").category_key == "generic"


def test_phone_battery_health_wording_remains_a_smartphone():
    title = "Apple iPhone 16 - 128 GB 256 GB - All Colors - Unlocked - 90%+ Battery Very Good"
    assert detect_product_category("iPhone 16", title).category_key == "smartphones"
    assert enrich_listing_category({"title": title}, "iPhone 16")["product_role"] == "complete_product"


def test_attribute_extraction_normalizes_values_and_prefers_source_aspects():
    phone = extract_category_attributes("iPhone 17 Pro 128GB", {"Storage": "256 GB", "Carrier": "Unlocked"}, "smartphones")
    laptop = extract_category_attributes("Dell XPS 15 Intel Core i7 16GB Memory 1TB SSD", {}, "laptops")
    headphones = extract_category_attributes("Sony WH-1000XM5 wireless noise cancelling headphones", {}, "headphones")
    camera = extract_category_attributes("Canon EOS R6 body only", {}, "cameras")
    assert phone["storage"] == "256GB" and phone["carrier"] == "Unlocked"
    assert laptop["ram"] == "16GB" and laptop["storage"] == "1TB"
    assert headphones["wireless"] == "Yes" and headphones["noise_cancelling"] == "Yes"
    assert camera["kit_type"] == "Body only"


def test_product_family_infers_brand_when_marketplace_brand_is_missing():
    iphone = enrich_listing_category(
        {"title": "iPhone 14 128GB Unlocked", "aspects": {"Brand": "Not stated"}},
        "iPhone 14",
    )
    galaxy = enrich_listing_category({"title": "Galaxy S24 256GB", "brand": "Unknown"}, "Galaxy S24")
    assert iphone["brand"] == "Apple"
    assert iphone["attributes"]["brand"] == "Apple"
    assert galaxy["brand"] == "Samsung"


def test_unknown_brand_placeholders_are_not_exposed_as_brand_facets():
    rows = [
        {"brand": "Apple", "condition": "New", "attributes": {}},
        {"brand": "Not stated", "condition": "Used", "attributes": {}},
    ]
    facets = generate_available_facets(rows, "smartphones")
    assert "brand" not in facets


def test_dynamic_facets_use_actual_values_and_sort_capacities_logically():
    rows = [
        enrich_listing_category({"title": "Apple iPhone 17 Pro 1TB", "condition": "New", "platform": "eBay", "price": 1200}, "iPhone 17 Pro"),
        enrich_listing_category({"title": "Apple iPhone 17 Pro 256GB", "condition": "Used", "platform": "eBay", "price": 900}, "iPhone 17 Pro"),
        enrich_listing_category({"title": "Apple iPhone 17 Pro 512GB", "condition": "New", "platform": "Walmart", "price": 1100}, "iPhone 17 Pro"),
    ]
    facets = generate_available_facets(rows, "smartphones")
    assert [entry["value"] for entry in facets["storage"]["values"]] == ["256GB", "512GB", "1TB"]
    assert "ram" not in facets
    assert facets["condition"]["values"][0]["count"] == 2


def test_filter_logic_is_or_within_facet_and_and_across_facets():
    rows = [
        {"platform": "eBay", "condition_display": "New", "normalized_price": 700, "attributes": {"storage": "256GB"}, "brand": "Apple"},
        {"platform": "eBay", "condition_display": "New", "normalized_price": 800, "attributes": {"storage": "512GB"}, "brand": "Apple"},
        {"platform": "Walmart", "condition_display": "Used", "normalized_price": 650, "attributes": {"storage": "256GB"}, "brand": "Apple"},
        {"platform": "eBay", "condition_display": "New", "normalized_price": 750, "attributes": {}, "brand": "Apple"},
    ]
    filtered = apply_category_filters(rows, {"storage": ["256GB", "512GB"]}, platform="eBay", condition="New")
    assert [row["normalized_price"] for row in filtered] == [700, 800]
    assert len(apply_category_filters(rows, {})) == 4


def test_generic_facets_do_not_expose_supported_category_attributes():
    rows = [
        enrich_listing_category({"title": "Acme garden trowel", "condition": "New"}, "garden trowel"),
        enrich_listing_category({"title": "Bravo garden trowel", "condition": "Used"}, "garden trowel"),
    ]
    facets = generate_available_facets(rows, "generic")
    assert set(facets).issubset({"brand", "condition"})


def test_ambiguous_apple_results_are_classified_independently():
    rows = [
        enrich_listing_category({"title": "Apple iPhone 12 128GB"}, "apple"),
        enrich_listing_category({"title": "Apple MacBook Air M2 laptop"}, "apple"),
        enrich_listing_category({"title": "Freeze Dried Fuji Apples snack"}, "apple"),
        enrich_listing_category({"title": "Green Apple Candy"}, "apple"),
    ]
    assert classify_query_intent("apple") == "ambiguous"
    assert [row["category_key"] for row in rows] == [
        "smartphones", "laptops", "food_and_grocery", "food_and_grocery"
    ]
    scope = category_scope(rows)
    assert scope["category_mode"] == "multi_category"
    assert scope["comparison_enabled"] is False
    assert scope["category_counts"] == {"smartphones": 1, "laptops": 1, "food_and_grocery": 2}


def test_category_selection_creates_coherent_dynamic_facet_scope():
    rows = [
        enrich_listing_category({"title": "Apple iPhone 12 128GB", "condition": "New", "aspects": {"Carrier": "Unlocked"}}, "apple"),
        enrich_listing_category({"title": "Apple iPhone 13 256GB", "condition": "Used", "aspects": {"Carrier": "Verizon"}}, "apple"),
        enrich_listing_category({"title": "MacBook Air M2 8GB Memory 256GB", "condition": "New"}, "apple"),
        enrich_listing_category({"title": "MacBook Pro M3 16GB Memory 512GB", "condition": "Used"}, "apple"),
        enrich_listing_category({"title": "Freeze Dried Apples", "condition": "New"}, "apple"),
    ]
    phone_scope = category_scope(rows, "smartphones")
    phone_facets = generate_available_facets([row for row in rows if row["category_key"] == "smartphones"], "smartphones")
    laptop_facets = generate_available_facets([row for row in rows if row["category_key"] == "laptops"], "laptops")
    food_facets = generate_available_facets([row for row in rows if row["category_key"] == "food_and_grocery"], "food_and_grocery")
    assert phone_scope["comparison_enabled"] is True
    assert {"model", "storage", "carrier"}.issubset(phone_facets)
    assert {"model", "cpu", "ram", "storage"}.issubset(laptop_facets)
    assert set(food_facets).issubset({"brand", "condition"})


def test_generic_product_types_distinguish_gucci_fragrance_and_makeup():
    fragrance = enrich_listing_category({"title": "Gucci Bloom Eau de Parfum perfume spray"}, "Gucci")
    makeup = enrich_listing_category({"title": "Gucci pressed powder makeup foundation"}, "Gucci")
    assert fragrance["category_key"] == makeup["category_key"] == "generic"
    assert fragrance["attributes"]["product_type"] == "fragrance"
    assert makeup["attributes"]["product_type"] == "makeup"
    scope = product_type_scope([fragrance, makeup])
    assert scope["multi_product_type"] is True
    assert scope["facet_coverage"] == {
        "eligible_record_count": 2,
        "populated_record_count": 2,
        "coverage_ratio": 1.0,
        "distinct_value_count": 2,
    }


def test_sparse_model_facet_is_hidden_using_complete_scope_coverage():
    rows = []
    for index in range(23):
        title = f"Lenovo ThinkPad T{index} laptop" if index < 3 else f"Laptop listing {index}"
        rows.append(enrich_listing_category({"title": title, "aspects": {"Brand": "Lenovo" if index < 12 else "Dell"}}, "laptop"))
    facets = generate_available_facets(rows, "laptops")
    assert "model" not in facets
    assert facets["brand"]["eligible_record_count"] == 23
    assert facets["brand"]["populated_record_count"] == 23
    assert facets["brand"]["coverage_ratio"] == 1.0


def test_model_facet_depends_on_selected_brand_and_clears_safely():
    rows = [
        enrich_listing_category({"title": "Lenovo ThinkPad X1 laptop"}, "laptop"),
        enrich_listing_category({"title": "Lenovo ThinkPad T14 laptop"}, "laptop"),
        enrich_listing_category({"title": "Dell XPS 13 laptop"}, "laptop"),
        enrich_listing_category({"title": "Dell XPS 15 laptop"}, "laptop"),
    ]
    all_models = generate_available_facets(rows, "laptops")
    lenovo_models = generate_available_facets(rows, "laptops", selected_facets={"brand": ["Lenovo"]})
    assert {entry["value"] for entry in all_models["model"]["values"]} == {"ThinkPad X1", "ThinkPad T14", "Dell XPS 13", "Dell XPS 15"}
    assert {entry["value"] for entry in lenovo_models["model"]["values"]} == {"ThinkPad X1", "ThinkPad T14"}
    assert lenovo_models["model"]["eligible_record_count"] == 2


def test_product_roles_separate_complete_products_accessories_plans_and_installments():
    rows = [
        enrich_listing_category({"title": "Apple iPhone 15 128GB"}, "smartphone"),
        enrich_listing_category({"title": "Smartphone desk stand holder"}, "smartphone"),
        enrich_listing_category({"title": "Phone protection service plan"}, "smartphone"),
        enrich_listing_category({"title": "Apple iPhone 15", "raw_price_text": "$29/month"}, "smartphone"),
        enrich_listing_category({"title": "Replacement phone screen assembly"}, "smartphone"),
    ]
    assert [row["product_role"] for row in rows] == [
        "complete_product", "accessory", "service_or_plan", "installment", "replacement_part"
    ]
    assert all(row["classification_source"] and row["classification_confidence"] >= 0 for row in rows)


def test_warranty_attribute_does_not_turn_a_complete_device_into_a_service_plan():
    device = enrich_listing_category(
        {"title": "Apple iPhone 16 128GB New Sealed 12 month Apple Warranty"},
        "smartphone",
    )
    explicit_plan = enrich_listing_category(
        {"title": "Extended warranty plan for Apple iPhone 16"},
        "smartphone",
    )
    assert device["product_role"] == "complete_product"
    assert explicit_plan["product_role"] == "service_or_plan"
