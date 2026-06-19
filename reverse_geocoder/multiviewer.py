#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TCP command sender for the multiviewer title API."""

import os
import socket


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


ENABLED = env_bool("MULTIVIEWER_ENABLED", True)
HOST = os.environ.get("MULTIVIEWER_HOST", "192.168.11.69")
PORT = int(env_float("MULTIVIEWER_PORT", 51069))
COMMAND_PREFIX = os.environ.get("MULTIVIEWER_COMMAND_PREFIX", "STW010V010")
TEXT_TEMPLATE = os.environ.get("MULTIVIEWER_TEXT_TEMPLATE", "{address_label}")
ENCODING = os.environ.get("MULTIVIEWER_ENCODING", "shift_jis")
TIMEOUT_SECONDS = env_float("MULTIVIEWER_TIMEOUT_SECONDS", 2.0)
SEND_ON_NOT_FOUND = env_bool("MULTIVIEWER_SEND_ON_NOT_FOUND", False)
DEDUP_TEXT = env_bool("MULTIVIEWER_DEDUP_TEXT", True)

_last_text = None


def render_text(position):
    if not position.get("ok") and not SEND_ON_NOT_FOUND:
        return ""
    try:
        text = TEXT_TEMPLATE.format(
            address_label=position.get("address_label", ""),
            prefecture=position.get("prefecture", ""),
            city=position.get("city", ""),
            ward=position.get("ward", ""),
            lat=position.get("lat", ""),
            lon=position.get("lon", ""),
            alt=position.get("alt", ""),
            time=position.get("time", ""),
        )
    except (KeyError, ValueError):
        text = position.get("address_label", "")
    return str(text).strip()


def send_text(text):
    global _last_text
    if not ENABLED:
        return {"enabled": False, "sent": False, "skipped": True, "reason": "disabled"}
    if not HOST:
        return {"enabled": True, "sent": False, "skipped": True, "reason": "host not configured"}
    if not text:
        return {"enabled": True, "sent": False, "skipped": True, "reason": "empty text"}
    if DEDUP_TEXT and text == _last_text:
        return {"enabled": True, "sent": False, "skipped": True, "reason": "duplicate text", "text": text}

    command = f"{COMMAND_PREFIX}{text}\r\n"
    payload = command.encode(ENCODING, errors="replace")
    with socket.create_connection((HOST, PORT), timeout=TIMEOUT_SECONDS) as sock:
        sock.settimeout(TIMEOUT_SECONDS)
        sock.sendall(payload)
        try:
            response = sock.recv(1024)
        except socket.timeout:
            response = b""
    _last_text = text
    return {
        "enabled": True,
        "sent": True,
        "skipped": False,
        "host": HOST,
        "port": PORT,
        "prefix": COMMAND_PREFIX,
        "text": text,
        "response": response.decode(ENCODING, errors="replace").strip(),
    }


def send_position(position):
    return send_text(render_text(position))
