# LED Text Scroller (16×64) — Complete User Guide

A single, consolidated guide for installing, running, deploying, operating, and troubleshooting the LED text scroller app for an ESP32-driven 16×64 LED matrix (WLED UDP Realtime or DDP), including “crisp letters” and a live browser preview.

---

## Contents
1. [Overview](#overview)  
2. [Requirements](#requirements)  
3. [Install](#install)  
4. [Run in Development](#run-in-development)  
5. [Production (Gunicorn WSGI)](#production-gunicorn-wsgi)  
6. [Optional: Keep Running with launchd (macOS)](#optional-keep-running-with-launchd-macos)  
7. [Using the Web UI](#using-the-web-ui)  
8. [Start / Stop / Clear](#start--stop--clear)  
9. [WLED / ESP32 Settings](#wled--esp32-settings)  
10. [Crisp vs Smooth Letters](#crisp-vs-smooth-letters)  
11. [Tearing / “Half-panel lag” Fix](#tearing--half-panel-lag-fix)  
12. [Troubleshooting](#troubleshooting)  
13. [HTTP API (for automation)](#http-api-for-automation)  
14. [Reset / Cleanup](#reset--cleanup)

---

## Overview
- Web app (Flask) that renders scrolling text for a **16×64** LED matrix.
- Sends frames via:
  - **DDP (port 4048)** — recommended; prevents tearing with frame “push”.
  - **WLED UDP Realtime (port 21324)** — works, but can show tearing on large matrices.
  - **Simple UDP (port 7777)** — matches the sample ESP32 sketch.
- Features:
  - Live **16×64 preview** (with scale/grid).
  - **Crisp letters** toggle (1-bit mask for sharp edges) or smooth anti-aliased text.
  - System font picker (macOS), font size, color, speed, direction.
  - Serpentine vs progressive **layout** for wiring.

---

## Requirements
- macOS (Apple Silicon or Intel).
- Python **3.10+** (3.12+ recommended).
- ESP32-based controller with **WLED** or the provided **Simple UDP** sketch.
- Mac and ESP32 on the **same network**.

---

## Install
In your project directory (where `app.py` and `templates/index.html` live):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # or: pip install flask pillow gunicorn
```

> If you don’t have a `requirements.txt`, create one with:
> ```
> flask
> pillow
> gunicorn
> ```

---

## Run in Development
```bash
source .venv/bin/activate
python app.py
```

Open: **http://127.0.0.1:5050**

---

## Production (Gunicorn WSGI)
Create `gunicorn.conf.py` in the project root:

```python
bind = "127.0.0.1:5080"
workers = 1            # IMPORTANT: keep 1 process (the app has a single sender thread)
threads = 8
worker_class = "gthread"
timeout = 60
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = "info"
```

Run:

```bash
source .venv/bin/activate
gunicorn -c gunicorn.conf.py app:app
```

Open: **http://127.0.0.1:5080**

> **Why one worker?** Multiple workers would each start a sender thread → duplicated frames.

---

## Optional: Keep Running with launchd (macOS)
Create log directory:

```bash
mkdir -p ~/Library/Logs/led-scroller
```

Create `~/Library/LaunchAgents/com.<yourname>.led-scroller.plist` (use absolute paths):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.yourname.led-scroller</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/<you>/path/to/project/.venv/bin/gunicorn</string>
    <string>-c</string>
    <string>/Users/<you>/path/to/project/gunicorn.conf.py</string>
    <string>app:app</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/<you>/path/to/project</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>StandardOutPath</key>
  <string>/Users/<you>/Library/Logs/led-scroller/out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/<you>/Library/Logs/led-scroller/err.log</string>
</dict>
</plist>
```

Validate & load:
```bash
plutil -lint ~/Library/LaunchAgents/com.yourname.led-scroller.plist
launchctl bootout gui/$UID/com.yourname.led-scroller 2>/dev/null || true
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.yourname.led-scroller.plist
launchctl enable gui/$UID/com.yourname.led-scroller
launchctl kickstart -k gui/$UID/com.yourname.led-scroller
```

Logs:
```bash
tail -f ~/Library/Logs/led-scroller/out.log ~/Library/Logs/led-scroller/err.log
```

---

## Using the Web UI
1. **Text**: enter your message.  
2. **Font**: choose from installed system fonts (macOS font folders are scanned).  
3. **Font size**: typically **10–14 px** looks best on 16-pixel-tall displays.  
4. **Color**: choose any color.  
5. **Speed**: pixels/second.  
6. **Direction**: left or right.  
7. **Layout**:
   - **Serpentine** (zig-zag rows) if your physical wiring alternates direction each row.
   - **Progressive** if each row goes strictly left→right.
8. **Mode**:
   - **DDP (4048)** — **recommended** (tear-free).  
   - **WLED UDP (21324)** — simpler, but can show tearing on large frames.  
   - **Simple UDP (7777)** — for the example Arduino sketch.
9. **Target IP / Port**: auto-fills per mode; set IP to your WLED/ESP32.
10. **Crisp letters**:
    - ON = 1-bit mask, razor sharp edges (best for LEDs).
    - OFF = anti-aliased, more “smooth” looking.
11. **Preview**: play/pause/reset, scale, and optional grid (preview does not send data).

---

## Start / Stop / Clear

### Start (UI)
- Set parameters → **Start (send)**.

### Stop (UI)
- Click **Stop**.

### Start/Stop via HTTP
```bash
# Start (dev server at 5050)
curl -X POST http://127.0.0.1:5050/start -H 'Content-Type: application/json' -d '{
  "text":"Hello!",
  "font_path":"/System/Library/Fonts/Supplemental/Arial.ttf",
  "font_size":14,
  "color":[255,255,255],
  "speed":40,
  "direction":"left",
  "serpentine":true,
  "mode":"ddp",
  "ip":"192.168.1.50",
  "port":4048,
  "ddp_channel":1,
  "crisp":true
}'
# Stop
curl -X POST http://127.0.0.1:5050/stop
```

> If running Gunicorn on 5080, change the base URL accordingly.

### Clear / Reset
- **Preview**: **Reset** button.  
- **Sender**: **Stop** then **Start** regenerates frames with your new settings.  
- **Server**: restart the process (Ctrl-C & rerun, or restart Gunicorn / launchd).

---

## WLED / ESP32 Settings

### WLED (recommended)
- **Total LEDs**: `1024` (16×64).
- **2D Matrix**: Width `64`, Height `16`; wiring pattern to match your panel (zig-zag vs progressive).
- **DDP (port 4048)**: enable in *Config → Sync Interfaces*.  
- **UDP Realtime (port 21324)**: enable in *Config → Sync Interfaces* if you use that mode.  
- Optional: **Realtime timeout** ~2s so WLED drops live data shortly after sender stops.

### Simple UDP (example Arduino sketch)
- Flash the provided sketch (`UDP_PORT = 7777`).
- Use **Mode = Simple UDP** and **Layout = Progressive** (sketch remaps to serpentine on-device).

---

## Crisp vs Smooth Letters
- **Crisp** (default ON): text is rendered with a **1-bit mask**; edges are strictly on/off → **sharp on LEDs**.
- **Smooth**: anti-aliased edges; can appear fuzzy at 16 px height.  
Toggle in UI; preview and LED output follow the setting.

---

## Tearing / “Half-panel lag” Fix
If you see one half lag behind the other, you’re seeing **tearing** from multi-packet updates.

**Solution:** use **DDP mode (4048)**.  
The app sends each frame as multiple DDP packets with the **PUSH flag set on the last packet** so WLED applies the entire frame **atomically**.

If you must use UDP Realtime:
- Reduce frame rate slightly (more time for all chunks to arrive).
- Prefer 5 GHz Wi-Fi, close to the AP.
- Drive fewer LEDs per stream (or multiple outputs), though skew can still occur.

---

## Troubleshooting

**Nothing shows on LEDs**
- Verify **Mode** and **Port** match device settings (DDP 4048, UDP 21324, or Simple 7777).
- Verify **Target IP** is the ESP32/WLED IP.
- macOS firewall may be blocking Python/Gunicorn — allow incoming connections.
- Try a **solid-color test** sender snippet to confirm networking.

**Text scrambled**
- Flip **Layout** (Serpentine ↔ Progressive) to match wiring.

**Fonts missing**
- Install `.ttf/.otf/.ttc` to `~/Library/Fonts`, then refresh the page.

**LED tearing**
- Use **DDP** mode (tearing-free with frame push).

**Gunicorn won’t start / port busy**
- Edit `bind` in `gunicorn.conf.py` to a free port (e.g., `127.0.0.1:5081`).

**LaunchAgent load failed**
- Ensure absolute paths in the plist, correct permissions, and that `gunicorn` path points to your venv.
- Validate plist: `plutil -lint <plist>`
- Use: `launchctl bootstrap gui/$UID <plist>` and check logs in `~/Library/Logs/led-scroller/`.

---

## HTTP API (for automation)

### Start
`POST /start` — JSON fields mirror the UI:

```json
{
  "text": "Hello!",
  "font_path": "/System/Library/Fonts/Supplemental/Arial.ttf",
  "font_size": 14,
  "color": [255, 255, 255],
  "speed": 40,
  "direction": "left",
  "serpentine": true,
  "mode": "ddp",
  "ip": "192.168.1.50",
  "port": 4048,
  "ddp_channel": 1,
  "crisp": true
}
```

### Stop
`POST /stop` → stops the sender thread.

### Health
`GET /healthz` → returns `ok` if the app is up.

---

## Reset / Cleanup

```bash
# Stop sender (if needed)
curl -X POST http://127.0.0.1:5050/stop  # or 5080 if using Gunicorn

# Stop dev server
#   (press Ctrl-C in the terminal running python app.py)

# Stop Gunicorn (foreground)
#   (press Ctrl-C in the Gunicorn terminal)

# Stop LaunchAgent (if used)
launchctl bootout gui/$UID/com.yourname.led-scroller

# Remove virtual env (fresh reinstall later)
deactivate 2>/dev/null || true
rm -rf .venv
```
