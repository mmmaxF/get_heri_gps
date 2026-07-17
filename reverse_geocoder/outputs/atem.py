import json
import os
import urllib.error
import urllib.request

from .base import OutputAdapter, OutputResult


ATEM_OUTPUT_URL = os.environ.get("ATEM_OUTPUT_URL", "http://atem-output:8030/api/position")
ATEM_OUTPUT_TIMEOUT_SECONDS = float(os.environ.get("ATEM_OUTPUT_TIMEOUT_SECONDS", "3.0"))


class AtemAdapter(OutputAdapter):
    name = "atem"

    def send(self, position):
        if not ATEM_OUTPUT_URL.strip():
            return OutputResult(
                name=self.name,
                enabled=False,
                sent=False,
                skipped=True,
                detail={"reason": "ATEM_OUTPUT_URL is empty"},
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
        return OutputResult(
            name=self.name,
            enabled=True,
            sent=bool(result.get("sent", False)),
            skipped=bool(result.get("skipped", False)),
            error=str(result.get("error", "")),
            detail={**result, "url": ATEM_OUTPUT_URL},
        )
