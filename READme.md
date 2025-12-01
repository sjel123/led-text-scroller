

## Start and Stop Deployment Server

Below are concise steps to start/stop the **production deployment** using either **Gunicorn** directly or a **launchctl** service on macOS.

### Option A — Run Gunicorn manually (foreground)

**Start**
```bash
cd /Users/sjelinsky/Documents/LEDMatrix/led_text_app
source .venv/bin/activate
gunicorn -c gunicorn.conf.py app:app
```

**Stop**
- Press `Ctrl-C` in that terminal.

**Logs**
- Shown live in the same terminal session.



### Option B — Run as a launchctl service (recommended)

> Assumes your LaunchAgent label is `com.sjelinsky.led-scroller` and the plist is at  
> `~/Library/LaunchAgents/com.sjelinsky.led-scroller.plist`.

**Start**
```bash
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.sjelinsky.led-scroller.plist
launchctl enable gui/$UID/com.sjelinsky.led-scroller
launchctl kickstart -k gui/$UID/com.sjelinsky.led-scroller
```

**Stop**
```bash
launchctl bootout gui/$UID/com.sjelinsky.led-scroller
```

**Restart (safe one-liner)**
```bash
launchctl bootout gui/$UID/com.sjelinsky.led-scroller 2>/dev/null || true
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.sjelinsky.led-scroller.plist
launchctl enable gui/$UID/com.sjelinsky.led-scroller
launchctl kickstart -k gui/$UID/com.sjelinsky.led-scroller
```

**Status**
```bash
launchctl print gui/$UID/com.sjelinsky.led-scroller | sed -n '1,120p'
```

**Logs (from the plist paths)**
```bash
tail -n 100 ~/Library/Logs/led-scroller/out.log ~/Library/Logs/led-scroller/err.log
```

---

### Common adjustments

**Change listening port**
Edit `gunicorn.conf.py`:
```python
bind = "127.0.0.1:5080"   # change if needed (e.g., 5081)
workers = 1               # keep 1 worker due to the single sender thread
```
Then restart the service (see above).

**If the port is "already in use"**
```bash
sudo lsof -nP -iTCP:5080 -sTCP:LISTEN
```
Stop that process (or change `bind`), then restart your service.

**When bootstrap fails**
```bash
log show --predicate 'process == "launchd"' --last 2m | tail -n +1
```
Likely causes: wrong paths in the plist, missing venv gunicorn binary, or a busy port.

---

### Quick verification

- **Health check**
  ```bash
  curl -s http://127.0.0.1:5080/healthz
  ```
  Expected: `ok`

- **Open the UI**
  - http://127.0.0.1:5080

---

## Daily Quote Workflow

- Place a valid OpenAI API key in `OPENAI_API_KEY` or `~/Documents/keys/OpenAIAPI.txt` so the generator can fetch fresh ChatGPT quotes (fallback quotes live in `daily_quote_generator.py`).
- The Flask UI exposes `/daily-quote` for the “Use daily quote” button and `/daily-quote?variant=alternate` for “Another quote,” so you can reload today’s quote or force a new LLM result without leaving the interface.
- Use the new “Reload daily quote” button after changing fonts, colors, or scroll direction to reapply today’s deterministic quote without making another API call.
