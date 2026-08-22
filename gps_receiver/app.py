#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web app for demodulating GPS/MOD frames from one SDI audio input."""

import asyncio
import csv
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import queue
import re
import shlex
import smtplib
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from gps_demodulator import DEFAULT_BAUD, crc16_x25, decode_samples


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR / "output"
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


def env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return bool(default)
    return value.strip().lower() in {"1", "true", "yes", "on"}


SAMPLE_RATE = env_int("SAMPLE_RATE", 48000)
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = env_int("PORT", 8010)
DEFAULT_OUTPUT_CSV = Path(os.environ.get("OUTPUT_CSV", OUTPUT_DIR / "gps_positions.csv"))
DEFAULT_INPUT_DEVICE = os.environ.get("INPUT_DEVICE", "hw:2,0")
DEFAULT_INPUT_CHANNELS = env_int("INPUT_CHANNELS", 2)
DEFAULT_REVERSE_GEOCODER_URL = os.environ.get("REVERSE_GEOCODER_URL", "http://reverse-geocoder:8020/api/position")
ATEM_OUTPUT_HEALTH_URL = os.environ.get("ATEM_OUTPUT_HEALTH_URL", "http://atem-output:8030/api/health")
ATEM_OUTPUT_FREE_TEXT_URL = os.environ.get("ATEM_OUTPUT_FREE_TEXT_URL", "http://atem-output:8030/api/free-text")
REVERSE_GEOCODER_HEALTH_PROBE_URL = os.environ.get(
    "REVERSE_GEOCODER_HEALTH_PROBE_URL",
    DEFAULT_REVERSE_GEOCODER_URL.replace("/api/position", "/api/health/probe"),
)
REVERSE_GEOCODER_TIMEOUT_SECONDS = env_float("REVERSE_GEOCODER_TIMEOUT_SECONDS", 3.0)
E2E_HEALTH_ENABLED = env_bool("E2E_HEALTH_ENABLED", True)
E2E_HEALTH_INTERVAL_SECONDS = env_float("E2E_HEALTH_INTERVAL_SECONDS", 300.0)
E2E_HEALTH_TIMEOUT_SECONDS = env_float("E2E_HEALTH_TIMEOUT_SECONDS", 5.0)
E2E_HEALTH_NOTIFY_ENABLED = env_bool("E2E_HEALTH_NOTIFY_ENABLED", True)
E2E_HEALTH_NOTIFY_REPEAT_SECONDS = env_float("E2E_HEALTH_NOTIFY_REPEAT_SECONDS", 1800.0)
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = env_int("SMTP_PORT", 587)
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_USE_TLS = env_bool("SMTP_USE_TLS", True)
SMTP_FROM = os.environ.get("SMTP_FROM", "").strip()
SMTP_TO = os.environ.get("SMTP_TO", "").strip()
GEOCODE_QUEUE_SIZE = env_int("GEOCODE_QUEUE_SIZE", 100)
GEOCODE_RETRY_COUNT = env_int("GEOCODE_RETRY_COUNT", 3)
GEOCODE_RETRY_BASE_SECONDS = env_float("GEOCODE_RETRY_BASE_SECONDS", 1.0)
GPS_NO_FIX_NOTIFY_SECONDS = env_float("GPS_NO_FIX_NOTIFY_SECONDS", 5.0)
CSV_RETENTION_DAYS = env_int("CSV_RETENTION_DAYS", 90)
PCM_SOCKET_HOST = os.environ.get("PCM_SOCKET_HOST", "0.0.0.0")
PCM_SOCKET_PORT = env_int("PCM_SOCKET_PORT", 9010)
PCM_SOCKET_HEADER_MAX_BYTES = env_int("PCM_SOCKET_HEADER_MAX_BYTES", 4096)
CAPTURE_AGENT_CONTROL_SOCKET = os.environ.get(
    "CAPTURE_AGENT_CONTROL_SOCKET",
    "/run/capture-agent/control.sock",
)
CONTAINER_RESTART_ENABLED = env_bool("CONTAINER_RESTART_ENABLED", False)
DOCKER_SOCKET_PATH = os.environ.get("DOCKER_SOCKET_PATH", "/var/run/docker.sock")
ATEM_OUTPUT_CONTAINER_NAME = os.environ.get("ATEM_OUTPUT_CONTAINER_NAME", "atem_output")
RESTARTABLE_CONTAINERS = {
    "get-heri-gps": os.environ.get("GPS_RECEIVER_CONTAINER_NAME", "get_heri_gps"),
    "reverse-geocoder": os.environ.get("REVERSE_GEOCODER_CONTAINER_NAME", "reverse_geocoder"),
    "atem-output": ATEM_OUTPUT_CONTAINER_NAME,
}
LOG_DIR = Path(os.environ.get("LOG_DIR", "/app/logs"))
LOG_FILE = os.environ.get("LOG_FILE", "gps_receiver.log")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_MAX_BYTES = env_int("LOG_MAX_BYTES", 5 * 1024 * 1024)
LOG_BACKUP_COUNT = env_int("LOG_BACKUP_COUNT", 5)
LOG_PROGRESS_SECONDS = env_float("LOG_PROGRESS_SECONDS", 5.0)
CAPTURE_DEVICE_INCLUDE_KEYWORDS = [
    item.strip().lower()
    for item in os.environ.get("CAPTURE_DEVICE_INCLUDE_KEYWORDS", "AJA,U-TAP,Blackmagic,DeckLink,UltraStudio,SDI,MS2109,USB Audio").split(",")
    if item.strip()
]
def setup_logger():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gps_receiver")
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
LOGGER.info("flow=gps_receiver init sample_rate=%s output_csv=%s reverse_geocoder_url=%s", SAMPLE_RATE, DEFAULT_OUTPUT_CSV, DEFAULT_REVERSE_GEOCODER_URL)


def now_jst():
    return datetime.now(JST)


def format_japanese_time(dt):
    if dt.tzinfo is not None:
        dt = dt.astimezone(JST)
    return dt.strftime("%Y/%m/%d %H:%M:%S")


@dataclass
class RuntimeConfig:
    mode: str = "socket"
    gps_channel: int = env_int("GPS_CHANNEL", 4)
    input_channels: int = DEFAULT_INPUT_CHANNELS
    input_device: str = DEFAULT_INPUT_DEVICE
    input_command: str = os.environ.get("INPUT_COMMAND", f"arecord -D {DEFAULT_INPUT_DEVICE} -f S16_LE -r {SAMPLE_RATE} -c {DEFAULT_INPUT_CHANNELS} -t raw")
    pcm_socket_host: str = PCM_SOCKET_HOST
    pcm_socket_port: int = PCM_SOCKET_PORT
    test_capture_dir: str = os.environ.get("TEST_CAPTURE_DIR", "../audio_capture/20260613_132355")
    output_csv: str = str(DEFAULT_OUTPUT_CSV)
    reverse_geocoder_url: str = DEFAULT_REVERSE_GEOCODER_URL
    window_seconds: float = env_float("WINDOW_SECONDS", 20.0)
    decode_interval_seconds: float = env_float("DECODE_INTERVAL_SECONDS", 1.0)


class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.config = RuntimeConfig()
        self.running = False
        self.started_at = None
        self.source_started_at = None
        self.status = "stopped"
        self.error = ""
        self.total_samples = 0
        self.decoded_count = 0
        self.geocode_success_count = 0
        self.geocode_error_count = 0
        self.geocode_queue_size = 0
        self.geocode_queue_dropped_count = 0
        self.latest_geocode = None
        self.geocode_error = ""
        self.input_status = "stopped"
        self.input_error = ""
        self.socket_connected = False
        self.socket_client = ""
        self.socket_header = None
        self.latest = None
        self.recent = deque(maxlen=30)
        self.worker = None
        self.stop_event = threading.Event()

    def snapshot(self):
        with self.lock:
            cfg = asdict(self.config)
            return {
                "config": cfg,
                "sample_rate": SAMPLE_RATE,
                "running": self.running,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "status": self.status,
                "error": self.error,
                "total_samples": self.total_samples,
                "decoded_count": self.decoded_count,
                "geocode_success_count": self.geocode_success_count,
                "geocode_error_count": self.geocode_error_count,
                "geocode_queue_size": self.geocode_queue_size,
                "geocode_queue_dropped_count": self.geocode_queue_dropped_count,
                "latest_geocode": self.latest_geocode,
                "geocode_error": self.geocode_error,
                "input_status": self.input_status,
                "input_error": self.input_error,
                "socket_connected": self.socket_connected,
                "socket_client": self.socket_client,
                "socket_header": self.socket_header,
                "latest": self.latest,
                "recent": list(self.recent),
            }

    def set_config(self, values):
        with self.lock:
            values = dict(values)
            if not values.get("mode"):
                values["mode"] = "command"
            normalized = {}
            for key, val in values.items():
                if hasattr(self.config, key):
                    cur = getattr(self.config, key)
                    if isinstance(cur, int):
                        val = int(val)
                    elif isinstance(cur, float):
                        val = float(val)
                    else:
                        val = str(val)
                    if key == "output_csv":
                        path = Path(val)
                        if not path.is_absolute():
                            path = BASE_DIR / path
                        val = str(path)
                    if key == "input_device" and val:
                        normalized["input_command"] = build_arecord_command(val, int(values.get("input_channels", self.config.input_channels)))
                    normalized[key] = val
            if self.running:
                changed = [key for key, val in normalized.items() if getattr(self.config, key) != val]
                if changed:
                    raise RuntimeError("設定を変更するには先に停止してください")
                return
            for key, val in normalized.items():
                setattr(self.config, key, val)

    def mark_started(self):
        with self.lock:
            self.running = True
            self.started_at = now_jst()
            self.source_started_at = self.started_at
            self.status = "running"
            self.error = ""
            self.total_samples = 0
            self.input_status = "waiting"
            self.input_error = ""
            self.socket_connected = False
            self.socket_client = ""
            self.socket_header = None

    def mark_stopped(self, error=""):
        with self.lock:
            self.running = False
            self.status = "error" if error else "stopped"
            self.error = error
            self.input_status = "error" if error else "stopped"
            self.socket_connected = False

    def add_row(self, row):
        with self.lock:
            self.decoded_count += 1
            self.latest = row
            if row.get("geocode"):
                self.latest_geocode = row["geocode"]
            self.recent.appendleft(row)

    def mark_geocode_success(self, geocode):
        with self.lock:
            self.geocode_success_count += 1
            self.latest_geocode = geocode
            self.geocode_error = ""

    def mark_geocode_not_found(self, geocode):
        with self.lock:
            self.latest_geocode = geocode
            self.geocode_error = ""

    def attach_geocode(self, payload_hex, geocode):
        with self.lock:
            if self.latest and self.latest.get("payload_hex") == payload_hex:
                self.latest["geocode"] = geocode
            for row in self.recent:
                if row.get("payload_hex") == payload_hex:
                    row["geocode"] = geocode
                    break

    def mark_geocode_error(self, error):
        with self.lock:
            self.geocode_error_count += 1
            self.geocode_error = error

    def set_geocode_queue_size(self, size):
        with self.lock:
            self.geocode_queue_size = size

    def mark_geocode_queue_drop(self):
        with self.lock:
            self.geocode_queue_dropped_count += 1

    def set_input_status(self, status, error=""):
        with self.lock:
            self.input_status = status
            self.input_error = error

    def set_socket_state(self, connected, client="", header=None):
        with self.lock:
            self.socket_connected = connected
            self.socket_client = client
            if header is not None:
                self.socket_header = header

    def apply_socket_header(self, header):
        with self.lock:
            self.config.input_channels = int(header["channels"])
            self.config.gps_channel = int(header["gps_channel"])

    def set_samples(self, total_samples):
        with self.lock:
            self.total_samples = total_samples


STATE = AppState()
CSV_HEADER = ["time", "source", "channel", "offset_sec", "lon", "lat", "alt", "group", "aircraft", "payload_hex"]
e2e_health = {
    "enabled": E2E_HEALTH_ENABLED,
    "ok": False,
    "status": "starting" if E2E_HEALTH_ENABLED else "disabled",
    "checked_at": "",
    "error": "",
    "decoded": False,
    "reverse_geocoder": {},
    "atem_probe": {},
    "lat": "",
    "lon": "",
    "alt": "",
    "address": "",
    "notification": {
        "enabled": E2E_HEALTH_NOTIFY_ENABLED,
        "configured": False,
        "last_sent_at": "",
        "last_error": "",
    },
}
e2e_health_notify_state = {
    "last_ok": None,
    "last_sent_monotonic": 0.0,
    "last_sent_at": "",
    "last_error": "",
}


def bcd_byte2(value):
    value = int(value)
    return ((value // 10) << 4) | (value % 10)


def dms_bcd_bytes(decimal_degrees):
    degrees = int(decimal_degrees)
    minutes_float = (decimal_degrees - degrees) * 60.0
    minutes = int(minutes_float)
    seconds_float = (minutes_float - minutes) * 60.0
    seconds = int(seconds_float)
    centi = int(round((seconds_float - seconds) * 100))
    if centi >= 100:
        seconds += 1
        centi = 0
    if seconds >= 60:
        minutes += 1
        seconds = 0
    if minutes >= 60:
        degrees += 1
        minutes = 0
    return bytes(
        [
            bcd_byte2(degrees // 100),
            bcd_byte2(degrees % 100),
            bcd_byte2(minutes),
            bcd_byte2(seconds),
            bcd_byte2(centi),
        ]
    )


def bits_lsb(data):
    bits = []
    for byte in data:
        bits.extend((byte >> bit) & 1 for bit in range(8))
    return bits


def hdlc_bit_stuff(bits):
    out = []
    ones = 0
    for bit in bits:
        out.append(bit)
        if bit:
            ones += 1
            if ones == 5:
                out.append(0)
                ones = 0
        else:
            ones = 0
    return out


def build_health_mod_frame(lat=34.693733, lon=135.5023, alt=100):
    address = b"\x82\xa0\xa4\xa6@@\x60" * 2
    payload = (
        b"MOD"
        + bytes([0x00, 0x01, 0x00, 0x01])
        + dms_bcd_bytes(lat)
        + dms_bcd_bytes(lon)
        + bytes([bcd_byte2(alt // 100), bcd_byte2(alt % 100)])
        + b"\x00"
    )
    frame_without_fcs = address + bytes([0x03, 0xF0]) + b":" + payload
    fcs = crc16_x25(frame_without_fcs) ^ 0xFFFF
    frame = frame_without_fcs + bytes([fcs & 0xFF, (fcs >> 8) & 0xFF])
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    return flag + hdlc_bit_stuff(bits_lsb(frame)) + flag


def modulate_health_bits(hdlc_bits):
    raw_bits = [0]
    for bit in hdlc_bits:
        raw_bits.append(raw_bits[-1] ^ (1 - int(bit)))
    samples_per_bit = int(round(SAMPLE_RATE / DEFAULT_BAUD))
    phase = 0.0
    amplitude = 12000
    samples = []
    for bit in raw_bits:
        frequency = 1800 if bit else 1200
        increment = 2.0 * math.pi * frequency / SAMPLE_RATE
        for _ in range(samples_per_bit):
            samples.append(int(amplitude * math.sin(phase)))
            phase += increment
            if phase >= 2.0 * math.pi:
                phase -= 2.0 * math.pi
    return np.asarray(samples, dtype=np.int16)


def build_health_pcm_channel():
    bits = []
    for _ in range(10):
        bits.extend(build_health_mod_frame())
    silence = np.zeros(SAMPLE_RATE, dtype=np.int16)
    return np.concatenate([silence, modulate_health_bits(bits), silence])


def run_e2e_health_check():
    started = time.monotonic()
    checked_at = format_japanese_time(now_jst())
    result = {
        "enabled": E2E_HEALTH_ENABLED,
        "ok": False,
        "status": "checking",
        "checked_at": checked_at,
        "duration_seconds": None,
        "error": "",
        "decoded": False,
        "reverse_geocoder": {},
        "atem_probe": {},
        "lat": "",
        "lon": "",
        "alt": "",
        "address": "",
    }
    try:
        channel_samples = build_health_pcm_channel()
        four_channel = np.zeros((len(channel_samples), 4), dtype=np.int16)
        four_channel[:, 3] = channel_samples
        fixes = decode_samples(four_channel[:, 3].copy(), 0, sample_rate=SAMPLE_RATE)
        if not fixes:
            raise RuntimeError("疑似4ch PCMからGPSを復調できません")
        fix = fixes[0]
        result.update(
            {
                "decoded": True,
                "lat": f"{fix.lat:.8f}",
                "lon": f"{fix.lon:.8f}",
                "alt": fix.alt,
            }
        )
        payload = {
            "health_probe": True,
            "time": checked_at,
            "lat": float(fix.lat),
            "lon": float(fix.lon),
            "alt": fix.alt,
            "source": "health_probe:pseudo_sdi_4ch",
            "channel": 4,
            "payload_hex": fix.payload_hex,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            REVERSE_GEOCODER_HEALTH_PROBE_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=E2E_HEALTH_TIMEOUT_SECONDS) as response:
            body = response.read(65536)
        geocode = json.loads(body.decode("utf-8"))
        atem_probe = geocode.get("atem_probe") or {}
        result.update(
            {
                "ok": bool(geocode.get("ok")) and bool(atem_probe.get("ok")),
                "status": "ok" if bool(geocode.get("ok")) and bool(atem_probe.get("ok")) else "ng",
                "reverse_geocoder": geocode,
                "atem_probe": atem_probe,
                "address": geocode.get("address_label", ""),
            }
        )
        if not result["ok"]:
            result["error"] = geocode.get("error") or atem_probe.get("error") or "E2E health probe failed"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        LOGGER.warning("flow=e2e_health error=%s", exc)
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    result["notification"] = notification_status()
    return result


def notification_configured():
    return bool(SMTP_HOST and SMTP_FROM and SMTP_TO)


def notification_status():
    return {
        "enabled": E2E_HEALTH_NOTIFY_ENABLED,
        "configured": notification_configured(),
        "last_sent_at": e2e_health_notify_state.get("last_sent_at", ""),
        "last_error": e2e_health_notify_state.get("last_error", ""),
        "repeat_seconds": E2E_HEALTH_NOTIFY_REPEAT_SECONDS,
    }


def send_e2e_health_email(result, recovered=False):
    if not E2E_HEALTH_NOTIFY_ENABLED:
        return False, "notification disabled"
    if not notification_configured():
        return False, "SMTP未設定"
    state = "復旧" if recovered else "NG"
    subject = f"[get_heri_gps] E2Eヘルスチェック{state}"
    body = "\n".join(
        [
            f"E2Eヘルスチェック: {state}",
            f"確認時刻: {result.get('checked_at', '')}",
            f"status: {result.get('status', '')}",
            f"ok: {result.get('ok')}",
            f"decoded: {result.get('decoded')}",
            f"address: {result.get('address', '')}",
            f"lat/lon/alt: {result.get('lat', '')}, {result.get('lon', '')}, {result.get('alt', '')}",
            f"error: {result.get('error', '')}",
            "",
            "reverse_geocoder:",
            json.dumps(result.get("reverse_geocoder", {}), ensure_ascii=False, indent=2),
            "",
            "atem_probe:",
            json.dumps(result.get("atem_probe", {}), ensure_ascii=False, indent=2),
        ]
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = SMTP_FROM
    message["To"] = SMTP_TO
    message.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            if SMTP_USE_TLS:
                smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(message)
        e2e_health_notify_state["last_error"] = ""
        return True, ""
    except Exception as exc:
        LOGGER.warning("flow=e2e_health notify_error error=%s", exc)
        return False, str(exc)


def maybe_notify_e2e_health(result):
    now = time.monotonic()
    previous_ok = e2e_health_notify_state.get("last_ok")
    current_ok = bool(result.get("ok"))
    should_send = False
    recovered = False
    if not current_ok:
        elapsed = now - float(e2e_health_notify_state.get("last_sent_monotonic") or 0.0)
        should_send = previous_ok is not False or elapsed >= E2E_HEALTH_NOTIFY_REPEAT_SECONDS
    elif previous_ok is False:
        should_send = True
        recovered = True
    e2e_health_notify_state["last_ok"] = current_ok
    if not should_send:
        result["notification"] = notification_status()
        return
    sent, error = send_e2e_health_email(result, recovered=recovered)
    if sent:
        e2e_health_notify_state["last_sent_monotonic"] = now
        e2e_health_notify_state["last_sent_at"] = format_japanese_time(now_jst())
    e2e_health_notify_state["last_error"] = error
    result["notification"] = notification_status()


def e2e_health_main():
    global e2e_health
    if not E2E_HEALTH_ENABLED:
        return
    while True:
        result = run_e2e_health_check()
        maybe_notify_e2e_health(result)
        with STATE.lock:
            e2e_health = result
        LOGGER.info(
            "flow=e2e_health status=%s ok=%s decoded=%s address=%s error=%s",
            result.get("status"),
            result.get("ok"),
            result.get("decoded"),
            result.get("address"),
            result.get("error"),
        )
        time.sleep(max(5.0, E2E_HEALTH_INTERVAL_SECONDS))


threading.Thread(target=e2e_health_main, daemon=True, name="e2e-health").start()


def post_reverse_geocode(config, row):
    url = (config.reverse_geocoder_url or "").strip()
    if not url:
        LOGGER.info("flow=reverse_geocode skipped reason=no_url lat=%s lon=%s", row.get("lat"), row.get("lon"))
        return None
    if row.get("event") == "decode_unavailable":
        payload = {
            "event": "decode_unavailable",
            "time": row["time"],
            "source": "get_heri_gps",
            "channel": row["channel"],
            "reason": row.get("reason", "no recent GPS fix"),
        }
    else:
        payload = {
            "time": row["time"],
            "lat": float(row["lat"]),
            "lon": float(row["lon"]),
            "alt": row["alt"],
            "source": "get_heri_gps",
            "channel": row["channel"],
            "payload_hex": row.get("payload_hex", ""),
        }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        LOGGER.info("flow=reverse_geocode post url=%s lat=%s lon=%s alt=%s", url, row.get("lat"), row.get("lon"), row.get("alt"))
        with urllib.request.urlopen(req, timeout=REVERSE_GEOCODER_TIMEOUT_SECONDS) as res:
            body = res.read(8192)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        LOGGER.warning("flow=reverse_geocode error url=%s error=%s", url, exc)
        STATE.mark_geocode_error(str(exc))
        return None
    try:
        geocode = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOGGER.warning("flow=reverse_geocode invalid_response error=%s", exc)
        STATE.mark_geocode_error(f"invalid geocoder response: {exc}")
        return None
    if geocode.get("ok") is False:
        LOGGER.warning("flow=reverse_geocode not_found lat=%s lon=%s error=%s", row.get("lat"), row.get("lon"), geocode.get("error", "reverse geocoder error"))
        # A valid HTTP/JSON response with no matching administrative area is
        # final, not a transport failure. Attach it to the current position so
        # the UI clears any older place name, and do not retry it.
        STATE.mark_geocode_not_found(geocode)
        return geocode
    LOGGER.info("flow=reverse_geocode success address=%s lat=%s lon=%s", geocode.get("address_label", ""), row.get("lat"), row.get("lon"))
    STATE.mark_geocode_success(geocode)
    return geocode


def request_capture_agent(path, method="GET", payload=None):
    data = b"" if payload is None else json.dumps(payload).encode("utf-8")
    request = (
        f"{method} {path} HTTP/1.0\r\n"
        "Host: local\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(data)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + data
    try:
        response = b""
        for attempt in range(3):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(REVERSE_GEOCODER_TIMEOUT_SECONDS)
                    client.connect(CAPTURE_AGENT_CONTROL_SOCKET)
                    client.sendall(request)
                    chunks = []
                    total = 0
                    while total < 1024 * 1024:
                        chunk = client.recv(min(65536, 1024 * 1024 - total))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        total += len(chunk)
                response = b"".join(chunks)
                break
            except (OSError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(0.05)
        head, body = response.split(b"\r\n\r\n", 1)
        status_code = int(head.split(b" ", 2)[1])
    except (OSError, TimeoutError, ValueError, IndexError) as exc:
        LOGGER.warning(
            "flow=capture_agent proxy_error socket=%s error=%s",
            CAPTURE_AGENT_CONTROL_SOCKET,
            exc,
        )
        return JSONResponse(
            {"ok": False, "error": f"capture-agent制御APIに接続できません: {exc}"},
            status_code=502,
        )
    try:
        result = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(
            {"ok": False, "error": "capture-agentから不正な応答を受信しました"},
            status_code=502,
        )
    return JSONResponse(result, status_code=status_code)


def geocode_sender_main(config, send_queue, worker_done):
    LOGGER.info("flow=reverse_geocode sender_start queue_size=%s retry_count=%s", GEOCODE_QUEUE_SIZE, GEOCODE_RETRY_COUNT)
    while not worker_done.is_set() or not send_queue.empty():
        try:
            row = send_queue.get(timeout=0.5)
        except queue.Empty:
            STATE.set_geocode_queue_size(send_queue.qsize())
            continue
        geocode = None
        error = ""
        for attempt in range(1, GEOCODE_RETRY_COUNT + 1):
            geocode = post_reverse_geocode(config, row)
            if geocode is not None:
                row["geocode"] = geocode
                STATE.attach_geocode(row.get("payload_hex", ""), geocode)
                break
            error = STATE.snapshot().get("geocode_error", "")
            if attempt < GEOCODE_RETRY_COUNT and not worker_done.is_set():
                sleep_sec = GEOCODE_RETRY_BASE_SECONDS * attempt
                LOGGER.info("flow=reverse_geocode retry_wait attempt=%s sleep=%.2f", attempt, sleep_sec)
                time.sleep(sleep_sec)
        if not geocode and error:
            LOGGER.warning("flow=reverse_geocode give_up lat=%s lon=%s error=%s", row.get("lat"), row.get("lon"), error)
        send_queue.task_done()
        STATE.set_geocode_queue_size(send_queue.qsize())
    LOGGER.info("flow=reverse_geocode sender_stop")


def enqueue_geocode(send_queue, row):
    if row.get("event") != "decode_unavailable" and not (
        row.get("lat") and row.get("lon")
    ):
        return
    # Place-name display is real-time data. Discard every pending older
    # position before enqueueing the newest fix instead of building a backlog.
    while True:
        try:
            dropped = send_queue.get_nowait()
            send_queue.task_done()
            STATE.mark_geocode_queue_drop()
            LOGGER.info(
                "flow=reverse_geocode superseded lat=%s lon=%s",
                dropped.get("lat"),
                dropped.get("lon"),
            )
        except queue.Empty:
            break
    try:
        send_queue.put_nowait(dict(row))
    except queue.Full:
        try:
            dropped = send_queue.get_nowait()
            send_queue.task_done()
            STATE.mark_geocode_queue_drop()
            LOGGER.warning("flow=reverse_geocode queue_full dropped lat=%s lon=%s", dropped.get("lat"), dropped.get("lon"))
        except queue.Empty:
            pass
        try:
            send_queue.put_nowait(dict(row))
        except queue.Full:
            STATE.mark_geocode_queue_drop()
            LOGGER.warning("flow=reverse_geocode queue_full drop_current lat=%s lon=%s", row.get("lat"), row.get("lon"))
            return
    STATE.set_geocode_queue_size(send_queue.qsize())


class CsvWriter:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute():
            self.path = BASE_DIR / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = None
        self.writer = None
        self.active_day = (
            datetime.fromtimestamp(self.path.stat().st_mtime, JST).date()
            if self.path.exists() and self.path.stat().st_size > 0
            else None
        )
        self._cleanup(now_jst().date())

    def _row_day(self, row):
        try:
            return datetime.strptime(
                str(row.get("time", ""))[:10],
                "%Y/%m/%d",
            ).date()
        except ValueError:
            return now_jst().date()

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
                LOGGER.info("flow=csv retention_delete path=%s", candidate)

    def _open(self, day):
        if self.active_day is not None and day > self.active_day:
            self.close()
            if self.path.exists() and self.path.stat().st_size > 0:
                archive = self._archive_path(self.active_day)
                self.path.replace(archive)
                LOGGER.info(
                    "flow=csv rotate path=%s archive=%s",
                    self.path,
                    archive,
                )
            self._cleanup(day)
            self.active_day = day
        elif self.active_day is None:
            self.active_day = day
        if self.file is None:
            self.file = self.path.open("a", newline="", encoding="utf-8")
            self.writer = csv.writer(self.file)
            if self.path.stat().st_size == 0:
                self.writer.writerow(CSV_HEADER)
                self.file.flush()
            LOGGER.info(
                "flow=csv open path=%s day=%s",
                self.path,
                self.active_day,
            )

    def write(self, row):
        self._open(self._row_day(row))
        self.writer.writerow([row.get(k, "") for k in CSV_HEADER])
        self.file.flush()
        LOGGER.info("flow=csv write path=%s time=%s lat=%s lon=%s alt=%s", self.path, row.get("time"), row.get("lat"), row.get("lon"), row.get("alt"))

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None
            self.writer = None


def read_metadata_start(capture_dir):
    meta = Path(capture_dir) / "metadata.json"
    if meta.exists():
        with meta.open() as f:
            data = json.load(f)
        return datetime.fromisoformat(data["start_time"])
    return now_jst()


def iter_test_chunks(config, stop_event):
    capture_dir = Path(config.test_capture_dir)
    if not capture_dir.is_absolute():
        capture_dir = (BASE_DIR / capture_dir).resolve()
    raw_path = capture_dir / f"ch{config.gps_channel}.raw"
    if not raw_path.exists():
        raise FileNotFoundError(f"test raw not found: {raw_path}")
    LOGGER.info("flow=input mode=test raw=%s channel=%s sample_rate=%s", raw_path, config.gps_channel, SAMPLE_RATE)
    start_time = read_metadata_start(capture_dir)
    chunk_samples = int(SAMPLE_RATE * 0.25)
    with raw_path.open("rb") as f:
        while not stop_event.is_set():
            data = f.read(chunk_samples * 2)
            if not data:
                break
            arr = np.frombuffer(data, dtype="<i2").copy()
            yield arr, str(capture_dir), start_time
            time.sleep(len(arr) / SAMPLE_RATE)


def iter_command_chunks(config, stop_event):
    command = config.input_command.strip() or build_arecord_command(config.input_device, config.input_channels)
    if not command:
        raise RuntimeError("INPUT_COMMAND is empty")
    cmd = shlex.split(command)
    LOGGER.info("flow=input mode=sdi command=%s gps_channel=%s input_channels=%s sample_rate=%s", command, config.gps_channel, config.input_channels, SAMPLE_RATE)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    source = command
    start_time = now_jst()
    chunk_frames = int(SAMPLE_RATE * 0.25)
    bytes_per_chunk = chunk_frames * config.input_channels * 2
    try:
        while not stop_event.is_set():
            data = proc.stdout.read(bytes_per_chunk)
            if not data:
                break
            arr = np.frombuffer(data, dtype="<i2")
            frames = len(arr) // config.input_channels
            if frames <= 0:
                continue
            arr = arr[: frames * config.input_channels].reshape(-1, config.input_channels)
            ch_index = max(0, min(config.gps_channel - 1, config.input_channels - 1))
            yield arr[:, ch_index].copy(), source, start_time
    finally:
        LOGGER.info("flow=input stop command=%s", command)
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def recv_until_newline(conn, max_bytes):
    data = bytearray()
    while len(data) < max_bytes:
        chunk = conn.recv(1)
        if not chunk:
            break
        if chunk == b"\n":
            return bytes(data)
        data.extend(chunk)
    if len(data) >= max_bytes:
        raise RuntimeError("PCM socket header is too large")
    return bytes(data)


def iter_socket_chunks(config, stop_event):
    host = config.pcm_socket_host or PCM_SOCKET_HOST
    port = int(config.pcm_socket_port or PCM_SOCKET_PORT)
    LOGGER.info(
        "flow=input mode=socket listen=%s:%s sample_rate=%s",
        host,
        port,
        SAMPLE_RATE,
    )
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(1)
        server.settimeout(0.5)
        STATE.set_input_status("waiting")
        while not stop_event.is_set():
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            client = f"{addr[0]}:{addr[1]}"
            with conn:
                conn.settimeout(1.0)
                header = {}
                try:
                    header_line = recv_until_newline(conn, PCM_SOCKET_HEADER_MAX_BYTES)
                    if not header_line:
                        raise RuntimeError("PCM socket header is required")
                    header = json.loads(header_line.decode("utf-8"))
                    if header.get("protocol") != "heri-pcm" or int(header.get("version", 0)) != 1:
                        raise RuntimeError("unsupported PCM socket protocol")
                    if int(header.get("sample_rate", 0)) != SAMPLE_RATE:
                        raise RuntimeError(
                            f"sample_rate must be {SAMPLE_RATE}: {header.get('sample_rate')}"
                        )
                    if header.get("sample_format") != "S16_LE":
                        raise RuntimeError(
                            f"sample_format must be S16_LE: {header.get('sample_format')}"
                        )
                    input_channels = int(header["channels"])
                    gps_channel = int(header["gps_channel"])
                    if input_channels < 1 or not 1 <= gps_channel <= input_channels:
                        raise RuntimeError("invalid channels or gps_channel")
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    OSError,
                    RuntimeError,
                ) as exc:
                    STATE.set_input_status("error", str(exc))
                    LOGGER.warning("flow=input socket_header_error client=%s error=%s", client, exc)
                    continue
                STATE.apply_socket_header(header)
                STATE.set_socket_state(True, client, header)
                STATE.set_input_status("connected")
                LOGGER.info("flow=input socket_connected client=%s header=%s", client, header)
                source = f"socket://{client}"
                start_time = now_jst()
                chunk_frames = int(SAMPLE_RATE * 0.25)
                bytes_per_frame = input_channels * 2
                bytes_per_chunk = chunk_frames * bytes_per_frame
                buffer = bytearray()
                try:
                    while not stop_event.is_set():
                        chunk = conn.recv(bytes_per_chunk - len(buffer))
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        if len(buffer) < bytes_per_chunk:
                            continue
                        data = bytes(buffer[:bytes_per_chunk])
                        del buffer[:bytes_per_chunk]
                        arr = np.frombuffer(data, dtype="<i2")
                        frames = len(arr) // input_channels
                        if frames <= 0:
                            continue
                        arr = arr[: frames * input_channels].reshape(-1, input_channels)
                        ch_index = gps_channel - 1
                        yield arr[:, ch_index].copy(), source, start_time
                except socket.timeout:
                    LOGGER.warning("flow=input socket_timeout client=%s", client)
                finally:
                    STATE.set_socket_state(False, "", header)
                    if not stop_event.is_set():
                        STATE.set_input_status("waiting")
                    LOGGER.info("flow=input socket_disconnected client=%s", client)


def worker_main():
    STATE.mark_started()
    config = STATE.config
    config.mode = "socket"
    writer = CsvWriter(config.output_csv)
    geocode_queue = queue.Queue(maxsize=GEOCODE_QUEUE_SIZE)
    geocode_done = threading.Event()
    geocode_thread = threading.Thread(target=geocode_sender_main, args=(config, geocode_queue, geocode_done), daemon=True)
    geocode_thread.start()
    sample_buffer = np.empty(0, dtype=np.int16)
    buffer_start_sample = 0
    next_decode = time.monotonic() + config.decode_interval_seconds
    seen = set()
    total_samples = 0
    active_source_start = None
    last_progress = time.monotonic()
    last_new_fix_at = time.monotonic()
    decode_unavailable_notified = False
    LOGGER.info(
        "flow=worker start mode=socket listen=%s:%s output_csv=%s",
        config.pcm_socket_host,
        config.pcm_socket_port,
        config.output_csv,
    )
    try:
        source_iter = iter_socket_chunks(config, STATE.stop_event)
        source_name = ""
        source_start = now_jst()
        for chunk, source_name, source_start in source_iter:
            if source_start != active_source_start:
                active_source_start = source_start
                sample_buffer = np.empty(0, dtype=np.int16)
                buffer_start_sample = 0
                seen.clear()
                next_decode = time.monotonic() + config.decode_interval_seconds
                LOGGER.info(
                    "flow=input new_stream source=%s channels=%s gps_channel=%s",
                    source_name,
                    config.input_channels,
                    config.gps_channel,
                )
            total_samples += len(chunk)
            STATE.set_samples(total_samples)
            sample_buffer = np.concatenate([sample_buffer, chunk])
            now = time.monotonic()
            if now - last_progress >= LOG_PROGRESS_SECONDS:
                LOGGER.info(
                    "flow=input progress source=%s total_samples=%s buffer_samples=%s",
                    source_name,
                    total_samples,
                    len(sample_buffer),
                )
                last_progress = now
            keep = int(config.window_seconds * SAMPLE_RATE)
            if len(sample_buffer) > keep:
                drop = len(sample_buffer) - keep
                sample_buffer = sample_buffer[drop:]
                buffer_start_sample += drop
            if time.monotonic() < next_decode or len(sample_buffer) < SAMPLE_RATE * 4:
                continue
            next_decode = time.monotonic() + config.decode_interval_seconds
            fixes = decode_samples(sample_buffer, buffer_start_sample, sample_rate=SAMPLE_RATE)
            if fixes:
                LOGGER.info("flow=demod decode_ok fixes=%s buffer_samples=%s buffer_start=%s", len(fixes), len(sample_buffer), buffer_start_sample)
            else:
                LOGGER.debug("flow=demod no_fix buffer_samples=%s buffer_start=%s", len(sample_buffer), buffer_start_sample)
            new_fix_received = False
            for fix in fixes:
                offset_sec = fix.sample_offset / SAMPLE_RATE
                key = (round(offset_sec, 2), fix.payload_hex)
                if key in seen:
                    continue
                seen.add(key)
                new_fix_received = True
                t = source_start + timedelta(seconds=offset_sec)
                row = {
                    "time": format_japanese_time(t),
                    "time_iso": t.isoformat(),
                    "source": source_name,
                    "channel": config.gps_channel,
                    "offset_sec": f"{offset_sec:.6f}",
                    "lon": f"{fix.lon:.8f}",
                    "lat": f"{fix.lat:.8f}",
                    "alt": fix.alt,
                    "group": fix.group,
                    "aircraft": "" if fix.aircraft is None else fix.aircraft,
                    "payload_hex": fix.payload_hex,
                }
                LOGGER.info(
                    "flow=gps fix time=%s lat=%s lon=%s alt=%s group=%s aircraft=%s offset_sec=%.6f",
                    row["time"],
                    row["lat"],
                    row["lon"],
                    row["alt"],
                    row["group"],
                    row["aircraft"],
                    offset_sec,
                )
                writer.write(row)
                STATE.add_row(row)
                enqueue_geocode(geocode_queue, row)
            if new_fix_received:
                last_new_fix_at = time.monotonic()
                decode_unavailable_notified = False
            elif (
                not decode_unavailable_notified
                and time.monotonic() - last_new_fix_at
                >= GPS_NO_FIX_NOTIFY_SECONDS
            ):
                event = {
                    "event": "decode_unavailable",
                    "time": format_japanese_time(now_jst()),
                    "source": source_name,
                    "channel": config.gps_channel,
                    "reason": f"no GPS fix for {GPS_NO_FIX_NOTIFY_SECONDS:g} seconds",
                    "payload_hex": "",
                }
                LOGGER.warning(
                    "flow=gps unavailable seconds=%.1f source=%s channel=%s",
                    GPS_NO_FIX_NOTIFY_SECONDS,
                    source_name,
                    config.gps_channel,
                )
                enqueue_geocode(geocode_queue, event)
                decode_unavailable_notified = True
    except Exception as exc:
        LOGGER.exception("flow=worker error error=%s", exc)
        STATE.mark_stopped(str(exc))
    else:
        LOGGER.info("flow=worker stop reason=completed total_samples=%s decoded_count=%s", total_samples, STATE.decoded_count)
        STATE.mark_stopped()
    finally:
        geocode_done.set()
        geocode_thread.join(timeout=5)
        writer.close()


app = FastAPI(title="get_heri_gps")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def start_pcm_receiver():
    with STATE.lock:
        if STATE.running:
            return
        STATE.stop_event.clear()
        STATE.worker = threading.Thread(target=worker_main, daemon=True)
        STATE.worker.start()


@app.on_event("shutdown")
def stop_pcm_receiver():
    STATE.stop_event.set()
    worker = STATE.worker
    if worker and worker.is_alive():
        worker.join(timeout=3)


def build_arecord_command(device, channels):
    return f"arecord -D {device} -f S16_LE -r {SAMPLE_RATE} -c {channels} -t raw"


def list_capture_devices():
    devices = []
    try:
        proc = subprocess.run(["arecord", "-l"], text=True, capture_output=True, timeout=3)
    except Exception:
        return devices
    if proc.returncode != 0:
        return devices
    pat = re.compile(r"card (\d+): ([^\[]+) \[([^\]]+)\], device (\d+): ([^\[]+) \[([^\]]+)\]")
    for line in proc.stdout.splitlines():
        m = pat.search(line)
        if not m:
            continue
        card, card_id, card_name, dev, dev_id, dev_name = m.groups()
        hw = f"hw:{card},{dev}"
        label = f"{card_name} / {dev_name} ({hw})"
        if CAPTURE_DEVICE_INCLUDE_KEYWORDS and not any(keyword in label.lower() for keyword in CAPTURE_DEVICE_INCLUDE_KEYWORDS):
            continue
        devices.append({"device": hw, "label": label, "card": int(card), "subdevice": int(dev), "default_channels": 2})
    return devices


def docker_engine_request(method, path, timeout=15.0):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(DOCKER_SOCKET_PATH)
        request = (
            f"{method} {path} HTTP/1.1\r\n"
            "Host: docker\r\n"
            "Content-Length: 0\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("utf-8")
        sock.sendall(request)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    raw = b"".join(chunks)
    header, _, body = raw.partition(b"\r\n\r\n")
    status_line = header.splitlines()[0].decode("iso-8859-1", errors="replace") if header else ""
    parts = status_line.split(" ", 2)
    status_code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    text = body.decode("utf-8", errors="replace").strip()
    if status_code >= 400 or status_code == 0:
        raise RuntimeError(text or status_line or "Docker Engine API request failed")
    return {"status_code": status_code, "body": text}


def restart_container(container_key):
    if not CONTAINER_RESTART_ENABLED:
        raise RuntimeError("コンテナ再起動APIが無効です")
    container_name = RESTARTABLE_CONTAINERS.get(container_key)
    if not container_name:
        raise RuntimeError(f"再起動対象外のコンテナです: {container_key}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", container_name):
        raise RuntimeError(f"コンテナ名が不正です: {container_name}")
    if not Path(DOCKER_SOCKET_PATH).exists():
        raise RuntimeError(f"Docker socket が見つかりません: {DOCKER_SOCKET_PATH}")
    return docker_engine_request(
        "POST",
        f"/containers/{container_name}/restart?t=10",
        timeout=20.0,
    )


def restart_container_async(container_key):
    container_name = RESTARTABLE_CONTAINERS.get(container_key, container_key)

    def run():
        time.sleep(0.2)
        try:
            result = restart_container(container_key)
            LOGGER.warning(
                "flow=system action=restart_container target=%s container=%s status_code=%s",
                container_key,
                container_name,
                result.get("status_code"),
            )
        except Exception as exc:
            LOGGER.exception(
                "flow=system action=restart_container_failed target=%s container=%s error=%s",
                container_key,
                container_name,
                exc,
            )

    thread = threading.Thread(target=run, daemon=True, name=f"restart-{container_key}")
    thread.start()


@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
def status():
    return JSONResponse(STATE.snapshot())


@app.get("/api/system/status")
def system_status():
    receiver = STATE.snapshot()
    capture_response = request_capture_agent("/api/status")
    try:
        capture_body = json.loads(capture_response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        capture_body = {}

    reverse_position_url = receiver["config"].get("reverse_geocoder_url", "")
    reverse_health_url = reverse_position_url.replace("/api/position", "/api/health")
    reverse_latest_url = reverse_position_url.replace("/api/position", "/api/latest")
    reverse_body = {}
    reverse_latest = {}
    reverse_error = ""
    atem_body = {}
    atem_error = ""
    try:
        with urllib.request.urlopen(reverse_health_url, timeout=2.0) as response:
            reverse_body = json.loads(response.read(65536).decode("utf-8"))
        with urllib.request.urlopen(reverse_latest_url, timeout=2.0) as response:
            reverse_latest = json.loads(response.read(65536).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        reverse_error = str(exc)
    try:
        with urllib.request.urlopen(ATEM_OUTPUT_HEALTH_URL, timeout=2.0) as response:
            atem_body = json.loads(response.read(65536).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        atem_error = str(exc)

    capture_config = capture_body.get("config", {})
    capture_logs = capture_body.get("logs", [])
    latest_fix = receiver.get("latest") or {}
    latest_multiviewer = reverse_latest.get("multiviewer") or {}
    return {
        "e2e_health": dict(e2e_health),
        "gps_receiver": {
            "ok": bool(receiver["running"]),
            "status": receiver["status"],
            "input_status": receiver["input_status"],
            "input": {
                "connected": receiver["socket_connected"],
                "client": receiver["socket_client"],
                "sample_rate": receiver["sample_rate"],
                "channels": receiver["config"].get("input_channels"),
                "gps_channel": receiver["config"].get("gps_channel"),
                "total_samples": receiver["total_samples"],
            },
            "output": {
                "decoded_count": receiver["decoded_count"],
                "latest_time": latest_fix.get("time", ""),
                "lat": latest_fix.get("lat", ""),
                "lon": latest_fix.get("lon", ""),
                "alt": latest_fix.get("alt", ""),
                "csv": receiver["config"].get("output_csv", ""),
                "geocode_queue": receiver["geocode_queue_size"],
            },
        },
        "capture_agent": {
            "ok": capture_response.status_code == 200,
            "running": bool(capture_body.get("running")),
            "error": capture_body.get("error", ""),
            "pid": capture_body.get("pid"),
            "input": {
                "device": capture_config.get("CAPTURE_DEVICE", ""),
                "sample_rate": capture_config.get("SAMPLE_RATE", ""),
                "sample_format": capture_config.get("SAMPLE_FORMAT", ""),
                "channels": capture_config.get("INPUT_CHANNELS", ""),
                "gps_channel": capture_config.get("GPS_CHANNEL", ""),
            },
            "output": {
                "host": capture_config.get("GPS_RECEIVER_HOST", ""),
                "port": capture_config.get("GPS_RECEIVER_PCM_PORT", ""),
                "last_log": capture_logs[-1] if capture_logs else "",
            },
        },
        "reverse_geocoder": {
            "ok": bool(reverse_body.get("ok")),
            "db_loaded": bool(reverse_body.get("db_loaded")),
            "area_count": reverse_body.get("area_count", 0),
            "error": reverse_error,
            "input": {
                "time": reverse_latest.get("time", ""),
                "lat": reverse_latest.get("lat", ""),
                "lon": reverse_latest.get("lon", ""),
            },
            "output": {
                "address": reverse_latest.get("address_label", ""),
                "admin_code": reverse_latest.get("admin_code", ""),
                "multiviewer_sent": latest_multiviewer.get("sent"),
                "multiviewer_error": latest_multiviewer.get("error", ""),
            },
        },
        "atem_output": {
            "ok": bool(atem_body.get("ok")),
            "error": atem_error,
            "atem_enabled": bool(atem_body.get("atem_enabled")),
            "atem_host": atem_body.get("atem_host", ""),
            "image_exists": bool(atem_body.get("image_exists")),
            "super_health": atem_body.get("super_health", {}),
            "latest": atem_body.get("latest", {}),
        },
    }


@app.post("/api/system/health/e2e")
def run_e2e_health_now():
    global e2e_health
    result = run_e2e_health_check()
    maybe_notify_e2e_health(result)
    with STATE.lock:
        e2e_health = result
    return result


@app.post("/api/system/atem/free-text")
def send_atem_free_text(payload: dict):
    text = str(payload.get("text", ""))
    clear_display = bool(payload.get("clear_display")) or text == ""
    body = json.dumps(
        {
            "text": "" if clear_display else text,
            "clear_display": clear_display,
            "time": format_japanese_time(now_jst()),
            "source": "ui_free_text",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        ATEM_OUTPUT_FREE_TEXT_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            response_body = response.read(65536)
        result = json.loads(response_body.decode("utf-8"))
        LOGGER.warning(
            "flow=system action=atem_free_text clear_display=%s text=%s queued=%s sent=%s error=%s",
            clear_display,
            text,
            result.get("upload_queued"),
            result.get("sent"),
            result.get("error", ""),
        )
        return result
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        LOGGER.warning("flow=system action=atem_free_text_failed error=%s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.get("/api/capture-agent/status")
def capture_agent_status():
    return request_capture_agent("/api/status")


@app.get("/api/capture-agent/devices")
def capture_agent_devices():
    return request_capture_agent("/api/devices")


@app.post("/api/capture-agent/config")
def capture_agent_config(payload: dict):
    return request_capture_agent("/api/config", method="POST", payload=payload)


@app.post("/api/capture-agent/start")
def capture_agent_start():
    return request_capture_agent("/api/start", method="POST", payload={})


@app.post("/api/capture-agent/stop")
def capture_agent_stop():
    return request_capture_agent("/api/stop", method="POST", payload={})


@app.post("/api/system/containers/{container_key}/restart")
def restart_system_container(container_key: str):
    try:
        container_name = RESTARTABLE_CONTAINERS.get(container_key)
        if not container_name:
            return JSONResponse(
                {"ok": False, "target": container_key, "error": "再起動対象外のコンテナです"},
                status_code=404,
            )
        restart_container_async(container_key)
        return {
            "ok": True,
            "target": container_key,
            "container": container_name,
            "message": f"{container_key} コンテナの再起動を開始しました",
        }
    except Exception as exc:
        LOGGER.exception("flow=system action=restart_container_failed target=%s error=%s", container_key, exc)
        return JSONResponse(
            {"ok": False, "target": container_key, "error": str(exc)},
            status_code=500,
        )


@app.post("/api/system/atem-output/restart")
def restart_atem_output():
    return restart_system_container("atem-output")


@app.get("/api/devices")
def devices():
    return {"devices": list_capture_devices()}


@app.post("/api/config")
async def set_config(payload: dict):
    try:
        STATE.set_config(payload)
        return {"ok": True, "status": STATE.snapshot()}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/start")
def start():
    with STATE.lock:
        if STATE.running:
            return {"ok": True}
        STATE.stop_event.clear()
        STATE.worker = threading.Thread(target=worker_main, daemon=True)
        STATE.worker.start()
    return {"ok": True}


@app.post("/api/stop")
def stop():
    STATE.stop_event.set()
    return {"ok": True}


@app.get("/api/download")
def download():
    path = Path(STATE.config.output_csv)
    if not path.is_absolute():
        path = BASE_DIR / path
    return FileResponse(path, filename=path.name, media_type="text/csv")


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_json(STATE.snapshot())
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
