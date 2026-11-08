from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import migration  # noqa: E402


class MigrationEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "vault"
        self.media = self.vault / "02-电影阅读" / "movie"
        self.media.mkdir(parents=True)
        (self.vault / ".obsidian").mkdir()
        (self.vault / "attachments").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_note(
        self,
        name: str = "sample.md",
        *,
        score_source: str | None = None,
        extra: str = "",
        callout: str = "未看完",
    ):
        source_line = f"scoreSource: {score_source}\n" if score_source else ""
        path = self.media / name
        path.write_text(
            "---\n"
            "title: 迁移测试\n"
            "type: movie\n"
            "score: 8.2\n"
            + source_line
            + extra
            + "status: false\n"
            "unknownField: keep\n"
            "---\n\n"
            f"> **📝 状态**：{callout}\n\n"
            "## 剧情简介\n"
            "正文与 [[双链]]、![图](attachments/a.png) 必须保留。\n",
            encoding="utf-8",
            newline="\n",
        )
        return path

    def dry_run(self, groups=None):
        return migration.create_dry_run(
            self.vault,
            selected_groups=groups or {"BASE_SCHEMA", "CALLOUT_STATUS"},
        )

    def apply(self, plan_path: Path, *, hooks=None):
        plan = migration.load_plan(plan_path)
        return migration.apply_plan(
            self.vault,
            plan_path,
            confirmation="APPLY %s" % plan["planId"],
            hooks=hooks,
        )

    def test_scan_and_dry_run_are_read_only_and_allocate_stable_uuid(self):
        path = self.write_note()
        before = path.read_bytes()
        before_mtime = path.stat().st_mtime_ns
        scan = migration.scan_media(self.vault)
        self.assertEqual(scan["summary"]["files"], 1)
        self.assertEqual(scan["summary"]["mediaIdMissing"], 1)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, before_mtime)

        result = self.dry_run()
        plan = result["plan"]
        self.assertRegex(plan["planId"], migration._UUID4_RE)
        self.assertRegex(
            plan["files"][0]["proposedMediaId"],
            migration._UUID4_RE,
        )
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, before_mtime)
        self.assertTrue(Path(result["planPath"]).is_file())
        self.assertTrue(Path(result["previewMarkdownPath"]).is_file())

        reloaded = migration.verify_plan(Path(result["planPath"]), self.vault)
        self.assertEqual(
            reloaded["files"][0]["proposedMediaId"],
            plan["files"][0]["proposedMediaId"],
        )

    def test_apply_preserves_body_unknowns_and_writes_compat_fields(self):
        path = self.write_note(score_source="douban")
        plan_result = self.dry_run({"BASE_SCHEMA", "CALLOUT_STATUS", "SCORE_PROMOTION"})
        plan = plan_result["plan"]
        applied = self.apply(Path(plan_result["planPath"]))
        self.assertEqual(applied["planState"], "APPLIED")
        groups = applied["groupResults"][plan["files"][0]["relativePath"]]
        self.assertEqual(groups["BASE_SCHEMA"]["runStatus"], "APPLIED")

        data = path.read_bytes()
        parsed = migration.parse_frontmatter(data)
        self.assertTrue(parsed.ok)
        fm = {key: field.value for key, field in parsed.fields.items()}
        self.assertEqual(fm["schemaVersion"], 2)
        self.assertEqual(fm["mediaId"], plan["files"][0]["proposedMediaId"])
        self.assertEqual(fm["progressStatus"], "planned")
        self.assertEqual(fm["doubanScore"], Decimal("8.2"))
        self.assertEqual(fm["unknownField"], "keep")
        text = data.decode("utf-8")
        self.assertIn("正文与 [[双链]]", text)
        self.assertIn("attachments/a.png", text)
        self.assertIn("> **📝 状态**：未看", text)
        self.assertTrue(
            (self.vault / ".media-app" / "backups" / "migrations" / plan["planId"]).is_dir()
        )

    def test_l2_score_is_review_only_but_base_schema_applies(self):
        path = self.write_note(extra="steamAppId: 123\n")
        result = self.dry_run()
        item = result["plan"]["files"][0]
        self.assertEqual(item["scoreEvidence"]["level"], "L2")
        self.assertFalse(any(
            group["groupType"] == "SCORE_PROMOTION" and group["status"] == "READY"
            for group in item["operationGroups"]
        ))
        self.assertEqual(item["migrationStatus"], "READY_WITH_REVIEW")
        applied = self.apply(Path(result["planPath"]))
        self.assertEqual(applied["planState"], "APPLIED")
        self.assertNotIn("doubanScore:", path.read_text(encoding="utf-8"))

    def test_source_field_type_or_value_mismatch_is_not_l1(self):
        path = self.write_note(score_source="metacritic")
        result = self.dry_run({"BASE_SCHEMA", "SCORE_PROMOTION"})
        evidence = result["plan"]["files"][0]["scoreEvidence"]
        self.assertEqual(evidence["level"], "L2")
        self.assertFalse(any(
            group["groupType"] == "SCORE_PROMOTION" and group["status"] == "READY"
            for group in result["plan"]["files"][0]["operationGroups"]
        ))
        self.apply(Path(result["planPath"]))
        self.assertNotIn("metacriticScore:", path.read_text(encoding="utf-8"))
        self.assertNotIn("doubanScore:", path.read_text(encoding="utf-8"))

    def test_legacy_game_decimal_score_is_scaled_for_metacritic(self):
        records = [{
            "relativePath": "02-电影阅读/game/sample.md",
            "source": "metacritic",
            "value": "6.8",
        }]
        evidence = migration.score_evidence(
            "02-电影阅读/game/sample.md",
            "game",
            {"score": Decimal("6.8")},
            records,
        )
        self.assertEqual(evidence["level"], "L1")
        self.assertEqual(evidence["proposedField"], "metacriticScore")
        self.assertEqual(evidence["proposedValue"], 68)

    def test_plan_checksum_tampering_is_rejected(self):
        self.write_note()
        result = self.dry_run()
        plan_path = Path(result["planPath"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["files"][0]["mediaType"] = "game"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(migration.PlanValidationError):
            migration.verify_plan(plan_path, self.vault)

    def test_path_traversal_is_rejected_even_with_recomputed_checksum(self):
        self.write_note()
        result = self.dry_run()
        plan_path = Path(result["planPath"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["files"][0]["relativePath"] = "02-电影阅读/../outside.md"
        plan["planChecksum"] = migration.plan_checksum(plan)
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(migration.PlanValidationError):
            migration.verify_plan(plan_path, self.vault)

    def test_external_change_before_apply_is_conflicting_and_preserved(self):
        path = self.write_note()
        result = self.dry_run()
        changed = path.read_text(encoding="utf-8").replace(
            "title: 迁移测试", "title: Obsidian 新标题"
        )
        path.write_text(changed, encoding="utf-8", newline="\n")
        applied = self.apply(Path(result["planPath"]))
        self.assertEqual(applied["planState"], "APPLYING")
        self.assertEqual(applied["fileResults"][result["plan"]["files"][0]["relativePath"]], "CONFLICT")
        self.assertIn("Obsidian 新标题", path.read_text(encoding="utf-8"))

    def test_change_after_temp_verification_before_replace_is_preserved(self):
        path = self.write_note()
        result = self.dry_run()

        def external_change(target):
            text = target.read_text(encoding="utf-8").replace(
                "title: 迁移测试", "title: 临时文件后修改"
            )
            target.write_text(text, encoding="utf-8", newline="\n")

        applied = self.apply(
            Path(result["planPath"]),
            hooks={"before_replace": external_change},
        )
        self.assertEqual(applied["planState"], "APPLYING")
        self.assertIn("临时文件后修改", path.read_text(encoding="utf-8"))

    def test_external_change_after_replace_blocks_automatic_rollback(self):
        path = self.write_note()
        result = self.dry_run()

        def external_change(target):
            text = target.read_text(encoding="utf-8").replace(
                "title: 迁移测试", "title: 替换后修改"
            )
            target.write_text(text, encoding="utf-8", newline="\n")

        applied = self.apply(
            Path(result["planPath"]),
            hooks={"after_replace": external_change},
        )
        self.assertEqual(applied["planState"], "APPLYING")
        self.assertEqual(
            applied["fileResults"][result["plan"]["files"][0]["relativePath"]],
            "RESTORE_CONFLICT",
        )
        self.assertIn("替换后修改", path.read_text(encoding="utf-8"))

    def test_snapshot_failure_prevents_apply(self):
        path = self.write_note()
        original = path.read_bytes()
        result = self.dry_run()
        with mock.patch.object(
            migration,
            "_create_snapshot",
            side_effect=migration.MigrationError("snapshot failed"),
        ):
            with self.assertRaises(migration.MigrationError):
                self.apply(Path(result["planPath"]))
        self.assertEqual(path.read_bytes(), original)

    def test_temp_verification_failure_leaves_original_unchanged(self):
        path = self.write_note()
        original = path.read_bytes()
        result = self.dry_run()
        with mock.patch.object(
            migration,
            "_verify_temp",
            side_effect=migration.MigrationError("temp verification failed"),
        ):
            applied = self.apply(Path(result["planPath"]))
        self.assertEqual(applied["planState"], "APPLYING")
        self.assertEqual(
            applied["fileResults"][result["plan"]["files"][0]["relativePath"]],
            "ERROR",
        )
        self.assertEqual(path.read_bytes(), original)

    def test_one_file_error_does_not_stop_later_ready_file(self):
        first = self.write_note("a.md")
        second = self.write_note("b.md")
        result = self.dry_run()
        original = migration._guarded_replace

        def fail_first(target, raw, **kwargs):
            if target.name == "a.md":
                raise migration.MigrationError("synthetic write failure")
            return original(target, raw, **kwargs)

        with mock.patch.object(migration, "_guarded_replace", side_effect=fail_first):
            applied = self.apply(Path(result["planPath"]))
        self.assertEqual(applied["fileResults"][result["plan"]["files"][0]["relativePath"]], "ERROR")
        self.assertEqual(applied["fileResults"][result["plan"]["files"][1]["relativePath"]], "APPLIED")
        self.assertNotIn("schemaVersion:", first.read_text(encoding="utf-8"))
        self.assertIn("schemaVersion: 2", second.read_text(encoding="utf-8"))

        resumed = migration.apply_plan(
            self.vault,
            Path(result["planPath"]),
            confirmation="APPLY %s" % result["plan"]["planId"],
            resume=True,
        )
        self.assertEqual(resumed["planState"], "APPLIED")
        self.assertIn("schemaVersion: 2", first.read_text(encoding="utf-8"))

    def test_duplicate_apply_is_rejected(self):
        self.write_note()
        result = self.dry_run()
        self.apply(Path(result["planPath"]))
        with self.assertRaises(migration.MigrationError):
            self.apply(Path(result["planPath"]))

    def test_exact_confirmation_and_global_lock_are_required(self):
        self.write_note()
        result = self.dry_run()
        with self.assertRaises(migration.MigrationError):
            migration.apply_plan(
                self.vault,
                Path(result["planPath"]),
                confirmation="yes",
            )
        lock = self.vault / ".media-app" / "migration" / "apply.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text('{"planId":"other"}', encoding="utf-8")
        with self.assertRaises(migration.MigrationError):
            self.apply(Path(result["planPath"]))
        self.assertTrue(lock.exists())

    def test_rollback_restores_original_and_conflict_preserves_user_edit(self):
        path = self.write_note()
        original = path.read_bytes()
        result = self.dry_run()
        plan = result["plan"]
        self.apply(Path(result["planPath"]))
        rolled = migration.rollback_plan(
            self.vault,
            Path(result["planPath"]),
            confirmation="ROLLBACK %s" % plan["planId"],
        )
        self.assertEqual(rolled["planState"], "ROLLED_BACK")
        self.assertEqual(path.read_bytes(), original)

        second = migration.create_dry_run(self.vault)
        plan2 = second["plan"]
        self.apply(Path(second["planPath"]))
        edited = path.read_text(encoding="utf-8").replace(
            "title: 迁移测试", "title: 用户迁移后编辑"
        )
        path.write_text(edited, encoding="utf-8", newline="\n")
        rolled2 = migration.rollback_plan(
            self.vault,
            Path(second["planPath"]),
            confirmation="ROLLBACK %s" % plan2["planId"],
        )
        self.assertEqual(rolled2["planState"], "ROLLED_BACK_WITH_CONFLICTS")
        self.assertIn("用户迁移后编辑", path.read_text(encoding="utf-8"))

    def test_future_schema_and_duplicate_ids_are_skipped(self):
        shared = "c3b9e4a8-7d21-4f8a-9e1b-3f2d1e0a9b8c"
        for index in (1, 2):
            (self.media / f"duplicate-{index}.md").write_text(
                "---\n"
                "schemaVersion: 2\n"
                f'mediaId: "{shared}"\n'
                f"title: duplicate-{index}\n"
                "type: movie\n"
                "status: false\n"
                "---\n\n正文\n",
                encoding="utf-8",
            )
        (self.media / "future.md").write_text(
            "---\n"
            "schemaVersion: 3\n"
            f'mediaId: "{shared[:-1]}d"\n'
            "title: future\n"
            "type: movie\n"
            "status: false\n"
            "---\n\n正文\n",
            encoding="utf-8",
        )
        result = self.dry_run()
        self.assertTrue(all(
            item["migrationStatus"] == "SKIPPED"
            for item in result["plan"]["files"]
        ))

    def test_cli_scan_server_entrypoint_is_read_only(self):
        path = self.write_note()
        original = path.read_bytes()
        completed = subprocess.run(
            [
                sys.executable,
                str(APP_DIR / "server.py"),
                "--migrate-vault",
                str(self.vault),
                "--scan",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["summary"]["files"], 1)
        self.assertEqual(path.read_bytes(), original)

    def test_cli_dry_run_apply_and_rollback_flow(self):
        path = self.write_note()
        original = path.read_bytes()
        dry = subprocess.run(
            [
                sys.executable,
                str(APP_DIR / "server.py"),
                "--migrate-vault", str(self.vault),
                "--dry-run",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(dry.returncode, 0, dry.stderr)
        dry_result = json.loads(dry.stdout)
        plan_path = dry_result["planPath"]
        apply_result = subprocess.run(
            [
                sys.executable,
                str(APP_DIR / "server.py"),
                "--migrate-vault", str(self.vault),
                "--apply",
                "--plan", plan_path,
                "--confirm", "APPLY %s" % dry_result["planId"],
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(apply_result.returncode, 0, apply_result.stderr)
        self.assertIn("schemaVersion: 2", path.read_text(encoding="utf-8"))
        rollback_result = subprocess.run(
            [
                sys.executable,
                str(APP_DIR / "server.py"),
                "--migrate-vault", str(self.vault),
                "--rollback",
                "--plan", plan_path,
                "--confirm", "ROLLBACK %s" % dry_result["planId"],
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(rollback_result.returncode, 0, rollback_result.stderr)
        self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
