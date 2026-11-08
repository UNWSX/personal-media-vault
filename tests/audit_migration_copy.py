from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from frontmatter_patch import parse_frontmatter  # noqa: E402
from media_compat import normalize_identity  # noqa: E402
from storage_safety import decode_note  # noqa: E402


STATUS_CALLOUT_LINE = re.compile(
    r"(?m)^> \*\*(?:📝|🕹️) 状态\*\*：.*(?:\r?\n|$)"
)


def normalized_body(text: str) -> str:
    parsed = parse_frontmatter(text.encode("utf-8"))
    if not parsed.ok:
        return text
    match = re.match(r"(?s)^.*?\n---\r?\n", text)
    body = text[match.end():] if match else text
    return STATUS_CALLOUT_LINE.sub("", body)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="比较迁移前 Vault 与迁移后副本，检查正文和原字段完整性"
    )
    parser.add_argument("original", type=Path)
    parser.add_argument("migrated", type=Path)
    args = parser.parse_args()
    original_vault = args.original.resolve()
    migrated_vault = args.migrated.resolve()
    original_files = sorted(
        (original_vault / "02-电影阅读").rglob("*.md")
    )
    migrated_files = sorted(
        (migrated_vault / "02-电影阅读").rglob("*.md")
    )
    original_rels = [
        path.relative_to(original_vault).as_posix() for path in original_files
    ]
    migrated_rels = [
        path.relative_to(migrated_vault).as_posix() for path in migrated_files
    ]
    failures = []
    ids = {}
    if original_rels != migrated_rels:
        failures.append("FILE_SET_CHANGED")
    for rel in sorted(set(original_rels) & set(migrated_rels)):
        before = (original_vault / rel).read_bytes()
        after = (migrated_vault / rel).read_bytes()
        before_parsed = parse_frontmatter(before)
        after_parsed = parse_frontmatter(after)
        if not after_parsed.ok:
            failures.append("%s:NEW_YAML_INVALID" % rel)
            continue
        before_fm = {
            key: field.value for key, field in before_parsed.fields.items()
        }
        after_fm = {
            key: field.value for key, field in after_parsed.fields.items()
        }
        for key, value in before_fm.items():
            if after_fm.get(key) != value:
                failures.append("%s:CHANGED_%s" % (rel, key))
        identity = normalize_identity(
            after_fm.get("schemaVersion"),
            after_fm.get("mediaId"),
        )
        if after_fm.get("schemaVersion") != 2:
            failures.append("%s:SCHEMA" % rel)
        if not identity["mediaIdValid"]:
            failures.append("%s:MEDIA_ID" % rel)
        else:
            key = identity["mediaId"].lower()
            if key in ids:
                failures.append("%s:DUPLICATE_MEDIA_ID" % rel)
            ids[key] = rel
        before_text, before_nl, before_bom = decode_note(before)
        after_text, after_nl, after_bom = decode_note(after)
        if before_nl != after_nl:
            failures.append("%s:NEWLINE" % rel)
        if before_bom != after_bom:
            failures.append("%s:BOM" % rel)
        if before_text.endswith("\n") != after_text.endswith("\n"):
            failures.append("%s:FINAL_NEWLINE" % rel)
        if normalized_body(before_text) != normalized_body(after_text):
            failures.append("%s:BODY_OR_LINK_CHANGED" % rel)
    print(
        "files=%d ids=%d failures=%d"
        % (len(migrated_files), len(ids), len(failures))
    )
    for failure in failures:
        print("FAILED", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
