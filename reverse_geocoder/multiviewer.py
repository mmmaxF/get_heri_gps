#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TCP command sender for the multiviewer title API."""

import os
import socket
from datetime import datetime


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
DATETIME_ENABLED = env_bool("MULTIVIEWER_DATETIME_ENABLED", True)
DATETIME_COMMAND_PREFIX = os.environ.get("MULTIVIEWER_DATETIME_COMMAND_PREFIX", "STW010V011")
DATETIME_FORMAT = os.environ.get("MULTIVIEWER_DATETIME_FORMAT", "%m/%d %H:%M:%S")
TEXT_TEMPLATE = os.environ.get("MULTIVIEWER_TEXT_TEMPLATE", "{address_label}")
ENCODING = os.environ.get("MULTIVIEWER_ENCODING", "shift_jis")
TIMEOUT_SECONDS = env_float("MULTIVIEWER_TIMEOUT_SECONDS", 2.0)
SEND_ON_NOT_FOUND = env_bool("MULTIVIEWER_SEND_ON_NOT_FOUND", False)
DEDUP_TEXT = env_bool("MULTIVIEWER_DEDUP_TEXT", True)

_last_text_by_prefix = {}


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


def send_text(text, allow_empty=False, command_prefix=None):
    command_prefix = command_prefix or COMMAND_PREFIX
    if not ENABLED:
        return {"enabled": False, "sent": False, "skipped": True, "reason": "disabled"}
    if not HOST:
        return {"enabled": True, "sent": False, "skipped": True, "reason": "host not configured"}
    if not text and not allow_empty:
        return {"enabled": True, "sent": False, "skipped": True, "reason": "empty text"}
    if DEDUP_TEXT and text == _last_text_by_prefix.get(command_prefix):
        return {"enabled": True, "sent": False, "skipped": True, "reason": "duplicate text", "text": text}

    command = f"{command_prefix}{text}\r\n"
    payload = command.encode(ENCODING, errors="replace")
    with socket.create_connection((HOST, PORT), timeout=TIMEOUT_SECONDS) as sock:
        sock.settimeout(TIMEOUT_SECONDS)
        sock.sendall(payload)
        try:
            response = sock.recv(1024)
        except socket.timeout:
            response = b""
    _last_text_by_prefix[command_prefix] = text
    return {
        "enabled": True,
        "sent": True,
        "skipped": False,
        "host": HOST,
        "port": PORT,
        "prefix": command_prefix,
        "text": text,
        "response": response.decode(ENCODING, errors="replace").strip(),
    }


def render_datetime(position):
    value = str(position.get("time", "")).strip()
    if not value:
        return ""
    try:
        parsed = datetime.strptime(value, "%Y/%m/%d %H:%M:%S")
    except ValueError:
        return value[-16:]
    return parsed.strftime(DATETIME_FORMAT)


def send_position(position):
    if position.get("clear_display"):
        location = send_text("", allow_empty=True)
        timestamp = (
            send_text("", allow_empty=True, command_prefix=DATETIME_COMMAND_PREFIX)
            if DATETIME_ENABLED
            else {"enabled": True, "sent": False, "skipped": True, "reason": "datetime disabled"}
        )
    else:
        location = send_text(render_text(position))
        timestamp = (
            send_text(render_datetime(position), command_prefix=DATETIME_COMMAND_PREFIX)
            if DATETIME_ENABLED
            else {"enabled": True, "sent": False, "skipped": True, "reason": "datetime disabled"}
        )
    result = {
        "enabled": bool(location.get("enabled") or timestamp.get("enabled")),
        "sent": bool(location.get("sent") or timestamp.get("sent")),
        "skipped": bool(location.get("skipped") and timestamp.get("skipped")),
        "text": location.get("text", ""),
        "location": location,
        "datetime": timestamp,
    }
    errors = [
        item.get("error")
        for item in (location, timestamp)
        if item.get("error")
    ]
    if errors:
        result["error"] = "; ".join(errors)
    return result
