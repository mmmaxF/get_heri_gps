#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from PIL import Image, ImageDraw, ImageFont


JST = timezone(timedelta(hours=9))


def env_int(name, default):
    try:
        return int(float(os.environ.get(name, default)))
    except ValueError:
        return default


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def rgba_env(name, default):
    raw = os.environ.get(name, default)
    parts = [int(float(item.strip())) for item in raw.split(",")]
    if len(parts) == 3:
        parts.append(255)
    if len(parts) != 4:
        raise ValueError(f"{name} must be R,G,B or R,G,B,A")
    return tuple(max(0, min(255, value)) for value in parts)


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = env_int("PORT", 8030)
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/app/output"))
LATEST_IMAGE = OUTPUT_DIR / "latest.png"
LATEST_JSON = OUTPUT_DIR / "latest.json"
IMAGE_WIDTH = env_int("IMAGE_WIDTH", 1920)
IMAGE_HEIGHT = env_int("IMAGE_HEIGHT", 1080)
TEXT_TEMPLATE = os.environ.get("TEXT_TEMPLATE", "{address_label}")
FONT_PATH = os.environ.get("FONT_PATH", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
FONT_SIZE = env_int("FONT_SIZE", 72)
TEXT_COLOR = rgba_env("TEXT_COLOR", "255,255,255,255")
BOX_COLOR = rgba_env("BOX_COLOR", "0,0,0,180")
MATTE_ENABLED = env_bool("MATTE_ENABLED", True)
BOX_PADDING_X = env_int("BOX_PADDING_X", 56)
BOX_PADDING_Y = env_int("BOX_PADDING_Y", 30)
POSITION_X = env_int("POSITION_X", 96)
POSITION_Y = env_int("POSITION_Y", 820)
POSITION_ANCHOR = os.environ.get("POSITION_ANCHOR", "top_left").strip().lower()
TEXT_ALIGN = os.environ.get("TEXT_ALIGN", "left").strip().lower()
MAX_TEXT_WIDTH = env_int("MAX_TEXT_WIDTH", 1500)
DEDUPE_TEXT = env_bool("DEDUPE_TEXT", True)
MIN_UPDATE_SECONDS = env_float("MIN_UPDATE_SECONDS", 3.0)

ATEM_ENABLED = env_bool("ATEM_ENABLED", False)
ATEM_HOST = os.environ.get("ATEM_HOST", "").strip()
ATEM_CONNECT_TIMEOUT_SECONDS = env_float("ATEM_CONNECT_TIMEOUT_SECONDS", 8.0)
ATEM_UPLOAD_TIMEOUT_SECONDS = env_float("ATEM_UPLOAD_TIMEOUT_SECONDS", 20.0)
ATEM_MEDIA_POOL_SLOT = env_int("ATEM_MEDIA_POOL_SLOT", 1)
ATEM_MEDIA_PLAYER = env_int("ATEM_MEDIA_PLAYER", 1)
ATEM_DSK = env_int("ATEM_DSK", 1)
ATEM_FILL_SOURCE = env_int("ATEM_FILL_SOURCE", 3010)
ATEM_KEY_SOURCE = env_int("ATEM_KEY_SOURCE", 3011)
ATEM_ON_AIR = env_bool("ATEM_ON_AIR", True)

LOG_DIR = Path(os.environ.get("LOG_DIR", "/app/logs"))
LOG_FILE = os.environ.get("LOG_FILE", "atem_output.log")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = env_int("LOG_MAX_BYTES", 5 * 1024 * 1024)
LOG_BACKUP_COUNT = env_int("LOG_BACKUP_COUNT", 5)


def setup_logger():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("atem_output")
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    if not logger.handlers:
        file_handler = RotatingFileHandler(
            LOG_DIR / LOG_FILE,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        stream_handler = logging.StreamHandler()
        file_handler.setFormatter(formatter)
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
    return logger


LOGGER = setup_logger()
app = FastAPI(title="atem_output")
lock = threading.Lock()
latest = {
    "ok": False,
    "error": "no graphic yet",
    "atem_enabled": ATEM_ENABLED,
    "atem_connected": False,
    "atem_sent": False,
}
last_text = ""
last_update_monotonic = 0.0


def now_jst():
    return datetime.now(JST)


def safe_format(template, payload):
    class Missing(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(Missing(payload)).strip()


def load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        LOGGER.warning("flow=atem font_fallback path=%s", FONT_PATH)
        return ImageFont.load_default()


def fit_font(text):
    size = FONT_SIZE
    while size >= 24:
        font = load_font(size)
        box = ImageDraw.Draw(Image.new("RGBA", (10, 10))).textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= MAX_TEXT_WIDTH:
            return font
        size -= 4
    return load_font(24)


def generate_png(text, clear_display=False):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (IMAGE_WIDTH, IMAGE_HEIGHT), (0, 0, 0, 0))
    if clear_display or not text:
        image.save(LATEST_IMAGE)
        return {
            "path": str(LATEST_IMAGE),
            "width": IMAGE_WIDTH,
            "height": IMAGE_HEIGHT,
            "clear_display": True,
        }

    draw = ImageDraw.Draw(image)
    font = fit_font(text)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    box_w = text_w + BOX_PADDING_X * 2
    box_h = text_h + BOX_PADDING_Y * 2
    if POSITION_ANCHOR in {"top_right", "right_top"}:
        box_x2 = min(IMAGE_WIDTH, POSITION_X)
        box_x1 = max(0, box_x2 - box_w)
        box_y1 = max(0, POSITION_Y)
        box_y2 = min(IMAGE_HEIGHT, box_y1 + box_h)
    else:
        box_x1 = max(0, POSITION_X)
        box_y1 = max(0, POSITION_Y)
        box_x2 = min(IMAGE_WIDTH, box_x1 + box_w)
        box_y2 = min(IMAGE_HEIGHT, box_y1 + box_h)
    if MATTE_ENABLED and BOX_COLOR[3] > 0:
        draw.rounded_rectangle((box_x1, box_y1, box_x2, box_y2), radius=0, fill=BOX_COLOR)
    if TEXT_ALIGN == "right":
        text_x = box_x2 - BOX_PADDING_X - text_w
    else:
        text_x = box_x1 + BOX_PADDING_X
    draw.text(
        (text_x, box_y1 + BOX_PADDING_Y - bbox[1]),
        text,
        fill=TEXT_COLOR,
        font=font,
    )
    image.save(LATEST_IMAGE)
    return {
        "path": str(LATEST_IMAGE),
        "width": IMAGE_WIDTH,
        "height": IMAGE_HEIGHT,
        "clear_display": False,
    }


class AtemUploadResult(dict):
    pass


def upload_to_atem(image_path, text, clear_display=False):
    if not ATEM_ENABLED:
        return AtemUploadResult(
            enabled=False,
            sent=False,
            skipped=True,
            reason="ATEM_ENABLED=0",
        )
    if not ATEM_HOST:
        return AtemUploadResult(
            enabled=True,
            sent=False,
            skipped=False,
            error="ATEM_HOST is empty",
        )
    try:
        return upload_to_atem_with_pyatem(image_path, text, clear_display)
    except Exception as exc:
        LOGGER.exception("flow=atem upload_error host=%s error=%s", ATEM_HOST, exc)
        return AtemUploadResult(
            enabled=True,
            sent=False,
            skipped=False,
            error=str(exc),
        )


def upload_to_atem_with_pyatem(image_path, text, clear_display=False):
    command = [
        sys.executable,
        "/app/atem_sender.py",
        "--host",
        ATEM_HOST,
        "--image",
        str(image_path),
        "--text",
        text,
        "--timeout",
        str(ATEM_UPLOAD_TIMEOUT_SECONDS),
    ]
    if clear_display:
        command.append("--clear-display")
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=ATEM_UPLOAD_TIMEOUT_SECONDS + 5,
        check=True,
    )
    if completed.stderr.strip():
        LOGGER.info("flow=atem sender_stderr %s", completed.stderr.strip())
    result = json.loads(completed.stdout.strip())
    LOGGER.info(
        "flow=atem connected host=%s mode=%s resolution=%s",
        result.get("host"),
        result.get("mode", ""),
        result.get("resolution", ""),
    )
    return AtemUploadResult(result)


def persist_latest(data):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def index():
    return """
<!doctype html>
<html lang="ja">
<meta charset="utf-8">
<title>ATEM PNG出力</title>
<body style="font-family:system-ui,sans-serif;margin:24px;background:#f3f5f6;color:#162027">
  <h1>ATEM PNG出力</h1>
  <p>最新PNG: <a href="/api/preview.png">/api/preview.png</a></p>
  <p>状態: <a href="/api/health">/api/health</a></p>
  <img src="/api/preview.png" style="max-width:100%;background:#222">
</body>
</html>
"""


@app.get("/api/health")
def health():
    with lock:
        data = dict(latest)
    return {
        "ok": True,
        "service": "atem-output",
        "atem_enabled": ATEM_ENABLED,
        "atem_host": ATEM_HOST,
        "image_exists": LATEST_IMAGE.exists(),
        "latest": data,
    }


@app.get("/api/latest")
def get_latest():
    with lock:
        return latest


@app.get("/api/preview.png")
def preview_png():
    if not LATEST_IMAGE.exists():
        generate_png("地名表示テスト")
    return FileResponse(LATEST_IMAGE, media_type="image/png", filename="atem_latest.png")


@app.post("/api/position")
async def post_position(payload: dict):
    global latest, last_text, last_update_monotonic
    clear_display = bool(payload.get("clear_display")) or payload.get("ok") is False
    text = "" if clear_display else safe_format(TEXT_TEMPLATE, payload)
    now = time.monotonic()
    if DEDUPE_TEXT and text == last_text and now - last_update_monotonic < MIN_UPDATE_SECONDS:
        response = {
            "ok": True,
            "sent": False,
            "skipped": True,
            "reason": "dedupe/min_interval",
            "text": text,
            "clear_display": clear_display,
            "time": payload.get("time", ""),
            "updated_at": now_jst().strftime("%Y/%m/%d %H:%M:%S"),
            "preview_url": "/api/preview.png",
        }
        with lock:
            latest = {**latest, **response}
        return response

    graphic = generate_png(text, clear_display=clear_display)
    upload = upload_to_atem(LATEST_IMAGE, text, clear_display=clear_display)
    response = {
        "ok": not bool(upload.get("error")),
        "sent": bool(upload.get("sent")),
        "skipped": bool(upload.get("skipped")),
        "error": upload.get("error", ""),
        "reason": upload.get("reason", ""),
        "text": text,
        "clear_display": clear_display,
        "time": payload.get("time", ""),
        "updated_at": now_jst().strftime("%Y/%m/%d %H:%M:%S"),
        "preview_url": "/api/preview.png",
        "graphic": graphic,
        "atem": dict(upload),
        "atem_enabled": ATEM_ENABLED,
        "atem_connected": bool(upload.get("sent")),
        "atem_sent": bool(upload.get("sent")),
    }
    with lock:
        latest = response
        last_text = text
        last_update_monotonic = now
    persist_latest(response)
    if response["sent"]:
        LOGGER.info("flow=atem sent text=%s host=%s", text, ATEM_HOST)
    elif response["skipped"]:
        LOGGER.info("flow=atem skipped reason=%s text=%s", response["reason"], text)
    else:
        LOGGER.warning("flow=atem not_sent error=%s text=%s", response["error"], text)
    return response


@app.post("/api/test")
async def test_graphic(payload: dict | None = None):
    payload = payload or {}
    text = payload.get("text", "大阪府大阪市")
    return await post_position({"ok": True, "address_label": text, "time": now_jst().strftime("%Y/%m/%d %H:%M:%S")})


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
