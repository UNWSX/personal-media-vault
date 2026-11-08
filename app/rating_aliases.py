from __future__ import annotations


# These overrides are limited to titles whose localized names do not map
# reliably through the public title search. IDs were verified against IMDb
# GraphQL title/type/year before being added.
IMDB_OVERRIDES = (
    ("艾克斯奥特曼剧场版", {"imdbId": "tt5264060"}),
    ("刀剑神域进击篇：无星之夜", {"imdbId": "tt13424422"}),
    ("刀剑神域：序列之争", {"imdbId": "tt5544384"}),
    ("刀剑神域爱丽丝篇异界战争最终季", {"imdbId": "tt2250192", "season": 4, "part": 2}),
    ("刀剑神域爱丽丝篇异界战争", {"imdbId": "tt2250192", "season": 4, "part": 1}),
    ("刀剑神域爱丽丝篇", {"imdbId": "tt2250192", "season": 3}),
    ("刀剑神域2", {"imdbId": "tt2250192", "season": 2}),
    ("鬼灭之刃：剧场版无限列车篇", {"imdbId": "tt11032374"}),
    ("鬼灭之刃：无限列车篇", {"imdbId": "tt9335498", "season": 2, "part": 1}),
    ("鬼灭之刃：游郭篇", {"imdbId": "tt9335498", "season": 2, "part": 2}),
    ("鬼灭之刃：锻刀村篇", {"imdbId": "tt9335498", "season": 3, "part": 2}),
    ("鬼灭之刃：柱训练篇", {"imdbId": "tt9335498", "season": 4}),
    ("灰与幻想的格林姆迦尔OVA", {"imdbId": "tt5341430", "allowTypeMismatch": True}),
    ("灰与幻想的格林姆迦尔", {"imdbId": "tt5341430"}),
    ("命运石之门剧场版", {"imdbId": "tt2967286"}),
    ("赛博朋克：边缘行者 第一季", {"imdbId": "tt12590266", "season": 1}),
    ("赛博朋克：边缘行者 第二季", {"imdbId": "tt12590266", "season": 2}),
    ("无职转生：到了异世界就拿出真本事Part.2", {"imdbId": "tt13293588", "season": 1, "part": 2}),
    ("无职转生Ⅲ", {"imdbId": "tt13293588", "season": 3}),
    ("无职转生 第三季", {"imdbId": "tt13293588", "season": 3}),
    ("无职转生Ⅱ", {"imdbId": "tt13293588", "season": 2, "part": 1, "partPattern": "Part.1"}),
    ("无职转生Ⅱ", {"imdbId": "tt13293588", "season": 2, "part": 2, "partPattern": "Part2"}),
    ("无职转生Ⅱ", {"imdbId": "tt13293588", "season": 2, "part": 2, "partPattern": "Part.2"}),
    ("夏洛特", {"imdbId": "tt4574736"}),
    ("香格里拉·开拓异境～粪作猎手挑战神作～第二季", {"imdbId": "tt28776190", "season": 2}),
    ("灵笼第二季", {"imdbId": "tt10827146", "season": 2}),
    ("雾山五行·犀川幻紫林", {"imdbId": "tt12953630", "season": 2}),
    ("雾山五行", {"imdbId": "tt12953630", "season": 1}),
    ("我的三体之章北海传", {"imdbId": "tt8829226", "season": 3}),
    ("我的三体之罗辑传", {"imdbId": "tt8829226", "season": 2}),
    ("鱿鱼游戏 第一季", {"imdbId": "tt10919420", "season": 1, "allowTypeMismatch": True}),
    ("这个勇者明明超强却过分慎重", {"imdbId": "tt10974198"}),
)

METACRITIC_TITLE_OVERRIDES = {
    "你的名字。": "Your Name.",
    "奥本海默": "Oppenheimer",
    "沙丘": "Dune: Part One",
    "沙丘2": "Dune: Part Two",
    "蜘蛛侠：平行宇宙": "Spider-Man: Into the Spider-Verse",
    "蜘蛛侠：纵横宇宙": "Spider-Man: Across the Spider-Verse",
}


def resolve_imdb_override(title: str) -> dict | None:
    text = str(title or "")
    for marker, candidate in IMDB_OVERRIDES:
        if marker not in text:
            continue
        part_pattern = candidate.get("partPattern")
        if part_pattern and part_pattern.casefold() not in text.casefold():
            continue
        return {
            key: value
            for key, value in candidate.items()
            if key != "partPattern"
        }
    return None


def resolve_metacritic_title(title: str) -> str | None:
    text = str(title or "")
    for marker, translated in METACRITIC_TITLE_OVERRIDES.items():
        if marker in text:
            return translated
    return None
