from __future__ import annotations

import codecs
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path


BACKUP_KEEP_PER_NOTE = 20
BACKUP_KEEP_SNAPSHOTS = 10


class NoteWriteError(ValueError):
    pass


class ConcurrentModificationError(Exception):
    pass


class RevisionConflict(Exception):
    def __init__(self, current: dict):
        super().__init__("笔记已在 Obsidian 或其他窗口中修改")
        self.current = current


def content_revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def decode_note(raw: bytes) -> tuple[str, str, bool]:
    """严格解码一篇笔记，并返回规范化文本、原换行风格、是否有 BOM。"""
    bom = raw.startswith(codecs.BOM_UTF8)
    body = raw[len(codecs.BOM_UTF8):] if bom else raw
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise NoteWriteError("笔记不是有效的 UTF-8，已停止写入以保护原文件") from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline, bom


def encode_note(text: str, newline: str, bom: bool) -> bytes:
    if newline != "\n":
        text = text.replace("\n", newline)
    raw = text.encode("utf-8")
    return (codecs.BOM_UTF8 + raw) if bom else raw


def _temp_path(target: Path) -> Path:
    token = "%d-%d-%s" % (
        os.getpid(),
        threading.get_ident(),
        uuid.uuid4().hex[:12],
    )
    return target.with_name(".%s.%s.tmp" % (target.name, token))


def _write_temp(target: Path, raw: bytes) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = _temp_path(target)
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return temp


def _replace_bytes(target: Path, raw: bytes) -> None:
    temp = _write_temp(target, raw)
    try:
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _backup_dir(backup_root: Path, backup_key: str) -> Path:
    key_path = Path(backup_key)
    if key_path.is_absolute() or ".." in key_path.parts:
        raise NoteWriteError("备份路径不安全")
    if key_path.suffix.lower() == ".md":
        key_path = key_path.with_suffix("")
    return backup_root / key_path


def backup_bytes(
    backup_root: Path,
    backup_key: str,
    raw: bytes,
    suffix: str = ".md",
    keep: int = BACKUP_KEEP_PER_NOTE,
) -> Path:
    folder = _backup_dir(backup_root, backup_key)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    name = "%s-%s%s" % (stamp, uuid.uuid4().hex[:8], suffix)
    target = folder / name
    _replace_bytes(target, raw)

    versions = sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == suffix.lower()),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    for old in versions[max(1, keep):]:
        try:
            old.unlink()
        except OSError:
            pass
    return target


def atomic_write_note(
    target: Path,
    new_raw: bytes,
    backup_root: Path,
    backup_key: str,
    verify,
    expected_revision: str | None = None,
) -> Path | None:
    """备份并原子替换笔记；只在内容仍属于本次写入时回滚。"""
    temp = _write_temp(target, new_raw)
    try:
        original = target.read_bytes() if target.is_file() else None
        if expected_revision and (
                original is None
                or hashlib.sha256(original).hexdigest() != expected_revision):
            raise ConcurrentModificationError("file changed before atomic replace")
        backup = None
        if original is not None:
            try:
                backup = backup_bytes(backup_root, backup_key, original)
            except Exception as exc:
                raise NoteWriteError("备份失败，已停止写入") from exc
        current = target.read_bytes() if target.is_file() else None
        if expected_revision and (
                current is None
                or hashlib.sha256(current).hexdigest() != expected_revision):
            raise ConcurrentModificationError("file changed after backup")
        os.replace(temp, target)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        verify()
    except Exception as verify_error:
        try:
            current_after_replace = target.read_bytes()
        except OSError as exc:
            raise ConcurrentModificationError(
                "file changed or disappeared after atomic replace"
            ) from exc
        if current_after_replace != new_raw:
            raise ConcurrentModificationError(
                "file changed by another writer before rollback"
            ) from verify_error
        if original is None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            restore_temp = _write_temp(target, original)
            try:
                if target.read_bytes() != new_raw:
                    raise ConcurrentModificationError(
                        "file changed by another writer during rollback"
                    )
                os.replace(restore_temp, target)
            except Exception:
                try:
                    restore_temp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        raise verify_error
    return backup


def atomic_write_new_bytes(target: Path, raw: bytes) -> None:
    if target.exists():
        raise NoteWriteError("目标文件已存在，已停止覆盖")
    _replace_bytes(target, raw)


def create_snapshot_backup(
    vault: Path,
    source_paths: list[Path],
    label: str,
    snapshots_root: Path | None = None,
) -> Path:
    """按原始相对路径创建完整快照；失败时不暴露半成品目录。"""
    root = snapshots_root or (vault / ".media-app" / "backups" / "snapshots")
    root.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40] or "manual"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    target = root / ("%s-%s-%s" % (stamp, safe_label, uuid.uuid4().hex[:8]))
    temp = root / (".%s.tmp" % target.name)
    temp.mkdir(parents=False, exist_ok=False)
    manifest = []
    try:
        for source in sorted(set(source_paths)):
            source = source.resolve()
            rel = source.relative_to(vault.resolve())
            dest = temp / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            raw = source.read_bytes()
            with open(dest, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            manifest.append({
                "path": rel.as_posix(),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
        manifest_path = temp / "manifest.json"
        manifest_raw = json.dumps(
            {
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
                "label": safe_label,
                "count": len(manifest),
                "files": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        with open(manifest_path, "wb") as handle:
            handle.write(manifest_raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise

    snapshots = sorted(
        (p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    for old in snapshots[BACKUP_KEEP_SNAPSHOTS:]:
        shutil.rmtree(old, ignore_errors=True)
    return target
