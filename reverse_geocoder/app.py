#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import queue
import re
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from geocoder import AdminGeocoder
from outputs import OutputManager


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(float(os.environ.get("PORT", "8020")))
DB_PATH = Path(os.environ.get("GEOCODER_DB_PATH", "/app/data/admin_area.sqlite"))
OUTPUT_CSV = Path(os.environ.get("GEOCODER_OUTPUT_CSV", "/app/output/geocoded_positions.csv"))
LOG_DIR = Path(os.environ.get("LOG_DIR", "/app/logs"))
LOG_FILE = os.environ.get("LOG_FILE", "reverse_geocoder.log")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = int(float(os.environ.get("LOG_MAX_BYTES", 5 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(float(os.environ.get("LOG_BACKUP_COUNT", 5)))
OUTPUT_QUEUE_SIZE = int(float(os.environ.get("OUTPUT_QUEUE_SIZE", 10)))
CSV_RETENTION_DAYS = int(float(os.environ.get("CSV_RETENTION_DAYS", 90)))
JST = timezone(timedelta(hours=9))
CAPTURE_LOCATION_ENABLED = os.environ.get("CAPTURE_LOCATION_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
CAPTURE_CAMERA_HEADING_BCD_OFFSET = int(float(os.environ.get("CAPTURE_CAMERA_HEADING_BCD_OFFSET", "31")))
CAPTURE_CAMERA_HEADING_SCALE = float(os.environ.get("CAPTURE_CAMERA_HEADING_SCALE", "0.1"))
CAPTURE_CAMERA_TILT_BCD_OFFSET = int(float(os.environ.get("CAPTURE_CAMERA_TILT_BCD_OFFSET", "29")))
CAPTURE_CAMERA_TILT_SCALE = float(os.environ.get("CAPTURE_CAMERA_TILT_SCALE", "0.01"))
CAPTURE_TILT_MIN_DEGREES = float(os.environ.get("CAPTURE_TILT_MIN_DEGREES", "1.0"))
CAPTURE_MAX_DISTANCE_METERS = float(os.environ.get("CAPTURE_MAX_DISTANCE_METERS", "10000"))


def setup_logger():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("reverse_geocoder")
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
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
    return logger


LOGGER = setup_logger()

app = FastAPI(title="reverse_geocoder")
geocoder = AdminGeocoder(DB_PATH)
outputs = OutputManager()
LOGGER.info(
    "flow=geocoder init db=%s output_csv=%s area_count=%s output_adapters=%s",
    DB_PATH,
    OUTPUT_CSV,
    geocoder.area_count(),
    ",".join(outputs.names()) or "none",
)
lock = threading.Lock()
latest = None
history = deque(maxlen=100)
output_queue = queue.Queue(maxsize=OUTPUT_QUEUE_SIZE)


CSV_HEADER = ["time", "lon", "lat", "alt", "prefecture", "city", "ward", "address_label", "admin_code"]


class DailyCsvAppender:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.active_day = (
            datetime.fromtimestamp(self.path.stat().st_mtime, JST).date()
            if self.path.exists() and self.path.stat().st_size > 0
            else None
        )
        self._cleanup(datetime.now(JST).date())

    def _row_day(self, row):
        try:
            return datetime.strptime(
                str(row.get("time", ""))[:10],
                "%Y/%m/%d",
            ).date()
        except ValueError:
            return datetime.now(JST).date()

    def _archive_path(self, day):
        base = self.path.with_name(
            f"{self.path.stem}.{day.isoformat()}{self.path.suffix}"
        )
        candidate = base
        index = 1
        while candidate.exists():
            candidate = self.path.with_name(
                f"{self.path.stem}.{day.isoformat()}.{index}{self.path.suffix}"
            )
            index += 1
        return candidate

    def _cleanup(self, current_day):
        if CSV_RETENTION_DAYS <= 0:
            return
        cutoff = current_day - timedelta(days=CSV_RETENTION_DAYS)
        pattern = re.compile(
            rf"^{re.escape(self.path.stem)}\.(\d{{4}}-\d{{2}}-\d{{2}})(?:\.\d+)?{re.escape(self.path.suffix)}$"
        )
        for candidate in self.path.parent.glob(
            f"{self.path.stem}.*{self.path.suffix}"
        ):
            match = pattern.match(candidate.name)
            if not match:
                continue
            try:
                archive_day = datetime.strptime(
                    match.group(1),
                    "%Y-%m-%d",
                ).date()
            except ValueError:
                continue
            if archive_day < cutoff:
                candidate.unlink()
                LOGGER.info("flow=geocoder csv_retention_delete path=%s", candidate)

    def append(self, row):
        day = self._row_day(row)
        with self.lock:
            if self.active_day is not None and day > self.active_day:
                if self.path.exists() and self.path.stat().st_size > 0:
                    archive = self._archive_path(self.active_day)
                    self.path.replace(archive)
                    LOGGER.info(
                        "flow=geocoder csv_rotate path=%s archive=%s",
                        self.path,
                        archive,
                    )
                self._cleanup(day)
                self.active_day = day
            elif self.active_day is None:
                self.active_day = day
            exists = self.path.exists() and self.path.stat().st_size > 0
            with self.path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not exists:
                    writer.writerow(CSV_HEADER)
                writer.writerow([row.get(k, "") for k in CSV_HEADER])


csv_appender = DailyCsvAppender(OUTPUT_CSV)


def append_csv(row):
    csv_appender.append(row)
    LOGGER.info("flow=geocoder csv_write path=%s time=%s address=%s lat=%s lon=%s", OUTPUT_CSV, row.get("time"), row.get("address_label"), row.get("lat"), row.get("lon"))


def bcd_byte(value):
    hi = (value >> 4) & 0xF
    lo = value & 0xF
    if hi > 9 or lo > 9:
        return None
    return hi * 10 + lo


def parse_bcd_u16(payload, offset):
    if offset < 0 or offset + 1 >= len(payload):
        return None
    high = bcd_byte(payload[offset])
    low = bcd_byte(payload[offset + 1])
    if high is None or low is None:
        return None
    return high * 100 + low


def project_position(lat, lon, bearing_degrees, distance_meters):
    radius = 6378137.0
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    bearing = math.radians(bearing_degrees)
    angular = distance_meters / radius
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), ((math.degrees(lon2) + 540) % 360) - 180


def capture_location_from_payload(payload, lat, lon, alt):
    if not CAPTURE_LOCATION_ENABLED:
        return {}
    payload_hex = str(payload.get("payload_hex", "")).strip()
    if not payload_hex:
        return {}
    try:
        raw = bytes.fromhex(payload_hex)
        altitude = float(alt)
    except (TypeError, ValueError):
        return {"capture_error": "invalid payload/alt"}

    heading_raw = parse_bcd_u16(raw, CAPTURE_CAMERA_HEADING_BCD_OFFSET)
    tilt_raw = parse_bcd_u16(raw, CAPTURE_CAMERA_TILT_BCD_OFFSET)
    if heading_raw is None or tilt_raw is None:
        return {"capture_error": "camera fields unavailable"}

    heading = (heading_raw * CAPTURE_CAMERA_HEADING_SCALE) % 360.0
    tilt = tilt_raw * CAPTURE_CAMERA_TILT_SCALE
    if tilt < CAPTURE_TILT_MIN_DEGREES:
        return {
            "capture_heading": round(heading, 3),
            "capture_tilt": round(tilt, 3),
            "capture_error": "tilt too shallow",
        }

    distance = altitude / math.tan(math.radians(tilt))
    if not math.isfinite(distance) or distance <= 0:
        return {"capture_heading": round(heading, 3), "capture_tilt": round(tilt, 3), "capture_error": "invalid distance"}
    distance = min(distance, CAPTURE_MAX_DISTANCE_METERS)
    target_lat, target_lon = project_position(lat, lon, heading, distance)
    capture_geocode = geocoder.reverse(target_lat, target_lon)
    response = {
        "capture_enabled": True,
        "capture_lat": target_lat,
        "capture_lon": target_lon,
        "capture_distance_m": round(distance, 1),
        "capture_heading": round(heading, 3),
        "capture_tilt": round(tilt, 3),
        "capture_ok": bool(capture_geocode.get("ok")),
        "capture_prefecture": capture_geocode.get("prefecture", ""),
        "capture_city": capture_geocode.get("city", ""),
        "capture_ward": capture_geocode.get("ward", ""),
        "capture_address_label": capture_geocode.get("address_label", ""),
        "capture_admin_code": capture_geocode.get("admin_code", ""),
    }
    if not capture_geocode.get("ok"):
        response["capture_error"] = capture_geocode.get("error", "capture reverse geocode not found")
    LOGGER.info(
        "flow=capture_location heading=%.3f tilt=%.3f distance_m=%.1f lat=%.8f lon=%.8f address=%s",
        response["capture_heading"],
        response["capture_tilt"],
        response["capture_distance_m"],
        target_lat,
        target_lon,
        response["capture_address_label"],
    )
    return response


def output_worker():
    while True:
        response = output_queue.get()
        try:
            output_results = outputs.send_all(response)
            with lock:
                response["outputs"] = output_results
                for result in output_results:
                    if result.get("name") == "multiviewer":
                        response["multiviewer"] = result
                    elif result.get("name") == "atem":
                        response["atem"] = result
            for result in output_results:
                if result.get("sent"):
                    LOGGER.info("flow=output sent name=%s text=%s", result.get("name"), result.get("text", ""))
                elif result.get("skipped"):
                    LOGGER.info("flow=output skipped name=%s reason=%s text=%s", result.get("name"), result.get("reason", ""), result.get("text", ""))
                elif result.get("error"):
                    LOGGER.warning("flow=output error name=%s error=%s", result.get("name"), result.get("error"))
        except Exception:
            LOGGER.exception("flow=output worker_error")
        finally:
            output_queue.task_done()


def enqueue_output(response):
    try:
        output_queue.put_nowait(response)
    except queue.Full:
        try:
            output_queue.get_nowait()
            output_queue.task_done()
        except queue.Empty:
            pass
        output_queue.put_nowait(response)
        LOGGER.warning("flow=output queue_full dropped_oldest size=%s", OUTPUT_QUEUE_SIZE)


threading.Thread(target=output_worker, daemon=True).start()


@app.get("/api/health")
def health():
    return {"ok": True, "db_loaded": DB_PATH.exists(), "area_count": geocoder.area_count()}


@app.get("/api/latest")
def get_latest():
    with lock:
        return latest or {"ok": False, "error": "no position yet"}


@app.get("/api/history")
def get_history():
    with lock:
        return {"items": list(history)}


@app.post("/api/position")
async def post_position(payload: dict):
    if payload.get("event") == "decode_unavailable":
        response = {
            "ok": False,
            "event": "decode_unavailable",
            "clear_display": True,
            "error": payload.get("reason", "GPS fix unavailable"),
            "prefecture": "",
            "city": "",
            "ward": "",
            "address_label": "",
            "admin_code": "",
            "time": payload.get("time", ""),
            "lat": "",
            "lon": "",
            "alt": "",
            "source": payload.get("source", ""),
            "channel": payload.get("channel", ""),
        }
        response["outputs"] = [
            {"name": name, "queued": True}
            for name in outputs.names()
        ]
        with lock:
            global latest
            latest = response
            history.appendleft(response)
        enqueue_output(response)
        LOGGER.warning(
            "flow=geocoder decode_unavailable time=%s source=%s channel=%s",
            response["time"],
            response["source"],
            response["channel"],
        )
        return response

    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
    except (KeyError, TypeError, ValueError):
        LOGGER.warning("flow=geocoder invalid_payload payload=%s", payload)
        return JSONResponse({"ok": False, "error": "lat/lon required"}, status_code=400)

    LOGGER.info("flow=geocoder receive lat=%.8f lon=%.8f alt=%s source=%s channel=%s", lat, lon, payload.get("alt", ""), payload.get("source", ""), payload.get("channel", ""))
    result = geocoder.reverse(lat, lon)
    response = {
        **result,
        "time": payload.get("time", ""),
        "lat": lat,
        "lon": lon,
        "alt": payload.get("alt", ""),
        "source": payload.get("source", ""),
        "channel": payload.get("channel", ""),
        "payload_hex": payload.get("payload_hex", ""),
    }
    response.update(capture_location_from_payload(payload, lat, lon, response["alt"]))
    if not response.get("ok"):
        response["clear_display"] = True
    csv_row = {
        "time": response["time"],
        "lon": f"{lon:.8f}",
        "lat": f"{lat:.8f}",
        "alt": response["alt"],
        "prefecture": response.get("prefecture", ""),
        "city": response.get("city", ""),
        "ward": response.get("ward", ""),
        "address_label": response.get("address_label", ""),
        "admin_code": response.get("admin_code", ""),
    }
    append_csv(csv_row)
    response["outputs"] = [
        {"name": name, "queued": True}
        for name in outputs.names()
    ]
    with lock:
        latest = response
        history.appendleft(response)
    enqueue_output(response)
    if response.get("ok"):
        LOGGER.info("flow=geocoder success address=%s lat=%.8f lon=%.8f", response.get("address_label", ""), lat, lon)
    else:
        LOGGER.warning("flow=geocoder not_found lat=%.8f lon=%.8f error=%s", lat, lon, response.get("error", ""))
    return response


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
