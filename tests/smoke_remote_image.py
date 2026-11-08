from __future__ import annotations

import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import server  # noqa: E402
from safe_network import fetch_remote_bytes  # noqa: E402


URL = (
    "https://img3.doubanio.com/view/photo/s_ratio_poster/"
    "public/p2936502228.jpg"
)


def main() -> int:
    raw, ctype = fetch_remote_bytes(
        URL,
        headers={
            "User-Agent": server.DOUBAN_UA,
            "Referer": "https://movie.douban.com/",
        },
        max_bytes=12 * 1024 * 1024,
    )
    detected = server.sniff_image(raw)
    print("bytes=%d header=%s detected=%s" % (len(raw), ctype, detected))
    return 0 if detected and detected.startswith("image/") else 1


if __name__ == "__main__":
    raise SystemExit(main())
