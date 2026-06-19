#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Send a title text command to the multiviewer from the terminal."""

import argparse
import os
import socket
from pathlib import Path


DEFAULT_ENV_FILE = Path(__file__).resolve().parent / "reverse_geocoder" / ".env"


def load_env_file(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def config_value(env_values, key, default):
    return os.environ.get(key) or env_values.get(key) or default


def send_command(host, port, prefix, text, encoding, timeout, raw=False):
    command = text if raw else f"{prefix}{text}"
    payload = (command + "\r\n").encode(encoding, errors="replace")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload)
        try:
            response = sock.recv(1024)
        except socket.timeout:
            response = b""
    return command, response.decode(encoding, errors="replace").strip()


def main():
    env_values = load_env_file(DEFAULT_ENV_FILE)
    parser = argparse.ArgumentParser(description="Send a Shift_JIS TCP command to the multiviewer.")
    parser.add_argument("text", help="Text to send. Example: 大阪府大阪市")
    parser.add_argument("--host", default=config_value(env_values, "MULTIVIEWER_HOST", "192.168.11.69"))
    parser.add_argument("--port", type=int, default=int(config_value(env_values, "MULTIVIEWER_PORT", "51069")))
    parser.add_argument("--prefix", default=config_value(env_values, "MULTIVIEWER_COMMAND_PREFIX", "STW010V010"))
    parser.add_argument("--encoding", default=config_value(env_values, "MULTIVIEWER_ENCODING", "shift_jis"))
    parser.add_argument("--timeout", type=float, default=float(config_value(env_values, "MULTIVIEWER_TIMEOUT_SECONDS", "5.0")))
    parser.add_argument("--raw", action="store_true", help="Send text as the full command without prefixing.")
    args = parser.parse_args()

    print(f"connect: {args.host}:{args.port}")
    command, response = send_command(
        host=args.host,
        port=args.port,
        prefix=args.prefix,
        text=args.text,
        encoding=args.encoding,
        timeout=args.timeout,
        raw=args.raw,
    )
    print(f"sent: {command!r} + CRLF")
    print(f"response: {response or '(no response)'}")


if __name__ == "__main__":
    main()
