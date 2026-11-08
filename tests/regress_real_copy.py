from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import server  # noqa: E402


STATUS_LINE = re.compile(r"(?m)^> \*\*(?:📝|🕹️) 状态\*\*：.*(?:\r?\n|$)")
COMPAT_FIELD = re.compile(
    r"(?m)(?:^|\r?\n)(?:status|progressStatus):[^\r\n]*(?=\r?\n|$)"
)


def comparable(text: str) -> str:
    fm = server.FM_RE.match(text)
    if not fm:
        return text
    frontmatter = COMPAT_FIELD.sub("", fm.group(1))
    body = STATUS_LINE.sub("", text[fm.end():])
    return "---\n" + frontmatter + "\n---\n" + body


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(
        description="在真实 Vault 的临时副本上重写全部媒体笔记并检查正文完整性"
    )
    parser.add_argument("vault", type=Path)
    args = parser.parse_args()
    source = args.vault.resolve()

    with tempfile.TemporaryDirectory(prefix="media-vault-regression-") as temp:
        copy = Path(temp) / "vault"
        shutil.copytree(source / "02-电影阅读", copy / "02-电影阅读")
        (copy / ".obsidian").mkdir()
        (copy / "attachments").mkdir()

        before = {
            path.relative_to(copy).as_posix(): path.read_bytes()
            for path in (copy / "02-电影阅读").rglob("*.md")
        }
        library = server.Library(copy)
        items = library.load()
        self_check_failures = []
        for item in items:
            rel = item["path"]
            original = before[rel]
            library.update(rel, {
                "status": item["status"],
                "revision": item["revision"],
            })
            updated = (copy / rel).read_bytes()
            old_text, old_nl, old_bom = server.decode_note(original)
            new_text, new_nl, new_bom = server.decode_note(updated)
            checks = {
                "newline": old_nl == new_nl,
                "bom": old_bom == new_bom,
                "finalNewline": old_text.endswith("\n") == new_text.endswith("\n"),
                "protectedContent": comparable(old_text) == comparable(new_text),
            }
            failed = [name for name, ok in checks.items() if not ok]
            if failed:
                self_check_failures.append((rel, failed))

        backup_count = sum(
            1 for _ in (copy / ".media-app" / "backups" / "notes").rglob("*.md")
        )
        print(
            "notes=%d updated=%d backups=%d failures=%d"
            % (len(before), len(items), backup_count, len(self_check_failures))
        )
        for rel, failed in self_check_failures:
            print("FAILED %s %s" % (rel, ",".join(failed)))
        return 1 if len(items) != len(before) or self_check_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
