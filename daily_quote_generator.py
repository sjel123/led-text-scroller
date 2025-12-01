import json
import os
import random
from datetime import datetime
import hashlib
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:  # fall back if old API still installed
    OpenAI = None

# ===== CONFIGURATION =====
# Provide an OpenAI API key via environment variable or the file shown below.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
DEFAULT_KEY_PATH = "/Users/sjelinsky/Documents/keys/openai.txt"
if not OPENAI_API_KEY:
    try:
        with open(DEFAULT_KEY_PATH, "r") as f:
            OPENAI_API_KEY = f.read().strip()
    except FileNotFoundError:
        OPENAI_API_KEY = None
        print(f"⚠️ API key not found. Please set OPENAI_API_KEY or create {DEFAULT_KEY_PATH} with your key.")

# Cache file for the daily quote (prevents regenerating on restart)
CACHE_PATH = Path("today_quote_cache.json")

# Personalize your prompt for the AI
QUOTE_PROMPT = "Generate a short, uplifting inspirational quote suitable for a home office. Make it about 10-15 words max. No markdown, just the quote text."
OPENAI_MODEL = "gpt-4.1"

# Local fallback quotes (used if API fails)
FALLBACK_QUOTES = [
    "Small steps today, big leaps tomorrow.",
    "Find joy in the journey, not just the destination.",
    "Together is our favorite place to be, especially when working."
]
# =========================


def _load_cached_quote():
    if not CACHE_PATH.is_file():
        return None
    try:
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        if "date" not in data or "quote" not in data:
            return None
        return data
    except Exception:
        return None


def _save_cached_quote(quote: str):
    data = {"date": datetime.now().strftime("%Y%m%d"), "quote": quote}
    try:
        with CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def get_ai_generated_quote():
    """Fetch a quote via OpenAI chat completion; return None on failure."""
    if not OPENAI_API_KEY or not OpenAI:
        return None

    client = OpenAI(api_key=OPENAI_API_KEY)
    system_prompt = "You are a thoughtful quote generator. Respond with only the requested quote text, nothing else."

    try:
        print("🔄 Contacting OpenAI for today's quote...")
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": QUOTE_PROMPT},
            ],
            max_tokens=60,
            temperature=0.8,
        )
        choice = response.choices[0]
        ai_quote = choice.message.content.strip()
        ai_quote = ai_quote.replace('"', '').replace('**', '').strip()
        return ai_quote

    except Exception as e:
        print(f"❌ API call failed: {e}")
        return None

def get_daily_quote():
    """
    Main function to get today's quote.
    Strategy: Try AI first, then fallback to local quotes with daily consistency.
    """
    today_str = datetime.now().strftime("%Y%m%d")
    cached = _load_cached_quote()
    if cached and cached.get("date") == today_str:
        return cached.get("quote")

    # Try to get a fresh AI quote first
    ai_quote = get_ai_generated_quote()
    
    if ai_quote:
        print("✅ Successfully generated new AI quote!")
        _save_cached_quote(ai_quote)
        return ai_quote
    
    # Fallback: Use local quotes with date-based selection
    print("⚠️ Using curated fallback quote for today.")
    today_str = datetime.now().strftime("%Y%m%d")
    
    # Create a consistent daily seed from the date
    hash_num = int(hashlib.md5(today_str.encode()).hexdigest(), 16)
    quote_index = hash_num % len(FALLBACK_QUOTES)
    fallback_quote = FALLBACK_QUOTES[quote_index]
    _save_cached_quote(fallback_quote)

    return fallback_quote


def get_random_local_quote():
    """Return a random fallback quote, bypassing date-based selection."""
    return random.choice(FALLBACK_QUOTES)


def get_fresh_quote():
    """Try to fetch a quote from the LLM, falling back to a random local entry."""
    ai_quote = get_ai_generated_quote()
    if ai_quote:
        return ai_quote
    return get_random_local_quote()


def main():
    """Run the quote generator and display the result."""
    print("\n" + "="*50)
    print("DAILY INSPIRATION GENERATOR")
    print("="*50)
    
    today_quote = get_daily_quote()
    
    print("\n✨ TODAY'S QUOTE FOR YOUR HOME OFFICE ✨")
    print("-" * 45)
    print(f'"{today_quote}"')
    print("-" * 45)
    print(f"\n📅 Generated on: {datetime.now().strftime('%A, %B %d, %Y')}")
    
    # Optional: Save to a file for your LED screen to read
    with open("today_quote.txt", "w") as f:
        f.write(today_quote)
    print("💾 Quote saved to 'today_quote.txt' for your LED screen.")

if __name__ == "__main__":
    if not OPENAI_API_KEY:
        print("❌ IMPORTANT: Please set OPENAI_API_KEY or place your key in the file above.")
    else:
        main()