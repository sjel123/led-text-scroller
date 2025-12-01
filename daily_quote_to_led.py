#!/usr/bin/env python3
import sys
import requests

SERVER_URL = "http://localhost:5070"  # change if running on another host/port

def main():
    try:
        r = requests.post(f"{SERVER_URL}/daily-quote-start", timeout=10)
        r.raise_for_status()
        data = r.json()
        # If your /daily-quote-start returns {"ok": true, ...}
        if not data.get("ok", True):
            print("Server reported error:", data.get("error", "unknown error"), file=sys.stderr)
            sys.exit(1)
        print("✅ Daily quote sent to LED screen.")
    except Exception as e:
        print("❌ Failed to send daily quote:", e, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
