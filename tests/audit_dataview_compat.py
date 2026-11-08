from __future__ import annotations

import argparse
import collections
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from frontmatter_patch import parse_frontmatter  # noqa: E402


def number(value):
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="静态验证 Dataview 看板依赖字段是否仍可等价读取"
    )
    parser.add_argument("vault", type=Path)
    args = parser.parse_args()
    vault = args.vault.resolve()
    media_root = vault / "02-电影阅读"
    files = sorted(media_root.rglob("*.md"))
    failures = []
    missing_tags = []
    types = collections.Counter()
    statuses = collections.Counter()
    scores = []
    for path in files:
        rel = path.relative_to(vault).as_posix()
        raw = path.read_bytes()
        parsed = parse_frontmatter(raw)
        if not parsed.ok:
            failures.append("%s:YAML" % rel)
            continue
        fm = {key: field.value for key, field in parsed.fields.items()}
        media_type = str(fm.get("type") or path.parent.name).lower()
        types[media_type] += 1
        status = fm.get("status")
        if status not in (True, False):
            failures.append("%s:STATUS" % rel)
        else:
            statuses[status] += 1
            progress = fm.get("progressStatus")
            if status is True and progress != "completed":
                failures.append("%s:COMPLETED_MISMATCH" % rel)
            if status is False and progress == "completed":
                failures.append("%s:INCOMPLETE_MISMATCH" % rel)
        score = number(fm.get("score"))
        if score is not None:
            scores.append(score)
        image = str(fm.get("image") or "").strip()
        if image and not image.startswith(("http://", "https://")):
            if not (vault / image).is_file():
                failures.append("%s:IMAGE" % rel)
        tags = fm.get("tags")
        genre = fm.get("genre")
        if not tags and not genre:
            missing_tags.append(rel)
    average = (sum(scores) / Decimal(len(scores))) if scores else Decimal("0")
    result = {
        "files": len(files),
        "types": dict(sorted(types.items())),
        "completed": statuses[True],
        "incomplete": statuses[False],
        "averageScore": float(average.quantize(Decimal("0.1"))),
        "scored": len(scores),
        "missingTags": len(missing_tags),
        "failures": len(failures),
    }
    print(result)
    for failure in failures:
        print("FAILED", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
