from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path


_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
}


def _clean(value):
    if isinstance(value, dict):
        return {
            str(k): "[redacted]" if str(k).lower() in _SENSITIVE_KEYS else _clean(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class ActivityLog:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "logs"
        self._lock = threading.Lock()

    def event(self, event: str, level: str = "info", **fields) -> None:
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
            "level": level,
            "event": event,
            **_clean(fields),
        }
        line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        path = self.root / time.strftime("media-vault-%Y%m%d.jsonl", time.localtime())
        try:
            with self._lock:
                self.root.mkdir(parents=True, exist_ok=True)
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.write(fd, line)
                finally:
                    os.close(fd)
        except OSError as exc:
            print("[log] 无法写入应用日志：%s" % type(exc).__name__, file=sys.stderr)
