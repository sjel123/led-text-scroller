import os
import time
import glob
import socket
import threading
from typing import List, Tuple, Optional

from flask import Flask, render_template, request, jsonify

from PIL import Image, ImageDraw, ImageFont

# Optional emoji support (Pilmoji); keep runtime robust if not installed
try:
    from pilmoji import Pilmoji
    PILMOJI_AVAILABLE = True
except Exception:
    PILMOJI_AVAILABLE = False

try:
    import emoji as emoji_lib  # must be emoji==1.7.0 for pilmoji compatibility
    EMOJI_AVAILABLE = True
except Exception:
    EMOJI_AVAILABLE = False


# =========================
# USER / DEVICE CONSTANTS
# =========================
MATRIX_H = 16
MATRIX_W = 64
PIXELS = MATRIX_W * MATRIX_H

# Defaults (per your request)
DEFAULT_TARGET_IP = "192.168.1.181"
DEFAULT_SIMPLE_UDP_PORT = 7777
DEFAULT_DDP_PORT = 4048
WLED_UDP_DEFAULT_PORT = 21324

# Dev server bind
DEV_HOST = "127.0.0.1"
DEV_PORT = 5050


# =========================
# GLOBALS
# =========================
app = Flask(__name__, template_folder="templates", static_folder="static")

SENDER_THREAD: Optional[threading.Thread] = None
STOP_EVENT = threading.Event()
STATE_LOCK = threading.Lock()


# =========================
# FONT SCANNING (macOS + common)
# =========================
FONT_DIRS = [
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
    "/usr/share/fonts",
    "/usr/local/share/fonts",
]

FONT_EXTS = (".ttf", ".otf", ".ttc")

def list_system_fonts() -> List[Tuple[str, str]]:
    """
    Return list of (display_name, path)
    """
    fonts = []
    seen = set()
    for d in FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for ext in FONT_EXTS:
            for p in glob.glob(os.path.join(d, f"*{ext}")):
                name = os.path.basename(p)
                # crude but works well enough for display
                display = os.path.splitext(name)[0]
                key = (display.lower(), p)
                if key in seen:
                    continue
                seen.add(key)
                fonts.append((display, p))
    # Prefer Arial Unicode if present by bubbling it earlier (UI also selects it)
    fonts.sort(key=lambda x: (0 if "arial unicode" in x[0].lower() else 1, x[0].lower()))
    return fonts


# =========================
# TEXT MEASUREMENT & CENTERING
# =========================
def measure_text_bbox_pil(text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int, int, int]:
    """
    Return (l, t, r, b) bounding box of rendered text (no stroke).
    """
    tmp = Image.new("L", (2, 2), 0)
    d = ImageDraw.Draw(tmp)
    return d.textbbox((0, 0), text, font=font)

def center_y_for_text(text: str, font: ImageFont.FreeTypeFont, canvas_h: int) -> int:
    """
    Compute Y so the visible glyphs are vertically centered.
    """
    l, t, r, b = measure_text_bbox_pil(text, font)
    text_h = max(0, b - t)
    return (canvas_h - text_h) // 2 - t

def measure_text_width(text: str, font: ImageFont.FreeTypeFont) -> int:
    tmp = Image.new("RGB", (1, 1))
    d = ImageDraw.Draw(tmp)
    return int(d.textlength(text, font=font))


def contains_emoji(s: str) -> bool:
    if not EMOJI_AVAILABLE:
        return False
    try:
        return bool(emoji_lib.emoji_list(s))
    except Exception:
        return False


# =========================
# FRAME RENDERERS
# =========================
def render_static_frame(
    text: str,
    font: ImageFont.FreeTypeFont,
    color_rgb: Tuple[int, int, int],
    bg=(0, 0, 0),
    crisp: bool = True,
) -> bytes:
    """
    Return a single centered 16x64 RGB frame (bytes).
    If emojis present or crisp=False and Pilmoji is available, render with Pilmoji (color emojis).
    """
    W, H = MATRIX_W, MATRIX_H
    text_w = measure_text_width(text, font)
    x = (W - text_w) // 2
    y = center_y_for_text(text, font, H)

    use_pilmoji = (contains_emoji(text) or not crisp) and PILMOJI_AVAILABLE

    if crisp and not use_pilmoji:
        # 1-bit mask → colorize
        mask = Image.new("1", (W, H), 0)
        md = ImageDraw.Draw(mask)
        md.text((x, y), text, fill=1, font=font)

        out = Image.new("RGB", (W, H), bg)
        color_layer = Image.new("RGB", (W, H), color_rgb)
        out.paste(color_layer, (0, 0), mask)
    else:
        out = Image.new("RGB", (W, H), bg)
        if use_pilmoji:
            with Pilmoji(out) as pm:
                pm.text((x, y), text, font=font, fill=color_rgb)
        else:
            d = ImageDraw.Draw(out)
            d.text((x, y), text, font=font, fill=color_rgb)

    return out.tobytes()


def render_scrolling_frames(
    text: str,
    font: ImageFont.FreeTypeFont,
    color_rgb: Tuple[int, int, int],
    speed_px_per_sec: float,
    direction: str = "left",
    bg=(0, 0, 0),
    crisp: bool = True,
) -> Tuple[List[bytes], int, int, float]:
    """
    Generates all frames for one full scroll across the 16x64 window.
    Returns (frames, width, height, delay_between_frames)
    """
    W, H = MATRIX_W, MATRIX_H

    text_w = measure_text_width(text, font)
    text_h = font.size  # only used to estimate canvas (real Y via center_y_for_text)
    canvas_w = max(W, text_w + W)
    canvas_h = H

    y = center_y_for_text(text, font, canvas_h)
    use_pilmoji = (contains_emoji(text) or not crisp) and PILMOJI_AVAILABLE

    if crisp and not use_pilmoji:
        # render a 1-bit mask into a wide canvas, then colorize
        full_mask = Image.new("1", (canvas_w, canvas_h), 0)
        md = ImageDraw.Draw(full_mask)
        md.text((W, y), text, fill=1, font=font)

        full_color = Image.new("RGB", (canvas_w, canvas_h), bg)
        color_layer = Image.new("RGB", (canvas_w, canvas_h), color_rgb)
        full_color.paste(color_layer, (0, 0), full_mask)
    else:
        # render with color / emoji support
        full_color = Image.new("RGB", (canvas_w, canvas_h), bg)
        if use_pilmoji:
            with Pilmoji(full_color) as pm:
                pm.text((W, y), text, font=font, fill=color_rgb)
        else:
            d = ImageDraw.Draw(full_color)
            d.text((W, y), text, font=font, fill=color_rgb)

    frames: List[bytes] = []
    if direction == "left":
        for shift in range(0, W + text_w):
            crop = full_color.crop((shift, 0, shift + W, H))
            frames.append(crop.tobytes())
    else:
        for shift in range(0, W + text_w):
            crop = full_color.crop((canvas_w - W - shift, 0, canvas_w - shift, H))
            frames.append(crop.tobytes())

    delay = 1.0 / max(speed_px_per_sec, 1.0)
    return frames, W, H, delay


# =========================
# PIXEL ORDER MAPPING
# =========================
def remap_serpentine(rgb_bytes: bytes, width: int, height: int, serpentine: bool) -> bytes:
    """
    Convert row-major progressive RGB bytes into desired physical order.
    If serpentine=False → progressive (L->R each row).
    If serpentine=True  → every odd row reversed (zig-zag).
    """
    if not serpentine:
        return rgb_bytes  # already progressive

    row_stride = width * 3
    out = bytearray(len(rgb_bytes))
    for y in range(height):
        row = rgb_bytes[y * row_stride:(y + 1) * row_stride]
        if y % 2 == 0:
            out[y * row_stride:(y + 1) * row_stride] = row
        else:
            # reverse pixel triplets in this row
            rev = bytearray(row_stride)
            for x in range(width):
                sx = x * 3
                dx = (width - 1 - x) * 3
                rev[dx:dx+3] = row[sx:sx+3]
            out[y * row_stride:(y + 1) * row_stride] = rev
    return bytes(out)


# =========================
# UDP SENDERS
# =========================
def send_simple_udp_frame(sock: socket.socket, ip: str, port: int, rgb_bytes: bytes):
    # Custom simple: just raw RGB stream
    sock.sendto(rgb_bytes, (ip, port))

# --- WLED UDP Realtime (DNRGB/WARLS-style) ---
# In practice, WLED will accept several UDP formats. The simplest reliable approach
# here is to send raw RGB per pixel for the configured total LEDs in real-time mode.
# For big matrices, prefer DDP to avoid tearing.
def send_wled_realtime_frame(sock: socket.socket, ip: str, port: int, rgb_bytes: bytes):
    sock.sendto(rgb_bytes, (ip, port))

# --- DDP (chunked, PUSH on last) ---
# DDP header: 10 bytes
# 0: flags/version (bit6 = PUSH)
# 1: channel (1..255)
# 2: sequence
# 3: data type (0 = raw)
# 4..7: byte offset (big-endian)
# 8..9: data length (big-endian)
def _ddp_header(offset_bytes: int, data_len: int, channel: int, seq: int, push: bool) -> bytes:
    flags = 0x01
    if push:
        flags |= 0x40  # PUSH
    h = bytearray(10)
    h[0] = flags
    h[1] = channel & 0xFF
    h[2] = seq & 0xFF
    h[3] = 0x00  # raw
    h[4] = (offset_bytes >> 24) & 0xFF
    h[5] = (offset_bytes >> 16) & 0xFF
    h[6] = (offset_bytes >> 8) & 0xFF
    h[7] = offset_bytes & 0xFF
    h[8] = (data_len >> 8) & 0xFF
    h[9] = data_len & 0xFF
    return bytes(h)

def send_ddp_frame(sock: socket.socket, ip: str, port: int, rgb_bytes: bytes, channel: int = 1, seq: int = 0):
    MAX_PAYLOAD = 1200  # under MTU; keep multiple of 3
    total = len(rgb_bytes)
    offset = 0
    idx = 0
    while offset < total:
        remain = total - offset
        pay = min(MAX_PAYLOAD - (MAX_PAYLOAD % 3), remain)
        push = (offset + pay) >= total
        header = _ddp_header(offset, pay, channel, (seq + idx) & 0xFF, push)
        sock.sendto(header + rgb_bytes[offset:offset+pay], (ip, port))
        offset += pay
        idx += 1


# =========================
# WORKER
# =========================
def scroller_worker(cfg: dict):
    """
    Single sender thread. Reads cfg and streams frames until STOP_EVENT is set.
    cfg keys (JSON from /start):
      text, font_path, font_size, color, speed, direction, serpentine,
      mode, ip, port, ddp_channel, crisp, display_mode
    """
    mode = cfg["mode"]                    # "ddp" | "wled_udp" | "simple"
    display_mode = cfg.get("display_mode", "scroll")
    text = cfg["text"]
    color = tuple(cfg["color"])
    font_size = cfg["font_size"]
    font_path = cfg.get("font_path")
    direction = cfg.get("direction", "left")
    speed = cfg.get("speed", 40.0)
    crisp = bool(cfg.get("crisp", True))
    serpentine = bool(cfg.get("serpentine", True))
    ip = cfg["ip"]
    port = int(cfg["port"])
    channel = int(cfg.get("ddp_channel", 1))

    # Load font
    try:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)

    try:
        if display_mode == "static":
            # One centered frame, resend occasionally (keep-alive)
            frame = render_static_frame(text, font, color, bg=(0, 0, 0), crisp=crisp)
            frame = remap_serpentine(frame, MATRIX_W, MATRIX_H, serpentine)
            keepalive = 0.5  # seconds; adjust to your WLED realtime timeout if needed
            while not STOP_EVENT.is_set():
                if mode == "ddp":
                    send_ddp_frame(sock, ip, port, frame, channel=channel, seq=0)
                elif mode == "wled_udp":
                    send_wled_realtime_frame(sock, ip, port, frame)
                else:
                    send_simple_udp_frame(sock, ip, port, frame)
                time.sleep(keepalive)
            return

        # Scroll mode
        frames, w, h, delay = render_scrolling_frames(
            text=text,
            font=font,
            color_rgb=color,
            speed_px_per_sec=speed,
            direction=direction,
            crisp=crisp,
        )
        # remap once per payload when sending
        while not STOP_EVENT.is_set():
            for f in frames:
                if STOP_EVENT.is_set():
                    break
                payload = remap_serpentine(f, w, h, serpentine)
                if mode == "ddp":
                    send_ddp_frame(sock, ip, port, payload, channel=channel, seq=0)
                elif mode == "wled_udp":
                    send_wled_realtime_frame(sock, ip, port, payload)
                else:
                    send_simple_udp_frame(sock, ip, port, payload)
                time.sleep(delay)
    finally:
        try:
            sock.close()
        except Exception:
            pass


# =========================
# ROUTES
# =========================
@app.route("/")
def index():
    fonts = list_system_fonts()
    return render_template(
        "index.html",
        fonts=fonts,
        default_ip=DEFAULT_TARGET_IP,
        default_ddp_port=DEFAULT_DDP_PORT,
    )

@app.route("/healthz")
def healthz():
    return "ok", 200

@app.route("/start", methods=["POST"])
def start():
    payload = request.get_json(silent=True) or {}
    # Validate & fill defaults
    text = str(payload.get("text", "Hello, world!"))
    font_path = payload.get("font_path")
    font_size = int(payload.get("font_size", 16))
    color = payload.get("color", [255, 255, 255])
    speed = float(payload.get("speed", 40.0))
    direction = str(payload.get("direction", "left")).lower()
    serpentine = bool(payload.get("serpentine", False))  # UI default is progressive
    mode = str(payload.get("mode", "ddp")).lower()
    ip = str(payload.get("ip", DEFAULT_TARGET_IP))
    port = int(payload.get("port", DEFAULT_DDP_PORT if mode == "ddp" else (WLED_UDP_DEFAULT_PORT if mode == "wled_udp" else DEFAULT_SIMPLE_UDP_PORT)))
    ddp_channel = int(payload.get("ddp_channel", 1))
    crisp = bool(payload.get("crisp", True))
    display_mode = str(payload.get("display_mode", "scroll")).lower()  # "scroll" | "static"

    # stop any previous sender
    stop_worker()

    cfg = {
        "text": text,
        "font_path": font_path,
        "font_size": font_size,
        "color": color,
        "speed": speed,
        "direction": direction,
        "serpentine": serpentine,
        "mode": mode,
        "ip": ip,
        "port": port,
        "ddp_channel": ddp_channel,
        "crisp": crisp,
        "display_mode": display_mode,
    }

    # start new worker
    with STATE_LOCK:
        STOP_EVENT.clear()
        global SENDER_THREAD
        SENDER_THREAD = threading.Thread(target=scroller_worker, args=(cfg,), daemon=True)
        SENDER_THREAD.start()

    return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def stop():
    stop_worker()
    return jsonify({"ok": True})

def stop_worker():
    with STATE_LOCK:
        global SENDER_THREAD
        if SENDER_THREAD and SENDER_THREAD.is_alive():
            STOP_EVENT.set()
            # give it a moment to finish
            SENDER_THREAD.join(timeout=2.0)
        STOP_EVENT.clear()
        SENDER_THREAD = None


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    # Run dev server
    app.run(host=DEV_HOST, port=DEV_PORT, debug=True)
