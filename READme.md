# LED Text Scroller (16×64) — ESP32 + WLED/DDP

Web UI + Flask sender for scrolling text on a 16×64 LED matrix.
- **Modes:** Simple UDP, WLED UDP Realtime (21324), DDP (4048)
- **Features:** system-font picker (macOS), color/size/speed/direction, 16×64 preview, **crisp** (1-bit) or smooth text
- **Tearing-free:** use **DDP** mode (sends with PUSH on the last packet)

## Quick start (dev)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5070
