#!/usr/bin/env python3
"""Capture PCM audio on the host and stream it to gps-receiver over TCP."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


STOP_EVENT = threading.Event()
LOGGER = logging.getLogger("capture-agent")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ホストでPCM音声をキャプチャし、gps-receiverへTCP送信します。"
    )
    parser.add_argument(
        "--env-file",
        default=str(Path(__file__).with_name(".env")),
        help="設定ファイル（既定: capture_agent/.env）",
    )
    parser.add_argument("--host", help="gps-receiverのホスト名またはIPアドレス")
    parser.add_argument("--port", type=int, help="gps-receiverのPCM Socketポート")
    parser.add_argument("--command", help="PCMを標準出力へ出すキャプチャコマンド")
    parser.add_argument("--list-devices", action="store_true", help="arecord -lを表示して終了")
    parser.add_argument("--check", action="store_true", help="設定と接続可否を確認して終了")
    return parser


def configure_logging() -> None:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y/%m/%d %H:%M:%S",
    )


def stop_handler(_signum, _frame) -> None:
    LOGGER.info("停止要求を受け付けました")
    STOP_EVENT.set()


def capture_stderr(pipe) -> None:
    if pipe is None:
        return
    for raw_line in iter(pipe.readline, b""):
        line = raw_line.decode("utf-8", errors="replace").rstrip()
        if line:
            LOGGER.info("capture: %s", line)


def terminate_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def connect(host: str, port: int, timeout: float) -> socket.socket:
    conn = socket.create_connection((host, port), timeout=timeout)
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    conn.settimeout(None)
    return conn


def stream_once(config: dict) -> None:
    host = config["host"]
    port = config["port"]
    LOGGER.info("gps-receiverへ接続します: %s:%s", host, port)
    conn = connect(host, port, config["connect_timeout"])
    process = None
    try:
        header = {
            "protocol": "heri-pcm",
            "version": 1,
            "sample_rate": config["sample_rate"],
            "sample_format": config["sample_format"],
            "channels": config["channels"],
            "gps_channel": config["gps_channel"],
            "agent_name": config["agent_name"],
        }
        conn.sendall(json.dumps(header, ensure_ascii=True).encode("utf-8") + b"\n")

        command = shlex.split(config["capture_command"])
        if not command:
            raise RuntimeError("CAPTURE_COMMAND is empty")
        LOGGER.info("キャプチャを開始します: %s", config["capture_command"])
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        stderr_thread = threading.Thread(
            target=capture_stderr,
            args=(process.stderr,),
            daemon=True,
        )
        stderr_thread.start()

        total_bytes = 0
        last_report = time.monotonic()
        while not STOP_EVENT.is_set():
            chunk = process.stdout.read(config["chunk_bytes"])
            if not chunk:
                return_code = process.poll()
                raise RuntimeError(
                    f"キャプチャコマンドが終了しました (exit={return_code})"
                )
            conn.sendall(chunk)
            total_bytes += len(chunk)
            now = time.monotonic()
            if now - last_report >= config["progress_seconds"]:
                LOGGER.info(
                    "PCM送信中: %.1f MiB (%.1f秒)",
                    total_bytes / (1024 * 1024),
                    total_bytes
                    / (
                        config["sample_rate"]
                        * config["channels"]
                        * config["bytes_per_sample"]
                    ),
                )
                last_report = now
    finally:
        terminate_process(process)
        conn.close()


def make_config(args: argparse.Namespace) -> dict:
    sample_rate = env_int("SAMPLE_RATE", 48000)
    channels = env_int("INPUT_CHANNELS", 4)
    sample_format = os.environ.get("SAMPLE_FORMAT", "S16_LE")
    if sample_format != "S16_LE":
        raise ValueError("現在対応しているSAMPLE_FORMATはS16_LEだけです")
    gps_channel = env_int("GPS_CHANNEL", 4)
    if channels < 1:
        raise ValueError("INPUT_CHANNELS must be at least 1")
    if not 1 <= gps_channel <= channels:
        raise ValueError("GPS_CHANNEL must be between 1 and INPUT_CHANNELS")

    device = os.environ.get("CAPTURE_DEVICE", "hw:2,0")
    default_command = (
        f"arecord -D {shlex.quote(device)} -f {sample_format} "
        f"-r {sample_rate} -c {channels} -t raw"
    )
    return {
        "host": args.host or os.environ.get("GPS_RECEIVER_HOST", "127.0.0.1"),
        "port": args.port or env_int("GPS_RECEIVER_PCM_PORT", 9010),
        "capture_command": args.command
        or os.environ.get("CAPTURE_COMMAND")
        or default_command,
        "sample_rate": sample_rate,
        "sample_format": sample_format,
        "channels": channels,
        "gps_channel": gps_channel,
        "agent_name": os.environ.get("AGENT_NAME", socket.gethostname()),
        "connect_timeout": env_float("CONNECT_TIMEOUT_SECONDS", 5.0),
        "reconnect_seconds": env_float("RECONNECT_SECONDS", 3.0),
        "progress_seconds": env_float("PROGRESS_LOG_SECONDS", 10.0),
        "chunk_bytes": env_int("CHUNK_BYTES", 65536),
        "bytes_per_sample": 2,
    }


def check_connection(config: dict) -> int:
    try:
        conn = connect(config["host"], config["port"], config["connect_timeout"])
    except OSError as exc:
        LOGGER.error("接続できません: %s", exc)
        return 1
    conn.close()
    LOGGER.info("接続できます: %s:%s", config["host"], config["port"])
    return 0


def main() -> int:
    bootstrap = build_parser()
    args = bootstrap.parse_args()
    load_env(Path(args.env_file))
    configure_logging()

    if args.list_devices:
        return subprocess.run(["arecord", "-l"], check=False).returncode

    try:
        config = make_config(args)
    except ValueError as exc:
        LOGGER.error("設定エラー: %s", exc)
        return 2

    LOGGER.info(
        "設定: receiver=%s:%s format=%s rate=%s channels=%s gps_channel=%s",
        config["host"],
        config["port"],
        config["sample_format"],
        config["sample_rate"],
        config["channels"],
        config["gps_channel"],
    )
    if args.check:
        return check_connection(config)

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    while not STOP_EVENT.is_set():
        try:
            stream_once(config)
        except (ConnectionError, OSError, RuntimeError) as exc:
            if not STOP_EVENT.is_set():
                LOGGER.error("送信を継続できません: %s", exc)
        if STOP_EVENT.wait(config["reconnect_seconds"]):
            break
        LOGGER.info("再接続します")
    LOGGER.info("capture-agentを停止しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
