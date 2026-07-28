import os
import random
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DATABASE", "precision_curator_production")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI is not set in .env")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
client.admin.command("ping")

db = client[MONGO_DB_NAME]

market_records = db["market_records"]
evidence_records = db["evidence_records"]
audit_logs = db["audit_logs"]

# Clear old synthetic demo records
market_records.delete_many({"demo_mode": True})
evidence_records.delete_many({"demo_mode": True})
audit_logs.delete_many({"demo_mode": True})

platforms = [
    "eBay",
    "Amazon Demo",
    "Shopee Demo",
    "Douyin Demo",
    "Xiaohongshu Demo",
]

products = {
    "iphone17": {
        "category": "Smartphones",
        "brand": "Apple",
        "base_price": 1299,
        "names": [
            "iPhone 17 128GB",
            "iPhone 17 256GB",
            "iPhone 17 Pro 256GB",
            "iPhone 17 Pro Max 512GB",
        ],
    },
    "wireless earbuds": {
        "category": "Audio",
        "brand": "SoundCore",
        "base_price": 129,
        "names": [
            "Wireless Earbuds Pro",
            "Noise Cancelling Earbuds",
            "Bluetooth Earbuds 2026",
            "Compact Wireless Earbuds",
        ],
    },
    "protein powder": {
        "category": "Health",
        "brand": "NutriFit",
        "base_price": 58,
        "names": [
            "Whey Protein Powder 1kg",
            "Plant Protein Powder",
            "High Protein Nutrition Powder",
            "Sports Protein Supplement",
        ],
    },
    "laptop": {
        "category": "Computers",
        "brand": "Lenovo",
        "base_price": 899,
        "names": [
            "Student Laptop 14 inch",
            "Business Laptop 16GB RAM",
            "Ultrabook 512GB SSD",
            "Creator Laptop 15 inch",
        ],
    },
    "smartwatch": {
        "category": "Wearables",
        "brand": "FitTime",
        "base_price": 229,
        "names": [
            "Smartwatch Series 9",
            "Fitness Smartwatch",
            "GPS Health Watch",
            "Waterproof Smartwatch",
        ],
    },
}

records = []
now = datetime.now(timezone.utc)

for query, meta in products.items():
    for i in range(15):
        platform = random.choice(platforms)
        product_name = random.choice(meta["names"])
        price_variation = random.uniform(0.72, 1.38)
        base_price = round(meta["base_price"] * price_variation, 2)

        if platform in ["Douyin Demo", "Xiaohongshu Demo"]:
            currency = "CNY"
            price = round(base_price * 7.2, 2)
            normalized_price = base_price
        elif platform == "Shopee Demo":
            currency = "SGD"
            price = round(base_price * 1.35, 2)
            normalized_price = base_price
        else:
            currency = "USD"
            price = base_price
            normalized_price = base_price

        record = {
            "query": query,
            "platform": platform,
            "product_name": f"{product_name} Model-{i}",
            "price": price,
            "currency": currency,
            "normalized_price": normalized_price,
            "category": meta["category"],
            "brand": meta["brand"],
            "availability": random.choice(["In Stock", "Limited Stock", "Out of Stock"]),
            "source_type": "Synthetic demo",
            "source_url": f"https://example.com/demo/{query.replace(' ', '-')}/{platform.lower().replace(' ', '-')}/{i}",
            "confidence": round(random.uniform(0.78, 0.98), 2),
            "collected_at": now - timedelta(hours=random.randint(0, 96)),
            "demo_mode": True,
            "evidence_status": "not_saved",
        }

        records.append(record)

market_records.insert_many(records)

audit_logs.insert_one({
    "action": "seed_mock_data",
    "display_name": "system",
    "role": "system",
    "message": f"Inserted {len(records)} synthetic market records.",
    "timestamp": datetime.now(timezone.utc),
    "demo_mode": True,
})

print(f"Inserted {len(records)} mock market records.")
print("Database:", db.name)
print("Collections:", db.list_collection_names())
