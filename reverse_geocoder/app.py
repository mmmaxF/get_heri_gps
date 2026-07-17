#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import logging
from logging.handlers import RotatingFileHandler
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
    }
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
