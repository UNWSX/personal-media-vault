from __future__ import annotations

import re
from typing import Any

from media_compat import PROGRESS_BY_TYPE, default_progress_status


CALLOUT_STATUS = {
    "movie": {"planned": "未看", "completed": "已看"},
    "teleplay": {
        "planned": "想看",
        "watching": "在看",
        "paused": "搁置",
        "completed": "已看完全集",
    },
    "game": {
        "backlog": "待玩",
        "playing": "正在游玩",
        "paused": "搁置",
        "completed": "已通关",
        "abandoned": "已弃坑",
    },
    "book": {
        "planned": "想读",
        "reading": "在读",
        "paused": "搁置",
        "completed": "已读完",
    },
}

CALLOUT_MARKER = {
    "game": "🕹️",
    "movie": "📝",
    "teleplay": "📝",
    "book": "📝",
}

CALLOUT_KNOWN_VALUES = {
    value
    for values in CALLOUT_STATUS.values()
    for value in values.values()
} | {
    "未看完", "已看完",
    "未通关", "已通关",
    "未读过", "未读完", "已读完",
    "未听完", "已听完",
}


def find_status_callout_lines(text: str) -> list[tuple[int, int, str, str]]:
    pattern = re.compile(r"^> \*\*(📝|🕹️) 状态\*\*：[ \t]*(.*)$")
    offsets = []
    cursor = 0
    fence_char = ""
    fence_len = 0
    for raw in text.splitlines(keepends=True):
        content = raw.rstrip("\r\n")
        fence = re.match(r"^[ ]{0,3}(`{3,}|~{3,})", content)
        if fence_char:
            if fence and fence.group(1).startswith(fence_char * fence_len):
                fence_char = ""
                fence_len = 0
        elif fence:
            fence_char = fence.group(1)[0]
            fence_len = len(fence.group(1))
        else:
            match = pattern.match(content)
            if match:
                offsets.append(
                    (cursor, cursor + len(content), match.group(1), match.group(2))
                )
        cursor += len(raw)
    return offsets


def status_callout_value(note_type: str, progress: str | None) -> str | None:
    return CALLOUT_STATUS.get(note_type, {}).get(progress)


def sync_status_callout(
    text: str,
    note_type: str,
    progress: str | None,
) -> tuple[str, str | None]:
    word = status_callout_value(note_type, progress)
    if word is None:
        return text, "CALLOUT_STATUS_UNSUPPORTED"

    matches = find_status_callout_lines(text)
    if len(matches) != 1:
        return text, "CALLOUT_NOT_FOUND" if not matches else "CALLOUT_AMBIGUOUS"

    start, end, marker, value = matches[0]
    expected_marker = CALLOUT_MARKER.get(note_type, "📝")
    if marker != expected_marker:
        return text, "CALLOUT_TYPE_MISMATCH"
    current = value.strip()
    if current and current not in CALLOUT_KNOWN_VALUES:
        return text, "CALLOUT_VALUE_UNRECOGNIZED"

    line = text[start:end]
    match = re.match(r"^(> \*\*(?:📝|🕹️) 状态\*\*：[ \t]*)(.*)$", line)
    if not match:
        return text, "CALLOUT_VALUE_UNRECOGNIZED"
    return text[:start] + match.group(1) + word + text[end:], None


def sync_callout_result(
    text: str,
    updates: dict[str, Any],
    note_type: str = "",
) -> tuple[str, str | None]:
    warning = None
    if "progressStatus" in updates or "status" in updates:
        progress = updates.get("progressStatus")
        done = False
        if progress is None and "status" in updates:
            done = bool(updates["status"])
            progress = default_progress_status(note_type, done)
        word = CALLOUT_STATUS.get(note_type, {}).get(progress)
        if word is None and note_type not in PROGRESS_BY_TYPE:
            word = "已看完" if done else "未看完"
        if word is None:
            warning = "CALLOUT_STATUS_UNSUPPORTED"
        else:
            matches = find_status_callout_lines(text)
            if len(matches) != 1:
                warning = "CALLOUT_NOT_FOUND" if not matches else "CALLOUT_AMBIGUOUS"
            else:
                start, end, marker, value = matches[0]
                expected_marker = CALLOUT_MARKER.get(note_type, "📝")
                current = value.strip()
                if marker != expected_marker:
                    warning = "CALLOUT_TYPE_MISMATCH"
                elif current and current not in CALLOUT_KNOWN_VALUES:
                    warning = "CALLOUT_VALUE_UNRECOGNIZED"
                else:
                    line = text[start:end]
                    match = re.match(
                        r"^(> \*\*(?:📝|🕹️) 状态\*\*：[ \t]*)(.*)$",
                        line,
                    )
                    if not match:
                        warning = "CALLOUT_VALUE_UNRECOGNIZED"
                    else:
                        text = text[:start] + match.group(1) + word + text[end:]

    if "score" in updates and updates["score"] is not None:
        value = updates["score"]
        shown = (
            "%d" % round(float(value))
            if note_type == "game"
            else "%g" % round(float(value), 1)
        )
        text = re.sub(
            r"(> \*\*⭐ 评分\*\*：).*",
            lambda match: match.group(1) + shown,
            text,
        )

    if "title" in updates and updates["title"]:
        text = re.sub(
            r"(> \*\*🎮 名称\*\*：).*",
            lambda match: match.group(1) + "《" + str(updates["title"]) + "》",
            text,
        )

    if "image" in updates and updates["image"]:
        image = str(updates["image"])
        text = re.sub(
            r"(!\[[^\]]*\]\()[^)]*(\))",
            lambda match: match.group(1) + image + match.group(2),
            text,
            count=1,
        )

    return text, warning


def sync_callout(text: str, updates: dict[str, Any], note_type: str = "") -> str:
    return sync_callout_result(text, updates, note_type)[0]
