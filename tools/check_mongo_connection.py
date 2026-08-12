import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

mongo_uri = os.getenv("MONGO_URI")

if not mongo_uri:
    raise RuntimeError("MONGO_URI is not set in .env")

client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
client.admin.command("ping")

db = client.get_database()

print("MongoDB connected successfully.")
print("Database:", db.name)
print("Collections:", db.list_collection_names())
