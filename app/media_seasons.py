from __future__ import annotations

import re


_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_ROMAN_DIGITS = {
    "Ⅰ": 1,
    "Ⅱ": 2,
    "Ⅲ": 3,
    "Ⅳ": 4,
    "Ⅴ": 5,
    "Ⅵ": 6,
    "Ⅶ": 7,
    "Ⅷ": 8,
    "Ⅸ": 9,
    "Ⅹ": 10,
}


def chinese_number(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "百" in text:
        left, _, right = text.partition("百")
        hundreds = _CN_DIGITS.get(left, 1) if left else 1
        rest = chinese_number(right) if right else 0
        return hundreds * 100 + (rest or 0)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        ones = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(text) == 1:
        return _CN_DIGITS.get(text)
    return None


def extract_season(title: str) -> dict:
    """Return the season number and the work title before the season marker."""
    text = str(title or "").strip()
    part_match = re.search(r"(?i)part\s*\.?\s*(\d{1,2})|第\s*(\d{1,2})\s*部分", text)
    part = None
    if part_match:
        part = int(part_match.group(1) or part_match.group(2))
    patterns = (
        re.compile(r"(?i)(?:\bseason\s*)(\d{1,3})\b"),
        re.compile(r"(?i)(?:^|[\s._-])s(\d{1,2})(?:$|[\s._-])"),
        re.compile(r"第\s*([零〇一二三四五六七八九十百两\d]+)\s*[季期]"),
        re.compile(r"(?<![A-Za-z])([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])(?=[\s：:._-]|$)"),
        re.compile(r"(?<=[\u4e00-\u9fff])([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ])"),
        re.compile(r"(?<=[\u4e00-\u9fff])([2-9])(?=$|[\s：:._-])"),
    )
    matches = []
    for pattern in patterns:
        matches.extend(pattern.finditer(text))
    if not matches:
        return {"title": text, "season": None, "part": part}
    match = sorted(matches, key=lambda item: item.start())[0]
    raw_season = match.group(1)
    season = _ROMAN_DIGITS.get(raw_season) or chinese_number(raw_season)
    if season is None or season < 1:
        return {"title": text, "season": None, "part": part}
    base = text[:match.start()].strip(" \t-_.:：")
    return {"title": base or text, "season": season, "part": part}
