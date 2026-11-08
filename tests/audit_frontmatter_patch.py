from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

from frontmatter_patch import parse_frontmatter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只读验证受限 Frontmatter 引擎能否解析媒体笔记"
    )
    parser.add_argument("vault", type=Path)
    args = parser.parse_args()

    media_root = args.vault.resolve() / "02-电影阅读"
    files = sorted(media_root.rglob("*.md"))
    failures = []
    kind_counts = collections.Counter()
    for path in files:
        result = parse_frontmatter(path.read_bytes())
        if result.ok:
            kind_counts.update(field.kind for field in result.fields.values())
        else:
            failures.append((path.relative_to(args.vault), result.diagnostics))

    print(
        "files=%d parsed=%d failed=%d"
        % (len(files), len(files) - len(failures), len(failures))
    )
    print("fieldKinds=%s" % dict(sorted(kind_counts.items())))
    for path, diagnostics in failures:
        for diagnostic in diagnostics:
            print(
                "UNSAFE %s line=%s code=%s message=%s"
                % (
                    path.as_posix(),
                    diagnostic.line,
                    diagnostic.code,
                    diagnostic.message,
                )
            )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
