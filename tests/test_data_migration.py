from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from data_migration import (  # noqa: E402
    DataMigrationError,
    MigrationOptions,
    apply_migration,
    normalize_data_root,
    scan_migration,
)


def write_file(path: Path, content: str, mtime: int = 1_700_000_000):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.utime(path, (mtime, mtime))


class DataMigrationTests(unittest.TestCase):
    def _paths(self, temp: str):
        root = Path(temp)
        old = root / "旧版" / "数据"
        new = root / "新版" / "数据"
        (old.parent / "程序").mkdir(parents=True)
        (new.parent / "程序").mkdir(parents=True)
        old.mkdir()
        new.mkdir()
        return old, new

    def test_package_root_is_normalized_to_data_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            old, _new = self._paths(temp)
            self.assertEqual(normalize_data_root(old.parent), old.resolve())

    def test_preview_classifies_copy_overwrite_skip_and_transient(self):
        with tempfile.TemporaryDirectory() as temp:
            old, new = self._paths(temp)
            write_file(old / "媒体库" / "movie" / "new.md", "new", 300)
            write_file(old / "媒体库" / "movie" / "same.md", "same", 100)
            write_file(old / "媒体库" / "movie" / "newer.md", "source", 300)
            write_file(old / "媒体库" / "movie" / "older.md", "source", 100)
            write_file(old / ".media-app" / "migration" / "apply.lock", "stale")
            write_file(new / "媒体库" / "movie" / "same.md", "same", 100)
            write_file(new / "媒体库" / "movie" / "newer.md", "old", 100)
            write_file(new / "媒体库" / "movie" / "older.md", "new", 300)
            plan = scan_migration(
                old.parent,
                new.parent,
                MigrationOptions(policy="newer"),
            )
        actions = {
            item["relativePath"]: item["action"]
            for item in plan["items"]
        }
        self.assertEqual(actions["媒体库/movie/new.md"], "copy")
        self.assertEqual(actions["媒体库/movie/same.md"], "skip")
        self.assertEqual(actions["媒体库/movie/newer.md"], "overwrite")
        self.assertEqual(actions["媒体库/movie/older.md"], "skip")
        self.assertNotIn(".media-app/migration/apply.lock", actions)

    def test_apply_backs_up_destination_copies_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as temp:
            old, new = self._paths(temp)
            write_file(old / "媒体库" / "movie" / "新增.md", "media", 300)
            write_file(old / "attachments" / "poster.jpg", "poster", 300)
            write_file(old / ".media-app" / "ratings" / "queue.json", "{}", 300)
            write_file(new / "使用说明.txt", "old package", 100)
            plan = scan_migration(old, new, MigrationOptions(policy="newer"))
            result = apply_migration(plan)
            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(
                (new / "媒体库" / "movie" / "新增.md").read_text(encoding="utf-8"),
                "media",
            )
            self.assertEqual(
                (new / "attachments" / "poster.jpg").read_text(encoding="utf-8"),
                "poster",
            )
            self.assertTrue(
                (new / ".media-app" / "ratings" / "queue.json").is_file()
            )
            self.assertTrue(
                (old / "媒体库" / "movie" / "新增.md").is_file()
            )
            backup = Path(result["backupPath"])
            self.assertTrue(backup.is_dir())
            self.assertTrue((backup / "使用说明.txt").is_file())
            report = Path(result["reportJsonPath"])
            self.assertTrue(report.is_file())
            self.assertEqual(
                json.loads(report.read_text(encoding="utf-8"))["status"],
                "COMPLETED",
            )

    def test_force_policy_overwrites_existing_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            old, new = self._paths(temp)
            write_file(old / "媒体库" / "movie" / "same-name.md", "source", 100)
            write_file(new / "媒体库" / "movie" / "same-name.md", "destination", 300)
            plan = scan_migration(old, new, MigrationOptions(policy="force"))
            self.assertEqual(plan["summary"]["actions"]["overwrite"], 1)
            apply_migration(plan)
            self.assertEqual(
                (new / "媒体库" / "movie" / "same-name.md").read_text(
                    encoding="utf-8"
                ),
                "source",
            )

    def test_same_or_nested_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            old, new = self._paths(temp)
            with self.assertRaises(DataMigrationError):
                scan_migration(old, old)
            with self.assertRaises(DataMigrationError):
                scan_migration(old, old / "nested")

    def test_changed_source_invalidates_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            old, new = self._paths(temp)
            note = old / "媒体库" / "movie" / "changed.md"
            write_file(note, "before", 100)
            plan = scan_migration(old, new, MigrationOptions(policy="newer"))
            write_file(note, "after", 200)
            with self.assertRaisesRegex(DataMigrationError, "数据已变化"):
                apply_migration(plan)

    def test_unrecognized_directory_requires_explicit_full_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            destination = root / "destination"
            write_file(source / "custom-folder" / "file.txt", "data")
            with self.assertRaisesRegex(DataMigrationError, "没有识别"):
                scan_migration(source, destination)
            plan = scan_migration(
                source,
                destination,
                MigrationOptions(include_all=True),
            )
            self.assertEqual(plan["summary"]["files"], 1)


if __name__ == "__main__":
    unittest.main()
