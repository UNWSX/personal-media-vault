from __future__ import annotations

import argparse
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import server  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="只读检查影音库笔记能否被当前版本解析")
    parser.add_argument("vault", type=Path)
    args = parser.parse_args()

    vault = args.vault.resolve()
    library = server.Library(vault)
    items = library.load()
    all_notes = sorted(library.media_root.rglob("*.md")) if library.media_root.exists() else []
    parsed = {item["path"] for item in items}
    unparsed = [p for p in all_notes if p.relative_to(vault).as_posix() not in parsed]
    missing_images = []
    for item in items:
        image = item.get("image") or ""
        if image and not image.startswith(("http://", "https://")):
            if not (vault / image).is_file():
                missing_images.append((item["path"], image))

    print(f"notes={len(all_notes)} parsed={len(items)} unparsed={len(unparsed)}")
    for path in unparsed:
        print(f"UNPARSED {path.relative_to(vault).as_posix()}")
    print(f"missing_images={len(missing_images)}")
    for note, image in missing_images:
        print(f"MISSING_IMAGE {note} -> {image}")
    return 1 if unparsed or missing_images else 0


if __name__ == "__main__":
    raise SystemExit(main())
