#!/usr/bin/env python3
"""Repair season scores and fill anime IMDb/RT/Bangumi ratings safely."""
from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import server as server_module
from batch_rotten_tomatoes import (
    _dedupe,
    _douban_title_candidate,
    _frontmatter_values,
    _md_cell,
    _read_json,
    _write_json,
)
from media_seasons import extract_season
from rating_aliases import resolve_imdb_override, resolve_metacritic_title
from server import (
    _rt_request_json,
    Library,
    lookup_anime_aliases,
    lookup_bangumi,
    lookup_external_ids,
    lookup_imdb,
    lookup_imdb_by_id,
    lookup_metacritic,
    lookup_rotten_tomatoes,
    lookup_rotten_tomatoes_season,
    lookup_wikidata,
    lookup_wikidata_by_id,
)
from storage_safety import RevisionConflict

PLAN_FORMAT_VERSION = 1
SUPPORTED_TYPES = {"movie", "teleplay"}
ANNUAL_FIELDS = (
    "imdbScore",
    "rottenTomatoesScore",
    "metacriticScore",
    "bangumiScore",
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _is_anime(item: dict) -> bool:
    tags = {str(tag).casefold() for tag in item.get("tags", [])}
    return "动画" in tags or "动漫" in tags or "animation" in tags


def _score_value(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _matches_from_attempts(attempts: list[dict], score_field: str) -> dict:
    matched = []
    ambiguous = []
    errors = []
    for attempt in attempts:
        result = attempt["result"]
        if result.get("status") == "matched":
            matched.append(attempt)
        elif result.get("status") == "ambiguous":
            ambiguous.append(attempt)
        elif result.get("status") == "error":
            errors.append(attempt)
    distinct = {
        (
            (attempt["result"].get("candidate") or {}).get("url")
            or (attempt["result"].get("candidate") or {}).get("imdbId")
            or (attempt["result"].get("candidate") or {}).get("bangumiId"),
            attempt["result"].get("score"),
        )
        for attempt in matched
    }
    if len(distinct) == 1 and matched:
        return {"status": "matched", **matched[0]["result"], "query": matched[0]["query"]}
    if len(distinct) > 1 or ambiguous:
        return {
            "status": "ambiguous",
            "score": None,
            "reason": "MULTIPLE_OR_AMBIGUOUS_CANDIDATES",
            "attempts": attempts,
        }
    if errors and len(errors) == len(attempts):
        return {
            "status": "error",
            "score": None,
            "reason": "LOOKUP_FAILED",
            "attempts": attempts,
        }
    return {
        "status": "no_match",
        "score": None,
        "reason": "NO_RELIABLE_MATCH",
        "attempts": attempts,
    }


def _lookup_queries(
    lookup,
    queries: list[tuple[str, str]],
    cache: dict,
    cache_scope: str,
    cache_path: Path | None,
    state: dict,
    wait_seconds: float,
) -> dict:
    attempts = []
    for query, source in queries:
        cache_key = "%s|%s|%s" % (lookup.__name__, cache_scope, query.casefold())
        result = cache.get(cache_key)
        if not isinstance(result, dict) or result.get("status") != "matched":
            result = lookup(query)
            cache[cache_key] = result
            if cache_path is not None:
                _write_json(cache_path, state)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        attempts.append({
            "query": query,
            "source": source,
            "result": result,
        })
        if result.get("status") == "matched":
            return {
                "status": "matched",
                **result,
                "query": query,
                "attempts": attempts,
            }
    combined = _matches_from_attempts(attempts, lookup.__name__)
    combined.setdefault("attempts", attempts)
    return combined


def _lookup_wikidata_queries(
    item: dict,
    queries: list[tuple[str, str]],
    cache: dict,
    cache_path: Path | None,
    state: dict,
    proxy: str | None,
    wait_seconds: float,
    score_fields: set[str],
    season: int | None = None,
    part: int | None = None,
) -> dict:
    for query, source in queries[:3]:
        cache_key = (
            "%s|%s|%s" % (
                item.get("type") or "",
                item.get("year") or "",
                query.casefold(),
            )
            if season is None and part is None
            else "season-v2|%s|%s|%s|%s|%s" % (
                item.get("type") or "",
                item.get("year") or "",
                season or "",
                part or "",
                query.casefold(),
            )
        )
        result = cache.get(cache_key)
        if isinstance(result, dict) and result.get("status") != "matched":
            result = None
        if isinstance(result, dict) and result.get("status") == "matched":
            candidate = dict(result.get("candidate") or {})
            if (
                "metacriticScore" in score_fields
                and not candidate.get("metacriticId")
                and candidate.get("wikidataId")
            ):
                refreshed = lookup_wikidata_by_id(
                    candidate["wikidataId"],
                    item.get("type"),
                    proxy=proxy,
                )
                if refreshed.get("status") == "matched":
                    candidate.update(refreshed.get("candidate") or {})
            missing_fields = {
                field
                for field in score_fields
                if (result.get("scores") or {}).get(field) is None
            }
            if missing_fields and candidate:
                refreshed_scores = lookup_external_ids(
                    candidate,
                    season=season,
                    part=part,
                    year=item.get("year"),
                    fields=missing_fields,
                )
                result = {
                    **result,
                    "candidate": candidate,
                    "scores": {
                        **(result.get("scores") or {}),
                        **{
                            key: value
                            for key, value in refreshed_scores.items()
                            if key == "errors" or value is not None
                        },
                    },
                }
                cache[cache_key] = result
                if cache_path is not None:
                    _write_json(cache_path, {"cacheFormatVersion": 2, **state})
        if not isinstance(result, dict):
            resolved = lookup_wikidata(
                query,
                item.get("type"),
                item.get("year"),
                proxy=proxy,
            )
            if resolved.get("status") == "matched":
                resolved = {
                    **resolved,
                    "query": query,
                    "source": source,
                    "scores": lookup_external_ids(
                        resolved["candidate"],
                        season=season,
                        part=part,
                        year=item.get("year"),
                        fields=score_fields,
                    ),
                }
            result = resolved
            cache[cache_key] = result
            if cache_path is not None:
                _write_json(cache_path, {"cacheFormatVersion": 2, **state})
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        if (
            result.get("status") == "matched"
            and any(
                result.get("scores", {}).get(field) is not None
                for field in score_fields
            )
        ):
            return result
    return {"status": "no_match", "reason": "NO_WIKIDATA_ID"}


def _movie_title_aliases(
    item: dict,
    cache_state: dict,
    cache_path: Path | None,
    proxy: str | None,
) -> list[str]:
    """Translate a localized franchise movie title through its parent series."""
    if item.get("type") != "movie":
        return []
    title = str(item.get("title") or "")
    match = re.search(r"^(.*?)(?:大电影|剧场版|劇場版)", title)
    if not match:
        return []
    base_title = match.group(1).strip(" ：:")
    if not base_title:
        return []
    cache_key = "parent|%s" % base_title
    parent = cache_state["wikidata"].get(cache_key)
    if not isinstance(parent, dict):
        parent = lookup_wikidata(
            base_title,
            "teleplay",
            None,
            proxy=proxy,
        )
        cache_state["wikidata"][cache_key] = parent
        if cache_path is not None:
            _write_json(cache_path, {"cacheFormatVersion": 1, **cache_state})
    english = str((parent.get("candidate") or {}).get("englishTitle") or "").strip()
    if not english:
        return []
    try:
        payload = _rt_request_json(english)
    except Exception:
        return []
    aliases = []
    for hit in payload.get("hits", []) if isinstance(payload, dict) else []:
        if str(hit.get("type") or "").casefold() != "movie":
            continue
        if str(hit.get("releaseYear") or "") != str(item.get("year") or ""):
            continue
        candidate_title = str(hit.get("title") or "").strip()
        if candidate_title:
            aliases.append(candidate_title)
    return list(dict.fromkeys(aliases))


def _build_identity(
    item: dict,
    fm: dict,
    proxy: str | None,
    cache_state: dict,
    cache_path: Path | None,
    wait_seconds: float,
    need_imdb: bool,
    need_rt: bool,
    need_metacritic: bool,
    need_bangumi: bool,
    use_aliases: bool,
    extra_titles: list[str] | None = None,
    forced_season: int | None = None,
    forced_part: int | None = None,
) -> dict:
    parsed = extract_season(item.get("title") or "")
    season = (
        forced_season or parsed["season"]
        if item.get("type") == "teleplay"
        else None
    )
    part = forced_part or parsed.get("part")
    if need_imdb or need_rt or need_metacritic:
        primary, title_source = _douban_title_candidate(
            server_module.LIB,
            item,
            fm,
            {},
        )
    else:
        primary, title_source = "", ""
    base_queries = [
        *[(title, "stored_alias") for title in (extra_titles or [])],
        (fm.get("originalTitle"), "existing_original_title"),
        (primary, title_source),
        (item.get("title"), "note_title"),
        *[(alias, "alias") for alias in item.get("aliases", [])],
    ]
    metacritic_hint = resolve_metacritic_title(item.get("title") or "")
    if metacritic_hint:
        base_queries.insert(0, (metacritic_hint, "metacritic_title_override"))
    queries = []
    bangumi = None
    if use_aliases and _is_anime(item) and (
        need_bangumi or need_imdb or need_rt or need_metacritic
    ):
        bangumi_key = "%s|%s|%s" % (
            item.get("title") or primary,
            item.get("year") or "",
            season or "",
        )
        bangumi = cache_state["bangumi"].get(bangumi_key)
        if not isinstance(bangumi, dict):
            bangumi = lookup_bangumi(
                item.get("title") or primary,
                item.get("year"),
                season,
                proxy=proxy,
            )
            cache_state["bangumi"][bangumi_key] = bangumi
            if cache_path is not None:
                _write_json(cache_path, {"cacheFormatVersion": 1, **cache_state})
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        if (
            bangumi.get("status") != "matched"
            and primary
            and primary != item.get("title")
        ):
            fallback_key = "%s|%s|%s" % (
                primary,
                item.get("year") or "",
                season or "",
            )
            fallback = cache_state["bangumi"].get(fallback_key)
            if not isinstance(fallback, dict):
                fallback = lookup_bangumi(
                    primary,
                    item.get("year"),
                    season,
                    proxy=proxy,
                )
                cache_state["bangumi"][fallback_key] = fallback
                if cache_path is not None:
                    _write_json(cache_path, {"cacheFormatVersion": 1, **cache_state})
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
            if fallback.get("status") == "matched":
                bangumi = fallback
        candidate = bangumi.get("candidate") or {}
        alias_source = candidate.get("originalTitle") or primary or item.get("title")
        if alias_source and (need_imdb or need_rt or need_metacritic):
            alias_query = (
                extract_season(alias_source)["title"]
                if season is not None
                else alias_source
            )
            alias_key = "%s|%s|%s" % (
                alias_query,
                item.get("year") or "",
                "",
            )
            anime = cache_state["anilist"].get(alias_key)
            if not isinstance(anime, dict):
                anime = lookup_anime_aliases(
                    alias_query,
                    item.get("year"),
                    None,
                    proxy=proxy,
                )
                cache_state["anilist"][alias_key] = anime
                if cache_path is not None:
                    _write_json(cache_path, {"cacheFormatVersion": 1, **cache_state})
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
            latin_aliases = [
                alias for alias in anime.get("aliases", [])
                if any(char.isalpha() and ord(char) < 128 for char in alias)
            ][:2]
            for alias in latin_aliases:
                alias_season = extract_season(alias)["season"]
                queries.append((
                    (
                        alias
                        if season is None or alias_season == season
                        else "%s Season %d" % (alias, season)
                    ),
                    "anilist_alias",
                ))
        queries.extend([
            (candidate.get("originalTitle"), "bangumi_original_title"),
            (primary, title_source),
            (item.get("title"), "note_title"),
        ])
    elif need_imdb or need_rt or need_metacritic:
        queries.extend(base_queries)
    if item.get("type") == "movie" and (need_imdb or need_rt or need_metacritic):
        queries = [
            (alias, "parent_series_movie_alias")
            for alias in _movie_title_aliases(
                item,
                cache_state,
                cache_path,
                proxy,
            )
        ] + queries
    return {
        "primary": primary,
        "titleSource": title_source,
        "season": season,
        "part": part,
        "isAnime": _is_anime(item),
        "bangumi": bangumi,
        "queries": queries,
    }


def build_plan(
    vault: Path,
    proxy: str | None = None,
    offset: int = 0,
    limit: int | None = None,
    only_title: str | list[str] | None = None,
    season_only: bool = False,
    missing_only: bool = False,
    imdb_only: bool = False,
    metacritic_only: bool = False,
    aliases: dict[str, list[str]] | None = None,
    wait_seconds: float = 0.1,
    cache_path: Path | None = None,
) -> dict:
    vault = vault.resolve()
    lib = Library(vault)
    server_module.LIB = lib
    items = lib.snapshot()
    cache_state = {
        "imdb": {},
        "rotten": {},
        "bangumi": {},
        "anilist": {},
        "wikidata": {},
        "metacritic": {},
    }
    if cache_path is not None and cache_path.is_file():
        stored = _read_json(cache_path)
        for key in cache_state:
            if isinstance(stored.get(key), dict):
                cache_state[key] = stored[key]
    title_filters = (
        [only_title]
        if isinstance(only_title, str)
        else list(only_title or [])
    )
    relevant = [
        item for item in items
        if item.get("type") in SUPPORTED_TYPES
        and (
            not title_filters
            or any(
                title.casefold() in item.get("title", "").casefold()
                for title in title_filters
            )
        )
        and (
            not season_only
            or extract_season(item.get("title") or "")["season"] is not None
        )
        and (
            not missing_only
            or (
                item.get("metacriticScore") is None
                if metacritic_only
                else item.get("imdbScore") is None
                if imdb_only
                else (
                    item.get("imdbScore") is None
                    or item.get("rottenTomatoesScore") is None
                    or item.get("metacriticScore") is None
                )
            )
        )
    ]
    if offset:
        relevant = relevant[offset:]
    if limit is not None:
        relevant = relevant[:limit]
    entries = []

    def save_cache():
        if cache_path is not None:
            _write_json(cache_path, {
                "cacheFormatVersion": 2,
                **cache_state,
            })

    for index, item in enumerate(relevant, 1):
        path = lib._note_target(item["path"])
        fm = _frontmatter_values(path)
        current = {
            field: _score_value(fm.get(field))
            for field in ANNUAL_FIELDS
        }
        parsed = extract_season(item.get("title") or "")
        imdb_override = resolve_imdb_override(item.get("title") or "")
        override_season = (imdb_override or {}).get("season")
        season = (
            override_season
            if override_season is not None
            else parsed["season"]
            if item.get("type") == "teleplay"
            else None
        )
        part = (imdb_override or {}).get("part") or parsed.get("part")
        is_anime = _is_anime(item)
        need_imdb = (
            not metacritic_only
            and (current["imdbScore"] is None or season is not None)
        )
        need_rt = (
            not imdb_only
            and not metacritic_only
            and (current["rottenTomatoesScore"] is None or season is not None)
        )
        need_metacritic = (
            not imdb_only
            and current["metacriticScore"] is None
        )
        need_bangumi = (
            not imdb_only
            and not metacritic_only
            and is_anime
            and current["bangumiScore"] is None
        )
        identity = (
            {
                "primary": "",
                "titleSource": "",
                "season": season,
                "part": part,
                "isAnime": is_anime,
                "bangumi": None,
                "queries": [],
            }
            if imdb_override and imdb_only
            else _build_identity(
                item,
                fm,
                proxy,
                cache_state,
                cache_path,
                wait_seconds,
                need_imdb,
                need_rt,
                need_metacritic,
                need_bangumi,
                use_aliases=not metacritic_only,
                extra_titles=(aliases or {}).get(item["path"]),
                forced_season=season,
                forced_part=part,
            )
        )
        entry = {
            "relativePath": item["path"],
            "title": item.get("title") or "",
            "type": item.get("type") or "",
            "year": item.get("year"),
            "revision": item.get("revision"),
            "season": season,
            "part": part,
            "isAnime": identity["isAnime"],
            "before": current,
            "bangumiLookup": identity["bangumi"],
            "updates": {},
        }

        queries = []
        for query, source in identity["queries"]:
            text = str(query or "").strip()
            if not text:
                continue
            parsed = extract_season(text)
            base = parsed["title"] if season is not None else text
            queries.append((base, source))
        queries = list(dict.fromkeys(queries))[:3]
        wikidata_lookup = (
            _lookup_wikidata_queries(
                item,
                queries,
                cache_state["wikidata"],
                cache_path,
                cache_state,
                proxy,
                wait_seconds,
                score_fields={
                    field
                    for field, needed in (
                        ("imdbScore", need_imdb),
                        ("rottenTomatoesScore", need_rt),
                        ("metacriticScore", need_metacritic),
                    )
                    if needed
                },
                season=season,
                part=part,
            )
            if need_imdb or need_rt or need_metacritic
            else {"status": "preserved", "reason": "NO_MISSING_SCORE"}
        )
        entry["wikidataLookup"] = wikidata_lookup
        wikidata_scores = wikidata_lookup.get("scores") or {}
        wikidata_title = str(
            (wikidata_lookup.get("candidate") or {}).get("englishTitle") or ""
        ).strip()
        if wikidata_title:
            queries = [(wikidata_title, "wikidata_english_title")] + queries

        if need_imdb and imdb_override:
            entry["imdbLookup"] = lookup_imdb_by_id(
                imdb_override["imdbId"],
                item["type"],
                item.get("year"),
                season=season,
                part=part,
                allow_type_mismatch=bool(imdb_override.get("allowTypeMismatch")),
            )
        elif need_imdb and wikidata_scores.get("imdbScore") is not None:
            entry["imdbLookup"] = {
                "status": "matched",
                "score": wikidata_scores["imdbScore"],
                "source": "wikidata_imdb_id",
                "candidate": wikidata_lookup.get("candidate"),
            }
        elif need_imdb:
            entry["imdbLookup"] = _lookup_queries(
                lambda query: (
                    lookup_imdb(
                        query,
                        item["type"],
                        item.get("year"),
                        season=season,
                        part=part,
                    )
                    if season is not None
                    else lookup_imdb(query, item["type"], item.get("year"))
                ),
                queries,
                cache_state["imdb"],
                "%s|%s|%s" % (
                    item["type"],
                    item.get("year") or "",
                    season or "",
                ),
                cache_path,
                cache_state,
                wait_seconds,
            )
        else:
            entry["imdbLookup"] = {"status": "preserved", "score": current["imdbScore"]}

        if need_rt and wikidata_scores.get("rottenTomatoesScore") is not None:
            entry["rottenLookup"] = {
                "status": "matched",
                "score": wikidata_scores["rottenTomatoesScore"],
                "source": "wikidata_rt_id",
                "candidate": wikidata_lookup.get("candidate"),
            }
        elif need_rt:
            entry["rottenLookup"] = _lookup_queries(
                lambda query: (
                    lookup_rotten_tomatoes_season(
                        query,
                        season,
                        item.get("year"),
                    )
                    if season is not None
                    else lookup_rotten_tomatoes(query, item["type"], item.get("year"))
                ),
                queries,
                cache_state["rotten"],
                "%s|%s|%s" % (
                    item["type"],
                    item.get("year") or "",
                    season or "",
                ),
                cache_path,
                cache_state,
                wait_seconds,
            )
        else:
            entry["rottenLookup"] = {
                "status": "preserved",
                "score": current["rottenTomatoesScore"],
            }

        if (
            need_metacritic
            and wikidata_scores.get("metacriticScore") is not None
        ):
            entry["metacriticLookup"] = {
                "status": "matched",
                "score": wikidata_scores["metacriticScore"],
                "source": "wikidata_metacritic_id",
                "candidate": wikidata_lookup.get("candidate"),
            }
        elif need_metacritic:
            entry["metacriticLookup"] = _lookup_queries(
                lambda query: lookup_metacritic(
                    query,
                    item["type"],
                    item.get("year"),
                    season=season,
                    part=part,
                ),
                queries,
                cache_state["metacritic"],
                "%s|%s|%s|%s" % (
                    item["type"],
                    item.get("year") or "",
                    season or "",
                    part or "",
                ),
                cache_path,
                cache_state,
                wait_seconds,
            )
        else:
            entry["metacriticLookup"] = {
                "status": "preserved",
                "score": current["metacriticScore"],
            }

        bangumi_lookup = identity["bangumi"] or {
            "status": "unsupported",
            "score": None,
        }
        if (
            need_bangumi
            and current["bangumiScore"] is None
            and bangumi_lookup.get("status") == "matched"
            and bangumi_lookup.get("score") is not None
        ):
            entry["updates"]["bangumiScore"] = bangumi_lookup["score"]
        if (
            entry["imdbLookup"].get("status") == "matched"
            and (current["imdbScore"] is None or season is not None)
            and abs(
                entry["imdbLookup"]["score"]
                - (current["imdbScore"] if current["imdbScore"] is not None else -999)
            ) > 1e-9
        ):
            entry["updates"]["imdbScore"] = entry["imdbLookup"]["score"]
        if (
            entry["rottenLookup"].get("status") == "matched"
            and (
                current["rottenTomatoesScore"] is None
                or season is not None
            )
            and abs(
                entry["rottenLookup"]["score"]
                - (
                    current["rottenTomatoesScore"]
                    if current["rottenTomatoesScore"] is not None
                    else -999
                )
            ) > 1e-9
        ):
            entry["updates"]["rottenTomatoesScore"] = entry["rottenLookup"]["score"]
        if (
            entry["metacriticLookup"].get("status") == "matched"
            and current["metacriticScore"] is None
            and entry["metacriticLookup"].get("score") is not None
        ):
            entry["updates"]["metacriticScore"] = entry["metacriticLookup"]["score"]
        entries.append(entry)
        save_cache()
        print(
            "[%d/%d] %s season=%s updates=%s"
            % (
                index,
                len(relevant),
                item.get("title") or item["path"],
                season or "-",
                ",".join(sorted(entry["updates"])) or "none",
            ),
            flush=True,
        )

    stats = {
        "relevant": len(relevant),
        "itemsWithUpdates": sum(bool(entry["updates"]) for entry in entries),
        "imdbUpdates": sum("imdbScore" in entry["updates"] for entry in entries),
        "rottenUpdates": sum(
            "rottenTomatoesScore" in entry["updates"] for entry in entries
        ),
        "metacriticUpdates": sum(
            "metacriticScore" in entry["updates"] for entry in entries
        ),
        "bangumiUpdates": sum("bangumiScore" in entry["updates"] for entry in entries),
        "seasonItems": sum(entry["season"] is not None for entry in entries),
        "animeItems": sum(entry["isAnime"] for entry in entries),
        "loadWarnings": len(lib.load_warnings),
    }
    plan_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    return {
        "planFormatVersion": PLAN_FORMAT_VERSION,
        "planId": plan_id,
        "createdAt": _now(),
        "vault": str(vault),
        "proxyConfigured": bool(proxy),
        "stats": stats,
        "loadWarnings": lib.load_warnings,
        "entries": entries,
    }


def write_preview_reports(plan: dict, output_dir: Path) -> tuple[Path, Path]:
    update_path = output_dir / "网络评分更新清单.md"
    pending_path = output_dir / "网络评分待确认清单.md"
    update_lines = [
        "# 网络评分更新清单",
        "",
        "计划：`%s`" % plan["planId"],
        "",
        "| 文件 | 标题 | 季 | IMDb | 烂番茄 | Metacritic | Bangumi |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    pending_lines = [
        "# 网络评分待确认清单",
        "",
        "计划：`%s`" % plan["planId"],
        "",
        "| 文件 | 标题 | 季 | 动画 | IMDb 状态 | 烂番茄状态 | Metacritic 状态 | Bangumi 状态 |",
        "|---|---|---:|:---:|---|---|---|---|",
    ]
    for entry in plan["entries"]:
        values = []
        for field in ANNUAL_FIELDS:
            values.append(entry["updates"].get(field, entry["before"].get(field)))
        row = (
            "| %s | %s | %s | %s | %s | %s | %s |"
            % (
                _md_cell(entry.get("relativePath")),
                _md_cell(entry.get("title")),
                _md_cell(entry.get("season") or ""),
                _md_cell(values[0] if values[0] is not None else ""),
                _md_cell(values[1] if values[1] is not None else ""),
                _md_cell(values[2] if values[2] is not None else ""),
                _md_cell(values[3] if values[3] is not None else ""),
            )
        )
        if entry["updates"]:
            update_lines.append(row)
        else:
            pending_lines.append(
                "| %s | %s | %s | %s | %s | %s | %s | %s |"
                % (
                    _md_cell(entry.get("relativePath")),
                    _md_cell(entry.get("title")),
                    _md_cell(entry.get("season") or ""),
                    "是" if entry.get("isAnime") else "否",
                    _md_cell(entry["imdbLookup"].get("status")),
                    _md_cell(entry["rottenLookup"].get("status")),
                    _md_cell(entry["metacriticLookup"].get("status")),
                    _md_cell((entry.get("bangumiLookup") or {}).get("status")),
                )
            )
    update_path.write_text("\n".join(update_lines) + "\n", encoding="utf-8")
    pending_path.write_text("\n".join(pending_lines) + "\n", encoding="utf-8")
    return update_path, pending_path


def apply_plan(vault: Path, plan: dict, confirm: str) -> dict:
    plan_id = str(plan.get("planId") or "")
    if plan.get("planFormatVersion") != PLAN_FORMAT_VERSION:
        raise ValueError("不支持的评分计划版本")
    if str(plan.get("vault") or "").casefold() != str(vault.resolve()).casefold():
        raise ValueError("计划的 Vault 路径与本次目标不一致")
    if confirm != "APPLY " + plan_id:
        raise ValueError("确认文本不匹配，必须输入 APPLY <planId>")
    lib = Library(vault.resolve())
    lib.load()
    backup = lib.create_snapshot_backup(
        "enhanced-ratings-" + plan_id,
        include_attachments=True,
    )
    entries = [entry for entry in plan.get("entries", []) if entry.get("updates")]
    results = []
    for entry in entries:
        result = {
            "relativePath": entry["relativePath"],
            "title": entry.get("title"),
            "season": entry.get("season"),
            "updates": entry["updates"],
        }
        payload = {"revision": entry["revision"], **entry["updates"]}
        try:
            rec = lib.update(entry["relativePath"], payload)
            for field, value in entry["updates"].items():
                if rec is None or rec.get(field) is None or abs(float(rec[field]) - float(value)) > 1e-9:
                    raise ValueError("写入后校验失败")
            result["status"] = "updated"
        except RevisionConflict:
            result.update({"status": "conflict", "reason": "EXTERNAL_MODIFICATION"})
        except Exception as exc:
            result.update({"status": "failed", "reason": type(exc).__name__})
        results.append(result)
        print("%s -> %s" % (entry["relativePath"], result["status"]), flush=True)
    stats = {
        "planned": len(entries),
        "updated": sum(result["status"] == "updated" for result in results),
        "conflict": sum(result["status"] == "conflict" for result in results),
        "failed": sum(result["status"] == "failed" for result in results),
    }
    return {
        "planId": plan_id,
        "appliedAt": _now(),
        "vault": str(vault.resolve()),
        "backupPath": str(backup),
        "stats": stats,
        "results": results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True)
    parser.add_argument("--proxy")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--only-title", action="append")
    parser.add_argument("--season-only", action="store_true")
    parser.add_argument("--missing-only", action="store_true")
    parser.add_argument("--imdb-only", action="store_true")
    parser.add_argument("--metacritic-only", action="store_true")
    parser.add_argument("--aliases-file")
    parser.add_argument("--wait", type=float, default=0.1)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--cache")
    parser.add_argument("--plan")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    vault = Path(args.vault)
    if args.dry_run:
        aliases = {}
        if args.aliases_file:
            loaded = _read_json(Path(args.aliases_file))
            if isinstance(loaded, dict):
                aliases = {
                    str(path): [str(value) for value in values]
                    for path, values in loaded.items()
                    if isinstance(values, list)
                }
        plan = build_plan(
            vault,
            proxy=args.proxy,
            offset=max(0, args.offset),
            limit=args.limit,
            only_title=args.only_title,
            season_only=args.season_only,
            missing_only=args.missing_only,
            imdb_only=args.imdb_only,
            metacritic_only=args.metacritic_only,
            aliases=aliases,
            wait_seconds=max(0, args.wait),
            cache_path=Path(args.cache) if args.cache else None,
        )
        output = Path(args.output) if args.output else Path.cwd() / (
            "enhanced-ratings-plan-%s.json" % plan["planId"]
        )
        _write_json(output, plan)
        update_report, pending_report = write_preview_reports(plan, output.parent)
        print(json.dumps({
            "ok": True,
            "plan": str(output),
            "updateReport": str(update_report),
            "pendingReport": str(pending_report),
            "planId": plan["planId"],
            "stats": plan["stats"],
        }, ensure_ascii=False))
        return 0
    if not args.plan:
        parser.error("--apply 需要 --plan")
    if not args.confirm:
        parser.error("--apply 需要 --confirm")
    plan = _read_json(Path(args.plan))
    report = apply_plan(vault, plan, args.confirm)
    output = Path(args.output) if args.output else Path(args.plan).with_name(
        "enhanced-ratings-apply-%s.json" % plan["planId"]
    )
    _write_json(output, report)
    print(json.dumps({
        "ok": report["stats"]["failed"] == 0 and report["stats"]["conflict"] == 0,
        "report": str(output),
        "stats": report["stats"],
        "backupPath": report["backupPath"],
    }, ensure_ascii=False))
    return 0 if report["stats"]["failed"] == 0 and report["stats"]["conflict"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
