import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
MARKETPLACE_ID = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US")

if not CLIENT_ID or not CLIENT_SECRET:
    raise RuntimeError("EBAY_CLIENT_ID or EBAY_CLIENT_SECRET is missing in .env")

# 关键：避免你之前那个 127.0.0.1:0 proxy 报错
session = requests.Session()
session.trust_env = False

def get_ebay_token():
    token_url = "https://api.ebay.com/identity/v1/oauth2/token"

    credentials = f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")
    encoded_credentials = base64.b64encode(credentials).decode("utf-8")

    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }

    response = session.post(token_url, headers=headers, data=data, timeout=20)
    print("Token status:", response.status_code)

    if response.status_code != 200:
        print(response.text)
        response.raise_for_status()

    return response.json()["access_token"]


def search_ebay(query="iphone 15", limit=5):
    token = get_ebay_token()

    search_url = "https://api.ebay.com/buy/browse/v1/item_summary/search"

    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": MARKETPLACE_ID,
    }

    params = {
        "q": query,
        "limit": limit,
    }

    response = session.get(search_url, headers=headers, params=params, timeout=20)
    print("Search status:", response.status_code)

    if response.status_code != 200:
        print(response.text)
        response.raise_for_status()

    data = response.json()
    items = data.get("itemSummaries", [])

    print(f"Found {len(items)} items.")
    for item in items:
        title = item.get("title")
        price = item.get("price", {})
        value = price.get("value")
        currency = price.get("currency")
        url = item.get("itemWebUrl")
        print(f"- {title} | {value} {currency} | {url}")


if __name__ == "__main__":
    search_ebay("iphone 15", 5)