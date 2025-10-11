import os
import time
import glob
import socket
import threading
from typing import List, Tuple, Optional

from flask import Flask, render_template, request, jsonify
from PIL import Image, ImageDraw, ImageFont

# Optional emoji support
try:
    from pilmoji import Pilmoji
    PILMOJI_AVAILABLE = True
except Exception:
    PILMOJI_AVAILABLE = False

try:
    import emoji as emoji_lib  # use emoji==1.7.0 with Pilmoji
    EMOJI_AVAILABLE = True
except Exception:
    EMOJI_AVAILABLE = False


# =========================
# USER / DEVICE CONSTANTS
# =========================
MATRIX_H = 16
MATRIX_W = 64

DEFAULT_TARGET_IP = "192.168.1.181"
DEFAULT_SIMPLE_UDP_PORT = 7777
DEFAULT_DDP_PORT = 4048
WLED_UDP_DEFAULT_PORT = 21324

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
# FONT SCANNING
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
    fonts = []
    seen = set()
    for d in FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for ext in FONT_EXTS:
            for p in glob.glob(os.path.join(d, f"*{ext}")):
                name = os.path.basename(p)
                display = os.path.splitext(name)[0]
                key = (display.lower(), p)
                if key in seen:
                    continue
                seen.add(key)
                fonts.append((display, p))
    # Prefer Arial Unicode near top
    fonts.sort(key=lambda x: (0 if "arial unicode" in x[0].lower() else 1, x[0].lower()))
    return fonts


# =========================
# EMOJI / METRICS HELPERS
# =========================
def contains_emoji(s: str) -> bool:
    if not EMOJI_AVAILABLE:
        return False
    try:
        return bool(emoji_lib.emoji_list(s))
    except Exception:
        return False

def _pil_textbbox(text: str, font: ImageFont.FreeTypeFont) -> Tuple[int,int,int,int]:
    tmp = Image.new("L", (2, 2), 0)
    d = ImageDraw.Draw(tmp)
    return d.textbbox((0, 0), text, font=font)

def _approx_pil_width(text: str, font: ImageFont.FreeTypeFont) -> int:
    tmp = Image.new("RGB", (1, 1))
    d = ImageDraw.Draw(tmp)
    return int(d.textlength(text, font=font))

def measure_bbox(text: str, font: ImageFont.FreeTypeFont) -> Tuple[int,int,int,int]:
    """
    Returns (l,t,r,b) of what will actually be drawn.
    If text contains emoji and Pilmoji is available, render offscreen with Pilmoji
    and compute bbox from the alpha channel (true drawn bounds). Otherwise use PIL.
    """
    if contains_emoji(text) and PILMOJI_AVAILABLE:
        approx_w = max(MATRIX_W*3, _approx_pil_width(text, font) + font.size*2)
        approx_h = max(MATRIX_H*2, int(font.size*2))
        rgba = Image.new("RGBA", (approx_w, approx_h), (0,0,0,0))
        with Pilmoji(rgba) as pm:
            pm.text((0, 0), text, font=font, fill=(255,255,255,255))
        a = rgba.split()[-1]
        bbox = a.getbbox()
        return bbox if bbox else (0,0,0,0)
    else:
        return _pil_textbbox(text, font)

def measure_text_width(text: str, font: ImageFont.FreeTypeFont) -> int:
    l, t, r, b = measure_bbox(text, font)
    return max(0, r - l)

def center_y_for_text(text: str, font: ImageFont.FreeTypeFont, canvas_h: int, emoji_baseline_offset: int = 0) -> int:
    """
    Vertically center using the renderer's bbox. If the text contains emoji,
    apply a user-provided baseline offset (px). Positive = move down.
    """
    l, t, r, b = measure_bbox(text, font)
    text_h = max(0, b - t)
    y = (canvas_h - text_h) // 2 - t
    if contains_emoji(text):
        y += int(emoji_baseline_offset)
    return y


# =========================
# COLOR / GRADIENTS
# =========================
def hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int,int,int]:
    i = int(h*6.0)
    f = h*6.0 - i
    p = v*(1.0-s); q = v*(1.0-f*s); t = v*(1.0-(1.0-f)*s)
    i = i % 6
    if i == 0: r,g,b = v,t,p
    elif i == 1: r,g,b = q,v,p
    elif i == 2: r,g,b = p,v,t
    elif i == 3: r,g,b = p,q,v
    elif i == 4: r,g,b = t,p,v
    else: r,g,b = v,p,q
    return (int(r*255), int(g*255), int(b*255))

def lerp(a: float,b: float,t: float)->float: return a+(b-a)*t
def lerp_rgb(a: Tuple[int,int,int], b: Tuple[int,int,int], t: float) -> Tuple[int,int,int]:
    return (int(lerp(a[0],b[0],t)), int(lerp(a[1],b[1],t)), int(lerp(a[2],b[2],t)))

def gradient_preset_color(t: float, preset: str) -> Tuple[int,int,int]:
    t = max(0.0, min(1.0, t))
    if preset == "rainbow":
        return hsv_to_rgb(t, 1.0, 1.0)
    if preset == "fire":
        stops = [(0,0,0),(120,0,0),(220,40,0),(255,140,0),(255,220,0),(255,255,255)]
        pos   = [0.00, 0.15, 0.35, 0.60, 0.85, 1.00]
    elif preset == "ocean":
        stops = [(0,10,40),(0,90,160),(0,180,255),(120,220,255)]
        pos   = [0.0, 0.4, 0.8, 1.0]
    elif preset == "sunset":
        stops = [(120,0,80),(200,40,0),(255,120,0),(255,220,120)]
        pos   = [0.0, 0.35, 0.7, 1.0]
    elif preset == "ice":
        stops = [(255,255,255),(200,240,255),(160,220,255),(120,200,255),(80,180,255)]
        pos   = [0.0, 0.25, 0.5, 0.75, 1.0]
    else:
        return (255,255,255)
    for i in range(len(pos)-1):
        if t>=pos[i] and t<=pos[i+1]:
            lt=(t-pos[i])/(pos[i+1]-pos[i])
            return lerp_rgb(stops[i], stops[i+1], lt)
    return stops[-1]

def make_horizontal_gradient(width: int, height: int, preset: str, reverse: bool=False,
                             offset_px: int = 0, period_w: Optional[int] = None) -> Image.Image:
    """Gradient image with wrap-around horizontal offset."""
    img = Image.new("RGB", (width, height), (0,0,0))
    px = img.load()
    period = period_w if period_w and period_w > 0 else width
    for x in range(width):
        xx = (x + offset_px) % period
        t = xx / float(max(1, period - 1))
        if reverse: t = 1.0 - t
        col = gradient_preset_color(t, preset)
        for y in range(height):
            px[x,y] = col
    return img


# =========================
# TEXT → ALPHA MASK
# =========================
def make_text_mask(canvas_w: int, canvas_h: int, x: int, y: int,
                   text: str, font: ImageFont.FreeTypeFont,
                   use_pilmoji: bool, crisp: bool) -> Image.Image:
    """Return 'L' mask for text/emoji. If crisp, threshold to 1-bit edges."""
    if use_pilmoji and PILMOJI_AVAILABLE:
        rgba = Image.new("RGBA", (canvas_w, canvas_h), (0,0,0,0))
        with Pilmoji(rgba) as pm:
            pm.text((x, y), text, font=font, fill=(255,255,255,255))
        mask = rgba.split()[-1]
    else:
        mask = Image.new("L", (canvas_w, canvas_h), 0)
        ImageDraw.Draw(mask).text((x, y), text, font=font, fill=255)
    if crisp:
        mask = mask.point(lambda a: 255 if a >= 128 else 0, mode='1').convert('L')
    return mask


# =========================
# FRAME RENDERERS
# =========================
def render_static_frame(
    text: str,
    font: ImageFont.FreeTypeFont,
    color_rgb: Tuple[int, int, int],
    bg=(0, 0, 0),
    crisp: bool = True,
    color_mode: str = "solid",
    gradient_preset: str = "rainbow",
    gradient_reverse: bool = False,
    gradient_shift_px: int = 0,
    emoji_baseline_offset: int = 0,
) -> bytes:
    W, H = MATRIX_W, MATRIX_H
    text_w = measure_text_width(text, font)
    x = (W - text_w) // 2
    y = center_y_for_text(text, font, H, emoji_baseline_offset)
    use_pilmoji = contains_emoji(text)

    if color_mode == "gradient":
        mask = make_text_mask(W, H, x, y, text, font, use_pilmoji, crisp)
        grad = make_horizontal_gradient(W, H, gradient_preset, gradient_reverse, offset_px=gradient_shift_px, period_w=W)
        out = Image.new("RGB", (W, H), bg)
        out.paste(grad, (0,0), mask)
        return out.tobytes()

    if crisp and not (use_pilmoji and PILMOJI_AVAILABLE):
        mask = make_text_mask(W, H, x, y, text, font, False, True)
        out = Image.new("RGB", (W, H), bg)
        out.paste(Image.new("RGB", (W, H), color_rgb), (0,0), mask)
    else:
        out = Image.new("RGB", (W, H), bg)
        if use_pilmoji and PILMOJI_AVAILABLE:
            with Pilmoji(out) as pm:
                pm.text((x, y), text, font=font, fill=color_rgb)
        else:
            ImageDraw.Draw(out).text((x, y), text, font=font, fill=color_rgb)
    return out.tobytes()


def render_scroll_window_frame(
    text: str,
    font: ImageFont.FreeTypeFont,
    color_rgb: Tuple[int,int,int],
    shift_px: int,
    direction: str,
    crisp: bool,
    center_when_short: bool,
    color_mode: str,
    gradient_preset: str,
    gradient_reverse: bool,
    gradient_shift_px: int,
    emoji_baseline_offset: int,
) -> bytes:
    """Render one 64x16 frame for current scroll position and gradient shift."""
    W, H = MATRIX_W, MATRIX_H
    text_w = measure_text_width(text, font)
    y = center_y_for_text(text, font, H, emoji_baseline_offset)
    use_pilmoji = contains_emoji(text)

    if center_when_short and text_w < W:
        center_x = (W - text_w) // 2
        x = center_x + (shift_px if direction == "right" else -shift_px)
    else:
        if direction == "left":
            x = W - shift_px
        else:
            x = shift_px - text_w

    if color_mode == "gradient":
        mask = make_text_mask(W, H, x, y, text, font, use_pilmoji, crisp)
        grad = make_horizontal_gradient(W, H, gradient_preset, gradient_reverse,
                                        offset_px=gradient_shift_px, period_w=W)
        out = Image.new("RGB", (W, H), (0,0,0))
        out.paste(grad, (0,0), mask)
        return out.tobytes()

    if crisp and not (use_pilmoji and PILMOJI_AVAILABLE):
        mask = make_text_mask(W, H, x, y, text, font, False, True)
        out = Image.new("RGB", (W, H), (0,0,0))
        out.paste(Image.new("RGB", (W, H), color_rgb), (0,0), mask)
    else:
        out = Image.new("RGB", (W, H), (0,0,0))
        if use_pilmoji and PILMOJI_AVAILABLE:
            with Pilmoji(out) as pm:
                pm.text((x, y), text, font=font, fill=color_rgb)
        else:
            ImageDraw.Draw(out).text((x, y), text, font=font, fill=color_rgb)
    return out.tobytes()


# =========================
# PIXEL ORDER MAPPING
# =========================
def remap_serpentine(rgb_bytes: bytes, width: int, height: int, serpentine: bool) -> bytes:
    if not serpentine:
        return rgb_bytes
    row_stride = width * 3
    out = bytearray(len(rgb_bytes))
    for y in range(height):
        row = rgb_bytes[y * row_stride:(y + 1) * row_stride]
        if y % 2 == 0:
            out[y * row_stride:(y + 1) * row_stride] = row
        else:
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
    sock.sendto(rgb_bytes, (ip, port))

def send_wled_realtime_frame(sock: socket.socket, ip: str, port: int, rgb_bytes: bytes):
    sock.sendto(rgb_bytes, (ip, port))

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
    MAX_PAYLOAD = 1200  # multiple of 3, < MTU
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
    mode = cfg["mode"]
    display_mode = cfg.get("display_mode", "scroll")
    center_short = bool(cfg.get("center_short", False))

    text = cfg["text"]
    color = tuple(cfg["color"])
    font_size = cfg["font_size"]
    font_path = cfg.get("font_path")
    direction = cfg.get("direction", "left")
    speed = cfg.get("speed", 40.0)
    crisp = bool(cfg.get("crisp", True))
    serpentine = bool(cfg.get("serpentine", False))  # default Progressive
    ip = cfg["ip"]
    port = int(cfg["port"])
    channel = int(cfg.get("ddp_channel", 1))

    color_mode = cfg.get("color_mode", "solid")
    gradient_preset = cfg.get("gradient_preset", "rainbow")
    gradient_reverse = bool(cfg.get("gradient_reverse", False))
    gradient_shift_speed = float(cfg.get("gradient_shift_speed", 0.0))
    emoji_baseline_offset = int(cfg.get("emoji_baseline_offset", 0))

    try:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)

    try:
        if display_mode == "static":
            # animate gradient by time
            last = time.time()
            grad_shift = 0.0
            frame_interval = 1.0 / 30.0  # ~30 FPS
            while not STOP_EVENT.is_set():
                now = time.time()
                dt = now - last
                last = now
                grad_shift = (grad_shift + gradient_shift_speed * dt) % MATRIX_W

                frame = render_static_frame(
                    text, font, color, bg=(0,0,0), crisp=crisp,
                    color_mode=color_mode,
                    gradient_preset=gradient_preset,
                    gradient_reverse=gradient_reverse,
                    gradient_shift_px=int(grad_shift),
                    emoji_baseline_offset=emoji_baseline_offset
                )
                payload = remap_serpentine(frame, MATRIX_W, MATRIX_H, serpentine)
                if mode == "ddp":
                    send_ddp_frame(sock, ip, port, payload, channel=channel, seq=0)
                elif mode == "wled_udp":
                    send_wled_realtime_frame(sock, ip, port, payload)
                else:
                    send_simple_udp_frame(sock, ip, port, payload)
                time.sleep(frame_interval)
            return

        # Scroll mode — render window frames on the fly
        last = time.time()
        grad_shift = 0.0
        text_w = measure_text_width(text, font)
        total_steps = (MATRIX_W + text_w)
        step = 0
        while not STOP_EVENT.is_set():
            now = time.time()
            dt = now - last
            last = now

            delay = 1.0 / max(float(cfg.get("speed", 40.0)), 1.0)
            grad_shift = (grad_shift + gradient_shift_speed * dt) % MATRIX_W

            frame = render_scroll_window_frame(
                text=text,
                font=font,
                color_rgb=color,
                shift_px=step,
                direction=direction,
                crisp=crisp,
                center_when_short=center_short,
                color_mode=color_mode,
                gradient_preset=gradient_preset,
                gradient_reverse=gradient_reverse,
                gradient_shift_px=int(grad_shift),
                emoji_baseline_offset=emoji_baseline_offset,
            )
            payload = remap_serpentine(frame, MATRIX_W, MATRIX_H, serpentine)
            if mode == "ddp":
                send_ddp_frame(sock, ip, port, payload, channel=channel, seq=0)
            elif mode == "wled_udp":
                send_wled_realtime_frame(sock, ip, port, payload)
            else:
                send_simple_udp_frame(sock, ip, port, payload)

            time.sleep(delay)
            step += 1
            if step >= total_steps:
                step = 0

    finally:
        try: sock.close()
        except Exception: pass


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

    text = str(payload.get("text", "Hello, world!"))
    font_path = payload.get("font_path")
    font_size = int(payload.get("font_size", 16))
    color = payload.get("color", [255, 255, 255])

    color_mode = str(payload.get("color_mode", "solid"))
    gradient_preset = str(payload.get("gradient_preset", "rainbow"))
    gradient_reverse = bool(payload.get("gradient_reverse", False))
    gradient_shift_speed = float(payload.get("gradient_shift_speed", 0.0))

    speed = float(payload.get("speed", 40.0))
    direction = str(payload.get("direction", "left")).lower()
    serpentine = bool(payload.get("serpentine", False))  # default Progressive
    mode = str(payload.get("mode", "ddp")).lower()
    ip = str(payload.get("ip", DEFAULT_TARGET_IP))
    port = int(payload.get("port", DEFAULT_DDP_PORT if mode == "ddp" else (WLED_UDP_DEFAULT_PORT if mode == "wled_udp" else DEFAULT_SIMPLE_UDP_PORT)))
    ddp_channel = int(payload.get("ddp_channel", 1))
    crisp = bool(payload.get("crisp", True))
    display_mode = str(payload.get("display_mode", "scroll")).lower()
    center_short = bool(payload.get("center_short", False))

    emoji_baseline_offset = int(payload.get("emoji_baseline_offset", 0))

    stop_worker()

    cfg = {
        "text": text,
        "font_path": font_path,
        "font_size": font_size,
        "color": color,
        "color_mode": color_mode,
        "gradient_preset": gradient_preset,
        "gradient_reverse": gradient_reverse,
        "gradient_shift_speed": gradient_shift_speed,
        "speed": speed,
        "direction": direction,
        "serpentine": serpentine,
        "mode": mode,
        "ip": ip,
        "port": port,
        "ddp_channel": ddp_channel,
        "crisp": crisp,
        "display_mode": display_mode,
        "center_short": center_short,
        "emoji_baseline_offset": emoji_baseline_offset,
    }

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
            SENDER_THREAD.join(timeout=2.0)
        STOP_EVENT.clear()
        SENDER_THREAD = None


# =========================
# MAIN
# =========================
if __name__ == "__main__":
    app.run(host=DEV_HOST, port=DEV_PORT, debug=True)
