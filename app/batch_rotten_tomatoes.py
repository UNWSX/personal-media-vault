#!/usr/bin/env python3
"""Preview and safely apply Rotten Tomatoes critic scores to a vault."""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

import server as server_module
from frontmatter_patch import parse_frontmatter
from server import Library, fetch_douban_detail, lookup_rotten_tomatoes, search_douban
from storage_safety import RevisionConflict

PLAN_FORMAT_VERSION = 1
SUPPORTED_TYPES = {"movie", "teleplay"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def _md_cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def write_preview_reports(plan: dict, output_dir: Path) -> tuple[Path, Path]:
    matched_path = output_dir / "匹配清单.md"
    pending_path = output_dir / "待确认清单.md"
    matched_lines = [
        "# 烂番茄指数匹配清单",
        "",
        "计划：`%s`" % plan["planId"],
        "",
        "| 文件 | 中文标题 | 查询原标题 | RT 标题 | 年份 | 影评人指数 | 来源 |",
        "|---|---|---|---|---:|---:|---|",
    ]
    pending_lines = [
        "# 烂番茄指数待确认清单",
        "",
        "计划：`%s`" % plan["planId"],
        "",
        "无可靠匹配或存在歧义的条目不会写入 Markdown。",
        "",
        "| 文件 | 中文标题 | 年份 | 结果 | 原因 | 查询尝试 |",
        "|---|---|---:|---|---|---|",
    ]
    for entry in plan.get("entries", []):
        if entry.get("status") == "matched":
            candidate = entry.get("candidate") or {}
            matched_lines.append(
                "| %s | %s | %s | %s | %s | %s | %s |"
                % (
                    _md_cell(entry.get("relativePath")),
                    _md_cell(entry.get("title")),
                    _md_cell(entry.get("query")),
                    _md_cell(candidate.get("title")),
                    _md_cell(candidate.get("year")),
                    _md_cell(entry.get("score")),
                    _md_cell(entry.get("titleSource")),
                )
            )
        else:
            attempts = "; ".join(
                "%s → %s/%s%s"
                % (
                    attempt.get("query") or "",
                    attempt.get("status") or "",
                    attempt.get("reason") or "",
                    (
                        " %s" % attempt.get("score")
                        if attempt.get("score") is not None
                        else ""
                    ),
                )
                for attempt in entry.get("attempts", [])
            )
            pending_lines.append(
                "| %s | %s | %s | %s | %s | %s |"
                % (
                    _md_cell(entry.get("relativePath")),
                    _md_cell(entry.get("title")),
                    _md_cell(entry.get("year")),
                    _md_cell(entry.get("status")),
                    _md_cell(entry.get("reason")),
                    _md_cell(attempts),
                )
            )
    matched_path.write_text("\n".join(matched_lines) + "\n", encoding="utf-8")
    pending_path.write_text("\n".join(pending_lines) + "\n", encoding="utf-8")
    return matched_path, pending_path


def _frontmatter_values(path: Path) -> dict:
    parsed = parse_frontmatter(path.read_bytes())
    if not parsed.ok:
        return {}
    return {key: field.value for key, field in parsed.fields.items()}


def _dedupe(values) -> list[str]:
    out = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _parse_douban_original_title(value: str) -> str:
    text = str(value or "").strip()
    marker = "原名:"
    if marker in text:
        text = text.split(marker, 1)[1]
        text = text.split(" / ", 1)[0]
    return text.strip()


def _douban_title_candidate(
    lib: Library,
    item: dict,
    fm: dict,
    cache: dict,
) -> tuple[str, str]:
    original = str(fm.get("originalTitle") or item.get("originalTitle") or "").strip()
    if original:
        return original, "existing_original_title"
    subject_id = str(fm.get("doubanId") or fm.get("id") or "").strip()
    cache_key = "%s|%s|%s" % (
        subject_id,
        item["type"],
        item.get("title") or "",
    )
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return str(cached.get("title") or ""), str(cached.get("source") or "")
    try:
        results = search_douban(item["title"], item["type"])
    except Exception:
        results = []
    for result in results if isinstance(results, list) else []:
        if subject_id and str(result.get("id") or "") != subject_id:
            continue
        if (
            not subject_id
            and item.get("year")
            and str(result.get("year") or "")[:4] != str(item["year"])
        ):
            continue
        subtitle = _parse_douban_original_title(result.get("sub"))
        if subtitle:
            cache[cache_key] = {
                "title": subtitle,
                "source": "douban_search",
            }
            return subtitle, "douban_search"
    if subject_id:
        detail = fetch_douban_detail(subject_id, item["type"])
        original = str(detail.get("originalTitle") or "").strip()
        if original:
            cache[cache_key] = {
                "title": original,
                "source": "douban_detail",
            }
            return original, "douban_detail"
    cache[cache_key] = {"title": "", "source": ""}
    return "", ""


def _lookup_candidates(
    lib: Library,
    item: dict,
    fm: dict,
    cache: dict,
    douban_cache: dict,
    cache_path: Path | None,
    wait_seconds: float,
) -> dict:
    primary, source = _douban_title_candidate(lib, item, fm, douban_cache)
    candidates = _dedupe(
        [primary, *item.get("aliases", [])]
        if primary
        else [item.get("title"), *item.get("aliases", [])]
    )
    attempts = []
    matched = []
    ambiguous = []
    errors = []
    for query in candidates:
        cache_key = "%s|%s|%s" % (
            item["type"],
            item.get("year") or "",
            query.casefold(),
        )
        result = cache.get(cache_key)
        if not isinstance(result, dict):
            result = lookup_rotten_tomatoes(query, item["type"], item.get("year"))
            cache[cache_key] = result
            if cache_path is not None:
                _write_json(cache_path, {
                    "cacheFormatVersion": 1,
                    "lookups": cache,
                    "doubanTitles": douban_cache,
                })
            if wait_seconds > 0:
                time.sleep(wait_seconds)
        attempt = {
            "query": query,
            "status": result.get("status"),
            "reason": result.get("reason"),
            "score": result.get("score"),
        }
        if result.get("candidate"):
            attempt["candidate"] = result["candidate"]
        if result.get("candidates"):
            attempt["candidates"] = result["candidates"]
        attempts.append(attempt)
        if result.get("status") == "matched":
            matched.append((query, result))
        elif result.get("status") == "ambiguous":
            ambiguous.append((query, result))
        elif result.get("status") == "error":
            errors.append((query, result))

    distinct = {
        (
            result.get("candidate", {}).get("url"),
            result.get("score"),
        )
        for _, result in matched
    }
    if len(distinct) == 1 and matched:
        query, result = matched[0]
        return {
            "status": "matched",
            "score": result["score"],
            "query": query,
            "titleSource": source or "note",
            "candidate": result.get("candidate"),
            "attempts": attempts,
        }
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


def build_plan(
    vault: Path,
    limit: int | None = None,
    wait_seconds: float = 0.25,
    cache_path: Path | None = None,
) -> dict:
    lib = Library(vault.resolve())
    server_module.LIB = lib
    items = lib.snapshot()
    cache_state = {"lookups": {}, "doubanTitles": {}}
    if cache_path is not None and cache_path.is_file():
        existing_cache = _read_json(cache_path)
        if isinstance(existing_cache.get("lookups"), dict):
            cache_state["lookups"] = existing_cache["lookups"]
        if isinstance(existing_cache.get("doubanTitles"), dict):
            cache_state["doubanTitles"] = existing_cache["doubanTitles"]
    cache = cache_state["lookups"]
    douban_cache = cache_state["doubanTitles"]
    entries = []
    relevant = [item for item in items if item.get("type") in SUPPORTED_TYPES]
    if limit is not None:
        relevant = relevant[:limit]
    for index, item in enumerate(relevant, 1):
        path = lib._note_target(item["path"])
        fm = _frontmatter_values(path)
        entry = {
            "relativePath": item["path"],
            "title": item.get("title") or "",
            "type": item.get("type") or "",
            "year": item.get("year"),
            "revision": item.get("revision"),
            "doubanId": str(fm.get("doubanId") or fm.get("id") or "").strip(),
        }
        existing = fm.get("rottenTomatoesScore")
        if existing not in (None, ""):
            entry.update({
                "status": "skipped_existing",
                "existingScore": existing,
                "reason": "ALREADY_PRESENT",
            })
        elif not entry["revision"]:
            entry.update({"status": "error", "reason": "MISSING_REVISION"})
        else:
            entry.update(_lookup_candidates(
                lib,
                item,
                fm,
                cache,
                douban_cache,
                cache_path,
                wait_seconds,
            ))
        entries.append(entry)
        if cache_path is not None:
            _write_json(cache_path, {
                "cacheFormatVersion": 1,
                "lookups": cache,
                "doubanTitles": douban_cache,
            })
        print(
            "[%d/%d] %s -> %s%s"
            % (
                index,
                len(relevant),
                item.get("title") or item["path"],
                entry["status"],
                (" %d" % entry["score"]) if entry.get("score") is not None else "",
            ),
            flush=True,
        )
    stats = {
        "relevant": len(relevant),
        "matched": sum(e["status"] == "matched" for e in entries),
        "no_match": sum(e["status"] == "no_match" for e in entries),
        "ambiguous": sum(e["status"] == "ambiguous" for e in entries),
        "error": sum(e["status"] == "error" for e in entries),
        "skipped_existing": sum(e["status"] == "skipped_existing" for e in entries),
        "loadWarnings": len(lib.load_warnings),
    }
    plan_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    return {
        "planFormatVersion": PLAN_FORMAT_VERSION,
        "planId": plan_id,
        "createdAt": _now(),
        "vault": str(vault.resolve()),
        "policy": {
            "mediaTypes": sorted(SUPPORTED_TYPES),
            "requireExactTitle": True,
            "requireExactYear": True,
            "requireUniqueCandidate": True,
            "overwriteExisting": False,
        },
        "stats": stats,
        "loadWarnings": lib.load_warnings,
        "entries": entries,
    }


def apply_plan(vault: Path, plan: dict, confirm: str) -> dict:
    plan_id = str(plan.get("planId") or "")
    if plan.get("planFormatVersion") != PLAN_FORMAT_VERSION:
        raise ValueError("不支持的批量导入计划版本")
    if str(plan.get("vault") or "").casefold() != str(vault.resolve()).casefold():
        raise ValueError("计划的 Vault 路径与本次目标不一致")
    if confirm != "APPLY " + plan_id:
        raise ValueError("确认文本不匹配，必须输入 APPLY <planId>")

    lib = Library(vault.resolve())
    lib.load()
    backup = lib.create_snapshot_backup(
        "rotten-tomatoes-" + plan_id,
        include_attachments=True,
    )
    results = []
    matched = [entry for entry in plan.get("entries", []) if entry.get("status") == "matched"]
    for entry in matched:
        result = {
            "relativePath": entry["relativePath"],
            "title": entry.get("title"),
            "score": entry.get("score"),
        }
        try:
            rec = lib.update(entry["relativePath"], {
                "revision": entry["revision"],
                "rottenTomatoesScore": entry["score"],
            })
            if rec is None or rec.get("rottenTomatoesScore") != entry["score"]:
                raise ValueError("写入后校验失败")
            result["status"] = "updated"
        except RevisionConflict:
            result.update({
                "status": "conflict",
                "reason": "EXTERNAL_MODIFICATION",
            })
        except Exception as exc:
            result.update({
                "status": "failed",
                "reason": type(exc).__name__,
            })
        results.append(result)
        print(
            "%s -> %s" % (entry["relativePath"], result["status"]),
            flush=True,
        )
    stats = {
        "planned": len(matched),
        "updated": sum(r["status"] == "updated" for r in results),
        "conflict": sum(r["status"] == "conflict" for r in results),
        "failed": sum(r["status"] == "failed" for r in results),
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
    parser.add_argument("--limit", type=int)
    parser.add_argument("--wait", type=float, default=0.25)
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
        plan = build_plan(
            vault,
            limit=args.limit,
            wait_seconds=max(0, args.wait),
            cache_path=Path(args.cache) if args.cache else None,
        )
        output = Path(args.output) if args.output else Path.cwd() / (
            "rotten-tomatoes-plan-%s.json" % plan["planId"]
        )
        _write_json(output, plan)
        matched_report, pending_report = write_preview_reports(plan, output.parent)
        print(json.dumps({
            "ok": True,
            "plan": str(output),
            "matchedReport": str(matched_report),
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
        "rotten-tomatoes-apply-%s.json" % plan["planId"]
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
