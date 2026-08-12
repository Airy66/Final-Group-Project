import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

if not api_key:
    raise RuntimeError("GEMINI_API_KEY is missing in .env")

client = genai.Client(api_key=api_key)

prompt = (
    "You are assisting a retail price intelligence system. "
    "Write a short market summary based on these prices: "
    "eBay iPhone 15: 404.99 USD, "
    "eBay iPhone 15 Plus: 280.00 USD, "
    "eBay iPhone 15 Pro: 479.99 USD."
)

try:
    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )

    print("Gemini API connected successfully.")
    print("Model:", model)
    print(response.text)

except Exception as e:
    print("Gemini API connected, but generation failed.")
    print("Model:", model)
    print("Error:", e)
    print()
    print("Fallback summary:")
    print(
        "The current price records show a visible spread across iPhone 15 listings. "
        "The lowest observed price is 280.00 USD, while the highest observed price is "
        "479.99 USD. This suggests that users should compare listing condition, storage, "
        "and seller information before making a decision."
    )
