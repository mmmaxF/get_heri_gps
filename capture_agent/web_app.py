#!/usr/bin/env python3
"""Host-side control API for configuring and running capture-agent."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socketserver
import subprocess
import sys
import threading
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
CONFIG_KEYS = (
    "GPS_RECEIVER_HOST",
    "GPS_RECEIVER_PCM_PORT",
    "SAMPLE_RATE",
    "SAMPLE_FORMAT",
    "INPUT_CHANNELS",
    "GPS_CHANNEL",
    "CAPTURE_DEVICE",
    "CAPTURE_COMMAND",
    "AGENT_NAME",
    "CONNECT_TIMEOUT_SECONDS",
    "RECONNECT_SECONDS",
    "PROGRESS_LOG_SECONDS",
    "CHUNK_BYTES",
    "LOG_LEVEL",
    "CAPTURE_AGENT_CONTROL_SOCKET",
)
DEFAULTS = {
    "GPS_RECEIVER_HOST": "127.0.0.1",
    "GPS_RECEIVER_PCM_PORT": "9010",
    "SAMPLE_RATE": "48000",
    "SAMPLE_FORMAT": "S16_LE",
    "INPUT_CHANNELS": "4",
    "GPS_CHANNEL": "4",
    "CAPTURE_DEVICE": "hw:2,0",
    "CAPTURE_COMMAND": "",
    "AGENT_NAME": "sdi-capture-01",
    "CONNECT_TIMEOUT_SECONDS": "5",
    "RECONNECT_SECONDS": "3",
    "PROGRESS_LOG_SECONDS": "10",
    "CHUNK_BYTES": "65536",
    "LOG_LEVEL": "INFO",
    "CAPTURE_AGENT_CONTROL_SOCKET": "run/control.sock",
}


def read_env(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_FILE
    values = dict(DEFAULTS)
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key in CONFIG_KEYS:
            values[key] = value
    return values


def env_text(values: dict[str, str]) -> str:
    return f"""# gps-receiver connection
GPS_RECEIVER_HOST={values["GPS_RECEIVER_HOST"]}
GPS_RECEIVER_PCM_PORT={values["GPS_RECEIVER_PCM_PORT"]}

# PCM format
SAMPLE_RATE={values["SAMPLE_RATE"]}
SAMPLE_FORMAT={values["SAMPLE_FORMAT"]}
INPUT_CHANNELS={values["INPUT_CHANNELS"]}
GPS_CHANNEL={values["GPS_CHANNEL"]}

# Host capture
CAPTURE_DEVICE={values["CAPTURE_DEVICE"]}
CAPTURE_COMMAND={values["CAPTURE_COMMAND"]}
AGENT_NAME={values["AGENT_NAME"]}

# Connection and logs
CONNECT_TIMEOUT_SECONDS={values["CONNECT_TIMEOUT_SECONDS"]}
RECONNECT_SECONDS={values["RECONNECT_SECONDS"]}
PROGRESS_LOG_SECONDS={values["PROGRESS_LOG_SECONDS"]}
CHUNK_BYTES={values["CHUNK_BYTES"]}
LOG_LEVEL={values["LOG_LEVEL"]}

# Local control API
CAPTURE_AGENT_CONTROL_SOCKET={values["CAPTURE_AGENT_CONTROL_SOCKET"]}
"""


def write_env(values: dict[str, str], path: Path | None = None) -> None:
    path = path or ENV_FILE
    temporary = path.with_suffix(".tmp")
    temporary.write_text(env_text(values), encoding="utf-8")
    temporary.replace(path)


def positive_int(values: dict, key: str, minimum: int, maximum: int) -> str:
    try:
        value = int(values[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key}は整数で指定してください") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{key}は{minimum}から{maximum}で指定してください")
    return str(value)


def normalize_config(payload: dict) -> dict[str, str]:
    current = read_env()
    values = {key: str(payload.get(key, current[key])).strip() for key in CONFIG_KEYS}
    values["GPS_RECEIVER_PCM_PORT"] = positive_int(
        values, "GPS_RECEIVER_PCM_PORT", 1, 65535
    )
    values["SAMPLE_RATE"] = positive_int(values, "SAMPLE_RATE", 8000, 192000)
    values["INPUT_CHANNELS"] = positive_int(values, "INPUT_CHANNELS", 1, 16)
    values["GPS_CHANNEL"] = positive_int(values, "GPS_CHANNEL", 1, 16)
    if int(values["GPS_CHANNEL"]) > int(values["INPUT_CHANNELS"]):
        raise ValueError("GPSチャンネルは入力チャンネル数以下にしてください")
    if values["SAMPLE_FORMAT"] != "S16_LE":
        raise ValueError("現在対応しているPCM形式はS16_LEだけです")
    if not values["GPS_RECEIVER_HOST"]:
        raise ValueError("gps-receiverのホストを入力してください")
    if not values["CAPTURE_DEVICE"] and not values["CAPTURE_COMMAND"]:
        raise ValueError("音声デバイスまたはキャプチャコマンドを指定してください")
    return values


def list_capture_devices() -> list[dict]:
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            text=True,
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pattern = re.compile(
        r"card (\d+): ([^\[]+) \[([^\]]+)\], "
        r"device (\d+): ([^\[]+) \[([^\]]+)\]"
    )
    devices = []
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        card, _card_id, card_name, device, _device_id, device_name = match.groups()
        devices.append(
            {
                "device": f"hw:{card},{device}",
                "label": f"{card_name} / {device_name} (hw:{card},{device})",
            }
        )
    return devices


class AgentManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.logs = deque(maxlen=300)
        self.exit_code: int | None = None

    def append_log(self, line: str) -> None:
        with self.lock:
            self.logs.append(line)

    def _read_output(self, process: subprocess.Popen) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self.append_log(line.rstrip())
        exit_code = process.wait()
        with self.lock:
            if self.process is process:
                self.exit_code = exit_code
                self.process = None

    def start(self) -> None:
        with self.lock:
            if self.process and self.process.poll() is None:
                return
            self.exit_code = None
            command = [
                sys.executable,
                str(BASE_DIR / "capture_agent.py"),
                "--env-file",
                str(ENV_FILE),
            ]
            self.process = subprocess.Popen(
                command,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            process = self.process
        threading.Thread(
            target=self._read_output,
            args=(process,),
            daemon=True,
        ).start()

    def stop(self) -> None:
        with self.lock:
            process = self.process
        if not process or process.poll() is not None:
            return
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def snapshot(self) -> dict:
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            return {
                "running": running,
                "pid": self.process.pid if running else None,
                "exit_code": self.exit_code,
                "config": read_env(),
                "logs": list(self.logs),
            }


MANAGER = AgentManager()


class ThreadingUnixHTTPServer(
    socketserver.ThreadingMixIn,
    socketserver.UnixStreamServer,
):
    daemon_threads = True


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "HeriCaptureAgent/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"local {fmt % args}")

    def send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1024 * 1024:
            raise ValueError("リクエストが大きすぎます")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            self.send_json(MANAGER.snapshot())
        elif path == "/api/devices":
            self.send_json({"devices": list_capture_devices()})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/config":
                if MANAGER.snapshot()["running"]:
                    raise ValueError("設定を変更するには先に送信を停止してください")
                values = normalize_config(self.read_json())
                write_env(values)
                self.send_json({"ok": True, "status": MANAGER.snapshot()})
            elif path == "/api/start":
                MANAGER.start()
                self.send_json({"ok": True, "status": MANAGER.snapshot()})
            elif path == "/api/stop":
                MANAGER.stop()
                self.send_json({"ok": True, "status": MANAGER.snapshot()})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(
                {"ok": False, "error": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )


def main() -> int:
    global ENV_FILE
    parser = argparse.ArgumentParser(description="capture-agent制御API")
    parser.add_argument("--env-file", default=str(ENV_FILE))
    args = parser.parse_args()
    ENV_FILE = Path(args.env_file).resolve()
    if not ENV_FILE.exists():
        write_env(dict(DEFAULTS), ENV_FILE)
    config = read_env(ENV_FILE)
    socket_path = Path(config["CAPTURE_AGENT_CONTROL_SOCKET"])
    if not socket_path.is_absolute():
        socket_path = BASE_DIR / socket_path
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    server = ThreadingUnixHTTPServer(str(socket_path), RequestHandler)
    os.chmod(socket_path, 0o666)
    print(f"capture-agent control API: unix://{socket_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        MANAGER.stop()
        server.server_close()
        socket_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
