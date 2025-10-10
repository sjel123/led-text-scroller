#!/usr/bin/env python3
# app.py — LED text scroller sender for 16x64 matrices
# Modes: simple UDP (custom), WLED UDP Realtime (DNRGB/21324), DDP (4048)

import os
import sys
import socket
import threading
import time
from pathlib import Path
from typing import List, Tuple

from flask import Flask, render_template, request, jsonify
from PIL import Image, ImageDraw, ImageFont

# =========================
# USER / DEVICE CONSTANTS
# =========================
MATRIX_H = 16
MATRIX_W = 64
PIXELS = MATRIX_W * MATRIX_H

DEFAULT_TARGET_IP = "192.168.1.200"
DEFAULT_SIMPLE_UDP_PORT = 7777
DEFAULT_DDP_PORT = 4048
WLED_UDP_DEFAULT_PORT = 21324  # WLED UDP Realtime

# =========================
# APP SETUP
# =========================
app = Flask(__name__)

# macOS font locations
FONT_DIRS = [
    "/System/Library/Fonts",
    "/Library/Fonts",
    str(Path.home() / "Library/Fonts"),
]
FONT_EXTS = {".ttf", ".otf", ".ttc"}


def list_system_fonts() -> List[Tuple[str, str]]:
    """Return [(display_name, filepath), ...] for system fonts."""
    fonts = []
    seen = set()
    for d in FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            p = Path(d) / name
            if p.suffix.lower() in FONT_EXTS and p.is_file():
                key = (p.name, str(p))
                if key in seen:
                    continue
                seen.add(key)
                disp = p.stem
                fonts.append((disp, str(p)))
    fonts.sort(key=lambda x: x[0].lower())
    return fonts


SYSTEM_FONTS = list_system_fonts()

# =========================
# RENDERING
# =========================
def render_scrolling_frames(
    text: str,
    font_path: str | None,
    font_size: int,
    color_rgb: Tuple[int, int, int],
    speed_px_per_sec: float,
    direction: str = "left",
    bg=(0, 0, 0),
    crisp: bool = True,   # NEW
):
    W, H = MATRIX_W, MATRIX_H
    try:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    tmp_img = Image.new("RGB", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)
    text_w = int(tmp_draw.textlength(text, font=font))
    text_h = font_size
    canvas_w = max(W, text_w + W)
    canvas_h = H

    if crisp:
        # 1-bit mask (hard edges), then colorize
        full_mask = Image.new("1", (canvas_w, canvas_h), 0)
        md = ImageDraw.Draw(full_mask)
        y = (canvas_h - text_h) // 2
        md.text((W, y), text, fill=1, font=font)

        full_color = Image.new("RGB", (canvas_w, canvas_h), bg)
        color_layer = Image.new("RGB", (canvas_w, canvas_h), color_rgb)
        full_color.paste(color_layer, (0, 0), full_mask)
    else:
        # normal anti-aliased render
        full_color = Image.new("RGB", (canvas_w, canvas_h), bg)
        d = ImageDraw.Draw(full_color)
        y = (canvas_h - text_h) // 2
        d.text((W, y), text, fill=color_rgb, font=font)

    frames = []
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


def map_serpentine(rgb_bytes: bytes, w: int, h: int) -> bytes:
    """
    Convert row-major RGB to serpentine (zig-zag) 1D layout.
    Assumes top-left origin, rows alternate direction.
    """
    row_len = w * 3
    out = bytearray(w * h * 3)
    for row in range(h):
        row_data = rgb_bytes[row * row_len : (row + 1) * row_len]
        if row % 2 == 0:
            out[row * row_len : (row + 1) * row_len] = row_data
        else:
            # reverse pixel triplets
            rev = bytearray(row_len)
            for col in range(w):
                src = col * 3
                dst = (w - 1 - col) * 3
                rev[dst : dst + 3] = row_data[src : src + 3]
            out[row * row_len : (row + 1) * row_len] = rev
    return bytes(out)

# =========================
# PROTOCOL BUILDERS
# =========================
def build_ddp_packet(payload: bytes, channel=1, offset=0) -> bytes:
    """
    DDP header (10 bytes) + RGB payload.
    """
    data_len = len(payload)
    header = bytearray(10)
    header[0] = 0x41          # Version 1 + PUSH
    header[1] = channel & 0xFF
    header[2] = 0x00          # sequence
    header[3] = 0x00          # data type = raw
    header[4] = (offset >> 24) & 0xFF
    header[5] = (offset >> 16) & 0xFF
    header[6] = (offset >> 8) & 0xFF
    header[7] = offset & 0xFF
    header[8] = (data_len >> 8) & 0xFF
    header[9] = data_len & 0xFF
    return bytes(header) + payload


def build_simple_packet(payload: bytes) -> bytes:
    """
    Simple custom packet: magic + width + height + RGB payload
    """
    return b"ST16x64" + bytes([MATRIX_W, MATRIX_H]) + payload


def wled_udp_send(sock, ip, port, rgb_bytes, led_count, timeout_sec=2):
    """
    WLED UDP Realtime using DNRGB (protocol 4).
    DNRGB supports a start index; chunk at 489 pixels.
    """
    CHUNK_PIXELS = 489
    proto = 4  # DNRGB
    tbyte = max(1, min(255, int(timeout_sec)))
    for start in range(0, led_count, CHUNK_PIXELS):
        n = min(CHUNK_PIXELS, led_count - start)
        header = bytes([proto, tbyte, (start >> 8) & 0xFF, start & 0xFF])
        payload = rgb_bytes[start * 3 : (start + n) * 3]
        sock.sendto(header + payload, (ip, port))

# =========================
# SCROLLER THREAD
# =========================
_scroller_thread = None
_stop_event = threading.Event()


def scroller_worker(cfg):
    """
    Loop through frames and send via chosen protocol until stopped.
    """
    mode = cfg["mode"]
    ip = cfg["ip"]
    port = cfg["port"]
    speed = cfg["speed"]
    serpentine = cfg["serpentine"]
    channel = cfg.get("ddp_channel", 1)

    print("[THREAD] Scroller starting…", flush=True)

    frames, w, h, delay = render_scrolling_frames(
        text=cfg["text"],
        font_path=cfg["font_path"],
        font_size=cfg["font_size"],
        color_rgb=tuple(cfg["color"]),
        speed_px_per_sec=speed,
        direction=cfg["direction"],
        crisp=cfg.get("crisp", True),  # NEW
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        while not _stop_event.is_set():
            for f in frames:
                if _stop_event.is_set():
                    break

                # Arrange bytes in desired physical order
                payload = map_serpentine(f, w, h) if serpentine else f

                try:
                    if mode == "ddp":
                        packet = build_ddp_packet(payload, channel=channel, offset=0)
                        sock.sendto(packet, (ip, port))
                    elif mode == "wled_udp":
                        # WLED UDP realtime expects physical LED order (index 0..N-1)
                        wled_udp_send(sock, ip, port, payload, w * h, timeout_sec=2)
                    else:
                        # simple UDP
                        packet = build_simple_packet(payload)
                        sock.sendto(packet, (ip, port))
                except Exception as e:
                    print(f"UDP send error: {e}", file=sys.stderr)
                    return

                time.sleep(delay)
    finally:
        sock.close()
        print("[THREAD] Scroller stopped.", flush=True)


def start_scroller(cfg):
    global _scroller_thread, _stop_event
    stop_scroller()
    _stop_event.clear()
    _scroller_thread = threading.Thread(target=scroller_worker, args=(cfg,), daemon=True)
    _scroller_thread.start()


def stop_scroller():
    global _scroller_thread, _stop_event
    if _scroller_thread and _scroller_thread.is_alive():
        _stop_event.set()
        _scroller_thread.join(timeout=1.0)
    _stop_event.clear()

# =========================
# ROUTES
# =========================
@app.route("/")
def home():
    return render_template(
        "index.html",
        fonts=SYSTEM_FONTS,
        default_ip=DEFAULT_TARGET_IP,
        default_udp_port=DEFAULT_SIMPLE_UDP_PORT,
        default_ddp_port=DEFAULT_DDP_PORT,
    )


@app.route("/fonts")
def fonts():
    # Return the scanned fonts (refresh app to rescan dirs)
    return jsonify({"fonts": SYSTEM_FONTS})


@app.route("/start", methods=["POST"])
def start():
    # 1) Parse JSON safely
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Invalid JSON payload."}), 400

    # 2) Pull and validate inputs
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "Text cannot be empty."}), 400

    font_path = payload.get("font_path") or ""
    if not os.path.exists(font_path):
        # Fallback to a common macOS font; else PIL default
        fallback = "/System/Library/Fonts/SFNS.ttf"
        font_path = fallback if os.path.exists(fallback) else None

    # typed fields
    try:
        font_size = int(payload.get("font_size", 14))
        speed = float(payload.get("speed", 40.0))
        color = [int(c) for c in (payload.get("color") or [255, 255, 255])]
    except Exception as e:
        return jsonify({"ok": False, "error": f"Bad numeric field: {e}"}), 400

    direction = payload.get("direction", "left")
    serpentine = bool(payload.get("serpentine", True))
    mode = payload.get("mode", "simple")
    crisp = bool(payload.get("crisp", True))  # default True

    # Port logic + defaults
    try:
        port = int(payload.get("port") or 0)
    except Exception:
        port = 0

    if mode == "wled_udp" and port in (0, DEFAULT_SIMPLE_UDP_PORT, DEFAULT_DDP_PORT):
        port = WLED_UDP_DEFAULT_PORT

    ip = payload.get("ip", DEFAULT_TARGET_IP)
    ddp_channel = int(payload.get("ddp_channel", 1))

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
        "crisp":crisp,
    }

    print("\n[START] cfg=", cfg, flush=True)
    start_scroller(cfg)
    return jsonify({"ok": True})


@app.route("/stop", methods=["POST"])
def stop():
    stop_scroller()
    return jsonify({"ok": True})


if __name__ == "__main__":
    # Flask dev server
    app.run(host="127.0.0.1", port=5070, debug=True)

