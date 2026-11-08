from __future__ import annotations

import codecs
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from frontmatter_patch import REMOVE_FIELD, parse_frontmatter, patch_frontmatter  # noqa: E402
from storage_safety import atomic_write_note, content_revision  # noqa: E402


def note(frontmatter: str, body: str = "正文 [[双链]]\n", eol: str = "\n") -> bytes:
    frontmatter = frontmatter.replace("\r\n", "\n").replace("\r", "\n")
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    return (
        "---" + eol
        + frontmatter.replace("\n", eol)
        + eol
        + "---" + eol
        + eol
        + body.replace("\n", eol)
    ).encode("utf-8")


class FrontmatterPatchTests(unittest.TestCase):
    def test_parses_supported_subset(self):
        raw = note(
            "title: '电影: #1'\n"
            "score: 8.7\n"
            "status: false\n"
            "empty:\n"
            "nothing: null\n"
            "tags:\n"
            "  - 动作\n"
            "  - 'Sci Fi'\n"
            "platform: [\"PC\", 'PS5']\n"
            "comment: |-\n"
            "  第一行\n"
            "  第二行"
        )
        parsed = parse_frontmatter(raw)
        self.assertTrue(parsed.ok, parsed.diagnostics)
        self.assertEqual(parsed.fields["title"].value, "电影: #1")
        self.assertEqual(parsed.fields["score"].value, Decimal("8.7"))
        self.assertFalse(parsed.fields["status"].value)
        self.assertIsNone(parsed.fields["empty"].value)
        self.assertIsNone(parsed.fields["nothing"].value)
        self.assertEqual(parsed.fields["tags"].value, ["动作", "Sci Fi"])
        self.assertEqual(parsed.fields["platform"].value, ["PC", "PS5"])
        self.assertEqual(parsed.fields["comment"].value, "第一行\n第二行")

    def test_duplicate_key_is_unsafe(self):
        parsed = parse_frontmatter(note("title: A\ntitle: B\nstatus: false"))
        self.assertFalse(parsed.ok)
        self.assertIn("DUPLICATE_KEY", [item.code for item in parsed.diagnostics])
        result = patch_frontmatter(note("title: A\ntitle: B\nstatus: false"), {"status": True})
        self.assertFalse(result.ok)
        self.assertEqual(result.data, result.data)

    def test_unsupported_structures_are_rejected(self):
        cases = {
            "anchor": "value: &anchor text",
            "alias": "value: *anchor",
            "tag": "value: !include other",
            "folded": "description: >\n  folded",
            "nested": "meta:\n  child: value",
            "flow_map": "meta: {a: b}",
        }
        for name, fm in cases.items():
            with self.subTest(name=name):
                parsed = parse_frontmatter(note(fm + "\nstatus: false"))
                self.assertFalse(parsed.ok)

    def test_list_mapping_item_and_nested_inline_list_are_rejected(self):
        cases = [
            "items:\n  - key: value",
            "items: [[nested]]",
        ]
        for fm in cases:
            with self.subTest(frontmatter=fm):
                parsed = parse_frontmatter(note(fm))
                self.assertFalse(parsed.ok)

    def test_update_preserves_bom_crlf_comments_and_body_bytes(self):
        body = "> [!info]\n> text\r\n正文 [![图](attachments/a.png)]\r\n"
        raw = codecs.BOM_UTF8 + note(
            "# keep\n"
            "title: 示例 # title comment\n"
            "status: false\n"
            "unknown: keep",
            body=body.rstrip("\r\n"),
            eol="\r\n",
        )
        result = patch_frontmatter(raw, {"status": True, "comment": "第一行\n第二行"})
        self.assertTrue(result.ok, result.diagnostics)
        self.assertTrue(result.data.startswith(codecs.BOM_UTF8))
        self.assertIn(b"\r\n", result.data)
        self.assertNotIn(b"\n", result.data.replace(b"\r\n", b""))
        self.assertIn("title: 示例 # title comment".encode(), result.data)
        self.assertIn(b"# keep\r\n", result.data)
        self.assertIn(b"unknown: keep\r\n", result.data)
        self.assertIn("正文 [![图](attachments/a.png)]".encode(), result.data)

    def test_none_empty_string_and_remove_are_distinct(self):
        raw = note("title: 示例\nfinishTime: 2026-01-01\nstatus: false")
        result = patch_frontmatter(
            raw,
            {
                "finishTime": None,
                "comment": "",
                "status": REMOVE_FIELD,
            },
        )
        self.assertTrue(result.ok, result.diagnostics)
        self.assertIn(b"finishTime: null\n", result.data)
        self.assertIn(b'comment: ""\n', result.data)
        self.assertNotIn(b"status:", result.data)
        self.assertEqual(parse_frontmatter(result.data).fields["finishTime"].value, None)
        self.assertEqual(parse_frontmatter(result.data).fields["comment"].value, "")

    def test_new_fields_are_inserted_once_before_closing_marker(self):
        raw = note("title: 示例\nstatus: false", body="正文")
        result = patch_frontmatter(
            raw,
            {
                "schemaVersion": 2,
                "mediaId": "c3b9e4a8-7d21-4f8a-9e1b-3f2d1e0a9b8c",
                "progressStatus": "planned",
            },
        )
        self.assertTrue(result.ok, result.diagnostics)
        text = result.data.decode()
        self.assertEqual(text.count("schemaVersion:"), 1)
        self.assertLess(text.index("schemaVersion:"), text.rindex("\n---"))
        self.assertIn('mediaId: "c3b9e4a8-7d21-4f8a-9e1b-3f2d1e0a9b8c"', text)
        self.assertIn("progressStatus: planned", text)

    def test_list_update_and_remove_fields(self):
        raw = note("title: 示例\ntags:\n  - 动作\n  - 科幻\nstatus: false")
        result = patch_frontmatter(
            raw,
            {"tags": ["动作", "冒险"], "status": REMOVE_FIELD},
        )
        self.assertTrue(result.ok, result.diagnostics)
        parsed = parse_frontmatter(result.data)
        self.assertEqual(parsed.fields["tags"].value, ["动作", "冒险"])
        self.assertNotIn("status", parsed.fields)

    def test_inline_list_with_comment_cannot_be_rebuilt(self):
        raw = note("tags: [动作, 科幻] # keep-list-comment\nstatus: false")
        result = patch_frontmatter(raw, {"tags": ["动作"], "status": True})
        self.assertFalse(result.ok)
        self.assertIn("COMMENT_PRESERVATION_UNSAFE", [item.code for item in result.diagnostics])
        self.assertEqual(result.data, raw)

    def test_list_comments_survive_unrelated_field_update(self):
        raw = note(
            "tags:\n"
            "  - 动作 # keep-action\n"
            "  - 科幻\n"
            "status: false"
        )
        result = patch_frontmatter(raw, {"status": True})
        self.assertTrue(result.ok, result.diagnostics)
        self.assertIn(b"  - \xe5\x8a\xa8\xe4\xbd\x9c # keep-action\n", result.data)

    def test_block_content_survives_unrelated_field_update(self):
        raw = note(
            "comment: |-\n"
            "  # 这是正文中的井号\n"
            "  status: not-a-key\n"
            "status: false"
        )
        result = patch_frontmatter(raw, {"status": True})
        self.assertTrue(result.ok, result.diagnostics)
        self.assertIn(b"  # \xe8\xbf\x99\xe6\x98\xaf\xe6\xad\xa3\xe6\x96\x87", result.data)
        self.assertIn(b"  status: not-a-key\n", result.data)

    def test_scalar_comment_blocks_multiline_conversion(self):
        raw = note("comment: old # keep-me\nstatus: false")
        result = patch_frontmatter(raw, {"comment": "line one\nline two"})
        self.assertFalse(result.ok)
        self.assertIn("COMMENT_PRESERVATION_UNSAFE", [item.code for item in result.diagnostics])
        self.assertEqual(result.data, raw)

    def test_numeric_string_round_trips_as_string(self):
        raw = note("title: 示例")
        result = patch_frontmatter(raw, {"isbn": "9787536455382"})
        self.assertTrue(result.ok, result.diagnostics)
        self.assertIn(b'isbn: "9787536455382"', result.data)
        self.assertEqual(parse_frontmatter(result.data).fields["isbn"].value, "9787536455382")

    def test_block_scalar_update(self):
        raw = note("comment: |-\n  旧内容\nstatus: false")
        result = patch_frontmatter(raw, {"comment": "新第一行\n新第二行"})
        self.assertTrue(result.ok, result.diagnostics)
        self.assertIn("comment: |-", result.data.decode())
        self.assertEqual(parse_frontmatter(result.data).fields["comment"].value, "新第一行\n新第二行")

    def test_invalid_utf8_never_patches(self):
        raw = b"---\ntitle: bad\n---\n\xff\xfe"
        result = patch_frontmatter(raw, {"status": True})
        self.assertFalse(result.ok)
        self.assertEqual(result.data, raw)
        self.assertIn("INVALID_UTF8", [item.code for item in result.diagnostics])

    def test_mixed_newlines_are_rejected(self):
        raw = b"---\r\ntitle: test\n---\r\n\nbody"
        result = patch_frontmatter(raw, {"status": True})
        self.assertFalse(result.ok)
        self.assertIn("UNSUPPORTED_NEWLINE", [item.code for item in result.diagnostics])
        self.assertEqual(result.data, raw)

    def test_missing_frontmatter_never_patches(self):
        raw = "正文".encode()
        result = patch_frontmatter(raw, {"status": True})
        self.assertFalse(result.ok)
        self.assertEqual(result.data, raw)
        self.assertIn("NO_FRONTMATTER", [item.code for item in result.diagnostics])

    def test_one_unsafe_update_aborts_all_changes(self):
        raw = note("title: 示例\ntags: [A, B] # comment\nstatus: false")
        result = patch_frontmatter(raw, {"status": True, "tags": ["A"]})
        self.assertFalse(result.ok)
        self.assertEqual(result.data, raw)

    def test_remove_absent_field_is_noop(self):
        raw = note("title: 示例")
        result = patch_frontmatter(raw, {"mediaId": REMOVE_FIELD})
        self.assertTrue(result.ok)
        self.assertFalse(result.changed)
        self.assertEqual(result.data, raw)

    def test_unmodified_fields_and_body_are_byte_identical(self):
        raw = codecs.BOM_UTF8 + note(
            "# 保留注释\n"
            "title: 中文 and English\n"
            'custom: "[[双链]]: # value"\n'
            "status: false\n"
            "unknown:\n"
            "  - keep\n"
            "  - 保留",
            body="## 正文\r\n![图](attachments/a.png)\r\n末尾无换行",
            eol="\r\n",
        )
        before = parse_frontmatter(raw)
        result = patch_frontmatter(raw, {"status": True})
        self.assertTrue(result.ok, result.diagnostics)
        after = parse_frontmatter(result.data)
        self.assertEqual(
            "".join(before.lines[before.close_index:]),
            "".join(after.lines[after.close_index:]),
        )
        self.assertIn(b"# \xe4\xbf\x9d\xe7\x95\x99\xe6\xb3\xa8\xe9\x87\x8a\r\n", result.data)
        self.assertIn(
            b'custom: "[[\xe5\x8f\x8c\xe9\x93\xbe]]: # value"\r\n',
            result.data,
        )

    def test_patch_result_is_compatible_with_stage1_atomic_writer(self):
        raw = note("title: 示例\nstatus: false", body="正文保持\n")
        result = patch_frontmatter(raw, {"status": True})
        self.assertTrue(result.ok, result.diagnostics)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "note.md"
            target.write_bytes(raw)
            backup = atomic_write_note(
                target,
                result.data,
                root / ".media-app" / "backups",
                "note.md",
                lambda: self.assertTrue(parse_frontmatter(target.read_bytes()).ok),
                expected_revision=content_revision(raw),
            )
            self.assertIsNotNone(backup)
            self.assertTrue(backup.is_file())
            self.assertTrue(parse_frontmatter(target.read_bytes()).fields["status"].value)
            self.assertTrue(target.read_bytes().endswith("---\n\n正文保持\n".encode()))


if __name__ == "__main__":
    unittest.main()
