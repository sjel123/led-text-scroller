---

## Start and Stop Deployment Server

Below are concise steps to start/stop the **production deployment** using either **Gunicorn** directly or a **launchctl** service on macOS.

### Option A — Run Gunicorn manually (foreground)

**Start**
```bash
cd /Users/sjelinsky/Documents/LEDMatrix/led_text_app
source .venv/bin/activate
gunicorn -c gunicorn.conf.py app:app
## Start and Stop Deployment Server

**Stop**
- Press `Ctrl-C` in that terminal.

**Logs**
- Shown live in the same terminal session.

---

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

---

## User Guide Overview

- **`app.py`** hosts a Flask UI plus REST endpoints: `/start`/`/stop` control the scrolling worker thread, `/daily-quote` returns today's or refreshed quotes, and `/healthz` lets you verify the service before clicking the UI. The worker thread renders static or scrolling frames, supports gradients, emoji-aware fonts, and sends via Simple UDP, DDP, or WLED UDP depending on the selected mode.
- **`templates/index.html`** is the single-page control panel: it lists fonts, lets you pick colors/gradients, maps layout/speed/mode options, and previews the LED animation locally. The page polls `/daily-quote`, `/daily-quote?variant=alternate`, or reuses the stored daily text when you hit the new reload button.
- **`daily_quote_generator.py`** wraps the OpenAI client (or local fallbacks) to produce quotes. It looks for `OPENAI_API_KEY` or `~/Documents/keys/OpenAIAPI.txt`, falls back to curated lines, and exposes both deterministic (`get_daily_quote`) and refreshable (`get_fresh_quote`) helpers for the Flask routes to reuse.
- **Static assets**: fonts are discovered from system directories; gradients and emoji handling live in helper functions inside `app.py`, so customizing behavior only requires tinkering with constants (e.g., `MATRIX_W`, `DEFAULT_DDP_PORT`).
- **Running locally**: activate the venv, run `python app.py`, then open http://127.0.0.1:5080 to configure the LED matrix, fetch quotes, and push display commands.
