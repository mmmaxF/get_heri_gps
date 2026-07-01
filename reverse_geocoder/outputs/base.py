from dataclasses import dataclass


@dataclass
class OutputResult:
    name: str
    enabled: bool
    sent: bool
    skipped: bool = False
    error: str = ""
    detail: dict | None = None

    def as_dict(self):
        data = {
            "name": self.name,
            "enabled": self.enabled,
            "sent": self.sent,
            "skipped": self.skipped,
        }
        if self.error:
            data["error"] = self.error
        if self.detail:
            data.update(self.detail)
        return data


class OutputAdapter:
    name = "base"

    def send(self, position):
        raise NotImplementedError
