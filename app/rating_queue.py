from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RatingJobQueue:
    """Small crash-safe queue for asynchronous external rating lookups."""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "ratings"
        self.path = self.root / "queue.json"
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        with self._lock:
            if not self.path.is_file():
                self._jobs = {}
                return
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                jobs = payload.get("jobs") if isinstance(payload, dict) else {}
                self._jobs = {
                    str(key): dict(value)
                    for key, value in (jobs or {}).items()
                    if isinstance(value, dict)
                }
                changed = False
                for job in self._jobs.values():
                    if job.get("status") == "running":
                        job["status"] = "retry"
                        job["nextAttemptAtTs"] = time.time()
                        job["lastError"] = "INTERRUPTED"
                        job["updatedAt"] = _now_iso()
                        job["updatedAtTs"] = time.time()
                        changed = True
                if changed:
                    self._save()
            except (OSError, ValueError, TypeError):
                self._jobs = {}

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 30 * 24 * 60 * 60
        for key, job in list(self._jobs.items()):
            if job.get("status") == "completed" and float(job.get("updatedAtTs") or 0) < cutoff:
                self._jobs.pop(key, None)
        payload = {
            "formatVersion": 1,
            "updatedAt": _now_iso(),
            "jobs": self._jobs,
        }
        temp = self.path.with_name(
            "%s.%s.tmp" % (self.path.name, uuid.uuid4().hex)
        )
        try:
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _key(path: str) -> str:
        return str(path).replace("\\", "/")

    def enqueue(self, path: str, fields: list[str] | set[str]) -> dict | None:
        normalized = self._key(path)
        requested = sorted(set(fields))
        if not normalized or not requested:
            return None
        now = time.time()
        with self._lock:
            job = self._jobs.get(normalized)
            if job is None:
                job = {
                    "path": normalized,
                    "fields": requested,
                    "status": "queued",
                    "attempts": 0,
                    "createdAt": _now_iso(),
                    "nextAttemptAtTs": now,
                    "lastError": None,
                }
                self._jobs[normalized] = job
            else:
                job["fields"] = sorted(set(job.get("fields") or []) | set(requested))
                if job.get("status") in {"completed", "no_match"}:
                    job["attempts"] = 0
                job["status"] = "queued"
                job["nextAttemptAtTs"] = now
                job["lastError"] = None
            job["updatedAt"] = _now_iso()
            job["updatedAtTs"] = now
            self._save()
            return dict(job)

    def retry(self, path: str) -> dict | None:
        normalized = self._key(path)
        with self._lock:
            job = self._jobs.get(normalized)
            if job is None:
                return None
            job["status"] = "queued"
            job["nextAttemptAtTs"] = time.time()
            job["lastError"] = None
            job["attempts"] = 0
            job["updatedAt"] = _now_iso()
            job["updatedAtTs"] = time.time()
            self._save()
            return dict(job)

    def due(self, now: float | None = None) -> dict | None:
        current = time.time() if now is None else now
        with self._lock:
            candidates = [
                job for job in self._jobs.values()
                if job.get("status") in {"queued", "retry"}
                and float(job.get("nextAttemptAtTs") or 0) <= current
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda item: (
                float(item.get("nextAttemptAtTs") or 0),
                str(item.get("path") or ""),
            ))
            job = candidates[0]
            job["status"] = "running"
            job["updatedAt"] = _now_iso()
            job["updatedAtTs"] = current
            self._save()
            return dict(job)

    def finish(
        self,
        path: str,
        status: str,
        *,
        error: str | None = None,
        fields: list[str] | set[str] | None = None,
        next_attempt: float | None = None,
    ) -> dict | None:
        normalized = self._key(path)
        now = time.time()
        with self._lock:
            job = self._jobs.get(normalized)
            if job is None:
                return None
            if fields is not None:
                job["fields"] = sorted(set(fields))
            job["status"] = status
            job["lastError"] = error
            job["updatedAt"] = _now_iso()
            job["updatedAtTs"] = now
            if status == "completed":
                job["completedAt"] = job["updatedAt"]
                job["nextAttemptAtTs"] = None
            else:
                job["attempts"] = int(job.get("attempts") or 0) + 1
                job["nextAttemptAtTs"] = now if next_attempt is None else next_attempt
            self._save()
            return dict(job)

    def snapshot(self, path: str | None = None) -> dict:
        with self._lock:
            jobs = [
                dict(value) for value in self._jobs.values()
                if path is None or value.get("path") == self._key(path)
            ]
        jobs.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        counts: dict[str, int] = {}
        for job in jobs:
            status = str(job.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        return {
            "ok": True,
            "queuePath": str(self.path),
            "counts": counts,
            "jobs": jobs,
        }

