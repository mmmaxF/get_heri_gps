#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import json
import os
import threading
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageDraw, ImageFont


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(float(os.environ.get("PORT", "8030")))
CONFIG_PATH = Path(os.environ.get("TELOP_CONFIG_PATH", "/app/config/telop_config.json"))
REVERSE_GEOCODER_LATEST_URL = os.environ.get("REVERSE_GEOCODER_LATEST_URL", "http://reverse-geocoder:8020/api/latest")


DEFAULT_CONFIG = {
    "v_output": "",
    "key_output": "",
    "format": {
        "width": 1920,
        "height": 1080,
        "frame_rate": "59.94i",
        "pixel_format": "yuv8",
        "key_mode": "matte",
        "safe_area": True,
    },
    "text_template": "{address_label}",
    "fallback_text": "現在地取得中",
    "font_family": "Noto Sans CJK JP",
    "font_size": 72,
    "font_weight": 700,
    "text_color": "#ffffff",
    "stroke_color": "#000000",
    "stroke_width": 6,
    "background_color": "#000000",
    "background_opacity": 0.35,
    "padding": 24,
    "box": {
        "x": 120,
        "y": 820,
        "width": 900,
        "height": 120,
        "scale": 1.0,
    },
}


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.config = self.load_config()
        self.running = False
        self.latest_text = ""
        self.latest_geocode = None
        self.error = ""

    def load_config(self):
        cfg = deepcopy(DEFAULT_CONFIG)
        if CONFIG_PATH.exists():
            try:
                saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                deep_update(cfg, saved)
            except Exception:
                pass
        return cfg

    def save_config(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")

    def snapshot(self):
        with self.lock:
            return {
                "ok": True,
                "running": self.running,
                "latest_text": self.latest_text,
                "latest_geocode": self.latest_geocode,
                "error": self.error,
                "config": deepcopy(self.config),
            }


def deep_update(base, incoming):
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_update(base[key], value)
        else:
            base[key] = value


STATE = State()
app = FastAPI(title="telop_output")


def hex_to_rgba(value, alpha=1.0):
    value = str(value or "#000000").strip()
    if value.startswith("#"):
        value = value[1:]
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        r = int(value[0:2], 16)
        g = int(value[2:4], 16)
        b = int(value[4:6], 16)
    except Exception:
        r, g, b = 0, 0, 0
    return (r, g, b, max(0, min(255, int(float(alpha) * 255))))


def find_font(font_family):
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    asset_fonts = sorted(Path("/app/assets/fonts").glob("*"))
    for path in list(asset_fonts) + [Path(c) for c in candidates]:
        if path.is_file() and (not font_family or font_family.lower().split()[0] in path.name.lower() or "noto" in path.name.lower()):
            return str(path)
    for c in candidates:
        if Path(c).is_file():
            return c
    return None


def get_latest_geocode():
    try:
        with urllib.request.urlopen(REVERSE_GEOCODER_LATEST_URL, timeout=0.5) as res:
            data = json.loads(res.read(8192).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    if not data.get("ok"):
        return None
    return data


def render_text(config, geocode):
    if geocode:
        text = config.get("text_template", "{address_label}").format(
            address_label=geocode.get("address_label", ""),
            prefecture=geocode.get("prefecture", ""),
            city=geocode.get("city", ""),
            ward=geocode.get("ward", ""),
        ).strip()
        if text:
            return text
    return str(config.get("fallback_text") or "")


def fit_font(draw, text, font_path, max_width, max_height, target_size):
    size = max(8, int(target_size))
    while size >= 8:
        try:
            font = ImageFont.truetype(font_path, size=size) if font_path else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=0)
        if bbox[2] - bbox[0] <= max_width and bbox[3] - bbox[1] <= max_height:
            return font
        size -= 2
    return ImageFont.load_default()


def render_rgba(config):
    fmt = config.get("format", {})
    width = int(fmt.get("width", 1920))
    height = int(fmt.get("height", 1080))
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    geocode = get_latest_geocode()
    text = render_text(config, geocode)
    with STATE.lock:
        STATE.latest_text = text
        STATE.latest_geocode = geocode

    box = config.get("box", {})
    x = int(float(box.get("x", 120)))
    y = int(float(box.get("y", 820)))
    bw = int(float(box.get("width", 900)))
    bh = int(float(box.get("height", 120)))
    padding = int(float(config.get("padding", 24)))
    scale = float(box.get("scale", 1.0))
    font_size = int(float(config.get("font_size", 72)) * scale)

    bg = hex_to_rgba(config.get("background_color", "#000000"), config.get("background_opacity", 0.35))
    if bg[3] > 0:
        draw.rounded_rectangle((x, y, x + bw, y + bh), radius=8, fill=bg)

    font_path = find_font(config.get("font_family"))
    font = fit_font(draw, text, font_path, max(1, bw - padding * 2), max(1, bh - padding * 2), font_size)
    stroke_width = int(float(config.get("stroke_width", 6)) * scale)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = x + padding
    ty = y + max(0, (bh - th) // 2) - bbox[1]
    if tw < bw - padding * 2:
        tx = x + (bw - tw) // 2 - bbox[0]

    draw.text(
        (tx, ty),
        text,
        font=font,
        fill=hex_to_rgba(config.get("text_color", "#ffffff"), 1.0),
        stroke_width=stroke_width,
        stroke_fill=hex_to_rgba(config.get("stroke_color", "#000000"), 1.0),
    )

    if fmt.get("safe_area"):
        margin_x = int(width * 0.05)
        margin_y = int(height * 0.05)
        draw.rectangle((margin_x, margin_y, width - margin_x, height - margin_y), outline=(255, 255, 255, 80), width=2)

    return img


def png_response(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


def output_devices():
    devices = [{"id": "", "label": "未選択", "kind": "none"}]
    for path in sorted(Path("/dev").glob("video*")):
        devices.append({"id": f"v4l2:{path}", "label": f"v4l2 {path}", "kind": "v4l2"})
    return devices


@app.get("/api/status")
def status():
    return STATE.snapshot()


@app.get("/api/output-devices")
def get_output_devices():
    return {"devices": output_devices()}


@app.get("/api/config")
def get_config():
    return STATE.snapshot()["config"]


@app.post("/api/config")
async def set_config(payload: dict):
    with STATE.lock:
        deep_update(STATE.config, payload)
        STATE.save_config()
    return {"ok": True, "config": STATE.snapshot()["config"]}


@app.post("/api/start")
def start():
    with STATE.lock:
        STATE.running = True
        STATE.error = ""
    return {"ok": True}


@app.post("/api/stop")
def stop():
    with STATE.lock:
        STATE.running = False
    return {"ok": True}


@app.get("/api/preview/v.png")
def preview_v():
    img = render_rgba(STATE.snapshot()["config"])
    bg = Image.new("RGBA", img.size, (0, 0, 0, 255))
    return png_response(Image.alpha_composite(bg, img).convert("RGB"))


@app.get("/api/preview/key.png")
def preview_key():
    img = render_rgba(STATE.snapshot()["config"])
    alpha = img.getchannel("A")
    return png_response(Image.merge("RGB", (alpha, alpha, alpha)))


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
