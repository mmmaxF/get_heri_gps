#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import logging
from logging.handlers import RotatingFileHandler
import os
import threading
from collections import deque
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from geocoder import AdminGeocoder


HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(float(os.environ.get("PORT", "8020")))
DB_PATH = Path(os.environ.get("GEOCODER_DB_PATH", "/app/data/admin_area.sqlite"))
OUTPUT_CSV = Path(os.environ.get("GEOCODER_OUTPUT_CSV", "/app/output/geocoded_positions.csv"))
LOG_DIR = Path(os.environ.get("LOG_DIR", "/app/logs"))
LOG_FILE = os.environ.get("LOG_FILE", "reverse_geocoder.log")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = int(float(os.environ.get("LOG_MAX_BYTES", 5 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(float(os.environ.get("LOG_BACKUP_COUNT", 5)))


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
LOGGER.info("flow=geocoder init db=%s output_csv=%s area_count=%s", DB_PATH, OUTPUT_CSV, geocoder.area_count())
lock = threading.Lock()
latest = None
history = deque(maxlen=100)


CSV_HEADER = ["time", "lon", "lat", "alt", "prefecture", "city", "ward", "address_label", "admin_code"]


def append_csv(row):
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    exists = OUTPUT_CSV.exists() and OUTPUT_CSV.stat().st_size > 0
    with OUTPUT_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(CSV_HEADER)
        w.writerow([row.get(k, "") for k in CSV_HEADER])
    LOGGER.info("flow=geocoder csv_write path=%s time=%s address=%s lat=%s lon=%s", OUTPUT_CSV, row.get("time"), row.get("address_label"), row.get("lat"), row.get("lon"))


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
    }
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
    with lock:
        global latest
        latest = response
        history.appendleft(response)
    if response.get("ok"):
        LOGGER.info("flow=geocoder success address=%s lat=%.8f lon=%.8f", response.get("address_label", ""), lat, lon)
    else:
        LOGGER.warning("flow=geocoder not_found lat=%.8f lon=%.8f error=%s", lat, lon, response.get("error", ""))
    return response


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
