#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web app for demodulating GPS/MOD frames from one SDI audio input."""

import asyncio
import csv
import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles


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


SAMPLE_RATE = env_int("SAMPLE_RATE", 48000)
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = env_int("PORT", 8010)
DEFAULT_OUTPUT_CSV = Path(os.environ.get("OUTPUT_CSV", OUTPUT_DIR / "gps_positions.csv"))
DEFAULT_INPUT_DEVICE = os.environ.get("INPUT_DEVICE", "hw:2,0")
DEFAULT_REVERSE_GEOCODER_URL = os.environ.get("REVERSE_GEOCODER_URL", "http://reverse-geocoder:8020/api/position")
DEFAULT_TELOP_OUTPUT_URL = os.environ.get("TELOP_OUTPUT_URL", "http://telop-output:8030")


def now_jst():
    return datetime.now(JST)


def bcd_byte(b):
    hi = (b >> 4) & 0xF
    lo = b & 0xF
    if hi > 9 or lo > 9:
        return None
    return hi * 10 + lo


def parse_u16_bcd(buf):
    if len(buf) != 2:
        return None
    a = bcd_byte(buf[0])
    b = bcd_byte(buf[1])
    if a is None or b is None:
        return None
    return a * 100 + b


def parse_dms_bcd(buf):
    if len(buf) != 5:
        return None
    deg_hi = bcd_byte(buf[0])
    deg_lo = bcd_byte(buf[1])
    minute = bcd_byte(buf[2])
    second = bcd_byte(buf[3])
    centi = bcd_byte(buf[4])
    if None in (deg_hi, deg_lo, minute, second, centi):
        return None
    deg = deg_hi * 100 + deg_lo
    if minute >= 60 or second >= 60:
        return None
    return deg + minute / 60.0 + (second + centi / 100.0) / 3600.0


def parse_mod_info(info):
    if not info.startswith(b":MOD") or len(info) < 21:
        return None
    payload = info[1:]
    if not payload.startswith(b"MOD"):
        return None
    group = parse_u16_bcd(payload[3:5])
    aircraft = parse_u16_bcd(payload[5:7])
    lat = parse_dms_bcd(payload[7:12])
    lon = parse_dms_bcd(payload[12:17])
    alt = parse_u16_bcd(payload[17:19])
    if lat is None or lon is None or alt is None:
        return None
    return {
        "group": group,
        "aircraft": aircraft,
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "payload_hex": payload.hex(),
    }


FLAG = np.asarray([0, 1, 1, 1, 1, 1, 1, 0], dtype=np.uint8)


def goertzel(blocks, freq):
    n = blocks.shape[1]
    t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
    osc = np.exp(-2j * np.pi * freq * t).astype(np.complex64)
    v = blocks @ osc
    return v.real * v.real + v.imag * v.imag


def fsk_bits(x, phase):
    step = int(round(SAMPLE_RATE / 1200))
    start = int(round(phase * step))
    usable = ((len(x) - start) // step) * step
    if usable < step * 64:
        return None
    blocks = x[start : start + usable].reshape(-1, step)
    return (goertzel(blocks, 1800) > goertzel(blocks, 1200)).astype(np.uint8)


def diff_bits(bits):
    if bits is None or len(bits) < 2:
        return bits
    return (bits[1:] ^ bits[:-1]).astype(np.uint8)


def find_flags(bits):
    flags = []
    for i in range(0, len(bits) - 7):
        if np.array_equal(bits[i : i + 8], FLAG):
            flags.append(i)
    return flags


def unstuff(bits):
    out = []
    ones = 0
    i = 0
    while i < len(bits):
        bit = int(bits[i])
        out.append(bit)
        if bit:
            ones += 1
            if ones == 5:
                if i + 1 < len(bits) and bits[i + 1] == 0:
                    i += 1
                ones = 0
        else:
            ones = 0
        i += 1
    return np.asarray(out, dtype=np.uint8)


def bits_to_bytes_lsb(bits):
    usable = (len(bits) // 8) * 8
    if usable <= 0:
        return b""
    arr = bits[:usable].reshape(-1, 8)
    return np.packbits(arr[:, ::-1].astype(np.uint8), axis=1, bitorder="big")[:, 0].tobytes()


def crc16_x25(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    return crc & 0xFFFF


def decode_bits(bits):
    flags = find_flags(bits)
    frames = []
    for a, b in zip(flags, flags[1:]):
        if b - a < 24:
            continue
        payload_bits = unstuff(bits[a + 8 : b])
        frame = bits_to_bytes_lsb(payload_bits)
        if len(frame) >= 18:
            frames.append((a, b, frame))
    return frames


def decode_samples(samples, window_start_sample):
    x = samples.astype(np.float32)
    x -= float(np.mean(x))
    peak = float(np.max(np.abs(x))) or 1.0
    x /= peak
    best_rows = []
    for phase in (0.0, 0.25, 0.5, 0.75):
        bits = fsk_bits(x, phase)
        if bits is None:
            continue
        bits = 1 - diff_bits(bits)
        phase_rows = []
        for bit_start, _bit_end, frame in decode_bits(bits):
            if len(frame) < 18 or frame[14] != 0x03 or frame[15] != 0xF0:
                continue
            if crc16_x25(frame) != 0xF0B8:
                continue
            parsed = parse_mod_info(frame[16:-2])
            if parsed:
                sample_offset = window_start_sample + int(bit_start * SAMPLE_RATE / 1200)
                phase_rows.append((sample_offset, phase, parsed))
        if len(phase_rows) > len(best_rows):
            best_rows = phase_rows
    return best_rows


@dataclass
class RuntimeConfig:
    mode: str = "command"
    gps_channel: int = env_int("GPS_CHANNEL", 2)
    input_channels: int = env_int("INPUT_CHANNELS", 2)
    input_device: str = DEFAULT_INPUT_DEVICE
    input_command: str = os.environ.get("INPUT_COMMAND", "arecord -D hw:2,0 -f S16_LE -r 48000 -c 2 -t raw")
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
        self.latest_geocode = None
        self.geocode_error = ""
        self.latest = None
        self.recent = deque(maxlen=30)
        self.worker = None
        self.stop_event = threading.Event()

    def snapshot(self):
        with self.lock:
            cfg = asdict(self.config)
            return {
                "config": cfg,
                "running": self.running,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "status": self.status,
                "error": self.error,
                "total_samples": self.total_samples,
                "decoded_count": self.decoded_count,
                "geocode_success_count": self.geocode_success_count,
                "geocode_error_count": self.geocode_error_count,
                "latest_geocode": self.latest_geocode,
                "geocode_error": self.geocode_error,
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

    def mark_stopped(self, error=""):
        with self.lock:
            self.running = False
            self.status = "error" if error else "stopped"
            self.error = error

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

    def mark_geocode_error(self, error):
        with self.lock:
            self.geocode_error_count += 1
            self.geocode_error = error

    def set_samples(self, total_samples):
        with self.lock:
            self.total_samples = total_samples


STATE = AppState()
CSV_HEADER = ["time", "source", "channel", "offset_sec", "lon", "lat", "alt", "group", "aircraft", "payload_hex"]


def post_reverse_geocode(config, row):
    url = (config.reverse_geocoder_url or "").strip()
    if not url:
        return None
    payload = {
        "time": row["time"],
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "alt": row["alt"],
        "source": "get_heri_gps",
        "channel": row["channel"],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=0.8) as res:
            body = res.read(8192)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        STATE.mark_geocode_error(str(exc))
        return None
    try:
        geocode = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        STATE.mark_geocode_error(f"invalid geocoder response: {exc}")
        return None
    if geocode.get("ok") is False:
        STATE.mark_geocode_error(str(geocode.get("error", "reverse geocoder error")))
        return None
    STATE.mark_geocode_success(geocode)
    return geocode


def telop_request(path, method="GET", payload=None, timeout=2.0):
    url = DEFAULT_TELOP_OUTPUT_URL.rstrip("/") + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read(), res.headers.get("Content-Type", "application/octet-stream")


class CsvWriter:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute():
            self.path = BASE_DIR / self.path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a", newline="")
        self.writer = csv.writer(self.file)
        if self.path.stat().st_size == 0:
            self.writer.writerow(CSV_HEADER)
            self.file.flush()

    def write(self, row):
        self.writer.writerow([row.get(k, "") for k in CSV_HEADER])
        self.file.flush()

    def close(self):
        self.file.close()


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
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def worker_main():
    STATE.mark_started()
    config = STATE.config
    if not config.mode:
        config.mode = "command"
    writer = CsvWriter(config.output_csv)
    sample_buffer = np.empty(0, dtype=np.int16)
    buffer_start_sample = 0
    next_decode = time.monotonic() + config.decode_interval_seconds
    seen = set()
    total_samples = 0
    try:
        source_iter = iter_test_chunks(config, STATE.stop_event) if config.mode == "test" else iter_command_chunks(config, STATE.stop_event)
        source_name = ""
        source_start = now_jst()
        for chunk, source_name, source_start in source_iter:
            total_samples += len(chunk)
            STATE.set_samples(total_samples)
            sample_buffer = np.concatenate([sample_buffer, chunk])
            keep = int(config.window_seconds * SAMPLE_RATE)
            if len(sample_buffer) > keep:
                drop = len(sample_buffer) - keep
                sample_buffer = sample_buffer[drop:]
                buffer_start_sample += drop
            if time.monotonic() < next_decode or len(sample_buffer) < SAMPLE_RATE * 4:
                continue
            next_decode = time.monotonic() + config.decode_interval_seconds
            for sample_offset, _phase, parsed in decode_samples(sample_buffer, buffer_start_sample):
                offset_sec = sample_offset / SAMPLE_RATE
                key = (round(offset_sec, 2), parsed["payload_hex"])
                if key in seen:
                    continue
                seen.add(key)
                t = source_start + timedelta(seconds=offset_sec)
                row = {
                    "time": t.isoformat(),
                    "source": source_name,
                    "channel": config.gps_channel,
                    "offset_sec": f"{offset_sec:.6f}",
                    "lon": f"{parsed['lon']:.8f}",
                    "lat": f"{parsed['lat']:.8f}",
                    "alt": parsed["alt"],
                    "group": parsed["group"],
                    "aircraft": "" if parsed["aircraft"] is None else parsed["aircraft"],
                    "payload_hex": parsed["payload_hex"],
                }
                geocode = post_reverse_geocode(config, row)
                if geocode:
                    row["geocode"] = geocode
                writer.write(row)
                STATE.add_row(row)
    except Exception as exc:
        STATE.mark_stopped(str(exc))
    else:
        STATE.mark_stopped()
    finally:
        writer.close()


app = FastAPI(title="get_heri_gps")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def build_arecord_command(device, channels):
    return f"arecord -D {device} -f S16_LE -r {SAMPLE_RATE} -c {channels} -t raw"


def list_capture_devices():
    devices = []
    try:
        proc = subprocess.run(["arecord", "-l"], text=True, capture_output=True, timeout=3)
    except Exception:
        return devices
    pat = re.compile(r"card (\d+): ([^\[]+) \[([^\]]+)\], device (\d+): ([^\[]+) \[([^\]]+)\]")
    for line in proc.stdout.splitlines():
        m = pat.search(line)
        if not m:
            continue
        card, card_id, card_name, dev, dev_id, dev_name = m.groups()
        hw = f"hw:{card},{dev}"
        label = f"{card_name} / {dev_name} ({hw})"
        devices.append({"device": hw, "label": label, "card": int(card), "subdevice": int(dev), "default_channels": 2})
    if not devices:
        devices.append({"device": DEFAULT_INPUT_DEVICE, "label": f"{DEFAULT_INPUT_DEVICE}", "card": None, "subdevice": None, "default_channels": 2})
    return devices


@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
def status():
    return JSONResponse(STATE.snapshot())


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


@app.get("/api/telop/status")
def telop_status():
    try:
        body, _ctype = telop_request("/api/status")
        return JSONResponse(json.loads(body.decode("utf-8")))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.get("/api/telop/output-devices")
def telop_output_devices():
    try:
        body, _ctype = telop_request("/api/output-devices")
        return JSONResponse(json.loads(body.decode("utf-8")))
    except Exception as exc:
        return JSONResponse({"devices": [{"id": "", "label": "未選択", "kind": "none"}], "error": str(exc)}, status_code=200)


@app.get("/api/telop/fonts")
def telop_fonts():
    try:
        body, _ctype = telop_request("/api/fonts")
        return JSONResponse(json.loads(body.decode("utf-8")))
    except Exception as exc:
        return JSONResponse({"fonts": [], "error": str(exc)}, status_code=200)


@app.get("/api/telop/config")
def telop_config():
    try:
        body, _ctype = telop_request("/api/config")
        return JSONResponse(json.loads(body.decode("utf-8")))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.post("/api/telop/config")
async def telop_set_config(payload: dict):
    try:
        body, _ctype = telop_request("/api/config", method="POST", payload=payload)
        return JSONResponse(json.loads(body.decode("utf-8")))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.post("/api/telop/start")
def telop_start():
    try:
        body, _ctype = telop_request("/api/start", method="POST", payload={})
        return JSONResponse(json.loads(body.decode("utf-8")))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.post("/api/telop/stop")
def telop_stop():
    try:
        body, _ctype = telop_request("/api/stop", method="POST", payload={})
        return JSONResponse(json.loads(body.decode("utf-8")))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@app.get("/api/telop/preview/{name}.png")
def telop_preview(name: str):
    if name not in ("v", "key"):
        return JSONResponse({"ok": False, "error": "unknown preview"}, status_code=404)
    try:
        body, ctype = telop_request(f"/api/preview/{name}.png", timeout=4.0)
        return Response(body, media_type=ctype, headers={"Cache-Control": "no-store"})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


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
