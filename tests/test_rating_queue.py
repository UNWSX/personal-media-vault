from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from rating_queue import RatingJobQueue  # noqa: E402


class RatingJobQueueTests(unittest.TestCase):
    def test_enqueue_persists_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue = RatingJobQueue(root)
            first = queue.enqueue("02-电影阅读/movie/a.md", ["metacriticScore"])
            second = queue.enqueue(
                "02-电影阅读/movie/a.md",
                ["imdbScore", "metacriticScore"],
            )

            reloaded = RatingJobQueue(root)
            state = reloaded.snapshot("02-电影阅读/movie/a.md")

        self.assertEqual(first["path"], second["path"])
        self.assertEqual(state["counts"], {"queued": 1})
        self.assertEqual(
            state["jobs"][0]["fields"],
            ["imdbScore", "metacriticScore"],
        )

    def test_interrupted_running_job_is_requeued(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue = RatingJobQueue(root)
            queue.enqueue("02-电影阅读/movie/a.md", ["metacriticScore"])
            claimed = queue.due(now=time.time() + 1)
            self.assertEqual(claimed["status"], "running")

            reloaded = RatingJobQueue(root)
            state = reloaded.snapshot("02-电影阅读/movie/a.md")

        self.assertEqual(state["jobs"][0]["status"], "retry")
        self.assertEqual(state["jobs"][0]["lastError"], "INTERRUPTED")
        self.assertLessEqual(
            state["jobs"][0]["nextAttemptAtTs"],
            time.time(),
        )

    def test_retry_tracks_attempts_and_next_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue = RatingJobQueue(root)
            queue.enqueue("02-电影阅读/movie/a.md", ["metacriticScore"])
            queue.due(now=time.time() + 1)
            next_time = time.time() + 60
            queue.finish(
                "02-电影阅读/movie/a.md",
                "retry",
                error="NETWORK_ERROR",
                next_attempt=next_time,
            )
            state = queue.snapshot("02-电影阅读/movie/a.md")

        self.assertEqual(state["jobs"][0]["attempts"], 1)
        self.assertEqual(state["jobs"][0]["lastError"], "NETWORK_ERROR")
        self.assertEqual(state["jobs"][0]["nextAttemptAtTs"], next_time)

    def test_completed_job_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            queue = RatingJobQueue(root)
            queue.enqueue("02-电影阅读/movie/a.md", ["metacriticScore"])
            queue.due(now=time.time() + 1)
            queue.finish("02-电影阅读/movie/a.md", "completed")
            payload = json.loads((root / "ratings" / "queue.json").read_text(
                encoding="utf-8"
            ))

        job = next(iter(payload["jobs"].values()))
        self.assertEqual(job["status"], "completed")
        self.assertIn("completedAt", job)


if __name__ == "__main__":
    unittest.main()

