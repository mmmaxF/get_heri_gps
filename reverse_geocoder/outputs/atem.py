import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

from .base import OutputAdapter, OutputResult


ATEM_OUTPUT_URL = os.environ.get("ATEM_OUTPUT_URL", "http://atem-output:8030/api/position")
ATEM_OUTPUT_TIMEOUT_SECONDS = float(os.environ.get("ATEM_OUTPUT_TIMEOUT_SECONDS", "3.0"))
ATEM_DEDUP_TEXT = os.environ.get("ATEM_DEDUP_TEXT", "1").strip().lower() in {"1", "true", "yes", "on"}
ATEM_MIN_UPDATE_SECONDS = float(os.environ.get("ATEM_MIN_UPDATE_SECONDS", "10.0"))
ATEM_TEXT_TEMPLATE = os.environ.get("ATEM_TEXT_TEMPLATE", "{atem_header} {address_label} 上空")
ATEM_TEXT_STATION_ENABLED = os.environ.get(
    "ATEM_TEXT_STATION_ENABLED",
    os.environ.get("ATEM_TEXT_HEADER_ENABLED", "1"),
).strip().lower() in {"1", "true", "yes", "on"}
ATEM_TEXT_STATION_TEMPLATE = os.environ.get("ATEM_TEXT_STATION_TEMPLATE", "YTV")
ATEM_TEXT_TIME_ENABLED = os.environ.get(
    "ATEM_TEXT_TIME_ENABLED",
    os.environ.get("ATEM_TEXT_HEADER_ENABLED", "1"),
).strip().lower() in {"1", "true", "yes", "on"}
ATEM_TEXT_TIME_TEMPLATE = os.environ.get("ATEM_TEXT_TIME_TEMPLATE", "{hhmm}")
ATEM_TEXT_HEADER_ENABLED = os.environ.get("ATEM_TEXT_HEADER_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
ATEM_TEXT_HEADER_TEMPLATE = os.environ.get("ATEM_TEXT_HEADER_TEMPLATE", "{atem_station} {atem_time}")
ATEM_CAPTURE_LINE_ENABLED = os.environ.get("ATEM_CAPTURE_LINE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
ATEM_CAPTURE_STATION_ENABLED = os.environ.get("ATEM_CAPTURE_STATION_ENABLED", str(int(ATEM_TEXT_STATION_ENABLED))).strip().lower() in {"1", "true", "yes", "on"}
ATEM_CAPTURE_TIME_ENABLED = os.environ.get("ATEM_CAPTURE_TIME_ENABLED", str(int(ATEM_TEXT_TIME_ENABLED))).strip().lower() in {"1", "true", "yes", "on"}
ATEM_CAPTURE_HEADER_TEMPLATE = os.environ.get("ATEM_CAPTURE_HEADER_TEMPLATE", "{atem_capture_station} {atem_capture_time}")
ATEM_CAPTURE_LINE_TEMPLATE = os.environ.get("ATEM_CAPTURE_LINE_TEMPLATE", "{atem_capture_header} {capture_address_label} 撮影")
ATEM_CAPTURE_LINE_SHOW_ON_UNKNOWN = os.environ.get("ATEM_CAPTURE_LINE_SHOW_ON_UNKNOWN", "0").strip().lower() in {"1", "true", "yes", "on"}
ATEM_CAPTURE_LINE_UNKNOWN_LABEL = os.environ.get("ATEM_CAPTURE_LINE_UNKNOWN_LABEL", "撮影位置不明")


def safe_format(template, payload):
    class Missing(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(Missing(payload)).strip()


def compact_spaces(text):
    return " ".join(str(text).split())


def time_hm(payload):
    raw = str(payload.get("time", "")).strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M")
        except ValueError:
            pass
    if len(raw) >= 16 and raw[13] == ":":
        return raw[11:16]
    if len(raw) >= 5 and raw[2] == ":":
        return raw[:5]
    return ""


class AtemAdapter(OutputAdapter):
    name = "atem"

    def __init__(self):
        self._last_text = None
        self._last_clear_display = None
        self._last_sent_monotonic = 0.0

    def _render_text(self, position):
        if bool(position.get("clear_display")) or position.get("ok") is False:
            return ""
        data = dict(position)
        data["hhmm"] = time_hm(data)
        data["atem_station"] = compact_spaces(safe_format(ATEM_TEXT_STATION_TEMPLATE, data)) if ATEM_TEXT_STATION_ENABLED else ""
        data["atem_time"] = compact_spaces(safe_format(ATEM_TEXT_TIME_TEMPLATE, data)) if ATEM_TEXT_TIME_ENABLED else ""
        data["atem_header"] = compact_spaces(safe_format(ATEM_TEXT_HEADER_TEMPLATE, data)) if ATEM_TEXT_HEADER_ENABLED else ""
        data["atem_capture_station"] = compact_spaces(safe_format(ATEM_TEXT_STATION_TEMPLATE, data)) if ATEM_CAPTURE_STATION_ENABLED else ""
        data["atem_capture_time"] = compact_spaces(safe_format(ATEM_TEXT_TIME_TEMPLATE, data)) if ATEM_CAPTURE_TIME_ENABLED else ""
        data["atem_capture_header"] = compact_spaces(safe_format(ATEM_CAPTURE_HEADER_TEMPLATE, data))
        if not data.get("capture_address_label") and ATEM_CAPTURE_LINE_SHOW_ON_UNKNOWN:
            data["capture_address_label"] = ATEM_CAPTURE_LINE_UNKNOWN_LABEL
        lines = [compact_spaces(safe_format(ATEM_TEXT_TEMPLATE, data))]
        if ATEM_CAPTURE_LINE_ENABLED and data.get("capture_address_label"):
            lines.append(compact_spaces(safe_format(ATEM_CAPTURE_LINE_TEMPLATE, data)))
        return "\n".join(line for line in lines if line)

    def send(self, position):
        if not ATEM_OUTPUT_URL.strip():
            return OutputResult(
                name=self.name,
                enabled=False,
                sent=False,
                skipped=True,
                detail={"reason": "ATEM_OUTPUT_URL is empty"},
            )
        text = self._render_text(position)
        clear_display = bool(position.get("clear_display")) or position.get("ok") is False
        if ATEM_DEDUP_TEXT and text == self._last_text and clear_display == self._last_clear_display:
            return OutputResult(
                name=self.name,
                enabled=True,
                sent=False,
                skipped=True,
                detail={
                    "reason": "duplicate text",
                    "text": text,
                    "clear_display": clear_display,
                    "url": ATEM_OUTPUT_URL,
                },
            )
        now = time.monotonic()
        elapsed = now - self._last_sent_monotonic
        if self._last_sent_monotonic and elapsed < ATEM_MIN_UPDATE_SECONDS:
            return OutputResult(
                name=self.name,
                enabled=True,
                sent=False,
                skipped=True,
                detail={
                    "reason": "min update interval",
                    "text": text,
                    "clear_display": clear_display,
                    "elapsed_seconds": round(elapsed, 3),
                    "min_update_seconds": ATEM_MIN_UPDATE_SECONDS,
                    "url": ATEM_OUTPUT_URL,
                },
            )
        data = json.dumps(position, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            ATEM_OUTPUT_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=ATEM_OUTPUT_TIMEOUT_SECONDS) as response:
                body = response.read(65536)
            result = json.loads(body.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return OutputResult(
                name=self.name,
                enabled=True,
                sent=False,
                skipped=False,
                error=str(exc),
                detail={"url": ATEM_OUTPUT_URL},
            )
        if not result.get("error"):
            self._last_text = text
            self._last_clear_display = clear_display
            self._last_sent_monotonic = now
        return OutputResult(
            name=self.name,
            enabled=True,
            sent=bool(result.get("sent", False)),
            skipped=bool(result.get("skipped", False)),
            error=str(result.get("error", "")),
            detail={**result, "url": ATEM_OUTPUT_URL},
        )
