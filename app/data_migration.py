from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


FORMAT_VERSION = 1
KNOWN_DATA_ROOTS = ("02-电影阅读", "媒体库", "attachments", ".media-app")
TRANSIENT_NAMES = {"apply.lock", ".DS_Store", "Thumbs.db"}
POLICIES = {"skip", "newer", "force"}


class DataMigrationError(RuntimeError):
    pass


class DataMigrationApplyError(DataMigrationError):
    def __init__(self, message: str, report: dict):
        self.report = report
        super().__init__(message)


@dataclass(frozen=True)
class MigrationOptions:
    policy: str = "newer"
    include_all: bool = False

    def validate(self) -> None:
        if self.policy not in POLICIES:
            raise DataMigrationError("未知的覆盖策略：%s" % self.policy)


def normalize_data_root(path: Path | str) -> Path:
    root = Path(path).expanduser().resolve()
    package_data = root / "数据"
    if package_data.is_dir() and (
        (root / "程序").is_dir()
        or (root / "双击启动影音库.exe").is_file()
        or (root / "打开本地网页版.exe").is_file()
    ):
        return package_data
    return root


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_transient(path: Path) -> bool:
    name = path.name
    lowered = name.casefold()
    return (
        name in TRANSIENT_NAMES
        or lowered.endswith((".tmp", ".partial", ".migrating"))
        or lowered.startswith("~$")
    )


def _selected_roots(source: Path, include_all: bool) -> list[Path]:
    if include_all:
        return [source]
    roots = [source / name for name in KNOWN_DATA_ROOTS if (source / name).exists()]
    if not roots:
        raise DataMigrationError(
            "没有识别到影音库数据。请选择包含“媒体库”或“02-电影阅读”的目录；"
            "高级用户可启用“迁移整个所选文件夹”。"
        )
    return roots


def _iter_source_files(source: Path, include_all: bool):
    warnings = []
    for root in _selected_roots(source, include_all):
        if root.is_symlink():
            warnings.append("跳过符号链接目录：%s" % root)
            continue
        if root.is_file():
            candidates = [(root.parent, [], [root.name])]
        else:
            candidates = os.walk(root, followlinks=False)
        for current, dirs, files in candidates:
            current_path = Path(current)
            kept_dirs = []
            for name in dirs:
                child = current_path / name
                if _is_transient(child) or child.is_symlink():
                    warnings.append("跳过临时或链接目录：%s" % child)
                    continue
                kept_dirs.append(name)
            dirs[:] = kept_dirs
            for name in files:
                path = current_path / name
                if _is_transient(path) or path.is_symlink():
                    warnings.append("跳过临时或链接文件：%s" % path)
                    continue
                yield path, path.relative_to(source), warnings


def _same_file(source: Path, destination: Path) -> bool:
    if destination.stat().st_size != source.stat().st_size:
        return False
    return _sha256_file(source) == _sha256_file(destination)


def _plan_signature(items: list[dict]) -> str:
    payload = [
        (
            item["relativePath"],
            item["sourceSize"],
            item["sourceMtimeNs"],
            item["destinationMtimeNs"],
            item["action"],
        )
        for item in items
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def scan_migration(
    source: Path | str,
    destination: Path | str,
    options: MigrationOptions | None = None,
) -> dict:
    options = options or MigrationOptions()
    options.validate()
    source_root = normalize_data_root(source)
    destination_root = normalize_data_root(destination)
    if not source_root.is_dir():
        raise DataMigrationError("源数据目录不存在：%s" % source_root)
    if source_root == destination_root:
        raise DataMigrationError("源目录和目标目录不能相同")
    if (
        destination_root == source_root
        or source_root in destination_root.parents
        or destination_root in source_root.parents
    ):
        raise DataMigrationError("源目录和目标目录不能互相包含")

    items = []
    warnings = []
    for source_file, relative, file_warnings in _iter_source_files(
        source_root, options.include_all
    ):
        warnings.extend(file_warnings)
        destination_file = destination_root / relative
        source_size = source_file.stat().st_size
        item = {
            "relativePath": relative.as_posix(),
            "sourceSize": source_size,
            "sourceMtimeNs": source_file.stat().st_mtime_ns,
            "destinationMtimeNs": None,
            "action": "copy",
            "reason": "目标中不存在",
        }
        if destination_file.exists():
            if destination_file.is_dir():
                item["action"] = "conflict"
                item["reason"] = "目标同名路径是目录"
            else:
                item["destinationMtimeNs"] = destination_file.stat().st_mtime_ns
                if _same_file(source_file, destination_file):
                    item["action"] = "skip"
                    item["reason"] = "内容相同"
                elif options.policy == "skip":
                    item["action"] = "skip"
                    item["reason"] = "目标已存在"
                elif options.policy == "force":
                    item["action"] = "overwrite"
                    item["reason"] = "强制覆盖"
                elif (
                    source_file.stat().st_mtime_ns
                    > destination_file.stat().st_mtime_ns
                ):
                    item["action"] = "overwrite"
                    item["reason"] = "源文件更新"
                else:
                    item["action"] = "skip"
                    item["reason"] = "目标文件相同或更新"
        items.append(item)

    action_counts = {}
    for item in items:
        action_counts[item["action"]] = action_counts.get(item["action"], 0) + 1
    plan = {
        "formatVersion": FORMAT_VERSION,
        "scanId": uuid.uuid4().hex,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sourceRoot": str(source_root),
        "destinationRoot": str(destination_root),
        "policy": options.policy,
        "includeAll": options.include_all,
        "items": items,
        "warnings": sorted(set(warnings)),
        "summary": {
            "files": len(items),
            "bytes": sum(item["sourceSize"] for item in items),
            "actions": action_counts,
        },
    }
    plan["signature"] = _plan_signature(items)
    return plan


def _copy_file_atomically(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(
        ".%s.migrating-%s" % (destination.name, uuid.uuid4().hex)
    )
    try:
        shutil.copy2(source, temp)
        if _sha256_file(source) != _sha256_file(temp):
            raise DataMigrationError("复制后哈希校验失败：%s" % source)
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def _backup_destination(destination: Path, backup_root: Path | None) -> Path | None:
    if not destination.exists() or not any(destination.iterdir()):
        return None
    if backup_root is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_root = destination.parent / (
            "%s-迁移备份-%s" % (destination.name, stamp)
        )
        counter = 2
        while backup_root.exists():
            backup_root = destination.parent / (
                "%s-迁移备份-%s-%d" % (destination.name, stamp, counter)
            )
            counter += 1
    if backup_root.exists():
        raise DataMigrationError("备份目录已存在：%s" % backup_root)
    shutil.copytree(destination, backup_root, symlinks=True)
    manifest = {
        "formatVersion": FORMAT_VERSION,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": str(destination),
        "files": sum(
            1 for path in backup_root.rglob("*") if path.is_file()
        ),
    }
    (backup_root / "migration-backup-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return backup_root


def _write_reports(destination: Path, report: dict) -> tuple[Path, Path]:
    reports = destination / ".media-app" / "migration" / "data-migrations"
    reports.mkdir(parents=True, exist_ok=True)
    report_id = report["reportId"]
    json_path = reports / ("data-migration-%s.json" % report_id)
    markdown_path = reports / ("data-migration-%s.md" % report_id)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# 影音库数据迁移报告",
        "",
        "- 状态：`%s`" % report["status"],
        "- 源目录：`%s`" % report["sourceRoot"],
        "- 目标目录：`%s`" % report["destinationRoot"],
        "- 备份目录：`%s`" % (report.get("backupPath") or "无"),
        "- 复制：%d" % report["counts"].get("copied", 0),
        "- 覆盖：%d" % report["counts"].get("overwritten", 0),
        "- 跳过：%d" % report["counts"].get("skipped", 0),
        "- 冲突：%d" % report["counts"].get("conflicts", 0),
        "",
    ]
    if report.get("error"):
        lines.extend(["## 错误", "", report["error"], ""])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path


def apply_migration(
    plan: dict,
    backup_root: Path | str | None = None,
    progress=None,
) -> dict:
    if plan.get("formatVersion") != FORMAT_VERSION:
        raise DataMigrationError("迁移预览格式版本不受支持")
    options = MigrationOptions(
        policy=str(plan.get("policy") or "newer"),
        include_all=bool(plan.get("includeAll")),
    )
    fresh = scan_migration(
        plan["sourceRoot"],
        plan["destinationRoot"],
        options,
    )
    if fresh["signature"] != plan.get("signature"):
        raise DataMigrationError("预览后数据已变化，请重新扫描预览")

    source = Path(fresh["sourceRoot"])
    destination = Path(fresh["destinationRoot"])
    backup_path = _backup_destination(
        destination,
        Path(backup_root).resolve() if backup_root else None,
    )
    report = {
        "formatVersion": FORMAT_VERSION,
        "reportId": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8],
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": "RUNNING",
        "sourceRoot": str(source),
        "destinationRoot": str(destination),
        "backupPath": str(backup_path) if backup_path else None,
        "counts": {
            "copied": 0,
            "overwritten": 0,
            "skipped": 0,
            "conflicts": 0,
        },
        "errors": [],
    }
    destination.mkdir(parents=True, exist_ok=True)
    total = len(fresh["items"])
    try:
        for index, item in enumerate(fresh["items"], 1):
            action = item["action"]
            if action == "conflict":
                report["counts"]["conflicts"] += 1
                report["errors"].append(
                    "%s：%s" % (item["relativePath"], item["reason"])
                )
            elif action == "skip":
                report["counts"]["skipped"] += 1
            else:
                source_file = source / Path(item["relativePath"])
                destination_file = destination / Path(item["relativePath"])
                _copy_file_atomically(source_file, destination_file)
                key = "overwritten" if action == "overwrite" else "copied"
                report["counts"][key] += 1
            if progress:
                progress(index, total, item)
        report["status"] = (
            "COMPLETED_WITH_CONFLICTS"
            if report["counts"]["conflicts"]
            else "COMPLETED"
        )
    except Exception as exc:
        report["status"] = "FAILED"
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        raise DataMigrationApplyError(
            "迁移中止：%s" % exc,
            report,
        ) from exc
    finally:
        report["finishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        json_path, markdown_path = _write_reports(destination, report)
        report["reportJsonPath"] = str(json_path)
        report["reportMarkdownPath"] = str(markdown_path)
    return report
