from __future__ import annotations

import sys
import tempfile
import unittest
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import server  # noqa: E402
import storage_safety  # noqa: E402
import callout_sync  # noqa: E402
from activity_log import ActivityLog  # noqa: E402
from storage_safety import NoteWriteError, RevisionConflict  # noqa: E402


class PhaseOneSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        self.media = self.vault / "02-电影阅读" / "movie"
        self.media.mkdir(parents=True)
        (self.vault / "attachments").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_library_canonicalizes_windows_style_alias_root(self):
        alias = self.vault / "child" / ".."
        library = server.Library(alias)
        self.assertEqual(library.vault, self.vault.resolve())
        self.assertEqual(library.media_root, self.vault.resolve() / "02-电影阅读")

    def write_note(self, name="sample.md", *, newline="\n", final_newline=True):
        text = (
            "---\n"
            "title: 示例\n"
            "type: movie\n"
            "score: 8.2\n"
            "status: false\n"
            "---\n\n"
            "> [!info]+ 基本信息\n"
            "> **📝 状态**：未看完\n"
            "> **⭐ 评分**：8.2\n\n"
            "## 剧情简介\n"
            "正文必须原样保留。\n"
        )
        if not final_newline:
            text = text.rstrip("\n")
        if newline != "\n":
            text = text.replace("\n", newline)
        path = self.media / name
        path.write_bytes(text.encode("utf-8"))
        return path

    def test_update_preserves_crlf_ending_body_and_creates_backup(self):
        path = self.write_note("crlf.md", newline="\r\n", final_newline=False)
        original = path.read_bytes()
        lib = server.Library(self.vault)
        lib.load()

        rec = path.relative_to(self.vault).as_posix()
        updated = lib.update(rec, {"status": True, "comment": "第一行\n第二行"})

        self.assertTrue(updated["status"])
        self.assertEqual(updated["comment"], "第一行\n第二行")
        content = path.read_bytes()
        self.assertIn(b"\r\n", content)
        self.assertFalse(content.endswith(b"\r\n"))
        self.assertNotIn(b"\n", content.replace(b"\r\n", b""))
        self.assertIn("正文必须原样保留。".encode(), content)
        self.assertIn("> **📝 状态**：已看".encode("utf-8"), content)

        backups = list((self.vault / ".media-app" / "backups" / "notes").rglob("*.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_unrelated_yaml_comments_quotes_lists_and_body_are_preserved(self):
        text = (
            "---\n"
            "# keep this comment\n"
            "title: '电影：测试 #1'\n"
            "type: movie\n"
            "desc: \"包含: 冒号、引号 \\\" 和 # 号\"\n"
            "tags:\n"
            "  - 剧情\n"
            "  - 科幻\n"
            "unknown:\n"
            "  - 保留值\n"
            "status: false\n"
            "---\n\n"
            "> [!info]+ 基本信息\n"
            "> **📝 状态**：未看完\n"
            "> ![封面](attachments/keep.png)\n\n"
            "## 剧情简介\n"
            "正文 [[内部链接]] 与图片 ![x](attachments/body.png)\n"
        )
        path = self.media / "complex.md"
        path.write_text(text, encoding="utf-8")
        lib = server.Library(self.vault)
        rec = lib.load()[0]
        lib.update(rec["path"], {"status": True, "revision": rec["revision"]})
        result = path.read_text(encoding="utf-8")
        self.assertIn("# keep this comment", result)
        self.assertIn("title: '电影：测试 #1'", result)
        self.assertIn('desc: "包含: 冒号、引号 \\" 和 # 号"', result)
        self.assertIn("  - 科幻\nunknown:", result)
        self.assertIn("正文 [[内部链接]] 与图片 ![x](attachments/body.png)", result)
        self.assertIn("> ![封面](attachments/keep.png)", result)
        self.assertIn("> **📝 状态**：已看", result)

    def test_multiple_callout_status_markers_are_not_modified(self):
        path = self.media / "ambiguous-callout.md"
        path.write_text(
            "---\n"
            "title: 歧义\n"
            "type: movie\n"
            "status: false\n"
            "---\n\n"
            "> **📝 状态**：未看\n"
            "> **📝 状态**：未看\n",
            encoding="utf-8",
        )
        lib = server.Library(self.vault)
        item = lib.snapshot()[0]
        updated = lib.update(item["path"], {"progressStatus": "completed"})
        self.assertEqual(updated["progressStatus"], "completed")
        self.assertEqual(updated["calloutWarning"], "CALLOUT_AMBIGUOUS")
        content = path.read_text(encoding="utf-8")
        self.assertEqual(content.count("> **📝 状态**：未看"), 2)

    def test_code_fenced_callout_is_ignored(self):
        path = self.media / "fenced-callout.md"
        path.write_text(
            "---\n"
            "title: 代码块\n"
            "type: movie\n"
            "status: false\n"
            "---\n\n"
            "```markdown\n"
            "> **📝 状态**：未看完\n"
            "```\n"
            "> **📝 状态**：未看完\n",
            encoding="utf-8",
        )
        lib = server.Library(self.vault)
        item = lib.snapshot()[0]
        updated = lib.update(item["path"], {"progressStatus": "completed"})
        self.assertNotIn("calloutWarning", updated)
        content = path.read_text(encoding="utf-8")
        self.assertIn(
            "```markdown\n> **📝 状态**：未看完\n```",
            content,
        )
        self.assertEqual(content.count("> **📝 状态**：已看"), 1)

    def test_callout_with_extra_explanation_is_not_modified(self):
        path = self.media / "annotated-callout.md"
        path.write_text(
            "---\n"
            "title: 注释\n"
            "type: movie\n"
            "status: false\n"
            "---\n\n"
            "> **📝 状态**：未看完（第二刷）\n",
            encoding="utf-8",
        )
        lib = server.Library(self.vault)
        item = lib.snapshot()[0]
        updated = lib.update(item["path"], {"progressStatus": "completed"})
        self.assertEqual(updated["calloutWarning"], "CALLOUT_VALUE_UNRECOGNIZED")
        self.assertIn("> **📝 状态**：未看完（第二刷）", path.read_text(encoding="utf-8"))

    def test_empty_callout_is_safely_filled(self):
        path = self.media / "empty-callout.md"
        path.write_text(
            "---\n"
            "title: 空状态\n"
            "type: movie\n"
            "status: false\n"
            "---\n\n"
            "> **📝 状态**：  \n\n"
            "正文保持不变。\n",
            encoding="utf-8",
        )
        lib = server.Library(self.vault)
        item = lib.snapshot()[0]
        updated = lib.update(item["path"], {"progressStatus": "completed"})
        self.assertNotIn("calloutWarning", updated)
        content = path.read_text(encoding="utf-8")
        self.assertIn("> **📝 状态**：  已看\n\n正文保持不变。\n", content)

    def test_shared_callout_sync_fills_empty_value(self):
        text = "> **🕹️ 状态**：  \n\n正文保持不变。\n"
        updated, warning = callout_sync.sync_status_callout(
            text,
            "game",
            "completed",
        )
        self.assertIsNone(warning)
        self.assertEqual(
            updated,
            "> **🕹️ 状态**：  已通关\n\n正文保持不变。\n",
        )

    def test_zero_or_mismatched_callouts_are_not_modified(self):
        cases = {
            "none.md": ("正文没有状态标记\n", "CALLOUT_NOT_FOUND"),
            "wrong-marker.md": ("> **🕹️ 状态**：未看完\n", "CALLOUT_TYPE_MISMATCH"),
        }
        for name, (body, warning) in cases.items():
            with self.subTest(name=name):
                path = self.media / name
                path.write_text(
                    "---\n"
                    f"title: {name}\n"
                    "type: movie\n"
                    "status: false\n"
                    "---\n\n" + body,
                    encoding="utf-8",
                )
                lib = server.Library(self.vault)
                item = next(row for row in lib.snapshot() if row["path"].endswith(name))
                updated = lib.update(item["path"], {"progressStatus": "completed"})
                self.assertEqual(updated["calloutWarning"], warning)
                self.assertIn(body, path.read_text(encoding="utf-8"))
                path.unlink()

    def test_music_keeps_legacy_callout_mapping(self):
        music_dir = self.vault / "02-电影阅读" / "music"
        music_dir.mkdir()
        path = music_dir / "album.md"
        path.write_text(
            "---\n"
            "title: 专辑\n"
            "type: music\n"
            "status: false\n"
            "---\n\n"
            "> **📝 状态**：未看完\n",
            encoding="utf-8",
        )
        lib = server.Library(self.vault)
        item = lib.snapshot()[0]
        updated = lib.update(item["path"], {"status": True})
        self.assertTrue(updated["status"])
        self.assertEqual(updated["statusGroup"], "completed")
        self.assertIn("> **📝 状态**：已看完", path.read_text(encoding="utf-8"))

    def test_future_schema_note_is_read_only(self):
        path = self.media / "future.md"
        path.write_text(
            "---\n"
            "schemaVersion: 3\n"
            "mediaId: \"c3b9e4a8-7d21-4f8a-9e1b-3f2d1e0a9b8c\"\n"
            "title: 未来\n"
            "type: movie\n"
            "status: false\n"
            "---\n\n正文\n",
            encoding="utf-8",
        )
        lib = server.Library(self.vault)
        item = lib.snapshot()[0]
        self.assertTrue(item["schemaFuture"])
        with self.assertRaises(ValueError):
            lib.update(item["path"], {"status": True})
        self.assertIn("schemaVersion: 3", path.read_text(encoding="utf-8"))

    def test_duplicate_media_ids_are_marked_without_changing_files(self):
        media_id = "c3b9e4a8-7d21-4f8a-9e1b-3f2d1e0a9b8c"
        for index, value in ((1, media_id), (2, media_id.upper())):
            (self.media / f"same-id-{index}.md").write_text(
                "---\n"
                "schemaVersion: 2\n"
                f'mediaId: "{value}"\n'
                f"title: ID {index}\n"
                "type: movie\n"
                "status: false\n"
                "---\n\n正文\n",
                encoding="utf-8",
            )
        lib = server.Library(self.vault)
        items = lib.snapshot()
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item.get("mediaIdDuplicate") for item in items))
        self.assertTrue(all(
            "DUPLICATE_MEDIA_ID" in item.get("identityWarnings", []) for item in items
        ))
        for item in items:
            with self.assertRaises(ValueError):
                lib.update(item["path"], {"status": True})

    def test_invalid_identity_values_are_read_only(self):
        cases = {
            "invalid-id.md": 'schemaVersion: 2\nmediaId: "not-a-uuid"\n',
            "quoted-schema.md": 'schemaVersion: "2"\nmediaId: "c3b9e4a8-7d21-4f8a-9e1b-3f2d1e0a9b8c"\n',
            "fractional-schema.md": "schemaVersion: 2.5\nmediaId: \"c3b9e4a8-7d21-4f8a-9e1b-3f2d1e0a9b8c\"\n",
        }
        for name, identity in cases.items():
            path = self.media / name
            path.write_text(
                "---\n"
                + identity
                + f"title: {name}\n"
                "type: movie\n"
                "status: false\n"
                "---\n\n正文\n",
                encoding="utf-8",
            )
        lib = server.Library(self.vault)
        items = lib.snapshot()
        self.assertEqual(len(items), len(cases))
        for item in items:
            self.assertFalse(item["writeAllowed"])
            with self.assertRaises(ValueError):
                lib.update(item["path"], {"status": True})

    def test_external_edit_causes_revision_conflict(self):
        path = self.write_note()
        lib = server.Library(self.vault)
        rec = lib.load()[0]

        external = path.read_text(encoding="utf-8").replace("title: 示例", "title: Obsidian 修改")
        path.write_text(external, encoding="utf-8")
        with self.assertRaises(RevisionConflict):
            lib.update(rec["path"], {"status": True, "revision": rec["revision"]})
        self.assertIn("title: Obsidian 修改", path.read_text(encoding="utf-8"))

    def test_same_revision_can_only_be_written_once(self):
        path = self.write_note()
        lib = server.Library(self.vault)
        rec = lib.load()[0]

        def update(comment):
            try:
                lib.update(rec["path"], {
                    "comment": comment,
                    "revision": rec["revision"],
                })
                return "ok"
            except RevisionConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ["并发 A", "并发 B"]))
        self.assertCountEqual(results, ["ok", "conflict"])
        self.assertIn("comment:", path.read_text(encoding="utf-8"))

    def test_change_during_temp_write_is_not_overwritten(self):
        path = self.write_note()
        lib = server.Library(self.vault)
        rec = lib.load()[0]
        external = path.read_text(encoding="utf-8").replace("title: 示例", "title: 后到修改")

        original_temp = storage_safety._write_temp

        def write_temp_then_external(target, raw):
            temp = original_temp(target, raw)
            path.write_text(external, encoding="utf-8")
            return temp

        with mock.patch("storage_safety._write_temp", side_effect=write_temp_then_external):
            with self.assertRaises(RevisionConflict):
                lib.update(rec["path"], {"status": True, "revision": rec["revision"]})
        self.assertIn("title: 后到修改", path.read_text(encoding="utf-8"))

    def test_change_after_backup_before_replace_is_not_overwritten(self):
        path = self.write_note()
        raw = path.read_bytes()
        external = path.read_text(encoding="utf-8").replace("title: 示例", "title: 备份后修改")
        replacement = raw.replace(b"status: false", b"status: true")

        real_backup = storage_safety.backup_bytes

        def backup_then_external(*args, **kwargs):
            result = real_backup(*args, **kwargs)
            path.write_text(external, encoding="utf-8")
            return result

        with mock.patch("storage_safety.backup_bytes", side_effect=backup_then_external):
            with self.assertRaises(storage_safety.ConcurrentModificationError):
                storage_safety.atomic_write_note(
                    path,
                    replacement,
                    self.vault / ".media-app" / "backups" / "notes",
                    "02-电影阅读/movie/sample.md",
                    lambda: None,
                    expected_revision=storage_safety.content_revision(raw),
                )
        self.assertIn("title: 备份后修改", path.read_text(encoding="utf-8"))

    def test_external_change_after_replace_is_preserved_during_failed_verification(self):
        path = self.write_note()
        raw = path.read_bytes()
        replacement = raw.replace(b"status: false", b"status: true")
        external = path.read_text(encoding="utf-8").replace(
            "title: 示例", "title: 替换后外部修改"
        )

        def verify_after_external_change():
            path.write_text(external, encoding="utf-8")
            raise NoteWriteError("synthetic verification failure")

        with self.assertRaises(storage_safety.ConcurrentModificationError):
            storage_safety.atomic_write_note(
                path,
                replacement,
                self.vault / ".media-app" / "backups" / "notes",
                "02-电影阅读/movie/sample.md",
                verify_after_external_change,
                expected_revision=storage_safety.content_revision(raw),
            )
        self.assertIn("title: 替换后外部修改", path.read_text(encoding="utf-8"))
        self.assertNotIn("title: 示例", path.read_text(encoding="utf-8"))

    def test_invalid_utf8_is_never_rewritten(self):
        path = self.media / "bad.md"
        raw = (
            b"---\ntitle: bad\ntype: movie\nstatus: false\n---\n"
            b"\xff\xfe"
        )
        path.write_bytes(raw)
        lib = server.Library(self.vault)
        self.assertEqual(lib.load(), [])
        with self.assertRaises(NoteWriteError):
            lib.update(path.relative_to(self.vault).as_posix(), {"status": True})
        self.assertEqual(path.read_bytes(), raw)

    def test_mutations_are_limited_to_media_markdown(self):
        outside = self.vault / "01-工作学习" / "private.md"
        outside.parent.mkdir()
        outside.write_text("---\ntitle: private\n---\n", encoding="utf-8")
        lib = server.Library(self.vault)

        with self.assertRaises(ValueError):
            lib.delete("01-工作学习/private.md", None)
        with self.assertRaises(ValueError):
            lib.update("01-工作学习/private.md", {"status": True})
        self.assertTrue(outside.exists())

        attachment = self.vault / "attachments" / "not-a-note.md"
        attachment.write_text("keep", encoding="utf-8")
        with self.assertRaises(ValueError):
            lib.delete("attachments/not-a-note.md", None)
        self.assertTrue(attachment.exists())

    def test_delete_recycles_unshared_local_cover(self):
        cover = self.vault / "attachments" / "unique-cover.png"
        cover.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2000)
        note = self.media / "with-cover.md"
        note.write_text(
            "---\n"
            "title: With Cover\n"
            "type: movie\n"
            "image: attachments/unique-cover.png\n"
            "status: false\n"
            "---\n",
            encoding="utf-8",
        )
        lib = server.Library(self.vault)
        result = lib.delete_with_details(
            "02-电影阅读/movie/with-cover.md",
            storage_safety.content_revision(note.read_bytes()),
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["attachmentMoved"])
        self.assertFalse(result["attachmentShared"])
        self.assertFalse(note.exists())
        self.assertFalse(cover.exists())
        self.assertTrue(any((self.vault / ".media-app" / "trash").glob("*with-cover.md")))
        self.assertTrue(any((self.vault / ".media-app" / "trash").glob("*_attachment_unique-cover.png")))

    def test_delete_keeps_shared_local_cover(self):
        cover = self.vault / "attachments" / "shared-cover.png"
        cover.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2000)
        for name in ("first.md", "second.md"):
            (self.media / name).write_text(
                "---\n"
                f"title: {name}\n"
                "type: movie\n"
                "image: attachments/shared-cover.png\n"
                "status: false\n"
                "---\n",
                encoding="utf-8",
            )
        lib = server.Library(self.vault)
        result = lib.delete_with_details("02-电影阅读/movie/first.md")
        self.assertTrue(result["ok"])
        self.assertTrue(result["attachmentShared"])
        self.assertFalse(result["attachmentMoved"])
        self.assertTrue(cover.exists())
        self.assertTrue((self.media / "second.md").exists())

    def test_dotdot_is_rejected_in_notes_and_attachments(self):
        note = self.write_note()
        lib = server.Library(self.vault)
        bad_note_paths = ["02-电影阅读/movie/../movie/sample.md"]
        self.assertTrue(note.exists())
        for rel in bad_note_paths:
            with self.subTest(path=rel):
                with self.assertRaises(ValueError):
                    lib.update(rel, {"status": True})
                with self.assertRaises(ValueError):
                    lib.delete(rel, None)

        bad_url = "attachments/../attachments/keep.png"
        with self.assertRaises(ValueError):
            lib.resolve_attachment(bad_url)

    def test_equivalent_paths_share_the_same_lock_and_revision_guard(self):
        path = self.write_note("locked.md")
        lib = server.Library(self.vault)
        rec = path.relative_to(self.vault).as_posix()
        canonical = "02-电影阅读/movie/locked.md"
        alternate_slash = "02-电影阅读//movie/./locked.md"
        alternate_backslash = r"02-电影阅读\movie\locked.md"
        self.assertIs(lib._path_lock(canonical), lib._path_lock(alternate_slash))
        self.assertIs(lib._path_lock(canonical), lib._path_lock(alternate_backslash))

        item = lib.load()[0]

        def update(rel, value):
            try:
                lib.update(rel, {
                    "comment": value,
                    "revision": item["revision"],
                })
                return "ok"
            except RevisionConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda args: update(*args),
                [
                    (canonical, "规范路径"),
                    (alternate_slash, "等价路径"),
                ],
            ))
        self.assertCountEqual(results, ["ok", "conflict"])
        self.assertEqual(rec, canonical)

    @unittest.skipUnless(os.name == "nt", "Windows path semantics")
    def test_windows_case_aliases_share_lock_and_revision_guard(self):
        path = self.write_note("case-alias.md")
        lib = server.Library(self.vault)
        item = lib.load()[0]
        canonical = "02-电影阅读/movie/case-alias.md"
        case_alias = "02-电影阅读/Movie/Case-Alias.md"
        self.assertTrue(path.is_file())
        self.assertIs(lib._path_lock(canonical), lib._path_lock(case_alias))

        def update(rel, value):
            try:
                lib.update(rel, {
                    "comment": value,
                    "revision": item["revision"],
                })
                return "ok"
            except RevisionConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda args: update(*args),
                [
                    (canonical, "Windows 规范大小写"),
                    (case_alias, "Windows 大小写别名"),
                ],
            ))
        self.assertCountEqual(results, ["ok", "conflict"])

    def test_snapshot_detects_external_change(self):
        path = self.write_note()
        lib = server.Library(self.vault)
        self.assertEqual(lib.snapshot()[0]["title"], "示例")

        path.write_text(
            path.read_text(encoding="utf-8").replace("title: 示例", "title: 外部改名"),
            encoding="utf-8",
        )
        self.assertEqual(lib.snapshot()[0]["title"], "外部改名")

    def test_attachment_repair_writes_a_new_version(self):
        original = b"\x89PNG\r\n\x1a\n" + (b"a" * 2000)
        replacement = b"\x89PNG\r\n\x1a\n" + (b"b" * 2000)

        first = server._write_attachment(self.vault, "封面", ".png", original)
        second = server._write_attachment(self.vault, "封面", ".png", replacement)

        self.assertNotEqual(first, second)
        self.assertEqual((self.vault / first).read_bytes(), original)
        self.assertEqual((self.vault / second).read_bytes(), replacement)

    def test_verification_failure_restores_original(self):
        path = self.write_note()
        original = path.read_bytes()
        lib = server.Library(self.vault)
        lib.load()

        with mock.patch.object(server, "read_note", return_value=None):
            with self.assertRaises(NoteWriteError):
                lib.update(path.relative_to(self.vault).as_posix(), {"comment": "不应落盘"})

        self.assertEqual(path.read_bytes(), original)
        backups = list((self.vault / ".media-app" / "backups" / "notes").rglob("*.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)

    def test_backup_failure_stops_write(self):
        path = self.write_note()
        original = path.read_bytes()
        lib = server.Library(self.vault)
        lib.load()
        with mock.patch("storage_safety.backup_bytes", side_effect=OSError("disk full")):
            with self.assertRaises(NoteWriteError):
                lib.update(path.relative_to(self.vault).as_posix(), {"comment": "不应写入"})
        self.assertEqual(path.read_bytes(), original)

    def test_duplicate_fields_and_unclosed_quotes_are_not_written(self):
        samples = [
            "title: one\ntitle: two\n",
            'title: "unclosed\ntype: movie\n',
        ]
        for index, fm in enumerate(samples):
            path = self.media / ("invalid-%d.md" % index)
            raw = ("---\n" + fm + "---\n\n正文\n").encode("utf-8")
            path.write_bytes(raw)
            lib = server.Library(self.vault)
            self.assertEqual(lib.load(), [])
            self.assertEqual(len(lib.load_warnings), index + 1)
            with self.assertRaises(ValueError):
                lib.update(path.relative_to(self.vault).as_posix(), {"status": True})
            self.assertEqual(path.read_bytes(), raw)

    def test_snapshot_backup_preserves_tree_and_manifest(self):
        note = self.write_note("snapshot.md")
        lib = server.Library(self.vault)
        lib.load()
        target = lib.create_snapshot_backup("phase1-test")
        copied = target / "02-电影阅读" / "movie" / "snapshot.md"
        self.assertEqual(copied.read_bytes(), note.read_bytes())
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["count"], 1)
        self.assertEqual(manifest["files"][0]["path"], "02-电影阅读/movie/snapshot.md")

    def test_log_redacts_sensitive_values(self):
        log = ActivityLog(self.vault / ".media-app")
        log.event("test", cookie="secret-cookie", user="safe")
        files = list((self.vault / ".media-app" / "logs").glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertNotIn("secret-cookie", text)
        self.assertIn("[redacted]", text)

    def test_colocated_temporary_app_uses_its_own_auxiliary_directory(self):
        app_dir = self.vault / ".media-app-stage1"
        app_dir.mkdir()
        with mock.patch.object(server, "APP_DIR", app_dir):
            selected = server.data_directory_for_vault(self.vault)
        self.assertEqual(selected, app_dir)
        self.assertNotEqual(selected, self.vault / ".media-app")

    def test_standalone_auxiliary_data_stays_with_media_data(self):
        standalone = self.vault / "standalone-data"
        standalone.mkdir()
        self.assertEqual(
            server.data_directory_for_vault(standalone),
            standalone / ".media-app",
        )

    def test_http_servers_use_exclusive_ports(self):
        first, first_port = server.bind_http_server(18100)
        second, second_port = server.bind_http_server(18100)
        try:
            self.assertNotEqual(first_port, second_port)
        finally:
            first.server_close()
            second.server_close()


if __name__ == "__main__":
    unittest.main()
