import os

from .base import OutputResult
from .multiviewer import MultiviewerAdapter


ADAPTERS = {
    "multiviewer": MultiviewerAdapter,
}


class OutputManager:
    def __init__(self, enabled_names=None):
        if enabled_names is None:
            enabled_names = os.environ.get("OUTPUT_ADAPTERS", "multiviewer")
        self.adapters = []
        self.unknown = []
        for name in [item.strip() for item in enabled_names.split(",") if item.strip()]:
            adapter_cls = ADAPTERS.get(name)
            if adapter_cls is None:
                self.unknown.append(name)
                continue
            self.adapters.append(adapter_cls())

    def names(self):
        return [adapter.name for adapter in self.adapters]

    def send_all(self, position):
        results = []
        for adapter in self.adapters:
            result = adapter.send(position)
            if isinstance(result, OutputResult):
                results.append(result.as_dict())
            else:
                results.append(dict(result))
        return results
