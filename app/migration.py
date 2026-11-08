from __future__ import annotations

"""Local-only Markdown migration engine.

The web server may call :func:`scan_media` for read-only reporting. Plan
creation, Apply and Rollback are CLI-only.
"""

import copy
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Iterable

from callout_sync import sync_status_callout
from frontmatter_patch import parse_frontmatter, patch_frontmatter
from media_compat import (
    MEDIA_TYPES,
    PROGRESS_BY_TYPE,
    normalize_identity,
    normalize_status,
    strict_bool,
)
from storage_safety import decode_note, encode_note


PLAN_FORMAT_VERSION = 1
SCHEMA_TARGET = 2
MEDIA_ROOT = "02-电影阅读"
DEFAULT_GROUPS = frozenset({"BASE_SCHEMA", "CALLOUT_STATUS"})
ALL_GROUPS = frozenset({
    "BASE_SCHEMA",
    "CONFLICTING_LEGACY_STATUS",
    "CALLOUT_STATUS",
    "SCORE_PROMOTION",
    "SCORE_REVIEW",
})
APPLY_STATES = frozenset({
    "FINALIZED",
    "APPLYING",
    "APPLIED",
    "ROLLED_BACK",
    "ROLLED_BACK_WITH_CONFLICTS",
})
EXECUTABLE_GROUPS = frozenset({
    "BASE_SCHEMA",
    "SCORE_PROMOTION",
    "CALLOUT_STATUS",
})
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class MigrationError(ValueError):
    pass


class PlanValidationError(MigrationError):
    pass


class MigrationConflict(MigrationError):
    pass


class RestoreConflict(MigrationError):
    pass


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_tag(raw: bytes) -> str:
    return "sha256:" + sha256_bytes(raw)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalized_vault_root(vault: Path) -> str:
    return os.path.normcase(os.path.abspath(str(vault)))


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        return bool(checker and checker())
    except OSError:
        return True


def _target_from_relative(vault: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise PlanValidationError("relativePath 不能为空")
    if "\x00" in relative or "\\" in relative:
        raise PlanValidationError("relativePath 必须是 POSIX 风格")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PlanValidationError("relativePath 含不安全片段")
    pure = Path(relative)
    if pure.is_absolute() or pure.drive:
        raise PlanValidationError("relativePath 不能是绝对路径")
    vault_root = vault.resolve()
    media_root = (vault_root / MEDIA_ROOT).resolve()
    target = (vault_root / pure).resolve()
    try:
        target.relative_to(media_root)
    except ValueError as exc:
        raise PlanValidationError("目标不在媒体目录内") from exc
    current = vault_root
    for part in pure.parts:
        current = current / part
        if _is_reparse(current):
            raise PlanValidationError("目标路径含符号链接或 junction")
    if target.suffix.lower() != ".md":
        raise PlanValidationError("迁移目标必须是 Markdown")
    return target


def iter_media_files(vault: Path) -> tuple[list[Path], list[dict[str, str]]]:
    root = vault / MEDIA_ROOT
    if not root.is_dir():
        raise MigrationError("Vault 中缺少 %s" % MEDIA_ROOT)
    files: list[Path] = []
    warnings: list[dict[str, str]] = []
    for current_root, dirs, names in os.walk(root, followlinks=False):
        current = Path(current_root)
        kept_dirs = []
        for name in dirs:
            child = current / name
            if name.startswith(".") or name in {
                "attachments", ".media-app", ".git", ".obsidian",
            }:
                continue
            if _is_reparse(child):
                warnings.append({
                    "code": "REPARSE_POINT_SKIPPED",
                    "path": child.relative_to(vault).as_posix(),
                })
                continue
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in names:
            path = current / name
            if path.suffix.lower() != ".md":
                continue
            if _is_reparse(path):
                warnings.append({
                    "code": "REPARSE_POINT_SKIPPED",
                    "path": path.relative_to(vault).as_posix(),
                })
                continue
            files.append(path)
    files.sort(key=lambda item: item.relative_to(vault).as_posix())
    return files, warnings


def _decimal_score(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _same_rating_value(left, right) -> bool:
    if left in (None, "") or right in (None, ""):
        return left in (None, "") and right in (None, "")
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return left == right


def _load_evidence(path: Path | None) -> list[dict]:
    if path is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError("评分证据文件无法读取") from exc
    if isinstance(data, dict):
        data = data.get("records")
    if not isinstance(data, list):
        raise MigrationError("评分证据必须是数组或含 records 数组的对象")
    return [record for record in data if isinstance(record, dict)]


def _evidence_matches(
    relative_path: str,
    score: Decimal,
    records: Iterable[dict],
) -> list[dict]:
    matches = []
    for record in records:
        if record.get("relativePath") != relative_path:
            continue
        record_score = _decimal_score(record.get("value"))
        if record_score == score:
            matches.append(record)
    return matches


def score_evidence(
    relative_path: str,
    media_type: str,
    fm: dict,
    records: Iterable[dict] = (),
) -> dict:
    raw = fm.get("score")
    present = raw not in (None, "")
    parsed = _decimal_score(raw)
    source_hint = str(fm.get("scoreSource") or "").strip().lower()
    refs: list[str] = []
    evidence_type = "NONE"
    level = "L3"
    source = "unknown"
    action = "keep_legacy"
    reasons: list[str] = []

    if not present:
        reasons.append("SCORE_MISSING")
    elif parsed is None:
        reasons.append("SCORE_INVALID")
    else:
        records_match = _evidence_matches(relative_path, parsed, records)
        record_sources = {
            str(record.get("source") or "").strip().lower()
            for record in records_match
            if str(record.get("source") or "").strip().lower() in {"douban", "metacritic"}
        }
        if (
            source_hint in {"douban", "metacritic"}
            and record_sources
            and record_sources != {source_hint}
        ):
            evidence_type = "CONFLICTING_SCORE_SOURCE_EVIDENCE"
            reasons.append("SCORE_SOURCE_FIELD_CONFLICTS_WITH_RECORDS")
            level = "L2"
        elif source_hint in {"douban", "metacritic"} and (
            not records_match or record_sources == {source_hint}
        ):
            evidence_type = "EXPLICIT_SCORE_SOURCE_FIELD"
            refs.append("scoreSource")
            source = source_hint
            level = "L1"
        elif len(record_sources) == 1:
            evidence_type = "EXACT_LOCAL_EVIDENCE_RECORD"
            refs.extend(
                str(record.get("id") or "record-%d" % index)
                for index, record in enumerate(records_match, 1)
            )
            source = next(iter(record_sources))
            level = "L1"
        elif len(record_sources) > 1:
            evidence_type = "CONFLICTING_EVIDENCE_RECORDS"
            reasons.append("CONFLICTING_SCORE_EVIDENCE")
            level = "L2"
        else:
            candidate = []
            if media_type == "game" and parsed > Decimal("10"):
                candidate.append("GAME_SCORE_OVER_10_WITHOUT_PROVENANCE")
            if fm.get("steamAppId"):
                candidate.append("STEAM_ID_WITHOUT_SCORE_PROVENANCE")
            if fm.get("doubanId"):
                candidate.append("DOUBAN_ID_WITHOUT_SCORE_PROVENANCE")
            if fm.get("imdbId") or fm.get("bangumiId"):
                candidate.append("PLATFORM_ID_WITHOUT_SCORE_PROVENANCE")
            if candidate:
                evidence_type = "INDIRECT_SOURCE_CANDIDATE"
                reasons.extend(candidate)
                level = "L2"
            else:
                evidence_type = "NO_SCORE_PROVENANCE"
                reasons.append("SCORE_SOURCE_NOT_PROVEN")
                level = "L3"

    proposed_field = None
    proposed_value = None
    if level == "L1" and parsed is not None:
        if source == "metacritic":
            if media_type == "game" and Decimal("0") <= parsed <= Decimal("10"):
                proposed_field = "metacriticScore"
                proposed_value = int(
                    (parsed * Decimal("10")).to_integral_value(rounding=ROUND_HALF_UP)
                )
                action = "copy"
            elif (
                media_type == "game"
                and Decimal("0") <= parsed <= Decimal("100")
                and parsed == parsed.to_integral_value()
            ):
                proposed_field = "metacriticScore"
                proposed_value = int(parsed)
                action = "copy"
            else:
                level = "L2"
                source = "unknown"
                reasons.append("METACRITIC_SOURCE_VALUE_OR_TYPE_MISMATCH")
        elif source == "douban":
            if media_type in {"movie", "teleplay", "book"} and Decimal("0") <= parsed <= Decimal("10"):
                proposed_field = "doubanScore"
                proposed_value = float(parsed)
                action = "copy"
            else:
                level = "L2"
                source = "unknown"
                reasons.append("DOUBAN_SOURCE_VALUE_OR_TYPE_MISMATCH")

    if action != "copy":
        action = "review" if level == "L2" or (level == "L3" and present) else "keep_legacy"

    return {
        "present": present,
        "rawText": None if raw is None else str(raw),
        "parsedValue": _json_number(parsed),
        "level": level,
        "source": source,
        "evidenceType": evidence_type,
        "evidenceRefs": refs,
        "proposedField": proposed_field,
        "proposedValue": proposed_value,
        "action": action,
        "scoreReviewRequired": action != "copy",
        "warnings": reasons,
    }


def status_evidence(media_type: str, fm: dict) -> dict:
    raw_status = fm.get("status")
    status_value = strict_bool(raw_status)
    progress_raw = fm.get("progressStatus")
    progress_text = (
        str(progress_raw).strip() if progress_raw not in (None, "") else None
    )
    result = normalize_status(media_type, raw_status, progress_text)
    allowed = PROGRESS_BY_TYPE.get(media_type, frozenset())
    progress_valid = progress_text in allowed if progress_text else False
    warnings = list(result.get("warnings") or [])
    if progress_text and not progress_valid:
        action = "keep_unknown"
    elif progress_text and status_value is not None and status_value != (progress_text == "completed"):
        action = "conflict"
        warnings.append("CONFLICTING_LEGACY_STATUS")
    elif status_value is not None and not progress_text:
        action = "set_progressStatus"
    elif progress_valid and status_value is None:
        action = "set_status"
    elif progress_valid:
        action = "keep"
    elif raw_status not in (None, "") and status_value is None:
        action = "invalid_status_review"
    else:
        action = "none"
    return {
        "legacyStatusRaw": None if raw_status is None else str(raw_status),
        "legacyStatusValid": status_value is not None,
        "progressStatusRaw": progress_text,
        "progressStatusValid": progress_valid,
        "resolvedStatus": result.get("progressStatus"),
        "action": action,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _scan_one(
    vault: Path,
    path: Path,
    evidence_records: list[dict],
) -> dict:
    relative = path.relative_to(vault).as_posix()
    raw = path.read_bytes()
    stat = path.stat()
    parsed = parse_frontmatter(raw)
    item = {
        "relativePath": relative,
        "originalSha256": sha256_bytes(raw),
        "originalSize": len(raw),
        "mtimeNs": stat.st_mtime_ns,
        "warnings": [],
        "_raw": raw,
        "_parsed": parsed,
    }
    if not parsed.ok:
        item.update({
            "parseOk": False,
            "migrationStatus": "SKIPPED",
            "skippedReason": "YAML_UNSAFE",
            "warnings": [diag.code for diag in parsed.diagnostics],
        })
        return item

    fm = {key: field.value for key, field in parsed.fields.items()}
    item["_fm"] = fm
    item["parseOk"] = True
    dir_type = path.parent.name
    field_type = str(fm.get("type") or "").strip().lower()
    media_type = field_type or dir_type
    item["mediaType"] = media_type
    item["directoryType"] = dir_type
    item["identity"] = normalize_identity(
        fm.get("schemaVersion"),
        fm.get("mediaId"),
    )
    item["scoreEvidence"] = score_evidence(
        relative, media_type, fm, evidence_records
    )
    item["statusEvidence"] = status_evidence(media_type, fm)
    warnings = []
    if media_type not in MEDIA_TYPES:
        warnings.append("UNSUPPORTED_MEDIA_TYPE")
    if dir_type not in MEDIA_TYPES:
        warnings.append("UNKNOWN_MEDIA_DIRECTORY")
    if field_type and field_type != dir_type:
        warnings.append("TYPE_DIRECTORY_MISMATCH")
    if item["identity"]["schemaFuture"]:
        warnings.append("FUTURE_SCHEMA_VERSION")
    if fm.get("mediaId") and not item["identity"]["mediaIdValid"]:
        warnings.append("INVALID_MEDIA_ID")
    warnings.extend(item["identity"]["identityWarnings"])
    warnings.extend(item["scoreEvidence"]["warnings"])
    warnings.extend(item["statusEvidence"]["warnings"])
    item["warnings"] = list(dict.fromkeys(warnings))
    item["proposedMediaId"] = None
    item["migrationStatus"] = "UNSCANNED"
    item["skippedReason"] = None
    return item


def public_scan_result(vault: Path) -> dict:
    scan = scan_media(vault)
    return _public_scan(scan)


def scan_media(
    vault: Path,
    *,
    evidence_path: Path | None = None,
) -> dict:
    vault = vault.resolve()
    files, path_warnings = iter_media_files(vault)
    records = _load_evidence(evidence_path)
    items = [_scan_one(vault, path, records) for path in files]

    existing_ids: dict[str, list[dict]] = {}
    for item in items:
        identity = item.get("identity") or {}
        if identity.get("mediaIdValid") and identity.get("mediaId"):
            existing_ids.setdefault(identity["mediaId"].lower(), []).append(item)
    for duplicates in existing_ids.values():
        if len(duplicates) < 2:
            continue
        for item in duplicates:
            item.setdefault("warnings", []).append("DUPLICATE_MEDIA_ID")
            item["identity"]["duplicateMediaId"] = True
            item["migrationStatus"] = "SKIPPED"
            item["skippedReason"] = "DUPLICATE_MEDIA_ID"

    for item in items:
        if item["migrationStatus"] == "SKIPPED":
            continue
        if not item.get("parseOk"):
            continue
        if item.get("mediaType") not in MEDIA_TYPES:
            item["migrationStatus"] = "SKIPPED"
            item["skippedReason"] = "UNSUPPORTED_MEDIA_TYPE"
            continue
        identity = item["identity"]
        if identity.get("schemaFuture"):
            item["migrationStatus"] = "SKIPPED"
            item["skippedReason"] = "FUTURE_SCHEMA_VERSION"
        elif not identity.get("identityWriteAllowed", True):
            item["migrationStatus"] = "SKIPPED"
            item["skippedReason"] = "INVALID_IDENTITY_FIELD"
        elif identity.get("schemaVersion") is not None and identity.get("schemaVersion") < 1:
            item["migrationStatus"] = "SKIPPED"
            item["skippedReason"] = "INVALID_SCHEMA_VERSION"
        elif item["_fm"].get("mediaId") and not identity.get("mediaIdValid"):
            item["migrationStatus"] = "SKIPPED"
            item["skippedReason"] = "INVALID_MEDIA_ID"

    summary = _scan_summary(items, path_warnings)
    return {
        "scanFormatVersion": 1,
        "scannedAt": utc_now(),
        "vaultRoot": str(vault),
        "vaultRootNormalized": normalized_vault_root(vault),
        "schemaTarget": SCHEMA_TARGET,
        "summary": summary,
        "warnings": path_warnings,
        "_items": items,
        "_evidenceRecords": records,
    }


def _scan_summary(items: list[dict], path_warnings: list[dict]) -> dict:
    type_counts = Counter(
        item.get("mediaType") or "unknown" for item in items
    )
    schema_counts = Counter(
        str((item.get("identity") or {}).get("schemaVersion")) for item in items
    )
    level_counts = Counter(
        (item.get("scoreEvidence") or {}).get("level", "L3") for item in items
    )
    status_actions = Counter(
        (item.get("statusEvidence") or {}).get("action", "none") for item in items
    )
    duplicate_ids = sum(
        "DUPLICATE_MEDIA_ID" in item.get("warnings", []) for item in items
    )
    return {
        "files": len(items),
        "mediaTypes": dict(sorted(type_counts.items())),
        "schemaVersions": dict(sorted(schema_counts.items())),
        "mediaIdMissing": sum(
            not (item.get("identity") or {}).get("mediaId") for item in items
        ),
        "mediaIdValid": sum(
            bool((item.get("identity") or {}).get("mediaIdValid")) for item in items
        ),
        "mediaIdInvalid": sum(
            bool(item.get("_fm", {}) and item["_fm"].get("mediaId"))
            and not bool((item.get("identity") or {}).get("mediaIdValid"))
            for item in items
        ),
        "mediaIdDuplicateFiles": duplicate_ids,
        "scoreFields": sum(
            bool((item.get("scoreEvidence") or {}).get("present")) for item in items
        ),
        "scoreEvidence": {
            "L1": level_counts.get("L1", 0),
            "L2": level_counts.get("L2", 0),
            "L3": level_counts.get("L3", 0),
        },
        "statusActions": dict(sorted(status_actions.items())),
        "yamlUnsafe": sum(not bool(item.get("parseOk")) for item in items),
        "pathWarnings": len(path_warnings),
        "skipped": sum(item.get("migrationStatus") == "SKIPPED" for item in items),
    }


def _public_scan(scan: dict) -> dict:
    clean_items = []
    for item in scan["_items"]:
        clean = {
            key: copy.deepcopy(value)
            for key, value in item.items()
            if not key.startswith("_") and key not in {"mtimeNs"}
        }
        clean_items.append(clean)
    return {
        key: copy.deepcopy(value)
        for key, value in scan.items()
        if not key.startswith("_")
    } | {"items": clean_items}


def _new_operation_id(counter: list[int]) -> str:
    counter[0] += 1
    return "op-%03d" % counter[0]


def _operation(
    operation_id: str,
    field: str,
    operation: str,
    before: Any,
    after: Any,
    reason: str,
) -> dict:
    return {
        "operationId": operation_id,
        "field": field,
        "operation": operation,
        "before": before,
        "after": after,
        "reason": reason,
    }


def _build_file_plan(
    scan_item: dict,
    *,
    proposed_media_id: str | None,
    selected_groups: set[str],
    operation_counter: list[int],
) -> dict:
    raw = scan_item["_raw"]
    fm = scan_item["_fm"]
    media_type = scan_item["mediaType"]
    identity = scan_item["identity"]
    status = scan_item["statusEvidence"]
    score = scan_item["scoreEvidence"]
    operations: list[dict] = []
    groups: dict[str, dict] = {}
    op_counter = operation_counter
    warnings = list(scan_item.get("warnings") or [])

    base_ops: list[dict] = []
    if identity.get("schemaVersion") != SCHEMA_TARGET:
        base_ops.append(_operation(
            _new_operation_id(op_counter),
            "schemaVersion",
            "set" if identity.get("schemaVersion") is not None else "insert",
            identity.get("schemaVersion"),
            SCHEMA_TARGET,
            "MISSING_SCHEMA_VERSION"
            if identity.get("schemaVersion") is None
            else "UPGRADE_SCHEMA_VERSION",
        ))
    if not identity.get("mediaIdValid"):
        base_ops.append(_operation(
            _new_operation_id(op_counter),
            "mediaId",
            "insert",
            None,
            proposed_media_id,
            "MISSING_MEDIA_ID",
        ))
    if status["action"] == "set_progressStatus":
        base_ops.append(_operation(
            _new_operation_id(op_counter),
            "progressStatus",
            "insert",
            None,
            status["resolvedStatus"],
            "LEGACY_STATUS_COMPAT",
        ))
    elif status["action"] == "set_status" and status["resolvedStatus"]:
        base_ops.append(_operation(
            _new_operation_id(op_counter),
            "status",
            "insert",
            None,
            status["resolvedStatus"] == "completed",
            "PROGRESS_STATUS_COMPAT",
        ))

    if base_ops:
        operations.extend(base_ops)
        groups["BASE_SCHEMA"] = _group(
            "BASE_SCHEMA", "READY", base_ops, selected_groups
        )

    if status["action"] == "conflict":
        groups["CONFLICTING_LEGACY_STATUS"] = _group(
            "CONFLICTING_LEGACY_STATUS",
            "REVIEW_REQUIRED",
            [],
            selected_groups,
            reason="STATUS_FIELDS_CONFLICT",
        )
    elif status["action"] in {"invalid_status_review", "keep_unknown"}:
        groups["CONFLICTING_LEGACY_STATUS"] = _group(
            "CONFLICTING_LEGACY_STATUS",
            "REVIEW_REQUIRED",
            [],
            selected_groups,
            reason=(
                "INVALID_LEGACY_STATUS"
                if status["action"] == "invalid_status_review"
                else "UNKNOWN_PROGRESS_STATUS"
            ),
        )

    score_ops: list[dict] = []
    if score["action"] == "copy" and score.get("proposedField"):
        current = fm.get(score["proposedField"])
        desired = score.get("proposedValue")
        if not _same_rating_value(current, desired):
            score_ops.append(_operation(
                _new_operation_id(op_counter),
                score["proposedField"],
                "set" if current is not None else "insert",
                current,
                desired,
                "L1_SCORE_PROMOTION",
            ))
    if score_ops:
        operations.extend(score_ops)
        groups["SCORE_PROMOTION"] = _group(
            "SCORE_PROMOTION", "READY", score_ops, selected_groups
        )
    elif score["present"] and score["action"] != "copy":
        groups["SCORE_REVIEW"] = _group(
            "SCORE_REVIEW",
            "REVIEW_REQUIRED",
            [],
            selected_groups,
            reason="%s_%s" % (score["level"], score["evidenceType"]),
        )

    desired_progress = None
    progress_op = next(
        (op for op in operations if op["field"] == "progressStatus"), None
    )
    if progress_op:
        desired_progress = progress_op["after"]
    elif status["progressStatusValid"]:
        desired_progress = status["resolvedStatus"]
    elif status["legacyStatusValid"] and status["action"] == "set_progressStatus":
        desired_progress = status["resolvedStatus"]

    callout_op = None
    callout_warning = None
    if status["action"] == "conflict":
        callout_warning = "STATUS_FIELDS_CONFLICT"
    elif desired_progress:
        try:
            text, _, _ = decode_note(raw)
        except Exception:
            callout_warning = "INVALID_UTF8"
        else:
            updated, callout_warning = sync_status_callout(
                text, media_type, desired_progress
            )
            if callout_warning is None and updated != text:
                callout_op = {
                    "operationId": _new_operation_id(op_counter),
                    "field": "statusCallout",
                    "operation": "callout_set",
                    "before": None,
                    "after": desired_progress,
                    "reason": "SYNC_STATUS_CALLOUT",
                }
                operations.append(callout_op)
                groups["CALLOUT_STATUS"] = _group(
                    "CALLOUT_STATUS", "READY", [callout_op], selected_groups
                )
    if callout_warning and "CALLOUT_STATUS" not in groups:
        groups["CALLOUT_STATUS"] = _group(
            "CALLOUT_STATUS",
            "REVIEW_REQUIRED",
            [],
            selected_groups,
            reason=callout_warning,
        )

    executable_groups = [
        group for group in groups.values()
        if group["status"] == "READY" and group["selected"]
    ]
    executable_ids = {
        operation_id
        for group in executable_groups
        for operation_id in group["operationIds"]
    }
    executable_ops = [
        operation for operation in operations
        if operation["operationId"] in executable_ids
    ]
    proposed_raw = raw
    if executable_ops:
        proposed_raw = apply_operations(raw, executable_ops, media_type)
        parsed = parse_frontmatter(proposed_raw)
        if not parsed.ok:
            raise MigrationError("生成候选笔记后 YAML 无法解析")

    review_groups = [
        group for group in groups.values()
        if group["status"] == "REVIEW_REQUIRED"
    ]
    if executable_ops and review_groups:
        migration_status = "READY_WITH_REVIEW"
    elif executable_ops:
        migration_status = "READY"
    elif review_groups:
        migration_status = "REVIEW_REQUIRED"
    else:
        migration_status = "UNCHANGED"

    result = {
        "relativePath": scan_item["relativePath"],
        "mediaType": media_type,
        "originalSha256": scan_item["originalSha256"],
        "originalSize": scan_item["originalSize"],
        "proposedSha256": sha256_bytes(proposed_raw) if executable_ops else None,
        "proposedMediaId": proposed_media_id,
        "operationGroups": list(groups.values()),
        "proposedChanges": operations,
        "scoreEvidence": score,
        "statusEvidence": status,
        "warnings": list(dict.fromkeys(warnings)),
        "migrationStatus": migration_status,
        "skippedReason": None,
        "preMigrationSha256": None,
        "postMigrationSha256": None,
        "backupPath": None,
        "migrationTimestamp": None,
    }
    return result


def _group(
    group_type: str,
    status: str,
    operations: list[dict],
    selected_groups: set[str],
    *,
    reason: str | None = None,
) -> dict:
    return {
        "groupType": group_type,
        "selected": group_type in selected_groups and status == "READY",
        "status": status,
        "reason": reason,
        "operationIds": [op["operationId"] for op in operations],
        "runStatus": "NOT_STARTED",
        "attemptedAt": None,
        "resultDetail": None,
    }


def apply_operations(raw: bytes, operations: list[dict], media_type: str) -> bytes:
    updates = {}
    callout_progress = None
    for operation in operations:
        kind = operation.get("operation")
        field = operation.get("field")
        if kind in {"insert", "set"}:
            if field in {"schemaVersion", "mediaId", "progressStatus", "status",
                         "doubanScore", "metacriticScore"}:
                updates[field] = operation.get("after")
            else:
                raise MigrationError("操作组包含未知字段 %s" % field)
        elif kind == "callout_set":
            callout_progress = operation.get("after")
        elif kind == "remove":
            raise MigrationError("迁移禁止删除字段")
        else:
            raise MigrationError("未知迁移操作 %s" % kind)

    result = patch_frontmatter(raw, updates)
    if not result.ok:
        message = "；".join(item.message for item in result.diagnostics)
        raise MigrationError("Frontmatter Patch 失败：%s" % message)
    candidate = result.data
    if callout_progress is not None:
        text, newline, bom = decode_note(candidate)
        text, warning = sync_status_callout(text, media_type, callout_progress)
        if warning:
            raise MigrationError("Callout 同步失败：%s" % warning)
        candidate = encode_note(text, newline, bom)
    return candidate


def plan_checksum(plan: dict) -> str:
    payload = copy.deepcopy(plan)
    payload.pop("planChecksum", None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_tag(encoded)


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(".%s.%s.tmp" % (path.name, uuid.uuid4().hex))
    try:
        with open(temp, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: dict) -> None:
    raw = (
        json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, raw)


def _migration_root(data_root: Path) -> Path:
    return data_root / "migration"


def _state_path(data_root: Path, plan_id: str) -> Path:
    return _migration_root(data_root) / "states" / ("state-%s.json" % plan_id)


def _journal_path(data_root: Path, plan_id: str) -> Path:
    return _migration_root(data_root) / "apply-journals" / ("%s.jsonl" % plan_id)


def _render_preview(plan: dict) -> str:
    lines = [
        "# 迁移预览报告",
        "",
        "- Plan ID：`%s`" % plan["planId"],
        "- Vault：`%s`" % plan["vaultRoot"],
        "- 文件数：%d" % len(plan["files"]),
        "- 计数：`%s`" % json.dumps(plan["counters"], ensure_ascii=False, sort_keys=True),
        "",
    ]
    for item in plan["files"]:
        lines.append("## %s" % item["relativePath"])
        lines.append("")
        lines.append("- 状态：`%s`" % item["migrationStatus"])
        lines.append("- 评分证据：`%s / %s`" % (
            item["scoreEvidence"]["level"],
            item["scoreEvidence"]["evidenceType"],
        ))
        if item["operationGroups"]:
            for group in item["operationGroups"]:
                lines.append(
                    "- 操作组 `%s`：`%s`，selected=%s"
                    % (group["groupType"], group["status"], str(group["selected"]).lower())
                )
        if item["proposedChanges"]:
            for operation in item["proposedChanges"]:
                lines.append(
                    "- `%s` `%s`：%r → %r"
                    % (
                        operation["operation"],
                        operation["field"],
                        operation["before"],
                        operation["after"],
                    )
                )
        if item["warnings"]:
            lines.append("- 警告：`%s`" % ", ".join(item["warnings"]))
        lines.append("")
    return "\n".join(lines) + "\n"


def create_dry_run(
    vault: Path,
    *,
    data_root: Path | None = None,
    evidence_path: Path | None = None,
    selected_groups: set[str] | None = None,
) -> dict:
    vault = vault.resolve()
    data_root = (data_root or (vault / ".media-app")).resolve()
    selected = set(selected_groups or DEFAULT_GROUPS)
    unknown = selected - ALL_GROUPS
    if unknown:
        raise MigrationError("未知操作组：%s" % ", ".join(sorted(unknown)))
    scan = scan_media(vault, evidence_path=evidence_path)
    plan_id = str(uuid.uuid4())
    used_ids = {
        (item.get("identity") or {}).get("mediaId", "").lower()
        for item in scan["_items"]
        if (item.get("identity") or {}).get("mediaIdValid")
    }
    plan_files = []
    operation_counter = [0]
    for item in scan["_items"]:
        if item.get("migrationStatus") == "SKIPPED":
            plan_files.append({
                "relativePath": item["relativePath"],
                "mediaType": item.get("mediaType"),
                "originalSha256": item["originalSha256"],
                "originalSize": item["originalSize"],
                "proposedSha256": None,
                "proposedMediaId": None,
                "operationGroups": [],
                "proposedChanges": [],
                "scoreEvidence": item.get("scoreEvidence") or {},
                "statusEvidence": item.get("statusEvidence") or {},
                "warnings": item.get("warnings") or [],
                "migrationStatus": "SKIPPED",
                "skippedReason": item.get("skippedReason"),
                "preMigrationSha256": None,
                "postMigrationSha256": None,
                "backupPath": None,
                "migrationTimestamp": None,
            })
            continue
        proposed_id = (item.get("identity") or {}).get("mediaId")
        if not proposed_id:
            while True:
                proposed_id = str(uuid.uuid4())
                if proposed_id.lower() not in used_ids:
                    break
            used_ids.add(proposed_id.lower())
        plan_files.append(_build_file_plan(
            item,
            proposed_media_id=proposed_id,
            selected_groups=selected,
            operation_counter=operation_counter,
        ))

    counters = Counter(item["migrationStatus"] for item in plan_files)
    plan = {
        "planFormatVersion": PLAN_FORMAT_VERSION,
        "planId": plan_id,
        "planState": "FINALIZED",
        "createdAt": utc_now(),
        "appVersion": "2.0.0",
        "schemaTarget": SCHEMA_TARGET,
        "vaultRoot": str(vault),
        "vaultRootNormalized": normalized_vault_root(vault),
        "policy": {
            "selectedGroups": sorted(selected),
            "preserveLegacyScore": True,
            "automaticScoreMigration": "L1_ONLY",
            "preserveUnknownFields": True,
            "preserveBodyExceptStatusCallout": True,
            "skipDuplicateKeys": True,
            "requireObsidianPaused": True,
            "runtimeStateFile": True,
        },
        "scanSummary": scan["summary"],
        "files": plan_files,
        "counters": {
            key: counters.get(key, 0)
            for key in (
                "READY",
                "READY_WITH_REVIEW",
                "REVIEW_REQUIRED",
                "SKIPPED",
                "UNCHANGED",
            )
        },
        "planChecksum": None,
    }
    plan["planChecksum"] = plan_checksum(plan)
    _verify_plan_shape(plan, vault, require_finalized=True)

    plan_path = _migration_root(data_root) / "plans" / ("plan-%s.json" % plan_id)
    report_path = _migration_root(data_root) / "reports" / ("preview-%s.json" % plan_id)
    markdown_path = _migration_root(data_root) / "reports" / ("preview-%s.md" % plan_id)
    state_path = _state_path(data_root, plan_id)
    _atomic_write_json(plan_path, plan)
    _atomic_write_json(report_path, _public_plan(plan) | {"scan": _public_scan(scan)})
    _atomic_write_bytes(markdown_path, _render_preview(plan).encode("utf-8"))
    _atomic_write_json(state_path, {
        "planId": plan_id,
        "planState": "FINALIZED",
        "createdAt": plan["createdAt"],
        "updatedAt": utc_now(),
        "fileResults": {},
        "counters": plan["counters"],
    })
    return {
        "plan": plan,
        "planPath": str(plan_path),
        "previewJsonPath": str(report_path),
        "previewMarkdownPath": str(markdown_path),
        "statePath": str(state_path),
    }


def _public_plan(plan: dict) -> dict:
    return copy.deepcopy(plan)


def load_plan(path: Path) -> dict:
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PlanValidationError("Plan 无法严格解析") from exc
    if not isinstance(plan, dict):
        raise PlanValidationError("Plan 顶层必须是对象")
    return plan


def _verify_plan_shape(
    plan: dict,
    vault: Path,
    *,
    require_finalized: bool,
) -> None:
    expected = plan_checksum(plan)
    if plan.get("planChecksum") != expected:
        raise PlanValidationError("Plan checksum 不匹配")
    if plan.get("planFormatVersion") != PLAN_FORMAT_VERSION:
        raise PlanValidationError("Plan 格式版本不受支持")
    plan_id = str(plan.get("planId") or "")
    if not _UUID4_RE.fullmatch(plan_id):
        raise PlanValidationError("planId 不是合法 UUIDv4")
    if require_finalized and plan.get("planState") != "FINALIZED":
        raise PlanValidationError("Apply 只接受 FINALIZED Plan")
    if plan.get("schemaTarget") != SCHEMA_TARGET:
        raise PlanValidationError("schemaTarget 必须为 2")
    if normalized_vault_root(Path(plan.get("vaultRoot") or "")) != normalized_vault_root(vault):
        raise PlanValidationError("Plan 与当前 Vault 不匹配")
    files = plan.get("files")
    if not isinstance(files, list):
        raise PlanValidationError("Plan files 必须是数组")
    paths = set()
    generated_ids: dict[str, str] = {}
    operation_ids = set()
    for item in files:
        if not isinstance(item, dict):
            raise PlanValidationError("文件计划项格式错误")
        relative = item.get("relativePath")
        target = _target_from_relative(vault, relative)
        if relative in paths:
            raise PlanValidationError("Plan 含重复文件路径")
        paths.add(relative)
        if not target.is_file():
            raise PlanValidationError("Plan 目标文件不存在")
        if not isinstance(item.get("originalSize"), int) or item["originalSize"] < 0:
            raise PlanValidationError("originalSize 非法")
        if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("originalSha256"))):
            raise PlanValidationError("originalSha256 格式非法")
        proposed_id = item.get("proposedMediaId")
        if proposed_id:
            if not _UUID4_RE.fullmatch(proposed_id):
                raise PlanValidationError("proposedMediaId 非法")
            key = proposed_id.lower()
            if key in generated_ids:
                raise PlanValidationError("proposedMediaId 重复")
            generated_ids[key] = relative
        groups = item.get("operationGroups")
        if not isinstance(groups, list):
            raise PlanValidationError("operationGroups 必须是数组")
        item_operation_ids = set()
        for group in groups:
            if not isinstance(group, dict) or group.get("groupType") not in ALL_GROUPS:
                raise PlanValidationError("操作组类型非法")
            if group.get("status") not in {
                "READY", "REVIEW_REQUIRED", "EXCLUDED",
            }:
                raise PlanValidationError("操作组状态非法")
            for operation_id in group.get("operationIds") or []:
                if operation_id in operation_ids:
                    raise PlanValidationError("operationId 被重复引用")
                operation_ids.add(operation_id)
                item_operation_ids.add(operation_id)
        changes = item.get("proposedChanges")
        if not isinstance(changes, list):
            raise PlanValidationError("proposedChanges 必须是数组")
        for operation in changes:
            if operation.get("operationId") not in item_operation_ids:
                raise PlanValidationError("操作未被操作组引用")
            if operation.get("operation") not in {"insert", "set", "callout_set"}:
                raise PlanValidationError("操作类型非法")
        original = target.read_bytes()
        if len(original) != item["originalSize"] or sha256_bytes(original) != item["originalSha256"]:
            continue
        executable_ids = {
            operation_id
            for group in groups
            if group.get("status") == "READY" and group.get("selected")
            for operation_id in group.get("operationIds") or []
        }
        executable = [
            operation for operation in changes
            if operation.get("operationId") in executable_ids
        ]
        if executable:
            candidate = apply_operations(original, executable, item["mediaType"])
            if sha256_bytes(candidate) != item.get("proposedSha256"):
                raise PlanValidationError("proposedSha256 无法由 Plan 重算")


def verify_plan(path: Path, vault: Path) -> dict:
    plan = load_plan(path)
    _verify_plan_shape(plan, vault.resolve(), require_finalized=True)
    return plan


def _append_journal(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)


class MigrationLock:
    def __init__(self, data_root: Path, plan_id: str):
        self.path = _migration_root(data_root) / "apply.lock"
        self.plan_id = plan_id
        self.fd: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(
                str(self.path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError as exc:
            raise MigrationError("迁移锁已存在；请人工检查后删除 apply.lock") from exc
        payload = {
            "pid": os.getpid(),
            "planId": self.plan_id,
            "createdAt": utc_now(),
        }
        os.write(self.fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        os.fsync(self.fd)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
            if current.get("planId") == self.plan_id:
                self.path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass


def _create_snapshot(
    vault: Path,
    data_root: Path,
    plan: dict,
) -> Path:
    plan_id = plan["planId"]
    root = data_root / "backups" / "migrations"
    target = root / plan_id
    if target.exists():
        raise MigrationError("迁移快照目录已存在")
    root.mkdir(parents=True, exist_ok=True)
    temp = root / (".%s.tmp" % plan_id)
    temp.mkdir(parents=False, exist_ok=False)
    manifest_files = []
    try:
        for item in plan["files"]:
            if not _executable_operations(item):
                continue
            source = _target_from_relative(vault, item["relativePath"])
            raw = source.read_bytes()
            if sha256_bytes(raw) != item["originalSha256"]:
                # The file remains unchanged; Apply will mark it CONFLICT and
                # continue with other files. It does not belong in the snapshot.
                continue
            destination = temp / Path(item["relativePath"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            with open(destination, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            manifest_files.append({
                "relativePath": item["relativePath"],
                "bytes": len(raw),
                "sha256": sha256_bytes(raw),
            })
        manifest = {
            "planId": plan_id,
            "createdAt": utc_now(),
            "sourceAppVersion": plan["appVersion"],
            "vaultRoot": plan["vaultRoot"],
            "files": manifest_files,
        }
        manifest_path = temp / "manifest.json"
        with open(manifest_path, "wb") as handle:
            handle.write(
                (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            )
            handle.flush()
            os.fsync(handle.fileno())
        for record in manifest_files:
            copied = (temp / Path(record["relativePath"])).read_bytes()
            if len(copied) != record["bytes"] or sha256_bytes(copied) != record["sha256"]:
                raise MigrationError("快照回读校验失败")
        os.replace(temp, target)
        return target
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def _verify_snapshot(snapshot: Path, plan: dict) -> dict:
    manifest_path = snapshot / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError("迁移快照 manifest 无法读取") from exc
    if manifest.get("planId") != plan["planId"]:
        raise MigrationError("迁移快照与 Plan 不匹配")
    for record in manifest.get("files") or []:
        relative = record.get("relativePath")
        backup = snapshot / Path(relative)
        if not backup.is_file():
            raise MigrationError("迁移快照缺少文件")
        raw = backup.read_bytes()
        if len(raw) != record.get("bytes") or sha256_bytes(raw) != record.get("sha256"):
            raise MigrationError("迁移快照哈希校验失败")
    return manifest


def _read_state(path: Path) -> dict:
    if not path.exists():
        raise MigrationError("Plan 缺少运行状态文件")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError("Plan 运行状态无法读取") from exc
    if state.get("planState") not in APPLY_STATES:
        raise MigrationError("Plan 运行状态非法")
    return state


def _write_state(path: Path, state: dict) -> None:
    state["updatedAt"] = utc_now()
    _atomic_write_json(path, state)


def _executable_operations(item: dict) -> list[dict]:
    ids = {
        operation_id
        for group in item.get("operationGroups") or []
        if group.get("status") == "READY" and group.get("selected")
        for operation_id in group.get("operationIds") or []
    }
    return [
        operation
        for operation in item.get("proposedChanges") or []
        if operation.get("operationId") in ids
    ]


def _verify_temp(path: Path, expected_sha: str, media_type: str) -> None:
    raw = path.read_bytes()
    if sha256_bytes(raw) != expected_sha:
        raise MigrationError("临时文件哈希不匹配")
    parsed = parse_frontmatter(raw)
    if not parsed.ok:
        raise MigrationError("临时文件 YAML 无法解析")
    if str((parsed.fields.get("type").value if parsed.fields.get("type") else "")).lower() not in MEDIA_TYPES | {""}:
        raise MigrationError("临时文件媒体类型非法")
    if media_type not in MEDIA_TYPES:
        raise MigrationError("计划媒体类型非法")


def _guarded_replace(
    target: Path,
    raw: bytes,
    *,
    expected_sha: str,
    proposed_sha: str,
    original: bytes,
    media_type: str,
    before_replace: Callable[[Path], None] | None = None,
    after_replace: Callable[[Path], None] | None = None,
) -> None:
    temp = target.with_name(".%s.%s.tmp" % (target.name, uuid.uuid4().hex))
    try:
        with open(temp, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _verify_temp(temp, proposed_sha, media_type)
        if before_replace:
            before_replace(target)
        current = target.read_bytes()
        if sha256_bytes(current) != expected_sha:
            raise MigrationConflict("最终替换前检测到外部修改")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)

    if after_replace:
        after_replace(target)
    try:
        final = target.read_bytes()
        if sha256_bytes(final) != proposed_sha:
            raise MigrationError("替换后最终哈希不匹配")
        if not parse_frontmatter(final).ok:
            raise MigrationError("替换后 YAML 无法解析")
    except Exception as verify_error:
        try:
            current = target.read_bytes()
        except OSError as exc:
            raise RestoreConflict("替换后目标消失") from exc
        if sha256_bytes(current) != proposed_sha:
            raise RestoreConflict("替换后目标被外部修改，禁止自动覆盖") from verify_error
        restore = target.with_name(".%s.restore.%s.tmp" % (target.name, uuid.uuid4().hex))
        try:
            with open(restore, "wb") as handle:
                handle.write(original)
                handle.flush()
                os.fsync(handle.fileno())
            if sha256_bytes(target.read_bytes()) != proposed_sha:
                raise RestoreConflict("恢复前目标再次变化")
            os.replace(restore, target)
            if target.read_bytes() != original:
                raise MigrationError("恢复后校验失败")
        finally:
            restore.unlink(missing_ok=True)
        raise verify_error


def _mark_groups(item: dict, selected_status: str, detail: str) -> None:
    now = utc_now()
    for group in item.get("operationGroups") or []:
        if group.get("selected") and group.get("status") == "READY":
            group["runStatus"] = selected_status
            group["attemptedAt"] = now
            group["resultDetail"] = detail
        elif group.get("status") == "REVIEW_REQUIRED":
            group["runStatus"] = "SKIPPED"
        else:
            group["runStatus"] = "SKIPPED"


def _group_results(item: dict) -> dict:
    return {
        group["groupType"]: {
            "selected": group.get("selected", False),
            "planStatus": group.get("status"),
            "runStatus": group.get("runStatus", "NOT_STARTED"),
            "attemptedAt": group.get("attemptedAt"),
            "resultDetail": group.get("resultDetail"),
        }
        for group in item.get("operationGroups") or []
    }


def _store_group_results(state: dict, item: dict) -> None:
    state.setdefault("groupResults", {})[item["relativePath"]] = _group_results(item)


def _set_stored_group_status(
    state: dict,
    item: dict,
    run_status: str,
    detail: str,
) -> None:
    groups = state.setdefault("groupResults", {}).setdefault(
        item["relativePath"], {}
    )
    now = utc_now()
    for group in item.get("operationGroups") or []:
        current = groups.setdefault(group["groupType"], _group_results(item)[group["groupType"]])
        if current.get("selected") and current.get("planStatus") == "READY":
            current["runStatus"] = run_status
            current["attemptedAt"] = now
            current["resultDetail"] = detail


def apply_plan(
    vault: Path,
    plan_path: Path,
    *,
    data_root: Path | None = None,
    confirmation: str,
    resume: bool = False,
    hooks: dict[str, Callable] | None = None,
) -> dict:
    vault = vault.resolve()
    data_root = (data_root or (vault / ".media-app")).resolve()
    plan = verify_plan(plan_path, vault)
    expected_confirmation = "APPLY %s" % plan["planId"]
    if confirmation != expected_confirmation:
        raise MigrationError("确认文本不匹配，需要 %s" % expected_confirmation)
    state_path = _state_path(data_root, plan["planId"])
    state = _read_state(state_path)
    if state["planState"] == "APPLIED":
        raise MigrationError("该 Plan 已应用，禁止重复 Apply")
    if state["planState"] in {"ROLLED_BACK", "ROLLED_BACK_WITH_CONFLICTS"}:
        raise MigrationError("该 Plan 已回滚，禁止再次 Apply")
    if state["planState"] == "APPLYING" and not resume:
        raise MigrationError("Plan 处于 APPLYING，只能使用 --resume 显式恢复")
    if state["planState"] == "FINALIZED" and resume:
        raise MigrationError("FINALIZED Plan 无需 resume")

    journal_path = _journal_path(data_root, plan["planId"])
    with MigrationLock(data_root, plan["planId"]):
        if state["planState"] == "APPLYING" and resume:
            snapshot = Path(state.get("snapshotPath") or "")
            snapshot_manifest = _verify_snapshot(snapshot, plan)
        else:
            snapshot = _create_snapshot(vault, data_root, plan)
            snapshot_manifest = _verify_snapshot(snapshot, plan)
        snapshot_paths = {
            record["relativePath"]
            for record in snapshot_manifest.get("files") or []
        }
        state["planState"] = "APPLYING"
        state["snapshotPath"] = str(snapshot)
        state["startedAt"] = state.get("startedAt") or utc_now()
        state.setdefault("groupResults", {})
        _write_state(state_path, state)
        _append_journal(journal_path, {
            "event": "apply_started",
            "timestamp": utc_now(),
            "planId": plan["planId"],
            "snapshotPath": str(snapshot),
            "resume": resume,
        })

        for item in plan["files"]:
            executable = _executable_operations(item)
            if not executable:
                state["fileResults"][item["relativePath"]] = "UNCHANGED"
                _mark_groups(item, "SKIPPED", "NO_SELECTED_OPERATIONS")
                _store_group_results(state, item)
                continue
            target = _target_from_relative(vault, item["relativePath"])
            current = target.read_bytes()
            current_sha = sha256_bytes(current)
            if current_sha == item.get("proposedSha256"):
                state["fileResults"][item["relativePath"]] = "APPLIED"
                _mark_groups(item, "APPLIED", "ALREADY_PRESENT_DURING_RESUME")
                _store_group_results(state, item)
                _append_journal(journal_path, {
                    "event": "file_reconciled",
                    "timestamp": utc_now(),
                    "relativePath": item["relativePath"],
                    "runStatus": "APPLIED",
                })
                _write_state(state_path, state)
                continue
            if current_sha != item["originalSha256"]:
                state["fileResults"][item["relativePath"]] = "CONFLICT"
                _mark_groups(item, "CONFLICT", "SOURCE_CHANGED_BEFORE_APPLY")
                _store_group_results(state, item)
                _append_journal(journal_path, {
                    "event": "file_result",
                    "timestamp": utc_now(),
                    "relativePath": item["relativePath"],
                    "runStatus": "CONFLICT",
                    "reason": "SOURCE_CHANGED_BEFORE_APPLY",
                })
                _write_state(state_path, state)
                continue
            if item["relativePath"] not in snapshot_paths:
                state["fileResults"][item["relativePath"]] = "CONFLICT"
                _mark_groups(item, "CONFLICT", "SNAPSHOT_MISSING")
                _store_group_results(state, item)
                _append_journal(journal_path, {
                    "event": "file_result",
                    "timestamp": utc_now(),
                    "relativePath": item["relativePath"],
                    "runStatus": "CONFLICT",
                    "reason": "SNAPSHOT_MISSING",
                })
                _write_state(state_path, state)
                continue
            try:
                candidate = apply_operations(current, executable, item["mediaType"])
                if sha256_bytes(candidate) != item["proposedSha256"]:
                    raise MigrationError("Patch 结果与 Plan 哈希不一致")
                _guarded_replace(
                    target,
                    candidate,
                    expected_sha=item["originalSha256"],
                    proposed_sha=item["proposedSha256"],
                    original=current,
                    media_type=item["mediaType"],
                    before_replace=(hooks or {}).get("before_replace"),
                    after_replace=(hooks or {}).get("after_replace"),
                )
            except RestoreConflict as exc:
                state["fileResults"][item["relativePath"]] = "RESTORE_CONFLICT"
                _mark_groups(item, "CONFLICT", str(exc))
                _store_group_results(state, item)
                _append_journal(journal_path, {
                    "event": "file_result",
                    "timestamp": utc_now(),
                    "relativePath": item["relativePath"],
                    "runStatus": "RESTORE_CONFLICT",
                    "reason": str(exc),
                })
            except MigrationConflict as exc:
                state["fileResults"][item["relativePath"]] = "CONFLICT"
                _mark_groups(item, "CONFLICT", str(exc))
                _store_group_results(state, item)
                _append_journal(journal_path, {
                    "event": "file_result",
                    "timestamp": utc_now(),
                    "relativePath": item["relativePath"],
                    "runStatus": "CONFLICT",
                    "reason": str(exc),
                })
            except Exception as exc:
                state["fileResults"][item["relativePath"]] = "ERROR"
                _mark_groups(item, "ERROR", "%s:%s" % (type(exc).__name__, exc))
                _store_group_results(state, item)
                _append_journal(journal_path, {
                    "event": "file_result",
                    "timestamp": utc_now(),
                    "relativePath": item["relativePath"],
                    "runStatus": "ERROR",
                    "reason": "%s:%s" % (type(exc).__name__, exc),
                })
            else:
                item["preMigrationSha256"] = item["originalSha256"]
                item["postMigrationSha256"] = item["proposedSha256"]
                item["backupPath"] = (
                    Path("backups") / "migrations" / plan["planId"] / Path(item["relativePath"])
                ).as_posix()
                item["migrationTimestamp"] = utc_now()
                state["fileResults"][item["relativePath"]] = "APPLIED"
                _mark_groups(item, "APPLIED", "VERIFIED")
                _store_group_results(state, item)
                _append_journal(journal_path, {
                    "event": "file_result",
                    "timestamp": utc_now(),
                    "relativePath": item["relativePath"],
                    "runStatus": "APPLIED",
                    "postSha256": item["proposedSha256"],
                })
            _write_state(state_path, state)

        post_audit = audit_plan_files(vault, plan)
        result_values = Counter(state["fileResults"].values())
        if (
            not post_audit["ok"]
            or result_values.get("CONFLICT", 0)
            or result_values.get("ERROR", 0)
            or result_values.get("RESTORE_CONFLICT", 0)
        ):
            state["planState"] = "APPLYING"
            state["postAudit"] = post_audit
            state["applyCompletedAt"] = utc_now()
        else:
            state["planState"] = "APPLIED"
            state["postAudit"] = post_audit
            state["applyCompletedAt"] = utc_now()
        _write_state(state_path, state)
        _append_journal(journal_path, {
            "event": "apply_finished",
            "timestamp": utc_now(),
            "planId": plan["planId"],
            "planState": state["planState"],
            "postAudit": post_audit,
        })
        return {
            "planId": plan["planId"],
            "planState": state["planState"],
            "snapshotPath": str(snapshot),
            "journalPath": str(journal_path),
            "statePath": str(state_path),
            "fileResults": state["fileResults"],
            "groupResults": state.get("groupResults", {}),
            "postAudit": post_audit,
        }


def audit_plan_files(vault: Path, plan: dict) -> dict:
    errors = []
    checked = 0
    id_counts: dict[str, int] = {}
    current_scan = scan_media(vault)
    for scanned in current_scan["_items"]:
        identity = scanned.get("identity") or {}
        if identity.get("mediaIdValid") and identity.get("mediaId"):
            key = identity["mediaId"].lower()
            id_counts[key] = id_counts.get(key, 0) + 1
    applied_ids = []
    for item in plan["files"]:
        if not _executable_operations(item):
            continue
        target = _target_from_relative(vault, item["relativePath"])
        raw = target.read_bytes()
        checked += 1
        if sha256_bytes(raw) != item["proposedSha256"]:
            errors.append("%s:HASH_MISMATCH" % item["relativePath"])
            continue
        parsed = parse_frontmatter(raw)
        if not parsed.ok:
            errors.append("%s:YAML_INVALID" % item["relativePath"])
            continue
        fm = {key: field.value for key, field in parsed.fields.items()}
        if fm.get("schemaVersion") != 2:
            errors.append("%s:SCHEMA_VERSION" % item["relativePath"])
        media_id = str(fm.get("mediaId") or "")
        if not _UUID4_RE.fullmatch(media_id):
            errors.append("%s:MEDIA_ID" % item["relativePath"])
        else:
            applied_ids.append((item["relativePath"], media_id.lower()))
    for relative, media_id in applied_ids:
        if id_counts.get(media_id, 0) != 1:
            errors.append("%s:DUPLICATE_MEDIA_ID" % relative)
    deduplicated_errors = list(dict.fromkeys(errors))
    return {
        "ok": not deduplicated_errors,
        "checkedFiles": checked,
        "errors": deduplicated_errors,
        "completedAt": utc_now(),
    }


def rollback_plan(
    vault: Path,
    plan_path: Path,
    *,
    data_root: Path | None = None,
    confirmation: str,
) -> dict:
    vault = vault.resolve()
    data_root = (data_root or (vault / ".media-app")).resolve()
    plan = verify_plan(plan_path, vault)
    expected = "ROLLBACK %s" % plan["planId"]
    if confirmation != expected:
        raise MigrationError("确认文本不匹配，需要 %s" % expected)
    state_path = _state_path(data_root, plan["planId"])
    state = _read_state(state_path)
    if state["planState"] != "APPLIED":
        raise MigrationError("只有完整 APPLIED Plan 可执行全局回滚")
    snapshot = Path(state.get("snapshotPath") or "")
    manifest_path = snapshot / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError("迁移快照 manifest 无法读取") from exc
    manifest_files = {
        record["relativePath"]: record for record in manifest.get("files", [])
    }
    journal_path = _journal_path(data_root, plan["planId"])

    with MigrationLock(data_root, plan["planId"]):
        state["planState"] = "APPLYING"
        state["rollbackStartedAt"] = utc_now()
        _write_state(state_path, state)
        _append_journal(journal_path, {
            "event": "rollback_started",
            "timestamp": utc_now(),
            "planId": plan["planId"],
        })
        for item in plan["files"]:
            if state.get("fileResults", {}).get(item["relativePath"]) != "APPLIED":
                continue
            record = manifest_files.get(item["relativePath"])
            if not record:
                state["fileResults"][item["relativePath"]] = "ERROR"
                _append_journal(journal_path, {
                    "event": "rollback_result",
                    "timestamp": utc_now(),
                    "relativePath": item["relativePath"],
                    "runStatus": "ERROR",
                    "reason": "SNAPSHOT_RECORD_MISSING",
                })
                _write_state(state_path, state)
                continue
            target = _target_from_relative(vault, item["relativePath"])
            current = target.read_bytes()
            if sha256_bytes(current) != item["proposedSha256"]:
                state["fileResults"][item["relativePath"]] = "RESTORE_CONFLICT"
                _append_journal(journal_path, {
                    "event": "rollback_result",
                    "timestamp": utc_now(),
                    "relativePath": item["relativePath"],
                    "runStatus": "RESTORE_CONFLICT",
                })
                _write_state(state_path, state)
                continue
            backup = snapshot / Path(item["relativePath"])
            try:
                original = backup.read_bytes()
                if (
                    len(original) != record["bytes"]
                    or sha256_bytes(original) != record["sha256"]
                    or sha256_bytes(original) != item["originalSha256"]
                ):
                    raise MigrationError("快照内容与 Plan 原始哈希不一致")
                _guarded_replace(
                    target,
                    original,
                    expected_sha=item["proposedSha256"],
                    proposed_sha=item["originalSha256"],
                    original=current,
                    media_type=item["mediaType"],
                )
            except RestoreConflict:
                state["fileResults"][item["relativePath"]] = "RESTORE_CONFLICT"
                result_status = "RESTORE_CONFLICT"
            except Exception:
                state["fileResults"][item["relativePath"]] = "ERROR"
                result_status = "ERROR"
            else:
                state["fileResults"][item["relativePath"]] = "RESTORED"
                result_status = "RESTORED"
            _set_stored_group_status(
                state,
                item,
                result_status,
                result_status,
            )
            _append_journal(journal_path, {
                "event": "rollback_result",
                "timestamp": utc_now(),
                "relativePath": item["relativePath"],
                "runStatus": result_status,
            })
            _write_state(state_path, state)

        results = Counter(state["fileResults"].values())
        conflicts = results.get("RESTORE_CONFLICT", 0) + results.get("ERROR", 0)
        state["planState"] = (
            "ROLLED_BACK_WITH_CONFLICTS" if conflicts else "ROLLED_BACK"
        )
        state["rollbackCompletedAt"] = utc_now()
        _write_state(state_path, state)
        _append_journal(journal_path, {
            "event": "rollback_finished",
            "timestamp": utc_now(),
            "planId": plan["planId"],
            "planState": state["planState"],
            "fileResults": state["fileResults"],
        })
        return {
            "planId": plan["planId"],
            "planState": state["planState"],
            "snapshotPath": str(snapshot),
            "journalPath": str(journal_path),
            "statePath": str(state_path),
            "fileResults": state["fileResults"],
        }

