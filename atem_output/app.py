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
UPLOAD_IMAGE_PREFIX = "atem_upload_"
IMAGE_WIDTH = env_int("IMAGE_WIDTH", 1920)
IMAGE_HEIGHT = env_int("IMAGE_HEIGHT", 1080)
TEXT_TEMPLATE = os.environ.get("ATEM_TEXT_TEMPLATE", os.environ.get("TEXT_TEMPLATE", "{address_label}"))
TEXT_STATION_ENABLED = env_bool("ATEM_TEXT_STATION_ENABLED", env_bool("ATEM_TEXT_HEADER_ENABLED", True))
TEXT_STATION_TEMPLATE = os.environ.get("ATEM_TEXT_STATION_TEMPLATE", "YTV")
TEXT_TIME_ENABLED = env_bool("ATEM_TEXT_TIME_ENABLED", env_bool("ATEM_TEXT_HEADER_ENABLED", True))
TEXT_TIME_TEMPLATE = os.environ.get("ATEM_TEXT_TIME_TEMPLATE", "{hhmm}")
TEXT_HEADER_ENABLED = env_bool("ATEM_TEXT_HEADER_ENABLED", True)
TEXT_HEADER_TEMPLATE = os.environ.get("ATEM_TEXT_HEADER_TEMPLATE", "{atem_station} {atem_time}")
CAPTURE_LINE_ENABLED = env_bool("ATEM_CAPTURE_LINE_ENABLED", False)
CAPTURE_STATION_ENABLED = env_bool("ATEM_CAPTURE_STATION_ENABLED", TEXT_STATION_ENABLED)
CAPTURE_TIME_ENABLED = env_bool("ATEM_CAPTURE_TIME_ENABLED", TEXT_TIME_ENABLED)
CAPTURE_HEADER_TEMPLATE = os.environ.get("ATEM_CAPTURE_HEADER_TEMPLATE", "{atem_capture_station} {atem_capture_time}")
CAPTURE_LINE_TEMPLATE = os.environ.get("ATEM_CAPTURE_LINE_TEMPLATE", "{atem_capture_header} {capture_address_label} 撮影")
CAPTURE_LINE_SHOW_ON_UNKNOWN = env_bool("ATEM_CAPTURE_LINE_SHOW_ON_UNKNOWN", False)
CAPTURE_LINE_UNKNOWN_LABEL = os.environ.get("ATEM_CAPTURE_LINE_UNKNOWN_LABEL", "撮影位置不明")
LINE_SPACING = env_int("LINE_SPACING", 4)
FONT_PATH = os.environ.get("FONT_PATH", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
FONT_SIZE = env_int("FONT_SIZE", 72)
TEXT_COLOR = rgba_env("TEXT_COLOR", "255,255,255,255")
TEXT_STROKE_WIDTH = env_int("TEXT_STROKE_WIDTH", 2)
TEXT_STROKE_COLOR = rgba_env("TEXT_STROKE_COLOR", "0,0,0,255")
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
ATEM_UPLOAD_COMPRESS = env_bool("ATEM_UPLOAD_COMPRESS", False)
ATEM_PERSISTENT_CONNECTION = env_bool("ATEM_PERSISTENT_CONNECTION", True)
ATEM_ASYNC_UPLOAD = env_bool("ATEM_ASYNC_UPLOAD", True)

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
latest_upload_job_id = 0


def now_jst():
    return datetime.now(JST)


def safe_format(template, payload):
    class Missing(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(Missing(payload)).strip()


def compact_spaces(text):
    return " ".join(str(text).split())


def time_hm(payload):
    raw = str(payload.get("time", "")).strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M")
        except ValueError:
            pass
    if len(raw) >= 16 and raw[13] == ":":
        return raw[11:16]
    if len(raw) >= 5 and raw[2] == ":":
        return raw[:5]
    return now_jst().strftime("%H:%M")


def render_text(payload):
    data = dict(payload)
    data["hhmm"] = time_hm(data)
    data["atem_station"] = compact_spaces(safe_format(TEXT_STATION_TEMPLATE, data)) if TEXT_STATION_ENABLED else ""
    data["atem_time"] = compact_spaces(safe_format(TEXT_TIME_TEMPLATE, data)) if TEXT_TIME_ENABLED else ""
    data["atem_header"] = compact_spaces(safe_format(TEXT_HEADER_TEMPLATE, data)) if TEXT_HEADER_ENABLED else ""
    data["atem_capture_station"] = compact_spaces(safe_format(TEXT_STATION_TEMPLATE, data)) if CAPTURE_STATION_ENABLED else ""
    data["atem_capture_time"] = compact_spaces(safe_format(TEXT_TIME_TEMPLATE, data)) if CAPTURE_TIME_ENABLED else ""
    data["atem_capture_header"] = compact_spaces(safe_format(CAPTURE_HEADER_TEMPLATE, data))
    if not data.get("capture_address_label") and CAPTURE_LINE_SHOW_ON_UNKNOWN:
        data["capture_address_label"] = CAPTURE_LINE_UNKNOWN_LABEL
    lines = [compact_spaces(safe_format(TEXT_TEMPLATE, data))]
    if CAPTURE_LINE_ENABLED and data.get("capture_address_label"):
        lines.append(compact_spaces(safe_format(CAPTURE_LINE_TEMPLATE, data)))
    return "\n".join(line for line in lines if line)


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
        draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        box = draw.multiline_textbbox(
            (0, 0),
            text,
            font=font,
            stroke_width=TEXT_STROKE_WIDTH,
            spacing=LINE_SPACING,
        )
        if box[2] - box[0] <= MAX_TEXT_WIDTH:
            return font
        size -= 4
    return load_font(24)


def text_size(draw, text, font):
    bbox = draw.multiline_textbbox(
        (0, 0),
        text,
        font=font,
        stroke_width=TEXT_STROKE_WIDTH,
        spacing=LINE_SPACING,
    )
    return bbox, bbox[2] - bbox[0], bbox[3] - bbox[1]


def generate_png(text, clear_display=False, image_path=LATEST_IMAGE, update_latest=True):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (IMAGE_WIDTH, IMAGE_HEIGHT), (0, 0, 0, 0))
    if clear_display or not text:
        image.save(image_path)
        if update_latest and image_path != LATEST_IMAGE:
            image.save(LATEST_IMAGE)
        return {
            "path": str(image_path),
            "width": IMAGE_WIDTH,
            "height": IMAGE_HEIGHT,
            "clear_display": True,
        }

    draw = ImageDraw.Draw(image)
    font = fit_font(text)
    bbox, text_w, text_h = text_size(draw, text, font)
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
    draw.multiline_text(
        (text_x, box_y1 + BOX_PADDING_Y - bbox[1]),
        text,
        fill=TEXT_COLOR,
        font=font,
        stroke_width=TEXT_STROKE_WIDTH,
        stroke_fill=TEXT_STROKE_COLOR,
        spacing=LINE_SPACING,
        align="right" if TEXT_ALIGN == "right" else "left",
    )
    image.save(image_path)
    if update_latest and image_path != LATEST_IMAGE:
        image.save(LATEST_IMAGE)
    return {
        "path": str(image_path),
        "width": IMAGE_WIDTH,
        "height": IMAGE_HEIGHT,
        "clear_display": False,
    }


class AtemUploadResult(dict):
    pass


class PersistentAtemClient:
    def __init__(self):
        self.lock = threading.Lock()
        self.connection = None
        self.connected = False
        self.mode_label = ""
        self.resolution = None
        self.upload_done = False
        self.upload_output = None

    def reset(self):
        self.connection = None
        self.connected = False
        self.mode_label = ""
        self.resolution = None
        self.upload_done = False
        self.upload_output = None

    def ensure_connected(self, deadline):
        if self.connection is not None and self.connected and self.resolution:
            return True

        from pyatem.protocol import AtemProtocol

        self.reset()
        connection = AtemProtocol(ATEM_HOST)
        self.connection = connection

        def connected():
            mode = connection.mixerstate["video-mode"]
            self.mode_label = mode.get_label()
            self.resolution = mode.get_resolution()
            self.connected = True

        connection.on("connected", connected)
        connection.on("upload-done", self.uploaded)
        connection.connect()

        while time.monotonic() < deadline and not self.connected:
            connection.loop()

        if not self.connected:
            self.reset()
            raise TimeoutError("ATEM connect timed out")
        return True

    def uploaded(self, store, slot):
        from pyatem.command import (
            DkeyOnairCommand,
            DkeySetFillCommand,
            DkeySetKeyCommand,
            MediaplayerSelectCommand,
        )

        slot_index = max(0, ATEM_MEDIA_POOL_SLOT - 1)
        player_index = max(0, ATEM_MEDIA_PLAYER - 1)
        dsk_index = max(0, ATEM_DSK - 1)
        clear_display = bool((self.upload_output or {}).get("clear_display"))
        commands = [
            MediaplayerSelectCommand(player_index, still=slot_index),
            DkeySetFillCommand(dsk_index, ATEM_FILL_SOURCE),
            DkeySetKeyCommand(dsk_index, ATEM_KEY_SOURCE),
            DkeyOnairCommand(dsk_index, bool(ATEM_ON_AIR and not clear_display)),
        ]
        self.connection.send_commands(commands)
        if self.upload_output is not None:
            self.upload_output["sent"] = True
        self.upload_done = True

    def upload(self, image_path, text, clear_display=False):
        from pyatem.command import TimeRequestCommand
        import pyatem.media

        with self.lock:
            started = time.monotonic()
            deadline = started + ATEM_UPLOAD_TIMEOUT_SECONDS
            was_connected = self.connection is not None and self.connected
            self.ensure_connected(deadline)

            slot_index = max(0, ATEM_MEDIA_POOL_SLOT - 1)
            output = AtemUploadResult(
                enabled=True,
                sent=False,
                skipped=False,
                host=ATEM_HOST,
                media_pool_slot=ATEM_MEDIA_POOL_SLOT,
                media_player=ATEM_MEDIA_PLAYER,
                dsk=ATEM_DSK,
                fill_source=ATEM_FILL_SOURCE,
                key_source=ATEM_KEY_SOURCE,
                mode=self.mode_label,
                resolution=list(self.resolution),
                persistent=True,
                connection_reused=was_connected,
                compress=ATEM_UPLOAD_COMPRESS,
                clear_display=bool(clear_display),
            )
            self.upload_output = output
            self.upload_done = False

            frame = Image.new("RGBA", self.resolution, (0, 0, 0, 0))
            graphic = Image.open(image_path).convert("RGBA")
            graphic.thumbnail(self.resolution, Image.Resampling.LANCZOS)
            frame.alpha_composite(graphic, (0, 0))
            frame_atem = pyatem.media.rgb_to_atem(frame.tobytes(), *self.resolution)

            self.connection.send_commands([TimeRequestCommand()])
            self.connection.upload(
                0,
                slot_index,
                frame_atem,
                name=text[:31] or "heri_gps",
                compress=ATEM_UPLOAD_COMPRESS,
            )

            while time.monotonic() < deadline and not self.upload_done:
                self.connection.loop()

            if not self.upload_done:
                self.reset()
                raise TimeoutError("ATEM upload timed out")

            output["duration_seconds"] = round(time.monotonic() - started, 3)
            return output


ATEM_CLIENT = PersistentAtemClient()


class AtemUploadWorker:
    def __init__(self):
        self.condition = threading.Condition()
        self.pending = None
        self.active = None
        self.thread = threading.Thread(target=self.run, daemon=True, name="atem-upload-worker")
        self.thread.start()

    def enqueue(self, job):
        with self.condition:
            dropped = self.pending
            self.pending = job
            self.condition.notify()
        if dropped:
            self.cleanup_job(dropped)
            LOGGER.info(
                "flow=atem upload_drop_old job_id=%s text=%s",
                dropped.get("job_id"),
                dropped.get("text", ""),
            )

    def snapshot(self):
        with self.condition:
            return {
                "pending": bool(self.pending),
                "pending_job_id": self.pending.get("job_id") if self.pending else None,
                "active": bool(self.active),
                "active_job_id": self.active.get("job_id") if self.active else None,
            }

    def cleanup_job(self, job):
        path = job.get("image_path")
        if not path:
            return
        try:
            candidate = Path(path)
            if candidate.name.startswith(UPLOAD_IMAGE_PREFIX) and candidate.exists():
                candidate.unlink()
        except OSError as exc:
            LOGGER.warning("flow=atem upload_cleanup_error path=%s error=%s", path, exc)

    def run(self):
        while True:
            with self.condition:
                while self.pending is None:
                    self.condition.wait()
                job = self.pending
                self.pending = None
                self.active = job
            try:
                upload = upload_to_atem(
                    Path(job["image_path"]),
                    job.get("text", ""),
                    clear_display=bool(job.get("clear_display")),
                )
                response = {
                    "ok": not bool(upload.get("error")),
                    "sent": bool(upload.get("sent")),
                    "skipped": bool(upload.get("skipped")),
                    "error": upload.get("error", ""),
                    "reason": upload.get("reason", ""),
                    "text": job.get("text", ""),
                    "clear_display": bool(job.get("clear_display")),
                    "time": job.get("time", ""),
                    "updated_at": now_jst().strftime("%Y/%m/%d %H:%M:%S"),
                    "preview_url": "/api/preview.png",
                    "graphic": job.get("graphic", {}),
                    "atem": dict(upload),
                    "atem_enabled": ATEM_ENABLED,
                    "atem_connected": bool(upload.get("sent")),
                    "atem_sent": bool(upload.get("sent")),
                    "async_upload": True,
                    "upload_job_id": job.get("job_id"),
                    "upload_queued": False,
                    "upload_pending": False,
                }
                with lock:
                    global latest
                    current_job_id = int(latest.get("upload_job_id") or 0)
                    if current_job_id <= int(job.get("job_id") or 0):
                        latest = {**latest, **response}
                        persist_data = dict(latest)
                    else:
                        latest["last_upload"] = response
                        persist_data = dict(latest)
                persist_latest(persist_data)
                if response["sent"]:
                    LOGGER.info(
                        "flow=atem sent job_id=%s text=%s host=%s",
                        job.get("job_id"),
                        job.get("text", ""),
                        ATEM_HOST,
                    )
                elif response["skipped"]:
                    LOGGER.info(
                        "flow=atem skipped job_id=%s reason=%s text=%s",
                        job.get("job_id"),
                        response["reason"],
                        job.get("text", ""),
                    )
                else:
                    LOGGER.warning(
                        "flow=atem not_sent job_id=%s error=%s text=%s",
                        job.get("job_id"),
                        response["error"],
                        job.get("text", ""),
                    )
            except Exception as exc:
                LOGGER.exception(
                    "flow=atem worker_error job_id=%s error=%s",
                    job.get("job_id"),
                    exc,
                )
            finally:
                self.cleanup_job(job)
                with self.condition:
                    self.active = None


ATEM_WORKER = AtemUploadWorker()


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
        if ATEM_PERSISTENT_CONNECTION:
            return ATEM_CLIENT.upload(image_path, text, clear_display)
        return upload_to_atem_with_pyatem_subprocess(image_path, text, clear_display)
    except Exception as exc:
        LOGGER.exception("flow=atem upload_error host=%s error=%s", ATEM_HOST, exc)
        return AtemUploadResult(
            enabled=True,
            sent=False,
            skipped=False,
            error=str(exc),
        )


def upload_to_atem_with_pyatem_subprocess(image_path, text, clear_display=False):
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
        "async_upload": ATEM_ASYNC_UPLOAD,
        "upload_worker": ATEM_WORKER.snapshot(),
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
def post_position(payload: dict):
    global latest, last_text, last_update_monotonic, latest_upload_job_id
    clear_display = bool(payload.get("clear_display")) or payload.get("ok") is False
    text = "" if clear_display else render_text(payload)
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

    latest_upload_job_id += 1
    job_id = latest_upload_job_id
    upload_image = OUTPUT_DIR / f"{UPLOAD_IMAGE_PREFIX}{job_id}.png"
    graphic = generate_png(text, clear_display=clear_display, image_path=upload_image)
    if ATEM_ASYNC_UPLOAD:
        response = {
            "ok": True,
            "sent": False,
            "skipped": False,
            "error": "",
            "reason": "",
            "text": text,
            "clear_display": clear_display,
            "time": payload.get("time", ""),
            "updated_at": now_jst().strftime("%Y/%m/%d %H:%M:%S"),
            "preview_url": "/api/preview.png",
            "graphic": {**graphic, "path": str(LATEST_IMAGE)},
            "atem": {"enabled": ATEM_ENABLED, "queued": True},
            "atem_enabled": ATEM_ENABLED,
            "atem_connected": False,
            "atem_sent": False,
            "async_upload": True,
            "upload_queued": True,
            "upload_job_id": job_id,
        }
        with lock:
            latest = response
            last_text = text
            last_update_monotonic = now
        persist_latest(response)
        ATEM_WORKER.enqueue(
            {
                "job_id": job_id,
                "image_path": str(upload_image),
                "text": text,
                "clear_display": clear_display,
                "time": payload.get("time", ""),
                "graphic": {**graphic, "path": str(LATEST_IMAGE)},
            }
        )
        LOGGER.info("flow=atem queued job_id=%s text=%s", job_id, text)
        return response

    graphic = {**graphic, "path": str(LATEST_IMAGE)}
    upload = upload_to_atem(upload_image, text, clear_display=clear_display)
    try:
        upload_image.unlink()
    except OSError:
        pass
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
    return post_position({"ok": True, "address_label": text, "time": now_jst().strftime("%Y/%m/%d %H:%M:%S")})


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
