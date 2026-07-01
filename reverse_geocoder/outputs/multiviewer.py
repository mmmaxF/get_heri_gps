from .base import OutputAdapter, OutputResult
from multiviewer import HOST, PORT, send_position


class MultiviewerAdapter(OutputAdapter):
    name = "multiviewer"

    def send(self, position):
        try:
            result = send_position(position)
        except Exception as exc:
            return OutputResult(
                name=self.name,
                enabled=True,
                sent=False,
                skipped=False,
                error=str(exc),
                detail={"host": HOST, "port": PORT},
            )
        return OutputResult(
            name=self.name,
            enabled=bool(result.get("enabled", True)),
            sent=bool(result.get("sent", False)),
            skipped=bool(result.get("skipped", False)),
            error=str(result.get("error", "")),
            detail=result,
        )
