#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影音库 · 本地网页版
-------------------
只依赖 Python 标准库，不需要安装任何东西。

    python server.py            # 用脚本所在目录的上一级作为库根目录
    python server.py --vault D:\\path\\to\\vault
    python server.py --port 8765 --no-browser

数据仍然是你 Obsidian 里的 markdown 文件：
读：解析 02-电影阅读/**/*.md 的 frontmatter
写：只替换目标字段和对应看板行，写前备份，写后校验并原子替换
"""
from __future__ import annotations

import contextlib
import html as html_mod
import json
import math
import mimetypes
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from decimal import Decimal
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from activity_log import ActivityLog
from frontmatter_patch import parse_frontmatter, patch_frontmatter
from media_seasons import extract_season
from rating_queue import RatingJobQueue
from media_compat import (
    PROGRESS_BY_TYPE,
    build_item_updates,
    default_progress_status,
    normalize_identity,
    normalize_ratings,
    normalize_status,
    normalize_tags,
    validated_rating,
)
from safe_network import UnsafeRemoteURLError, fetch_remote_bytes, validate_remote_image_url
from storage_safety import (
    ConcurrentModificationError,
    NoteWriteError,
    RevisionConflict,
    atomic_write_new_bytes,
    atomic_write_note,
    content_revision,
    create_snapshot_backup,
    decode_note,
    encode_note,
)
from douban_auth import (
    DoubanAuthError,
    clear_session as clear_douban_session,
    load_session as load_douban_session,
    save_session as save_douban_session,
)

APP_ID = "media-vault"
APP_VERSION = "2.0.0"
APP_PROTOCOL_VERSION = 1
APP_INSTANCE_ID = uuid.uuid4().hex
RATING_QUEUE: RatingJobQueue | None = None
_RATING_WORKER_THREAD: threading.Thread | None = None
_RATING_WORKER_STOP = threading.Event()
_RATING_WORKER_WAKE = threading.Event()
_ACCESS_RESTRICTED_HOSTS: set[str] = set()


class UpstreamAccessRestricted(RuntimeError):
    """Raised when a source explicitly denies or throttles automated access."""

    def __init__(self, host: str, status: int):
        self.host = host
        self.status = int(status)
        super().__init__("上游服务拒绝或限制了访问（HTTP %d）" % self.status)


def _raise_for_access_restriction(exc: BaseException, url: str) -> None:
    if not isinstance(exc, urllib.error.HTTPError):
        return
    if exc.code not in (401, 403, 418, 429):
        return
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host:
        _ACCESS_RESTRICTED_HOSTS.add(host)
    raise UpstreamAccessRestricted(host or "unknown", exc.code) from exc


def _assert_host_not_restricted(url: str) -> None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host and host in _ACCESS_RESTRICTED_HOSTS:
        raise UpstreamAccessRestricted(host, 403)

def _app_dir() -> Path:
    # 打包成 exe 后，代码在临时解压目录里，但「库路径」要以 exe 所在目录为准
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
# 静态资源：打包后在 sys._MEIPASS/web，开发时在脚本目录/web
WEB_DIR = Path(getattr(sys, "_MEIPASS", str(APP_DIR))) / "web"
# 如果 exe 旁边就有 web/ 目录，优先用它 —— 以后改界面不用重新打包
_local_web = APP_DIR / "web"
if _local_web.is_dir():
    WEB_DIR = _local_web
DEFAULT_VAULT = APP_DIR.parent
MEDIA_DIR_NAME = "02-电影阅读"
# 独立版（非 Obsidian）用的数据目录名
STANDALONE_DIR_NAME = "媒体库"
OBSIDIAN_MODE = True


def is_obsidian_vault(p: Path) -> bool:
    try:
        return (p / ".obsidian").is_dir()
    except OSError:
        return False


def data_directory_for_vault(vault: Path) -> Path:
    if not is_obsidian_vault(vault):
        return APP_DIR
    try:
        if APP_DIR.resolve().parent == vault.resolve():
            return APP_DIR
    except OSError:
        pass
    return vault / ".media-app"


def normalize_vault_relative_path(rel: str) -> str:
    if not isinstance(rel, str):
        raise ValueError("路径必须是字符串")
    raw = rel.strip().replace("\\", "/")
    if not raw or "\x00" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise ValueError("路径必须是 Vault 内的相对路径")
    parts = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError("路径不能包含 ..")
        if part.endswith((".", " ")):
            raise ValueError("Windows 路径段不能以点或空格结尾")
        parts.append(part)
    if not parts:
        raise ValueError("路径必须是 Vault 内的相对路径")
    return "/".join(parts)


def default_data_root() -> Path:
    """独立版数据目录：优先放在程序旁边的「数据」文件夹（便携、好备份）。"""
    portable = APP_DIR / "数据"
    try:
        portable.mkdir(parents=True, exist_ok=True)
        return portable
    except OSError:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "影音库" / "数据"


def ensure_library(root: Path) -> None:
    """首次运行时把目录结构建好。"""
    media = root / MEDIA_DIR_NAME
    for sub in ("movie", "teleplay", "game", "book"):
        (media / sub).mkdir(parents=True, exist_ok=True)
    (root / "attachments").mkdir(parents=True, exist_ok=True)

TYPE_LABEL = {
    "movie": "电影",
    "teleplay": "剧集",
    "game": "游戏",
    "book": "图书",
    "music": "音乐",
}

FM_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.S)
KEY_RE = re.compile(r"^([A-Za-z_][\w\- ]*):(.*)$")
ITEM_RE = re.compile(r"^[ \t]*-[ \t]*(.*)$")

MIME_EXTRA = {
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".avif": "image/avif",
}

ALLOWED_IMAGE_EXTS = frozenset(MIME_EXTRA)
MAX_LOCAL_IMAGE_BYTES = 12 * 1024 * 1024


# ---------------------------------------------------------------- frontmatter
def unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _balanced_scalar(value: str) -> bool:
    value = value.strip()
    if not value:
        return True
    if value[0] in "\"'":
        if len(value) < 2 or value[-1] != value[0]:
            return False
        quote = value[0]
        escaped = False
        for ch in value[1:-1]:
            if escaped:
                escaped = False
            elif ch == "\\" and quote == '"':
                escaped = True
            elif ch == quote:
                return False
    if value.startswith("[") != value.endswith("]"):
        return False
    if value.startswith("{") != value.endswith("}"):
        return False
    return True


def validate_fm_for_write(fm_text: str) -> None:
    """Stage 1 的保守校验：拒绝重复字段和明显损坏的结构。"""
    seen: set[str] = set()
    current_key: str | None = None
    block_indent: int | None = None
    for line_number, raw in enumerate(fm_text.split("\n"), 1):
        line = raw.rstrip("\r")
        if "\t" in line[:len(line) - len(line.lstrip())]:
            raise ValueError("Frontmatter 第 %d 行使用了不被支持的缩进制表符" % line_number)
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            m = KEY_RE.match(line)
            if not m:
                raise ValueError("Frontmatter 第 %d 行结构无法安全解析" % line_number)
            key = m.group(1).strip()
            value = m.group(2).strip()
            if key in seen:
                raise ValueError("Frontmatter 字段重复：%s" % key)
            if not _balanced_scalar(value):
                raise ValueError("Frontmatter 字段 %s 的引号或括号不完整" % key)
            seen.add(key)
            current_key = key
            block_indent = None
            continue

        if current_key is None:
            raise ValueError("Frontmatter 第 %d 行缺少所属字段" % line_number)
        stripped = line.lstrip(" ")
        if stripped.startswith("- "):
            continue
        if block_indent is None:
            block_indent = indent
        if indent < block_indent:
            raise ValueError("Frontmatter 第 %d 行缩进不一致" % line_number)


def parse_fm(fm: str) -> dict:
    """把 frontmatter 解析成 dict：标量 -> str，列表 -> list，块标量 -> str。"""
    data: dict = {}
    key: str | None = None
    mode: str | None = None  # 'list' | 'block' | None
    block: list[str] = []

    def flush_block():
        nonlocal key, mode, block
        if mode == "block" and key is not None:
            data[key] = "\n".join(block).strip("\n")

    for raw in fm.split("\n"):
        line = raw.rstrip("\r")
        if mode == "block":
            if line.startswith("  ") or line.strip() == "":
                block.append(line[2:] if line.startswith("  ") else "")
                continue
            flush_block()
            mode = None

        m = KEY_RE.match(line)
        if m and not line[:1].isspace():
            k = m.group(1).strip()
            v = m.group(2).strip()
            if v in ("|", "|-", ">", ">-"):
                key, mode, block = k, "block", []
                data[k] = ""
            elif v == "":
                key, mode = k, "list"
                data[k] = []
            elif v == "[]":
                key, mode = k, None
                data[k] = []
            elif v.startswith("[") and v.endswith("]"):
                key, mode = k, None
                data[k] = [unquote(x) for x in v[1:-1].split(",") if x.strip()]
            else:
                data[k] = unquote(v)
                key, mode = k, None
            continue

        if mode == "list":
            im = ITEM_RE.match(line)
            if im and key is not None and isinstance(data.get(key), list):
                data[key].append(unquote(im.group(1)))

    flush_block()
    return data


def as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    return [s] if s else []


def as_float(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def input_number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("%s必须是数字" % label)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("%s必须是数字" % label) from exc
    if not math.isfinite(number):
        raise ValueError("%s必须是有限数字" % label)
    return number


def input_list(value, label: str) -> list[str]:
    if isinstance(value, str):
        values = value.split("/")
    elif isinstance(value, (list, tuple)):
        values = list(value)
    elif value is None:
        values = []
    else:
        raise ValueError("%s必须是字符串或列表" % label)
    if any(not isinstance(item, str) for item in values):
        raise ValueError("%s中的每一项都必须是字符串" % label)
    return [item.strip() for item in values if item.strip()]


def as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1")


# ------------------------------------------------------------------- 写 frontmatter
def yaml_value(key: str, value) -> list[str]:
    """把字段序列化成 YAML 文本行。"""
    if key == "status":
        return ["status: " + ("true" if value else "false")]
    if key in ("score", "myScore"):
        num = float(value)
        txt = ("%d" % num) if abs(num - round(num)) < 1e-9 else ("%.1f" % num)
        return [key + ": " + txt]
    if key != "comment":
        if key in ("finishTime", "purchaseDate"):
            txt = str(value).strip() if value else ""
            return [(key + ": " + txt).rstrip()]
        if key == "price":
            num = float(value)
            return [key + ": " + (str(int(num)) if num.is_integer() else ("%.2f" % num))]
        if key == "platform":
            items = [str(x).strip() for x in (value or []) if str(x).strip()]
            if not items:
                return ["platform: []"]
            out = ["platform:"]
            for it in items:
                out.append("  - " + yaml_scalar(it))
            return out
        return [key + ": " + yaml_scalar(value)]
    # comment：一律用块标量，避免引号 / 冒号 / 换行把 YAML 弄坏
    text = "" if value is None else str(value).replace("\r\n", "\n").split("\n")
    out = ["comment: |-"]
    for ln in text:
        out.append("  " + ln if ln.strip() else "")
    return out


def set_fields(fm_text: str, updates: dict) -> str:
    """只替换指定字段，其余行原样保留。"""
    lines = fm_text.split("\n")
    out: list[str] = []
    handled = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = KEY_RE.match(line)
        if not m:
            out.append(line)
            i += 1
            continue

        key = m.group(1).strip()
        rest = m.group(2).strip()
        block = [line]
        j = i + 1
        if rest in ("|", "|-", ">", ">-"):
            while j < len(lines) and (lines[j].startswith("  ") or lines[j].strip() == ""):
                block.append(lines[j])
                j += 1
        else:
            while j < len(lines) and re.match(r"^[ \t]+\S", lines[j]):
                block.append(lines[j])
                j += 1

        if key in updates:
            val = updates[key]
            if val is None:
                pass  # None 表示删除这个字段
            else:
                out.extend(yaml_value(key, val))
            handled.add(key)
        else:
            out.extend(block)
        i = j

    for key, val in updates.items():
        if key in handled or val is None:
            continue
        while out and out[-1].strip() == "":
            out.pop()
        out.extend(yaml_value(key, val))
    return "\n".join(out)


# ------------------------------------------------------------------- 数据读取
def excerpt_from(body: str, limit: int = 300) -> str:
    """从正文里抽出简介段落，用于卡片悬停和详情页。"""
    m = re.search(r"(?m)^#{1,3}[ \t]*(剧情简介|内容简介|核心玩法|简介)[ \t]*$", body)
    seg = body[m.end():] if m else body
    seg = re.sub(r"```[\s\S]*?```", " ", seg)
    seg = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", seg)
    seg = re.sub(r"\[\[[^\]]*\]\]", " ", seg)
    seg = re.sub(r"(?m)^[ \t]*>.*$", " ", seg)
    seg = re.sub(r"[#*_`~]+", " ", seg)
    seg = re.sub(r"\s+", " ", seg).strip(" -")
    return seg[:limit]


def read_note(path: Path, vault: Path) -> dict | None:
    try:
        raw = path.read_bytes()
        stat = path.stat()
    except OSError:
        return None
    frontmatter = parse_frontmatter(raw)
    if not frontmatter.ok or not frontmatter.fields:
        return None
    try:
        text, _, _ = decode_note(raw)
    except NoteWriteError:
        return None
    m = FM_RE.match(text)
    if not m:
        return None
    fm = {key: field.value for key, field in frontmatter.fields.items()}
    if not fm:
        return None

    rel = path.relative_to(vault).as_posix()
    title = str(fm.get("title") or path.stem).strip()
    raw_type = str(fm.get("type") or "").strip().lower()
    if not raw_type:
        raw_type = path.parent.name
    ratings = normalize_ratings(raw_type, fm)
    status_fields = normalize_status(raw_type, fm.get("status"), fm.get("progressStatus"))
    identity = normalize_identity(fm.get("schemaVersion"), fm.get("mediaId"))
    tags = normalize_tags(fm)

    creator = []
    for k in ("director", "author", "publisher"):
        creator.extend(as_list(fm.get(k)))
        if creator:
            break

    date = str(fm.get("datePublished") or "").strip()
    year_raw = fm.get("year")
    try:
        year = int(year_raw) if year_raw not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    if year is None:
        ym = re.match(r"(\d{4})", date)
        year = int(ym.group(1)) if ym else None

    img = fm.get("image")
    image = img.strip() if isinstance(img, str) else ""
    body = text[m.end():]

    finish_raw = fm.get("finishTime")
    finish_time = normalize_date(finish_raw)
    purchase_time = normalize_date(fm.get("purchaseDate"))
    schema_version_raw = fm.get("schemaVersion")
    if isinstance(schema_version_raw, Decimal):
        schema_version_raw = float(schema_version_raw)
    price_raw = fm.get("price")
    price = None
    if price_raw not in (None, ""):
        try:
            price = float(price_raw)
            if price.is_integer():
                price = int(price)
        except (TypeError, ValueError):
            price = None
    platform_list = as_list(fm.get("platform"))

    # 封面文件是否存在 / 是不是真的图片（反爬页面曾被当成 jpg 存下来过）
    image_broken = False
    if image and not image.startswith("http"):
        fp = vault / image
        try:
            ok_img = fp.is_file() and fp.stat().st_size > 1500
            if ok_img:
                with open(fp, "rb") as f:
                    ok_img = bool(sniff_image(f.read(64)))
            image_broken = not ok_img
        except OSError:
            image_broken = True

    return {
        "path": rel,
        "title": title,
        "type": raw_type,
        "typeLabel": TYPE_LABEL.get(raw_type, raw_type or "其他"),
        **ratings,
        **status_fields,
        **identity,
        "schemaVersionRaw": schema_version_raw,
        "mediaIdRaw": fm.get("mediaId"),
        "scoreStar": str(fm.get("scoreStar") or "").strip(),
        "originalTitle": str(fm.get("originalTitle") or "").strip(),
        "aliases": as_list(fm.get("aliases")),
        "year": year,
        "date": date[:10],
        "image": image,
        "imageBroken": image_broken,
        "finishTime": finish_time,
        "startDate": normalize_date(fm.get("startDate")),
        "purchaseDate": purchase_time,
        "price": price,
        "platform": platform_list,
        "tags": tags,
        "creator": creator[:2],
        "comment": str(fm.get("comment") or "").strip(),
        "desc": (str(fm.get("desc") or "").strip())[:400],
        "excerpt": excerpt_from(body),
        "mtime": int(stat.st_mtime),
        "mtimeNs": stat.st_mtime_ns,
        "sourceSize": len(raw),
        "revision": content_revision(raw),
    }


class Library:
    def __init__(self, vault: Path, data_dir: Path | None = None):
        # Canonicalize once so Windows 8.3 aliases and long paths compare
        # consistently when files discovered below the root are read back.
        self.vault = Path(vault).resolve()
        self.data_dir = (
            Path(data_dir).resolve()
            if data_dir is not None
            else self.vault / ".media-app"
        )
        self.media_root = self.vault / MEDIA_DIR_NAME
        self.lock = threading.RLock()  # snapshot() 里会重入 load()
        self.create_lock = threading.RLock()
        self._path_locks: dict[str, threading.RLock] = {}
        self._path_locks_guard = threading.Lock()
        self.items: list[dict] = []
        self.file_signatures: dict[str, tuple[int, int]] = {}
        self.load_warnings: list[dict] = []
        self.loaded_at = 0.0
        self.backup_root = self.data_dir / "backups" / "notes"
        self.log = ActivityLog(self.data_dir)

    def create_snapshot_backup(self, label: str, include_attachments: bool = False) -> Path:
        note_paths = sorted(self.media_root.rglob("*.md")) if self.media_root.is_dir() else []
        paths = list(note_paths)
        if include_attachments:
            attachment_root = self.vault / "attachments"
            if attachment_root.is_dir():
                paths.extend(sorted(p for p in attachment_root.rglob("*") if p.is_file()))
        rels = sorted(str(p.relative_to(self.vault)).replace("\\", "/") for p in note_paths)
        try:
            with contextlib.ExitStack() as stack:
                for rel in rels:
                    stack.enter_context(self._path_lock(rel))
                target = create_snapshot_backup(
                    self.vault,
                    paths,
                    label,
                    snapshots_root=self.data_dir / "backups" / "snapshots",
                )
        except Exception as exc:
            self.log.event(
                "snapshot_backup_failed",
                level="error",
                label=label,
                errorType=type(exc).__name__,
            )
            raise ValueError("备份失败，已停止后续写入") from exc
        self.log.event("snapshot_backup_created", label=label, path=target.name, count=len(paths))
        return target

    def load(self) -> list[dict]:
        with self.lock:
            items = []
            signatures = {}
            warnings = []
            previous = {it["path"]: it for it in self.items}
            if self.media_root.exists():
                for p in sorted(self.media_root.rglob("*.md")):
                    try:
                        rel = p.relative_to(self.vault).as_posix()
                        stat = p.stat()
                    except OSError:
                        continue
                    signature = (stat.st_mtime_ns, stat.st_size)
                    rec = previous.get(rel)
                    if (rec and rec.get("mtimeNs") == signature[0]
                            and rec.get("sourceSize") == signature[1]):
                        items.append(rec)
                    else:
                        rec = read_note(p, self.vault)
                        if rec:
                            items.append(rec)
                        else:
                            warnings.append({
                                "path": rel,
                                "message": "YAML、编码或 frontmatter 结构无法安全解析，已跳过写入",
                            })
                    signatures[rel] = signature
            media_ids: dict[str, list[dict]] = {}
            for item in items:
                item.pop("mediaIdDuplicate", None)
                item["identityWarnings"] = [
                    warning for warning in item.get("identityWarnings", [])
                    if warning != "DUPLICATE_MEDIA_ID"
                ]
                item["writeAllowed"] = not (
                    item.get("schemaFuture")
                    or not item.get("identityWriteAllowed", True)
                )
                media_id = item.get("mediaId")
                if item.get("mediaIdValid") and media_id:
                    media_ids.setdefault(media_id.lower(), []).append(item)
            for duplicates in media_ids.values():
                if len(duplicates) < 2:
                    continue
                for item in duplicates:
                    item["mediaIdDuplicate"] = True
                    item.setdefault("identityWarnings", []).append("DUPLICATE_MEDIA_ID")
                    item["writeAllowed"] = False

            self.items = items
            self.file_signatures = signatures
            self.load_warnings = warnings
            self.loaded_at = time.time()
            if warnings:
                self.log.event(
                    "notes_skipped",
                    level="warning",
                    count=len(warnings),
                    paths=[item["path"] for item in warnings[:5]],
                )
            return list(items)

    def load_if_changed(self) -> None:
        discovered: dict[str, tuple[int, int]] = {}
        if self.media_root.exists():
            for p in self.media_root.rglob("*.md"):
                try:
                    rel = p.relative_to(self.vault).as_posix()
                    stat = p.stat()
                    discovered[rel] = (stat.st_mtime_ns, stat.st_size)
                except OSError:
                    continue
        with self.lock:
            unchanged = discovered == self.file_signatures and bool(self.items or not discovered)
        if not unchanged:
            self.load()

    def snapshot(self) -> list[dict]:
        self.load_if_changed()
        with self.lock:
            return [dict(it) for it in self.items]

    def _path_lock(self, rel: str) -> threading.RLock:
        target = self._note_target(rel)
        key = os.path.normcase(str(target))
        with self._path_locks_guard:
            return self._path_locks.setdefault(key, threading.RLock())

    def _note_target(self, rel: str) -> Path:
        raw_rel = normalize_vault_relative_path(rel)
        vault = self.vault.resolve()
        media_root = self.media_root.resolve()
        target = (vault / raw_rel).resolve()
        try:
            target.relative_to(media_root)
        except ValueError as exc:
            raise ValueError("只能修改影音库目录内的笔记") from exc
        if target.suffix.lower() != ".md":
            raise ValueError("只能修改 Markdown 笔记")
        return target

    def resolve_attachment(self, rel: str) -> Path:
        raw_rel = normalize_vault_relative_path(rel)
        vault = self.vault.resolve()
        attachment_root = (vault / "attachments").resolve()
        target = (vault / raw_rel).resolve()
        try:
            target.relative_to(attachment_root)
        except ValueError as exc:
            raise ValueError("只能读取附件目录中的图片") from exc
        return target

    def _cache_item(self, rel: str, rec: dict | None) -> None:
        rel = normalize_vault_relative_path(rel)
        with self.lock:
            self.items = [it for it in self.items if it["path"] != rel]
            if rec:
                cached = dict(rec)
                cached.pop("calloutWarning", None)
                self.items.append(cached)
                self.file_signatures[rel] = (rec["mtimeNs"], int(self.vault.joinpath(rel).stat().st_size))
            else:
                self.file_signatures.pop(rel, None)

    def _check_revision(self, target: Path, rel: str, expected) -> None:
        if not expected:
            return
        raw = target.read_bytes()
        if str(expected) != content_revision(raw):
            self.log.event("external_modification_conflict", level="warning", path=rel)
            raise RevisionConflict(read_note(target, self.vault) or {
                "path": rel,
                "revision": content_revision(raw),
            })

    def _attachment_referenced_elsewhere(
        self,
        target: Path,
        image_rel: str,
        attachment: Path,
    ) -> bool:
        normalized = image_rel.replace("\\", "/").lstrip("/")
        needles = {
            normalized.lower().encode("utf-8"),
            normalized.replace("/", "\\").lower().encode("utf-8"),
            attachment.name.lower().encode("utf-8"),
        }
        target_resolved = target.resolve()
        for note in self.media_root.rglob("*.md"):
            try:
                if note.resolve() == target_resolved:
                    continue
                raw = note.read_bytes().lower()
            except OSError:
                # If a note cannot be inspected, retain the shared attachment.
                return True
            if any(needle in raw for needle in needles if needle):
                return True
        return False

    def delete_with_details(self, rel: str, expected_revision=None) -> dict:
        """Move a note and its unshared local cover into trash."""
        rel = normalize_vault_relative_path(rel)
        with self._path_lock(rel):
            target = self._note_target(rel)
            if not target.is_file():
                return {"ok": False, "path": rel}
            self._check_revision(target, rel, expected_revision)

            note = read_note(target, self.vault) or {}
            image_value = str(note.get("image") or "").strip()
            attachment = None
            attachment_rel = ""
            attachment_shared = False
            if image_value and not image_value.startswith(("http://", "https://")):
                try:
                    attachment = self.resolve_attachment(image_value)
                    attachment_rel = image_value.replace("\\", "/")
                    if attachment.is_file():
                        attachment_shared = self._attachment_referenced_elsewhere(
                            target,
                            attachment_rel,
                            attachment,
                        )
                    else:
                        attachment = None
                except (OSError, ValueError):
                    attachment = None

            trash = self.data_dir / "trash"
            trash.mkdir(parents=True, exist_ok=True)
            stamp = "%s-%06d" % (time.strftime("%Y%m%d-%H%M%S"), int(time.time_ns() % 1_000_000))
            dest = trash / ("%s_%s" % (stamp, target.name))
            attachment_dest = (
                trash / ("%s_attachment_%s" % (stamp, attachment.name))
                if attachment is not None and not attachment_shared
                else None
            )
            n = 2
            while dest.exists() or (
                attachment_dest is not None and attachment_dest.exists()
            ):
                dest = trash / ("%s_%d_%s" % (stamp, n, target.name))
                if attachment is not None and not attachment_shared:
                    attachment_dest = trash / (
                        "%s_%d_attachment_%s" % (stamp, n, attachment.name)
                    )
                n += 1
            os.replace(target, dest)
            self._cache_item(rel, None)

            attachment_moved = False
            if attachment is not None and not attachment_shared and attachment_dest is not None:
                try:
                    os.replace(attachment, attachment_dest)
                    attachment_moved = True
                except OSError:
                    # The note remains safely recoverable even if the cover
                    # could not be moved and therefore stays in attachments.
                    attachment_dest = None

            self.log.event(
                "markdown_deleted",
                path=rel,
                trash=dest.name,
                attachment=attachment_dest.name if attachment_dest else None,
                attachmentShared=attachment_shared,
            )
            return {
                "ok": True,
                "path": rel,
                "trash": dest.name,
                "attachment": attachment_rel or None,
                "attachmentMoved": attachment_moved,
                "attachmentShared": attachment_shared,
            }

    def delete(self, rel: str, expected_revision=None) -> bool:
        """Backward-compatible boolean delete wrapper."""
        return bool(self.delete_with_details(rel, expected_revision).get("ok"))

    def update(self, rel: str, payload: dict) -> dict | None:
        self.load_if_changed()
        rel = normalize_vault_relative_path(rel)
        with self._path_lock(rel):
            target = self._note_target(rel)
            if not target.is_file():
                raise ValueError("文件不存在")

            raw = target.read_bytes()
            expected_revision = payload.get("revision")
            if expected_revision is not None and not isinstance(expected_revision, str):
                raise ValueError("修订号必须是字符串")
            self._check_revision(target, rel, expected_revision)
            text, nl, bom = decode_note(raw)

            parsed_frontmatter = parse_frontmatter(raw)
            if not parsed_frontmatter.ok:
                message = "；".join(item.message for item in parsed_frontmatter.diagnostics)
                raise ValueError("Frontmatter 不在可安全修改的子集内：%s" % message)
            fm_now = {key: field.value for key, field in parsed_frontmatter.fields.items()}
            cur_type = str(fm_now.get("type") or "").strip().lower()
            identity = normalize_identity(fm_now.get("schemaVersion"), fm_now.get("mediaId"))
            if identity["schemaFuture"]:
                raise ValueError("该笔记使用未来 schemaVersion，当前版本只读")
            if not identity["writeAllowed"]:
                raise ValueError("该笔记的 schemaVersion 或 mediaId 异常，当前版本只读")
            cached = next((item for item in self.items if item.get("path") == rel), None)
            if cached and cached.get("mediaIdDuplicate"):
                raise ValueError("该笔记与其他笔记重复使用 mediaId，当前版本只读")

            updates = build_item_updates(cur_type, fm_now, payload)
            if "image" in payload:
                v = updates.get("image")
                if v:
                    if v.startswith(("http://", "https://")):
                        validate_remote_image_url(v)
                    else:
                        self.resolve_attachment(v)

            if not updates:
                return None

            patch_result = patch_frontmatter(raw, updates)
            if not patch_result.ok:
                message = "；".join(item.message for item in patch_result.diagnostics)
                raise ValueError("Frontmatter Patch 失败：%s" % message)
            new_text, _, _ = decode_note(patch_result.data)
            new_text, callout_warning = sync_callout_result(new_text, updates, cur_type)
            new_raw = encode_note(new_text, nl, bom)

            def verify():
                if target.read_bytes() != new_raw:
                    raise NoteWriteError("写入后内容校验失败")
                if read_note(target, self.vault) is None:
                    raise NoteWriteError("写入后笔记无法解析")
                if target.read_bytes() != new_raw:
                    raise NoteWriteError("校验期间笔记再次发生变化")

            try:
                backup = atomic_write_note(
                    target,
                    new_raw,
                    self.backup_root,
                    rel,
                    verify,
                    expected_revision=content_revision(raw),
                )
            except ConcurrentModificationError as exc:
                latest = read_note(target, self.vault) or {
                    "path": rel,
                    "revision": content_revision(target.read_bytes()),
                }
                self.log.event("external_modification_conflict", level="warning", path=rel)
                raise RevisionConflict(latest) from exc
            except Exception as exc:
                self.log.event(
                    "markdown_update_failed",
                    level="error",
                    path=rel,
                    errorType=type(exc).__name__,
                )
                raise
            rec = read_note(target, self.vault)
            if callout_warning:
                rec["calloutWarning"] = callout_warning
            self._cache_item(rel, rec)
            self.log.event(
                "markdown_updated",
                path=rel,
                fields=sorted(updates),
                backup=backup.name if backup else None,
            )
            return rec


LIB: Library | None = None


def log_image_failure(operation: str, url: str, exc: Exception) -> None:
    if LIB is None:
        return
    host = urllib.parse.urlsplit(url).hostname or ""
    LIB.log.event(
        "image_fetch_failed",
        level="warning",
        operation=operation,
        host=host,
        errorType=type(exc).__name__,
    )

# ------------------------------------------------------------------- 新建笔记
CREATE_TYPES = {"movie", "teleplay", "game", "book", "music"}
BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
DATA_URL_RE = re.compile(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.+)$", re.S)

EXT_BY_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"RIFF", ".webp"),  # 近似：webp 以 RIFF....WEBP 开头
]


# 版本后缀：这些是「有意义的后缀」，不能当简繁重复砍掉
TITLE_SUFFIX = (
    "复刻版", "重制版", "重置版", "皇家版", "终极版", "完全版", "决定版",
    "导演剪辑版", "導演剪輯版", "剧场版", "劇場版", "特别版", "特別版",
    "周年纪念版", "高清版", "豪华版", "豪華版", "黄金版", "年度版", "试玩版",
    "重制", "复刻", "决定版", "紀念版", "周年版",
)

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
_LATIN_CORE = re.compile(r"[A-Za-z0-9:'\-\.,&!?()]+")
_PUNCT = re.compile(r"[\s·・：:．、\-—–_]+")


def has_cjk(s: str) -> bool:
    return bool(CJK_RE.search(s or ""))


def _core(s: str) -> str:
    return _PUNCT.sub("", s or "")


def _is_latin_only(s: str) -> bool:
    core = _core(s)
    return bool(core) and bool(_LATIN_CORE.fullmatch(core))


def _overlap(head: str, tail: str) -> float:
    h, x = _core(head), _core(tail)
    if not x:
        return 0.0
    return len({c for c in x if c in h}) / len(x)


def clean_title(raw: str) -> str:
    """剥离中文标题后面的英文原名 / 日文假名 / 繁体异体重复段。

    纯外文标题（不含汉字）原样返回，避免破坏 NBA 2K24、GTA 5 这类。
    例：'只狼：影逝二度 Sekiro: Shadows Die Twice' -> '只狼：影逝二度'
        '十三机兵防卫圈 十三機兵防衛圏'            -> '十三机兵防卫圈'
        '地平线：零之曙光 复刻版'                  -> 原样保留
    """
    if not raw:
        return raw
    s = str(raw).strip()
    if not has_cjk(s):
        return s

    # 1) 分隔符：第一段含中文，就丢掉后面的纯外文段
    for sep in ("/", "|", "｜", "／"):
        if sep in s:
            segs = [x.strip() for x in s.split(sep) if x.strip()]
            if segs and has_cjk(segs[0]):
                keep = [segs[0]]
                for x in segs[1:]:
                    if has_cjk(x) and not KANA_RE.search(x):
                        keep.append(x)
                s = " ".join(keep)
            break

    # 2) 空格分词，从左往右扫
    tokens = [x for x in re.split(r"\s+", s) if x]
    if len(tokens) < 2:
        return s

    kept = [tokens[0]]
    for tok in tokens[1:]:
        core = _core(tok)
        if not core:
            kept.append(tok)
            continue
        if any(suf in tok for suf in TITLE_SUFFIX):   # 版本后缀：保留
            kept.append(tok)
            continue
        if KANA_RE.search(core):                      # 日文假名：截断
            break
        if _is_latin_only(core):                      # 纯英文原名：截断
            break
        if has_cjk(core) and _overlap("".join(kept), core) >= 0.3:
            break                                     # 繁体/异体重复：截断
        kept.append(tok)

    out = " ".join(kept).strip()
    return out or s


TITLE_EN_ALIAS = {
    "亚托莉": "ATRI -My Dear Moments-",
    "传说之下": "Undertale",
    "使命召唤19：现代战争2": "Call of Duty: Modern Warfare II",
    "侠盗猎车手5": "Grand Theft Auto V",
    "光与影：33号远征队": "Clair Obscur: Expedition 33",
    "十三机兵防卫圈": "13 Sentinels: Aegis Rim",
    "去月球": "To the Moon",
    "双人成行": "It Takes Two",
    "双影奇境": "Split Fiction",
    "取景器": "Viewfinder",
    "只狼：影逝二度": "Sekiro: Shadows Die Twice",
    "史莱姆牧场": "Slime Rancher",
    "史莱姆牧场2": "Slime Rancher 2",
    "吸血鬼的旋律": "Vampires' Melody",
    "吸血鬼的旋律 2": "Vampires' Melody 2",
    "在我们之中": "Among Us",
    "地平线：零之曙光 复刻版": "Horizon Zero Dawn Remastered",
    "地狱潜兵": "HELLDIVERS",
    "多重人生": "The Alters",
    "天国：拯救2": "Kingdom Come: Deliverance II",
    "奇异小队": "Strange Brigade",
    "奥日与黑暗森林：决定版": "Ori and the Blind Forest: Definitive Edition",
    "女神异闻录3": "Persona 3 Reload",
    "女神异闻录5 皇家版": "Persona 5 Royal",
    "孤岛惊魂5": "Far Cry 5",
    "守望先锋": "Overwatch",
    "守望先锋2": "Overwatch 2",
    "寂静岭2": "Silent Hill 2",
    "密室逃脱模拟器2": "Escape Simulator 2",
    "尼尔：机械纪元": "NieR:Automata",
    "山海旅人": "The Rewinder",
    "底特律：化身为人": "Detroit: Become Human",
    "影之刃零": "Phantom Blade Zero",
    "怪物猎人 世界：冰原": "Monster Hunter World: Iceborne",
    "战地1": "Battlefield 1",
    "战地风云5": "Battlefield V",
    "拣爱": "LoveChoice",
    "捞女游戏": "Revenge on Gold Diggers",
    "控制：终极合辑": "Control Ultimate Edition",
    "无人深空": "No Man's Sky",
    "无垠之心": "A Space for the Unbound",
    "星球大战：前线2": "STAR WARS Battlefront II",
    "星球工匠": "The Planet Crafter",
    "星界战士": "Astral Ascent",
    "星际拓荒": "Outer Wilds",
    "星露谷物语": "Stardew Valley",
    "最后生还者 第一部 重制版": "The Last of Us Part I",
    "极乐迪斯科": "Disco Elysium",
    "极限竞速：地平线4": "Forza Horizon 4",
    "极限竞速：地平线5": "Forza Horizon 5",
    "极限竞速：地平线6": "Forza Horizon 6",
    "死亡搁浅2：冥滩之上": "Death Stranding 2: On the Beach",
    "死亡搁浅：导演剪辑版": "Death Stranding Director's Cut",
    "永恒天空": "Forever Skies",
    "沉没意志": "Minds Beneath Us",
    "波兰球：编年史": "Bang-On Balls: Chronicles",
    "泰坦天降2": "Titanfall 2",
    "泰拉瑞亚": "Terraria",
    "活体脑细胞": "SOMA",
    "深海迷航": "Subnautica",
    "深海迷航2：异星水域": "Subnautica 2",
    "深海迷航：零度之下": "Subnautica: Below Zero",
    "混音青春": "Mixtape",
    "漫威蜘蛛侠2": "Marvel's Spider-Man 2",
    "漫威蜘蛛侠 复刻版": "Marvel's Spider-Man Remastered",
    "瓦尔哈拉：赛博朋克酒保行动": "VA-11 HALL-A: Cyberpunk Bartender Action",
    "生化危机2：重制版": "Resident Evil 2",
    "生化危机3：重制版": "Resident Evil 3",
    "皇牌空战7：未知空域": "ACE COMBAT 7: SKIES UNKNOWN",
    "直到那时": "Until Then",
    "碧蓝幻想：Relink": "Granblue Fantasy: Relink",
    "空洞骑士": "Hollow Knight",
    "空洞骑士：丝之歌": "Hollow Knight: Silksong",
    "米塔": "MiSide",
    "精灵与萤火意志": "Ori and the Will of the Wisps",
    "维修物语": "ReStory: Chill Electronic Repairs",
    "编程矿场": "The Farmer Was Replaced",
    "羊羊的小冒险": "Sheepy: A Short Adventure",
    "艾迪芬奇的记忆": "What Remains of Edith Finch",
    "荒野大镖客：救赎2": "Red Dead Redemption 2",
    "蔚蓝": "Celeste",
    "质量效应：传奇版": "Mass Effect Legendary Edition",
    "赛博朋克 2077：终极版": "Cyberpunk 2077: Ultimate Edition",
    "辐射4": "Fallout 4",
    "逃离后室": "Escape the Backrooms",
    "逸剑风云决": "Wandering Sword",
    "都市：天际线": "Cities: Skylines",
    "饥荒联机版": "Don't Starve Together",
    "鬼灭之刃 火之神血风谭": "Demon Slayer -Kimetsu no Yaiba- The Hinokami Chronicles",
    "GRIS": "GRIS",
    "Neva": "Neva",
}

def english_name_of(title: str) -> str:
    """从 '中文 English Name' 里取出英文部分，取不到返回空串。"""
    parts = [p for p in re.split(r"\s+", str(title or "")) if p]
    out, started = [], False
    for p in parts:
        if re.search(r"[A-Za-z]", p):
            started = True
        if started:
            out.append(p)
    return " ".join(out).strip(" -–—:：")


def safe_filename(title: str) -> str:
    name = BAD_CHARS.sub("", title).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    name = name[:80] or "未命名"
    if name.upper() in WINDOWS_RESERVED:
        name = "_" + name
    return name


def yaml_scalar(value) -> str:
    """尽量输出裸标量，必要时加双引号。"""
    s = str(value)
    need_quote = (
        s == "" or s != s.strip() or s[:1] in "-?:,[]{}#&*!|>%@`\"'"
        or ": " in s or " #" in s or "\n" in s
    )
    if need_quote:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


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


def _find_status_callout_lines(text: str) -> list[tuple[int, int, str, str]]:
    pattern = re.compile(r"^> \*\*(📝|🕹️) 状态\*\*：[ \t]*(.*)$")
    offsets = []
    cursor = 0
    fence_char = ""
    fence_len = 0
    for raw in text.splitlines(keepends=True):
        content, _ = raw.rstrip("\r\n"), ""
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
                offsets.append((cursor, cursor + len(content), match.group(1), match.group(2)))
        cursor += len(raw)
    return offsets


def sync_callout_result(text: str, updates: dict, note_type: str = "") -> tuple[str, str | None]:
    """把 Frontmatter 变化同步到唯一匹配的正文 Callout 行。"""
    warning = None
    if "progressStatus" in updates or "status" in updates:
        progress = updates.get("progressStatus")
        if progress is None and "status" in updates:
            done = bool(updates["status"])
            progress = default_progress_status(note_type, done)
        word = CALLOUT_STATUS.get(note_type, {}).get(progress)
        if word is None and note_type not in PROGRESS_BY_TYPE:
            word = "已看完" if done else "未看完"
        if word is None:
            warning = "CALLOUT_STATUS_UNSUPPORTED"
        else:
            matches = _find_status_callout_lines(text)
            if len(matches) == 1:
                start, end, marker, value = matches[0]
                expected_marker = CALLOUT_MARKER.get(note_type, "📝")
                if marker != expected_marker:
                    warning = "CALLOUT_TYPE_MISMATCH"
                else:
                    current = value.strip()
                    if current and current not in CALLOUT_KNOWN_VALUES:
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
            elif not matches:
                warning = "CALLOUT_NOT_FOUND"
            else:
                warning = "CALLOUT_AMBIGUOUS"

    if "score" in updates and updates["score"] is not None:
        val = updates["score"]
        shown = ("%g" % round(float(val), 1)) if note_type != "game" else ("%d" % round(float(val)))
        text = re.sub(r"(> \*\*⭐ 评分\*\*：).*", lambda m: m.group(1) + shown, text)

    if "title" in updates and updates["title"]:
        text = re.sub(r"(> \*\*🎮 名称\*\*：).*", lambda m: m.group(1) + "《" + str(updates["title"]) + "》", text)

    if "image" in updates:
        img = updates["image"]
        if img:
            text = re.sub(r"(!\[[^\]]*\]\()[^)]*(\))", lambda m: m.group(1) + str(img) + m.group(2), text, count=1)

    return text, warning


def sync_callout(text: str, updates: dict, note_type: str = "") -> str:
    """兼容旧调用方；需要警告时应使用 sync_callout_result。"""
    return sync_callout_result(text, updates, note_type)[0]


def build_frontmatter(fields: list[tuple[str, object]]) -> str:
    out = ["---"]
    for key, val in fields:
        if val is None or val == "" or val == []:
            if key in ("finishTime",):
                out.append(key + ":")
            continue
        if isinstance(val, list):
            out.append(key + ":")
            for item in val:
                out.append("  - " + yaml_scalar(item))
        elif isinstance(val, bool):
            out.append(key + ": " + ("true" if val else "false"))
        elif isinstance(val, (int, float)):
            num = float(val)
            out.append(key + ": " + (("%d" % num) if abs(num - round(num)) < 1e-9 else ("%.1f" % num)))
        else:
            out.append(key + ": " + yaml_scalar(val))
    out.append("---")
    return "\n".join(out)


def build_body(t: str, d: dict) -> str:
    """生成正文：callout 信息卡 + 简介段落（不用裸 HTML，避免阅读模式解析问题）。"""
    title = d["title"]
    image = d.get("image") or ""

    def info_block(head: str, rows: list[tuple[str, str]]) -> str:
        lines = [head]
        if image:
            lines.append("> ![|200](%s)" % image)
            lines.append(">")
        for k, v in rows:
            if v:
                lines.append("> **%s**：%s" % (k, v))
        return "\n".join(lines)

    if d.get("score") is None:
        score_txt = ""
    elif t == "game":
        score_txt = "%d" % round(float(d["score"]))
    else:
        score_txt = "%.1f" % d["score"]
    status_txt = "已看完" if d.get("status") else "未看完"
    desc = d.get("desc") or ""

    if t == "game":
        head = info_block("> [!info]- 游戏看板", [
            ("🎮 名称", "《%s》" % title),
            ("🏢 发行商", d.get("publisher") or ""),
            ("📆 发行", d.get("datePublished") or ""),
            ("🎮 平台", " / ".join(d.get("platform") or [])),
            ("⭐ 评分", score_txt),
            ("🕹️ 状态", status_txt),
        ])
        return (head + "\n\n## 核心玩法\n\n" + (desc or "待补充")
                + "\n\n## 进度追踪\n\n- **开始时间**：\n- **通关时间**：\n- **时长**：\n")

    if t == "book":
        head = info_block("> [!info]+ 基本信息", [
            ("📚 书名", "《%s》" % title),
            ("👤 作者", d.get("author") or ""),
            ("🏢 出版社", d.get("publisher") or ""),
            ("📅 出版", d.get("datePublished") or ""),
            ("📖 页数", d.get("pages") or ""),
            ("⭐ 评分", score_txt),
            ("📝 状态", status_txt),
        ])
        return (head + "\n\n## 内容简介\n\n" + desc
                + "\n\n## 阅读记录\n\n- **开始时间**：\n- **阅读平台**：\n- **个人评分**：\n")

    icon = {"movie": "🎬", "teleplay": "📺", "music": "🎵"}.get(t, "🎬")
    head = info_block("> [!info]+ 基本信息", [
        ("%s 标题" % icon, "《%s》" % title),
        ("👨‍💼 导演", d.get("director") or ""),
        ("👥 演员", d.get("actor") or ""),
        ("📅 上映", d.get("datePublished") or ""),
        ("🌍 地区", " / ".join(d.get("country") or [])),
        ("⭐ 评分", score_txt),
        ("📝 状态", status_txt),
    ])
    return head + "\n\n## 剧情简介\n\n" + desc


def _configured_https_proxy() -> str | None:
    proxies = urllib.request.getproxies()
    value = proxies.get("https") or proxies.get("http")
    if not value or not value.startswith(("http://", "https://")):
        return None
    return value


def auto_lookup_external_ratings(payload: dict) -> dict:
    """Resolve optional external scores for a newly created film or TV item."""
    media_type = str(payload.get("type") or "").strip().lower()
    if media_type not in {"movie", "teleplay"}:
        return {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return {}
    original_title = str(
        payload.get("_lookupTitle") or payload.get("originalTitle") or ""
    ).strip()
    parsed = extract_season(title)
    season = parsed["season"] if media_type == "teleplay" else None
    part = parsed.get("part")
    raw_year = payload.get("datePublished") or payload.get("year")
    year_match = re.search(r"\d{4}", str(raw_year or ""))
    year = year_match.group(0) if year_match else None
    tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    is_anime = any(
        str(tag).casefold() in {"动画", "动漫", "animation"}
        for tag in tags
    )
    fields = ("imdbScore", "rottenTomatoesScore", "metacriticScore")
    missing = {
        field for field in fields
        if payload.get(field) in (None, "")
    }
    if not missing and not (is_anime and payload.get("bangumiScore") in (None, "")):
        return {}

    candidates = []
    for candidate in (original_title, title):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    proxy = _configured_https_proxy()
    updates = {}

    # Prefer stable Wikidata IDs, then fall back to source-specific exact lookup.
    for candidate in candidates:
        lookup_query = extract_season(candidate)["title"] if season is not None else candidate
        try:
            resolved = lookup_wikidata(
                lookup_query,
                media_type,
                year,
                proxy=proxy,
            )
            if resolved.get("status") == "matched":
                external = lookup_external_ids(
                    resolved["candidate"],
                    season=season,
                    part=part,
                    year=year,
                    fields=missing,
                )
                updates.update({
                    key: value
                    for key, value in external.items()
                    if key in fields and value is not None
                })
        except UpstreamAccessRestricted:
            raise
        except Exception:
            pass
        if not any(
            payload.get(field) in (None, "") and field not in updates
            for field in fields
        ):
            break

    for candidate in candidates:
        remaining = {
            field for field in fields
            if payload.get(field) in (None, "") and field not in updates
        }
        if not remaining:
            break
        source_title = (
            extract_season(candidate)["title"]
            if season is not None
            else candidate
        )
        try:
            if "imdbScore" in remaining:
                imdb = (
                    lookup_imdb(
                        source_title,
                        media_type,
                        year,
                        season=season,
                        part=part,
                    )
                    if season is not None
                    else lookup_imdb(candidate, media_type, year)
                )
                if imdb.get("status") == "matched":
                    updates["imdbScore"] = imdb.get("score")
        except UpstreamAccessRestricted:
            raise
        except Exception:
            pass
        try:
            if "rottenTomatoesScore" in remaining:
                rotten = (
                    lookup_rotten_tomatoes_season(source_title, season, year)
                    if season is not None
                    else lookup_rotten_tomatoes(source_title, media_type, year)
                )
                if rotten.get("status") == "matched":
                    updates["rottenTomatoesScore"] = rotten.get("score")
        except UpstreamAccessRestricted:
            raise
        except Exception:
            pass
        try:
            if "metacriticScore" in remaining:
                metacritic = lookup_metacritic(
                    source_title,
                    media_type,
                    year,
                    season=season,
                    part=part,
                )
                if metacritic.get("status") == "matched":
                    updates["metacriticScore"] = metacritic.get("score")
        except UpstreamAccessRestricted:
            raise
        except Exception:
            pass

    if is_anime and payload.get("bangumiScore") in (None, ""):
        for candidate in candidates:
            try:
                bangumi = lookup_bangumi(
                    candidate,
                    year,
                    season,
                    proxy=proxy,
                )
                if bangumi.get("status") == "matched":
                    updates["bangumiScore"] = bangumi.get("score")
                    break
            except UpstreamAccessRestricted:
                raise
            except Exception:
                continue
    return updates


def _missing_external_fields(item: dict) -> list[str]:
    if item.get("type") not in {"movie", "teleplay"}:
        return []
    fields = [
        field for field in ("imdbScore", "rottenTomatoesScore", "metacriticScore")
        if item.get(field) is None
    ]
    tags = {str(tag).casefold() for tag in item.get("tags", [])}
    if (
        tags.intersection({"动画", "动漫", "animation"})
        and item.get("bangumiScore") is None
    ):
        fields.append("bangumiScore")
    return fields


def _queue_rating_job(lib: "Library", item: dict) -> dict | None:
    global RATING_QUEUE
    fields = _missing_external_fields(item)
    if not fields:
        return None
    try:
        if RATING_QUEUE is None:
            RATING_QUEUE = RatingJobQueue(lib.data_dir)
        job = RATING_QUEUE.enqueue(item["path"], fields)
        _RATING_WORKER_WAKE.set()
        return job
    except Exception as exc:
        lib.log.event(
            "rating_job_enqueue_failed",
            level="error",
            path=item.get("path"),
            errorType=type(exc).__name__,
        )
        return {
            "path": item.get("path"),
            "status": "queue_failed",
            "errorType": type(exc).__name__,
        }


def _rating_retry_delay(attempts: int) -> float:
    schedule = (5, 15, 60, 300, 900, 3600)
    return schedule[min(max(0, attempts), len(schedule) - 1)]


def _process_rating_job(lib: "Library", job: dict) -> None:
    global RATING_QUEUE
    if RATING_QUEUE is None:
        return
    rel = str(job.get("path") or "")
    item = next(
        (entry for entry in lib.snapshot() if entry.get("path") == rel),
        None,
    )
    if item is None:
        RATING_QUEUE.finish(rel, "completed")
        return
    fields = set(job.get("fields") or []) & set(_missing_external_fields(item))
    if not fields:
        RATING_QUEUE.finish(rel, "completed")
        return
    payload = {
        "type": item.get("type"),
        "title": item.get("title"),
        "originalTitle": item.get("originalTitle"),
        "datePublished": item.get("date"),
        "tags": item.get("tags") or [],
        "imdbScore": item.get("imdbScore"),
        "rottenTomatoesScore": item.get("rottenTomatoesScore"),
        "metacriticScore": item.get("metacriticScore"),
        "bangumiScore": item.get("bangumiScore"),
    }
    try:
        updates = {
            key: value
            for key, value in auto_lookup_external_ratings(payload).items()
            if key in fields and value is not None
        }
    except UpstreamAccessRestricted as exc:
        lib.log.event(
            "rating_job_access_restricted",
            level="warning",
            path=rel,
            status=exc.status,
        )
        RATING_QUEUE.finish(rel, "blocked", error="ACCESS_RESTRICTED")
        return
    current = item
    if updates:
        try:
            current = lib.update(rel, {
                "revision": item["revision"],
                **updates,
            }) or item
            lib.log.event(
                "rating_job_updated",
                path=rel,
                fields=sorted(updates),
            )
        except RevisionConflict:
            lib.log.event(
                "rating_job_conflict",
                level="warning",
                path=rel,
            )
            RATING_QUEUE.finish(
                rel,
                "retry",
                error="EXTERNAL_MODIFICATION",
                next_attempt=time.time() + _rating_retry_delay(int(job.get("attempts") or 0)),
            )
            return
        except Exception as exc:
            lib.log.event(
                "rating_job_failed",
                level="error",
                path=rel,
                errorType=type(exc).__name__,
            )
            RATING_QUEUE.finish(
                rel,
                "retry",
                error=type(exc).__name__,
                next_attempt=time.time() + _rating_retry_delay(int(job.get("attempts") or 0)),
            )
            return
    remaining = set(_missing_external_fields(current))
    remaining &= set(job.get("fields") or [])
    if not remaining:
        RATING_QUEUE.finish(rel, "completed")
        lib.log.event("rating_job_completed", path=rel)
        return
    attempts = int(job.get("attempts") or 0)
    RATING_QUEUE.finish(
        rel,
        "retry",
        error="NO_NEW_SCORE" if not updates else "PARTIAL_SCORE",
        fields=sorted(remaining),
        next_attempt=time.time() + _rating_retry_delay(attempts),
    )


def _rating_worker_loop(lib: "Library") -> None:
    global RATING_QUEUE
    while not _RATING_WORKER_STOP.is_set():
        job = RATING_QUEUE.due() if RATING_QUEUE is not None else None
        if job is None:
            _RATING_WORKER_WAKE.wait(2)
            _RATING_WORKER_WAKE.clear()
            continue
        try:
            _process_rating_job(lib, job)
        except Exception as exc:
            lib.log.event(
                "rating_job_worker_failed",
                level="error",
                path=str(job.get("path") or ""),
                errorType=type(exc).__name__,
            )
            RATING_QUEUE.finish(
                str(job.get("path") or ""),
                "retry",
                error=type(exc).__name__,
                next_attempt=time.time() + 60,
            )


def start_rating_worker(lib: "Library") -> None:
    global RATING_QUEUE, _RATING_WORKER_THREAD
    if _RATING_WORKER_THREAD is not None and _RATING_WORKER_THREAD.is_alive():
        return
    RATING_QUEUE = RatingJobQueue(lib.data_dir)
    _RATING_WORKER_STOP.clear()
    _RATING_WORKER_WAKE.clear()
    _RATING_WORKER_THREAD = threading.Thread(
        target=_rating_worker_loop,
        args=(lib,),
        name="rating-jobs",
        daemon=True,
    )
    _RATING_WORKER_THREAD.start()


def stop_rating_worker() -> None:
    _RATING_WORKER_STOP.set()
    _RATING_WORKER_WAKE.set()


def create_note(lib: "Library", payload: dict) -> dict:
    raw_title = payload.get("title")
    if not isinstance(raw_title, str):
        raise ValueError("标题必须是字符串")
    title = raw_title.strip()
    if not title:
        raise ValueError("标题不能为空")
    if len(title) > 120:
        raise ValueError("标题太长")

    t = str(payload.get("type") or "movie").strip().lower()
    if t not in CREATE_TYPES:
        t = "movie"
    score = payload.get("score")
    if score is not None and score != "":
        score = input_number(score, "评分")
        limit = 100 if t == "game" else 10
        if score < 0 or score > limit:
            raise ValueError("评分需要在 0-%d 之间" % limit)
    else:
        score = None

    def optional_rating(key: str, label: str, maximum: Decimal, integer=False, places=1):
        value = payload.get(key)
        if value in (None, ""):
            return None
        rating = validated_rating(
            value,
            label=label,
            minimum=Decimal("0"),
            maximum=maximum,
            integer=integer,
            decimal_places=None if integer else places,
        )
        return int(rating) if integer else float(rating)

    douban_value = optional_rating("doubanScore", "豆瓣评分", Decimal("10"))
    metacritic_value = optional_rating(
        "metacriticScore", "Metacritic 评分", Decimal("100"), integer=True
    )
    imdb_value = optional_rating("imdbScore", "IMDb 评分", Decimal("10"))
    bangumi_value = optional_rating("bangumiScore", "Bangumi 评分", Decimal("10"))
    rotten_value = optional_rating(
        "rottenTomatoesScore",
        "烂番茄评分",
        Decimal("100"),
        integer=True,
    )
    if t == "game" and metacritic_value is not None:
        score = metacritic_value
    elif t != "game" and douban_value is not None:
        score = douban_value

    status_value = payload.get("status", False)
    if not isinstance(status_value, bool):
        raise ValueError("状态必须是布尔值")
    progress_value = payload.get("progressStatus")
    if t in PROGRESS_BY_TYPE:
        if progress_value is None:
            progress_value = default_progress_status(t, status_value)
        elif progress_value not in PROGRESS_BY_TYPE[t]:
            raise ValueError("该媒体类型不支持状态 %s" % progress_value)
        status_value = progress_value == "completed"
    else:
        progress_value = None
    tags_value = input_list(payload.get("tags"), "标签")
    platform_value = input_list(payload.get("platform"), "平台")
    country_value = input_list(payload.get("country"), "地区")
    date_value = payload.get("datePublished") or ""
    if not isinstance(date_value, str):
        raise ValueError("发行日期必须是字符串")
    start_date = payload.get("startDate") or ""
    if not isinstance(start_date, str):
        raise ValueError("开始日期必须是字符串")

    name = safe_filename(title)
    media_id = str(uuid.uuid4())

    image_path = payload.get("imagePath") or ""
    if not isinstance(image_path, str):
        raise ValueError("封面路径必须是字符串")
    image = image_path.strip()
    if image:
        if image.startswith(("http://", "https://")):
            validate_remote_image_url(image)
        else:
            attachment = lib.resolve_attachment(image)
            if not attachment.is_file():
                raise ValueError("指定的本地封面不存在")
    data_url = payload.get("imageData")
    if data_url is not None and not isinstance(data_url, str):
        raise ValueError("封面上传数据格式无效")
    image_url = payload.get("imageUrl") or ""
    if not isinstance(image_url, str):
        raise ValueError("封面网址必须是字符串")
    url = image_url.strip()
    created_image = ""
    if not image and data_url:
        image = save_data_image(lib.vault, name, data_url)
        created_image = image
    elif not image and url:
        validate_remote_image_url(url)
        image = save_remote_image(lib.vault, name, url, referer=payload.get("imageReferer")) or url
        if image.startswith("attachments/"):
            created_image = image

    from datetime import date
    today = date.today().isoformat()

    common = [
        ("schemaVersion", 2),
        ("mediaId", media_id),
        ("title", title),
        ("originalTitle", str(payload.get("originalTitle") or "").strip()),
        ("type", t),
        ("score", score),
        ("scoreStar", ("⭐" * max(1, min(5,
            round(score / 20) if (t == "game" and score > 10) else round(score / 2))))
         if score else None),
        ("image", image),
        ("datePublished", str(payload.get("datePublished") or "").strip()),
        ("doubanScore", douban_value),
        ("metacriticScore", metacritic_value),
        ("imdbScore", imdb_value),
        ("bangumiScore", bangumi_value),
        ("rottenTomatoesScore", rotten_value),
    ]

    if t == "game":
        fields = common + [
            ("publisher", str(payload.get("creator") or "").strip()),
            ("genre", tags_value),
            ("platform", platform_value),
        ]
    elif t == "book":
        fields = common + [
            ("publisher", str(payload.get("publisher") or "").strip()),
            ("author", str(payload.get("creator") or "").strip()),
            ("pages", payload.get("pages") or None),
            ("tags", tags_value),
        ]
    else:
        fields = common + [
            ("country", country_value),
            ("director", str(payload.get("creator") or "").strip()),
            ("actor", str(payload.get("actor") or "").strip()),
            ("tags", tags_value),
        ]

    fields += [
        ("currentDate", today),
        ("startDate", start_date.strip() or None),
        ("finishTime", None),
        ("status", status_value),
    ]
    if progress_value is not None:
        fields.append(("progressStatus", progress_value))

    d = {
        "title": title,
        "image": image,
        "score": score,
        "status": status_value,
        "progressStatus": progress_value,
        "desc": str(payload.get("desc") or "").strip(),
        "datePublished": date_value.strip(),
        "director": str(payload.get("creator") or "").strip(),
        "author": str(payload.get("creator") or "").strip(),
        "publisher": str(payload.get("publisher") or payload.get("creator") or "").strip(),
        "actor": str(payload.get("actor") or "").strip(),
        "country": country_value,
        "platform": platform_value,
        "pages": payload.get("pages") or "",
    }

    text = build_frontmatter(fields).lstrip("\ufeff") + "\n\n" + build_body(t, d) + "\n"
    with lib.create_lock:
        folder = lib.media_root / t
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (name + ".md")
        n = 2
        while target.exists():
            target = folder / ("%s-%d.md" % (name, n))
            n += 1
        try:
            atomic_write_new_bytes(target, text.encode("utf-8"))
        except Exception:
            if created_image:
                try:
                    lib.resolve_attachment(created_image).unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
            raise
        rec = read_note(target, lib.vault)
        if rec is None:
            target.unlink(missing_ok=True)
            if created_image:
                try:
                    lib.resolve_attachment(created_image).unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
            raise ValueError("创建成功但读取失败，已撤销新文件")
        lib._cache_item(rec["path"], rec)
        lib.log.event("markdown_created", path=rec["path"], type=t)
    return rec


def save_data_image(vault: Path, name: str, data_url: str) -> str:
    m = DATA_URL_RE.match(data_url.strip())
    if not m:
        raise ValueError("图片数据格式无法识别")
    ext = "." + m.group(1).split("+")[0].replace("jpeg", "jpg")
    if ext not in (".png", ".jpg", ".gif", ".webp", ".avif"):
        ext = ".png"
    import base64
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError as exc:
        raise ValueError("图片数据损坏") from exc
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("图片太大（上限 12MB）")
    if not sniff_image(raw):
        raise ValueError("上传内容不是有效图片")
    for magic, guess in EXT_BY_MAGIC:
        if raw.startswith(magic):
            ext = guess
            break
    return _write_attachment(vault, name, ext, raw)


IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def sniff_image(raw: bytes) -> str:
    """返回 MIME；不是图片就返回空串——用来挡掉反爬返回的 HTML 页面。"""
    if not raw or len(raw) < 64:
        return ""
    for magic, mime in IMAGE_MAGIC:
        if raw.startswith(magic):
            return mime
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if raw[4:8] == b"ftyp":
        return "image/avif"
    return ""


def douban_image_variants(url: str) -> list[str]:
    """Return only the URL supplied by the upstream service.

    Older releases rotated a Douban image URL across img1..img9 after an
    error.  That can look like an attempt to evade access controls, so the
    application now fails closed and leaves retry decisions to the user.
    """
    return [str(url or "").strip()]


def download_image(url: str, referer: str | None = None) -> bytes:
    """Download the exact upstream image URL once, verify it, then save."""
    global _CDN_FAILS, _CDN_DOWN
    last_err: Exception | None = None
    for candidate in douban_image_variants(url):
        _assert_host_not_restricted(candidate)
        is_cdn = not ("doubanio.com" in candidate or "douban.com" in candidate)
        if is_cdn and _CDN_DOWN:
            continue
        try:
            if not is_cdn:
                request_headers = douban_headers()
                request_headers["Referer"] = referer or "https://www.douban.com/"
            else:
                request_headers = {
                    "User-Agent": DOUBAN_UA,
                    "Accept": "image/*,*/*;q=0.8",
                }
            raw, _ = fetch_remote_bytes(
                candidate,
                headers=request_headers,
                max_bytes=12 * 1024 * 1024,
                connect_timeout=5,
                read_timeout=10,
            )
            _CDN_FAILS = 0
        except UnsafeRemoteURLError as e:
            # 404/403 = 这张图本来就不存在（比如没有 2x 版），不是网络故障
            last_err = e
            status_match = re.search(r"HTTP (\d+)", str(e))
            if status_match and int(status_match.group(1)) in (401, 403, 418, 429):
                host = (urllib.parse.urlsplit(candidate).hostname or "").lower()
                if host:
                    _ACCESS_RESTRICTED_HOSTS.add(host)
                raise UpstreamAccessRestricted(
                    host or "unknown",
                    int(status_match.group(1)),
                ) from e
            if is_cdn:
                _CDN_FAILS += 1
                if _CDN_FAILS >= 6:
                    _CDN_DOWN = True
            continue
        except urllib.error.HTTPError as e:
            last_err = e
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # 超时 / 连接失败才计入熔断
            last_err = e
            if is_cdn:
                _CDN_FAILS += 1
                if _CDN_FAILS >= 6:
                    _CDN_DOWN = True
            continue
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
        if not sniff_image(raw):
            last_err = ValueError("返回的不是图片（很可能是反爬页面）：%r" % raw[:30])
            continue
        if len(raw) < 1500:
            last_err = ValueError("图片太小，疑似占位图：%d 字节" % len(raw))
            continue
        return raw
    raise last_err or ValueError("下载失败")


def save_remote_image(vault: Path, name: str, url: str, referer: str | None = None) -> str:
    if not re.match(r"^https?://", url):
        return ""
    if MOCK_DIR:  # 测试模式
        try:
            raw = mock_douban_get(url)
            ext = ".png" if raw.startswith(b"\x89PNG") else ".jpg"
            return _write_attachment(vault, name, ext, raw)
        except Exception:
            return ""
    try:
        raw = download_image(url, referer)
    except Exception as e:  # noqa: BLE001
        print("[image] 跳过：%s -> %r" % (url[:70], e), file=sys.stderr)
        log_image_failure("save_remote_image", url, e)
        return ""
    ext = mimetypes.guess_extension(sniff_image(raw)) or ".jpg"
    if ext in (".jpe", ".jpeg"):
        ext = ".jpg"
    return _write_attachment(vault, name, ext, raw)


def _write_attachment(vault: Path, name: str, ext: str, raw: bytes,
                      prefer_rel: str = "") -> str:
    """原子写入新附件；旧封面可能被多条记录共用，因此不原地覆盖。"""
    att = vault / "attachments"
    att.mkdir(parents=True, exist_ok=True)

    fname = safe_filename(name) + ext
    target = att / fname
    n = 2
    while True:
        while target.exists():
            target = att / ("%s-%d%s" % (safe_filename(name), n, ext))
            n += 1
        try:
            atomic_write_new_bytes(target, raw)
            return "attachments/" + target.name
        except NoteWriteError:
            continue


# ------------------------------------------------------------------- 豆瓣搜索
DOUBAN_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")
# 测试时可指向本地 fixture 目录，避免真的联网
MOCK_DIR = os.environ.get("MEDIAVAULT_DOUBAN_FIXTURES")

# 可被环境变量覆盖，方便本地起假豆瓣做回归测试
HOST_MOVIE = os.environ.get("MEDIAVAULT_HOST_MOVIE", "https://movie.douban.com")
HOST_BOOK = os.environ.get("MEDIAVAULT_HOST_BOOK", "https://book.douban.com")
HOST_WWW = os.environ.get("MEDIAVAULT_HOST_WWW", "https://www.douban.com")
HOST_REXXAR = os.environ.get("MEDIAVAULT_HOST_REXXAR", "https://m.douban.com")

_HEADER_CACHE = {"key": None, "headers": None}

def douban_headers() -> dict:
    """Use only the encrypted session explicitly saved by the user."""
    base = {
        "User-Agent": DOUBAN_UA,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "identity",  # 关键：不让服务器压缩，否则解析会失败
    }
    if LIB is None:
        return base

    local = None
    local_mtime = 0.0
    try:
        local = load_douban_session(LIB.data_dir)
        if local is not None:
            local_mtime = local["path"].stat().st_mtime
    except (OSError, DoubanAuthError):
        local = None

    cache_key = local_mtime
    if _HEADER_CACHE["headers"] is not None and _HEADER_CACHE["key"] == cache_key:
        return dict(_HEADER_CACHE["headers"])

    headers = dict(base)
    if local is not None:
        headers["Cookie"] = local["cookie"]
        _HEADER_CACHE["key"] = cache_key
        _HEADER_CACHE["headers"] = dict(headers)
        return headers

    _HEADER_CACHE["key"] = cache_key
    _HEADER_CACHE["headers"] = dict(headers)
    return headers


def douban_logged_in() -> bool:
    return any(k.lower() == "cookie" for k in douban_headers())


def douban_session_status() -> dict:
    local = None
    try:
        local = load_douban_session(LIB.data_dir) if LIB is not None else None
    except (OSError, DoubanAuthError):
        local = None
    return {
        "ok": True,
        "loggedIn": local is not None,
        "source": "standalone" if local else "none",
        "canLogin": True,
    }


def decode_body(raw: bytes, content_encoding: str | None) -> bytes:
    """兜底：万一服务器还是压缩了，这里解一下。"""
    enc = (content_encoding or "").lower().strip()
    if enc == "gzip" or raw[:2] == b"\x1f\x8b":
        try:
            import gzip
            return gzip.decompress(raw)
        except Exception:
            return raw
    if enc == "deflate":
        try:
            import zlib
            return zlib.decompress(raw, -zlib.MAX_WBITS)
        except Exception:
            try:
                import zlib
                return zlib.decompress(raw)
            except Exception:
                return raw
    return raw


def douban_get(url: str, referer: str | None = None, timeout: float = 10.0) -> bytes:
    if MOCK_DIR:
        return mock_douban_get(url)
    _assert_host_not_restricted(url)
    headers = douban_headers()
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(8 * 1024 * 1024)
            return decode_body(raw, resp.headers.get("Content-Encoding"))
    except urllib.error.HTTPError as exc:
        _raise_for_access_restriction(exc, url)
        raise


def mock_douban_get(url: str) -> bytes:
    """测试用假数据：按 URL 特征返回 fixture。"""
    if "j/search" in url:
        name = "search_games.html"
    elif "subject_suggest" in url:
        if "book.douban.com" in url:
            name = "suggest_book.json"
        else:
            name = "suggest_movie.json"
    elif "/rexxar/api/v2/movie/" in url:
        name = "detail_movie.json"
    elif "/rexxar/api/v2/book/" in url:
        name = "detail_book.json"
    elif url.endswith(".jpg") or url.endswith(".webp"):
        name = "cover.png"
    else:
        raise OSError("mock: 未知 URL " + url)
    path = os.path.join(MOCK_DIR, name)
    with open(path, "rb") as f:
        return f.read()


def search_douban(q: str, target_type: str) -> list[dict]:
    """按 Obsidian 豆瓣插件的方式使用 www.douban.com/j/search。"""
    cat = {"book": "1001", "game": "3114"}.get(target_type, "1002")
    try:
        items = search_douban_html(q, cat, target_type)
        if items:
            return items
    except Exception:
        pass

    # 插件搜索页失效时，继续尝试 subject_suggest。
    try:
        if target_type == "book":
            url = HOST_BOOK + "/j/subject_suggest?q=" + urllib.parse.quote(q)
            raw = douban_get(url, referer="https://book.douban.com/")
            data = json.loads(raw.decode("utf-8", errors="replace"))
            out = []
            for d in data if isinstance(data, list) else []:
                title = str(d.get("title") or "").strip()
                if not title:
                    continue
                out.append({
                    "id": str(d.get("id") or ""),
                    "title": title,
                    "year": str(d.get("year") or ""),
                    "cover": str(d.get("pic") or ""),
                    "sub": str(d.get("author_name") or ""),
                    "score": None,
                    "type": "book",
                })
            return out[:20]

        url = HOST_MOVIE + "/j/subject_suggest?q=" + urllib.parse.quote(q)
        raw = douban_get(url, referer="https://movie.douban.com/")
        data = json.loads(raw.decode("utf-8", errors="replace"))
        out = []
        for d in data if isinstance(data, list) else []:
            title = str(d.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "id": str(d.get("id") or ""),
                "title": title,
                "year": str(d.get("year") or ""),
                "cover": str(d.get("img") or ""),
                "sub": str(d.get("sub_title") or ""),
                "score": None,
                "type": target_type,
            })
        return out[:20]
    except Exception:
        return []


class _SearchHtmlParser(HTMLParser):
    """解析 www.douban.com/j/search 返回的 HTML（结构见插件 SearchParser.ts）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.result_depth = 0
        self.in_result = False
        self.stack: list[str] = []
        self.items: list[dict] = []
        self.cur: dict | None = None
        self.cap: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        cls = a.get("class", "").split()
        if tag == "div":
            self.depth += 1
            if "result" in cls and not self.in_result:
                self.in_result = True
                self.result_depth = self.depth
                self.cur = {"id": "", "title": "", "cover": "", "sub": "", "score": None, "desc": ""}
                self.stack.append(tag)
                return
        if not self.in_result or self.cur is None:
            self.stack.append(tag)
            return

        if tag == "img" and not self.cur["cover"]:
            src = a.get("src") or a.get("data-src") or ""
            if src:
                self.cur["cover"] = src

        if tag == "a":
            href = a.get("href") or ""
            if not self.cur["id"]:
                m = re.search(r"subject/(\d+)", href) or re.search(r"/game/(\d+)", href)
                if not m:
                    decoded = urllib.parse.unquote(href)
                    m = re.search(r"subject/(\d+)", decoded) or re.search(r"/game/(\d+)", decoded)
                if m:
                    self.cur["id"] = m.group(1)
            # 标题链接在 h3 里；封面链接在 .pic 里先出现，所以这里要独立判断
            if "h3" in self.stack and self.cap is None and not self.cur["title"]:
                self.cap = {"key": "title", "tag": "a", "buf": []}
        elif tag == "span" and "subject-cast" in cls:
            self.cap = {"key": "sub", "tag": "span", "buf": []}
        elif tag == "span" and "rating_nums" in cls:
            self.cap = {"key": "score", "tag": "span", "buf": []}
        elif tag == "p" and self.cur and not self.cur["desc"]:
            self.cap = {"key": "desc", "tag": "p", "buf": []}

        self.stack.append(tag)

    def handle_data(self, data):
        if self.cap is not None:
            self.cap["buf"].append(data)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

        if self.cap is not None and self.cap["tag"] == tag:
            text = re.sub(r"\s+", " ", "".join(self.cap["buf"])).strip()
            key = self.cap["key"]
            if self.cur is not None and text:
                if key == "score":
                    self.cur["score"] = as_float(text)
                elif not self.cur[key]:
                    self.cur[key] = text
            self.cap = None

        if tag == "div":
            self.depth -= 1
            if self.in_result and self.depth < self.result_depth:
                self.in_result = False
                if self.cur and self.cur["title"]:
                    self.items.append(self.cur)
                self.cur = None


def search_douban_html(q: str, cat: str, target_type: str) -> list[dict]:
    url = HOST_WWW + "/j/search?q=%s&start=0&cat=%s" % (urllib.parse.quote(q), cat)
    referer = "https://www.douban.com/search?q=%s" % urllib.parse.quote(q)
    raw = douban_get(url, referer=referer)
    html = raw.decode("utf-8", errors="replace")

    # 豆瓣这个接口有两种返回：
    #   1) 直接 HTML
    #   2) JSON 信封 {"items": ["<div class=\"result\">...", ...], "total": N}
    # 之前只处理了 items 是对象的情况，导致游戏永远搜不到。
    stripped = html.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(html)
            parts = []
            for it in (data.get("items") or []):
                if isinstance(it, dict):
                    parts.append(str(it.get("html") or ""))
                elif isinstance(it, str):
                    parts.append(it)
            html = "\n".join(parts)
        except Exception:
            pass
    elif stripped.startswith("["):
        try:
            data = json.loads(html)
            html = "\n".join(x for x in data if isinstance(x, str))
        except Exception:
            pass

    parser = _SearchHtmlParser()
    parser.feed(html)
    out = []
    for it in parser.items:
        year = ""
        m = re.search(r"(19|20)\d{2}", it.get("desc") or it.get("sub") or "")
        if m:
            year = m.group(0)
        out.append({
            "id": it["id"],
            "title": it["title"],
            "year": year,
            "cover": it["cover"],
            "sub": it.get("sub") or it.get("desc") or "",
            "score": it.get("score"),
            "type": target_type,
        })
    return out[:20]


def normalize_date(value) -> str:
    """豆瓣的 pubdate 可能是数组、也可能是 '2014-11-12(中国大陆)'，统一成 YYYY-MM-DD。"""
    if isinstance(value, list):
        value = value[0] if value else ""
    s = str(value or "").strip()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{4})-(\d{1,2})", s)
    if m:
        return "%04d-%02d-01" % (int(m.group(1)), int(m.group(2)))
    m = re.search(r"(\d{4})", s)
    if m:
        return m.group(1) + "-01-01"
    return ""


def upgrade_douban_image(url: str) -> str:
    """搜索结果给的是缩略图（68×96 那种），换成豆瓣的大图地址。"""
    if not url:
        return url
    url = re.sub(r"/spic/", "/lpic/", url)                       # 游戏：/spic/s123.jpg -> /lpic/s123.jpg
    url = re.sub(r"/view/photo/(?:s|m|icon)_", "/view/photo/l_", url)  # 海报
    url = re.sub(r"/view/subject/s/", "/view/subject/l/", url)   # 图书
    return url


def extract_original_name(cast: str) -> str:
    """从豆瓣搜索结果的 subject-cast 里抠出英文原名，例如：
    '原名: Hollow Knight / 别名: 空洞骑士'  -> 'Hollow Knight'"""
    if not cast:
        return ""
    m = re.search(r"原名[:：]\s*([^/｜|]+)", cast)
    if not m:
        return ""
    name = m.group(1).strip()
    if re.search(r"[\u4e00-\u9fff]", name):
        return ""
    return name


_HTTP_UA = DOUBAN_UA
_STEAM_CACHE: dict = {}
_CDN_FAILS = 0
_CDN_DOWN = False
_STEAM_FAILS = 0
_BGM_FAILS = 0
_STEAM_DOWN = False
_BGM_DOWN = False


def call_with_timeout(fn, seconds: float, default=None):
    """跑一个函数，最多等 seconds 秒（防止 DNS/握手卡死整批任务）。"""
    import threading as _th
    box: dict = {}

    def _run():
        try:
            box["v"] = fn()
        except Exception:
            box["v"] = default

    th = _th.Thread(target=_run, daemon=True)
    th.start()
    th.join(seconds)
    return box.get("v", default)


def http_json(url: str, timeout: float = 8.0, headers: dict | None = None):
    """轻量 JSON 请求：不重试、不抛大异常，失败由调用方兜。"""
    _assert_host_not_restricted(url)
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": _HTTP_UA, "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        _raise_for_access_restriction(exc, url)
        raise


def steam_search_appid(name: str):
    """Steam 搜索：先国区中文，再英文。返回 (appid, 官方名)。"""
    global _STEAM_FAILS, _STEAM_DOWN
    key = (name or "").strip().lower()
    if not key or _STEAM_DOWN:
        return None, None
    if key in _STEAM_CACHE:
        return _STEAM_CACHE[key]
    res = (None, None)
    for lang, cc in (("schinese", "CN"), ("english", "US")):
        try:
            url = ("https://store.steampowered.com/api/storesearch/?term=%s&l=%s&cc=%s"
                   % (urllib.parse.quote(name), lang, cc))
            data = http_json(url, timeout=7)
            items = data.get("items") or []
            if items and items[0].get("id"):
                res = (items[0]["id"], items[0].get("name"))
                _STEAM_FAILS = 0
                break
        except Exception:
            _STEAM_FAILS += 1
            if _STEAM_FAILS >= 2:
                _STEAM_DOWN = True
            continue
    _STEAM_CACHE[key] = res
    return res


def steam_cover_candidates(names: list[str]) -> list[tuple[str, str | None]]:
    """Steam 官方 600×900 竖版海报，2x 优先。不含 header.jpg（横图会变形）。"""
    out: list[tuple[str, str | None]] = []
    seen = set()
    for name in names or []:
        appid, _ = steam_search_appid(name)
        if not appid or appid in seen:
            continue
        seen.add(appid)
        ref = "https://store.steampowered.com/app/%s/" % appid
        for host in ("https://shared.akamai.steamstatic.com/store_item_assets/steam/apps",
                     "https://steamcdn-a.akamaihd.net/steam/apps"):
            for fname in ("library_600x900_2x.jpg", "library_600x900.jpg"):
                out.append(("%s/%s/%s" % (host, appid, fname), ref))
    return out


def bangumi_cover_candidates(name: str) -> list[tuple[str, str | None]]:
    """主机独占（PS/Switch）兜底：Bangumi 大图。"""
    global _BGM_FAILS, _BGM_DOWN
    if not name or _BGM_DOWN:
        return []
    try:
        url = ("https://api.bgm.tv/search/subject/%s?type=4&responseGroup=small"
               % urllib.parse.quote(name))
        data = http_json(url, timeout=7, headers={
            "User-Agent": "mediavault/1.0 (personal use)", "Accept": "application/json",
        })
        for item in (data.get("list") or [])[:3]:
            imgs = item.get("images") or {}
            img = imgs.get("large") or imgs.get("common")
            if img:
                _BGM_FAILS = 0
                return [(img.replace("http://", "https://"), "https://bgm.tv/")]
    except Exception:
        _BGM_FAILS += 1
        if _BGM_FAILS >= 2:
            _BGM_DOWN = True
        return []
    return []


def fetch_metacritic_score(appid=None, en_name: str = "", zh_name: str = "") -> float | None:
    """Metacritic 评分（0–100）。先 Steam appdetails，再 MC 官方搜索。"""
    if appid:
        try:
            data = http_json("https://store.steampowered.com/api/appdetails?appids=%s" % appid, timeout=7)
            info = (data.get(str(appid)) or {}).get("data") or {}
            mc = info.get("metacritic") or {}
            if isinstance(mc, dict) and mc.get("score"):
                return float(mc["score"])
        except Exception:
            pass

    for q in [x for x in (en_name, zh_name) if x]:
        try:
            url = ("https://backend.metacritic.com/finder/metacritic/search/%s/web"
                   "?offset=0&limit=5&mcoTypeId=13" % urllib.parse.quote(q))
            data = http_json(url, timeout=6, headers={
                "User-Agent": _HTTP_UA, "Accept": "application/json",
            })
            for item in ((data.get("data") or {}).get("items") or []):
                cs = (item.get("criticScoreSummary") or {}).get("score")
                if cs:
                    return float(cs)
        except UpstreamAccessRestricted:
            raise
        except Exception:
            continue
    return None


def _metacritic_text(url: str) -> str:
    _assert_host_not_restricted(url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _HTTP_UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read(6 * 1024 * 1024).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        _raise_for_access_restriction(exc, url)
        raise


def lookup_metacritic(
    title: str,
    media_type: str,
    year=None,
    season: int | None = None,
    part: int | None = None,
) -> dict:
    """Look up movie/TV or TV-season Metacritic critic scores."""
    if media_type not in {"movie", "teleplay"} or not str(title or "").strip():
        return {"status": "unsupported", "score": None, "reason": "UNSUPPORTED_TYPE"}
    try:
        wanted_year = int(str(year)[:4]) if year not in (None, "") else None
    except (TypeError, ValueError):
        wanted_year = None
    query = extract_season(title)["title"] if season is not None else str(title)
    type_id = 2 if media_type == "movie" else 5 if season is not None else 1
    url = (
        "https://backend.metacritic.com/finder/metacritic/search/%s/web"
        "?offset=0&limit=20&mcoTypeId=%d"
        % (urllib.parse.quote(query.strip()), type_id)
    )
    try:
        data = http_json(
            url,
            timeout=10,
            headers={"User-Agent": _HTTP_UA, "Accept": "application/json"},
        )
    except Exception as exc:
        return {
            "status": "error",
            "score": None,
            "reason": "LOOKUP_FAILED",
            "errorType": type(exc).__name__,
        }
    wanted = _rt_normalize_title(query)
    exact = []
    for item in ((data.get("data") or {}).get("items") or []):
        if not isinstance(item, dict):
            continue
        if media_type == "movie" and item.get("type") != "movie":
            continue
        if media_type == "teleplay" and season is None and item.get("type") != "show":
            continue
        if season is not None:
            if item.get("type") != "season":
                continue
            candidate = extract_season(item.get("title") or "")
            if candidate["season"] != season:
                continue
            if _rt_normalize_title(candidate["title"]) != wanted:
                continue
        else:
            if _rt_normalize_title(item.get("title")) != wanted:
                continue
        item_year = item.get("premiereYear")
        if not item_year:
            date_match = re.match(r"(\d{4})", str(item.get("releaseDate") or ""))
            item_year = int(date_match.group(1)) if date_match else None
        if season is None and wanted_year is not None and item_year != wanted_year:
            continue
        score = (item.get("criticScoreSummary") or {}).get("score")
        if score is None and item.get("slug"):
            try:
                page = _metacritic_text(
                    "https://www.metacritic.com/%s/%s/"
                    % (
                        "movie" if media_type == "movie" else "tv",
                        str(item["slug"]).strip("/"),
                    )
                )
                score_match = re.search(r'"ratingValue":(\d{1,3})', page)
                if score_match:
                    score = int(score_match.group(1))
            except Exception:
                pass
        try:
            score = int(score)
        except (TypeError, ValueError):
            continue
        if not 1 <= score <= 100:
            continue
        exact.append({
            "title": str(item.get("title") or "").strip(),
            "year": item_year,
            "season": season,
            "part": part,
            "score": score,
            "url": (
                "https://www.metacritic.com/%s/%s/"
                % (
                    "movie" if media_type == "movie" else "tv",
                    str(item.get("slug")).strip("/"),
                )
                if item.get("slug")
                else "https://www.metacritic.com/"
            ),
        })
    if len(exact) == 1:
        return {
            "status": "matched",
            "score": exact[0]["score"],
            "candidate": exact[0],
        }
    if len(exact) > 1:
        return {
            "status": "ambiguous",
            "score": None,
            "reason": "MULTIPLE_EXACT_CANDIDATES",
            "candidates": exact,
        }
    return {"status": "no_match", "score": None, "reason": "NO_EXACT_MATCH"}


RT_ALGOLIA_URL = (
    "https://79FRDP12PN-dsn.algolia.net/1/indexes/content_rt/query"
)
RT_ALGOLIA_APPLICATION_ID = "79FRDP12PN"
RT_ALGOLIA_SEARCH_KEY = "175588f6e5f8319b27702e4cc4013561"


def _rt_normalize_title(value) -> str:
    """Normalize a release title for an exact identity check."""
    text = unicodedata.normalize("NFKC", html_mod.unescape(str(value or "")))
    text = text.casefold().replace("&", "and").replace("’", "'")
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def _rt_request_json(query: str) -> dict:
    body = json.dumps({
        "query": query,
        "hitsPerPage": 20,
        "filters": "isEmsSearchable = 1",
    }).encode("utf-8")
    last_error = None
    _assert_host_not_restricted(RT_ALGOLIA_URL)
    for attempt in range(3):
        request = urllib.request.Request(
            RT_ALGOLIA_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": _HTTP_UA,
                "Referer": "https://www.rottentomatoes.com/",
                "X-Algolia-Application-Id": RT_ALGOLIA_APPLICATION_ID,
                "X-Algolia-API-Key": RT_ALGOLIA_SEARCH_KEY,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                return json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            _raise_for_access_restriction(exc, RT_ALGOLIA_URL)
            last_error = exc
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))
        except (OSError, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.6 * (attempt + 1))
    raise last_error


def lookup_rotten_tomatoes(
    title: str,
    media_type: str,
    year=None,
) -> dict:
    """Return a score only for one exact title/type/year match."""
    if media_type not in {"movie", "teleplay"} or not str(title or "").strip():
        return {
            "status": "unsupported",
            "score": None,
            "reason": "UNSUPPORTED_MEDIA_TYPE",
        }
    try:
        wanted_year = int(str(year)[:4]) if year not in (None, "") else None
    except (TypeError, ValueError):
        wanted_year = None
    if wanted_year is None:
        return {"status": "no_match", "score": None, "reason": "MISSING_YEAR"}

    wanted_title = _rt_normalize_title(title)
    if not wanted_title:
        return {"status": "no_match", "score": None, "reason": "EMPTY_TITLE"}
    try:
        payload = _rt_request_json(str(title).strip())
    except Exception as exc:
        return {
            "status": "error",
            "score": None,
            "reason": "LOOKUP_FAILED",
            "errorType": type(exc).__name__,
        }
    hits = payload.get("hits") if isinstance(payload, dict) else []
    allowed_types = {"movie"} if media_type == "movie" else {"tv", "tvSeries"}
    exact = []
    seen = set()
    for hit in hits if isinstance(hits, list) else []:
        if not isinstance(hit, dict):
            continue
        if str(hit.get("type") or "").casefold() not in allowed_types:
            continue
        try:
            candidate_year = int(hit.get("releaseYear"))
        except (TypeError, ValueError):
            continue
        if candidate_year != wanted_year:
            continue
        aliases = hit.get("titles") if isinstance(hit.get("titles"), list) else []
        names = [hit.get("title"), *aliases]
        if not any(_rt_normalize_title(name) == wanted_title for name in names):
            continue
        score_data = hit.get("rottenTomatoes")
        value = (
            score_data.get("criticsScore")
            if isinstance(score_data, dict)
            else None
        )
        if isinstance(value, bool):
            continue
        try:
            score = int(value)
        except (TypeError, ValueError):
            continue
        if not 0 <= score <= 100:
            continue
        identity = str(
            hit.get("emsId")
            or hit.get("objectID")
            or hit.get("vanity")
            or (hit.get("title"), candidate_year, score)
        )
        if identity in seen:
            continue
        seen.add(identity)
        exact.append({
            "title": str(hit.get("title") or "").strip(),
            "year": candidate_year,
            "type": str(hit.get("type") or "").strip(),
            "score": score,
            "vanity": str(hit.get("vanity") or "").strip(),
            "url": (
                "https://www.rottentomatoes.com/%s/%s"
                % ("m" if media_type == "movie" else "tv", hit.get("vanity"))
                if hit.get("vanity")
                else "https://www.rottentomatoes.com/"
            ),
        })
    if len(exact) == 1:
        return {
            "status": "matched",
            "score": exact[0]["score"],
            "candidate": exact[0],
        }
    if len(exact) > 1:
        return {
            "status": "ambiguous",
            "score": None,
            "reason": "MULTIPLE_EXACT_CANDIDATES",
            "candidates": exact,
        }
    return {"status": "no_match", "score": None, "reason": "NO_EXACT_MATCH"}


def fetch_rotten_tomatoes_score(
    title: str,
    media_type: str,
    year=None,
) -> int | None:
    """Best-effort score lookup for new movie/TV imports."""
    return lookup_rotten_tomatoes(title, media_type, year).get("score")


def _rt_text(url: str) -> str:
    _assert_host_not_restricted(url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _HTTP_UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read(4 * 1024 * 1024).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        _raise_for_access_restriction(exc, url)
        raise


def lookup_rotten_tomatoes_season(
    title: str,
    season: int,
    year=None,
) -> dict:
    """Resolve the critic score from one Rotten Tomatoes TV season page."""
    if not str(title or "").strip() or not season:
        return {"status": "unsupported", "score": None, "reason": "MISSING_SEASON"}
    try:
        wanted_year = int(str(year)[:4]) if year not in (None, "") else None
    except (TypeError, ValueError):
        wanted_year = None
    wanted_title = _rt_normalize_title(title)
    try:
        payload = _rt_request_json(str(title).strip())
    except Exception as exc:
        return {
            "status": "error",
            "score": None,
            "reason": "LOOKUP_FAILED",
            "errorType": type(exc).__name__,
        }
    candidates = []
    seen = set()
    for hit in payload.get("hits", []) if isinstance(payload, dict) else []:
        if not isinstance(hit, dict):
            continue
        if str(hit.get("type") or "").casefold() not in {"tv", "tvseries"}:
            continue
        aliases = hit.get("titles") if isinstance(hit.get("titles"), list) else []
        if not any(
            _rt_normalize_title(name) == wanted_title
            for name in [hit.get("title"), *aliases]
        ):
            continue
        vanity = str(hit.get("vanity") or "").strip()
        if not vanity or vanity in seen:
            continue
        seen.add(vanity)
        candidates.append(hit)

    exact = []
    for hit in candidates:
        vanity = str(hit.get("vanity") or "").strip()
        url = "https://www.rottentomatoes.com/tv/%s/s%02d" % (vanity, season)
        try:
            page = _rt_text(url)
        except Exception:
            continue
        if not re.search(
            r"<title>\s*[^<]+:\s*Season\s+%d\s*\|\s*Rotten Tomatoes" % season,
            page,
            re.I,
        ):
            continue
        date_match = re.search(r'"dateCreated":"(\d{4})-', page)
        season_year = int(date_match.group(1)) if date_match else None
        if wanted_year is not None and season_year != wanted_year:
            continue
        score_match = re.search(
            r'"criticsScore":\{"averageRating":"[^"]*".{0,800}?'
            r'"scorePercent":"(\d{1,3})%"',
            page,
        )
        if not score_match:
            continue
        score = int(score_match.group(1))
        if 0 <= score <= 100:
            exact.append({
                "title": "%s: Season %d" % (
                    str(hit.get("title") or title).strip(),
                    season,
                ),
                "seriesTitle": str(hit.get("title") or title).strip(),
                "season": season,
                "year": season_year,
                "type": "tvSeason",
                "score": score,
                "url": url,
            })
    if len(exact) == 1:
        return {
            "status": "matched",
            "score": exact[0]["score"],
            "candidate": exact[0],
            "matchMode": "season",
        }
    if len(exact) > 1:
        return {
            "status": "ambiguous",
            "score": None,
            "reason": "MULTIPLE_SEASON_CANDIDATES",
            "candidates": exact,
        }
    return {"status": "no_match", "score": None, "reason": "NO_SEASON_MATCH"}


IMDB_SUGGESTION_URL = "https://v3.sg.media-imdb.com/suggestion/x/%s.json"
IMDB_GRAPHQL_URL = "https://api.graphql.imdb.com/"


def _imdb_suggestion_json(title: str) -> dict:
    url = IMDB_SUGGESTION_URL % urllib.parse.quote(str(title).strip(), safe="")
    return http_json(
        url,
        timeout=12,
        headers={
            "User-Agent": _HTTP_UA,
            "Accept": "application/json",
        },
    )


def _imdb_graphql_query(query: str, variables: dict | None = None) -> dict:
    body = json.dumps({
        "query": query,
        "variables": variables or {},
    }).encode("utf-8")
    _assert_host_not_restricted(IMDB_GRAPHQL_URL)
    request = urllib.request.Request(
        IMDB_GRAPHQL_URL,
        data=body,
        method="POST",
        headers={
            "User-Agent": _HTTP_UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://www.imdb.com",
            "Referer": "https://www.imdb.com/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        _raise_for_access_restriction(exc, IMDB_GRAPHQL_URL)
        raise
    return payload if isinstance(payload, dict) else {}


def _imdb_graphql_title(imdb_id: str) -> dict:
    payload = _imdb_graphql_query(
        "query ($id: ID!) { title(id: $id) { id titleText { text } "
        "releaseYear { year } titleType { id } "
        "ratingsSummary { aggregateRating } } }",
        {"id": imdb_id},
    )
    value = ((payload.get("data") or {}).get("title")
             if isinstance(payload, dict) else None)
    return value if isinstance(value, dict) else {}


def lookup_imdb(
    title: str,
    media_type: str,
    year=None,
    season: int | None = None,
    part: int | None = None,
) -> dict:
    """Find one exact IMDb title and return its 0-10 aggregate rating."""
    if media_type not in {"movie", "teleplay"} or not str(title or "").strip():
        return {
            "status": "unsupported",
            "score": None,
            "reason": "UNSUPPORTED_MEDIA_TYPE",
        }
    try:
        wanted_year = int(str(year)[:4]) if year not in (None, "") else None
    except (TypeError, ValueError):
        wanted_year = None
    if wanted_year is None:
        return {"status": "no_match", "score": None, "reason": "MISSING_YEAR"}

    wanted_title = _rt_normalize_title(title)
    try:
        payload = _imdb_suggestion_json(title)
    except Exception as exc:
        return {
            "status": "error",
            "score": None,
            "reason": "LOOKUP_FAILED",
            "errorType": type(exc).__name__,
        }
    allowed_types = (
        {"movie", "tvmovie"}
        if media_type == "movie"
        else {"tvseries", "tvminiseries"}
    )
    candidates = []
    seen = set()
    for hit in payload.get("d", []) if isinstance(payload, dict) else []:
        if not isinstance(hit, dict):
            continue
        imdb_id = str(hit.get("id") or "")
        if not re.fullmatch(r"tt\d{5,}", imdb_id):
            continue
        if str(hit.get("qid") or "").casefold() not in allowed_types:
            continue
        try:
            candidate_year = int(hit.get("y"))
        except (TypeError, ValueError):
            candidate_year = None
        if season is None and candidate_year != wanted_year:
            continue
        if _rt_normalize_title(hit.get("l")) != wanted_title:
            continue
        if imdb_id in seen:
            continue
        seen.add(imdb_id)
        candidates.append(hit)

    if season is not None:
        return _lookup_imdb_season_candidates(
            candidates,
            season,
            wanted_year,
            allowed_types,
            part=part,
        )

    verified = []
    for hit in candidates:
        try:
            detail = _imdb_graphql_title(str(hit["id"]))
        except Exception as exc:
            return {
                "status": "error",
                "score": None,
                "reason": "DETAIL_LOOKUP_FAILED",
                "errorType": type(exc).__name__,
            }
        title_value = detail.get("titleText") or {}
        release_value = detail.get("releaseYear") or {}
        type_value = detail.get("titleType") or {}
        rating_value = detail.get("ratingsSummary") or {}
        try:
            detail_year = int(release_value.get("year"))
        except (TypeError, ValueError):
            continue
        detail_type = str(type_value.get("id") or "").casefold()
        if detail_year != wanted_year or detail_type not in allowed_types:
            continue
        if _rt_normalize_title(
            title_value.get("text") if isinstance(title_value, dict) else ""
        ) != wanted_title:
            continue
        rating = (
            rating_value.get("aggregateRating")
            if isinstance(rating_value, dict)
            else None
        )
        if isinstance(rating, bool):
            continue
        try:
            score = round(float(rating), 1)
        except (TypeError, ValueError):
            continue
        if not 0 <= score <= 10:
            continue
        verified.append({
            "imdbId": str(hit["id"]),
            "title": str((title_value or {}).get("text") or hit.get("l") or "").strip(),
            "year": detail_year,
            "type": detail_type,
            "score": score,
            "url": "https://www.imdb.com/title/%s/" % hit["id"],
        })
    if len(verified) == 1:
        return {
            "status": "matched",
            "score": verified[0]["score"],
            "candidate": verified[0],
        }
    if len(verified) > 1:
        return {
            "status": "ambiguous",
            "score": None,
            "reason": "MULTIPLE_EXACT_CANDIDATES",
            "candidates": verified,
        }
    return {"status": "no_match", "score": None, "reason": "NO_EXACT_MATCH"}


_IMDB_EPISODES_CACHE: dict[str, list[dict]] = {}


def _imdb_series_episodes(imdb_id: str) -> list[dict]:
    if imdb_id in _IMDB_EPISODES_CACHE:
        return _IMDB_EPISODES_CACHE[imdb_id]
    query = (
        "query ($id: ID!, $after: ID) { title(id: $id) { episodes { "
        "episodes(first: 250, after: $after) { pageInfo { endCursor hasNextPage } "
        "edges { node { id releaseYear { year } "
        "ratingsSummary { aggregateRating } "
        "series { episodeNumber { seasonNumber episodeNumber } } } } } } } }"
    )
    episodes = []
    cursor = None
    while True:
        payload = _imdb_graphql_query(query, {"id": imdb_id, "after": cursor})
        block = (
            ((payload.get("data") or {}).get("title") or {}).get("episodes") or {}
        ).get("episodes") or {}
        for edge in block.get("edges") or []:
            node = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(node, dict):
                continue
            number = (node.get("series") or {}).get("episodeNumber")
            if not isinstance(number, dict):
                continue
            episodes.append({
                "id": str(node.get("id") or ""),
                "season": number.get("seasonNumber"),
                "episode": number.get("episodeNumber"),
                "year": (node.get("releaseYear") or {}).get("year"),
                "rating": (node.get("ratingsSummary") or {}).get("aggregateRating"),
            })
        page_info = block.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break
    _IMDB_EPISODES_CACHE[imdb_id] = episodes
    return episodes


def _lookup_imdb_season_candidates(
    candidates: list[dict],
    season: int,
    wanted_year: int,
    allowed_types: set[str],
    part: int | None = None,
) -> dict:
    exact = []
    for hit in candidates:
        imdb_id = str(hit.get("id") or "")
        try:
            episodes = _imdb_series_episodes(imdb_id)
        except Exception as exc:
            return {
                "status": "error",
                "score": None,
                "reason": "EPISODE_LOOKUP_FAILED",
                "errorType": type(exc).__name__,
            }
        season_episodes = sorted(
            [
                episode for episode in episodes
                if episode.get("season") == season
            ],
            key=lambda episode: episode.get("episode") or 0,
        )
        if part in {1, 2} and len(season_episodes) >= 2:
            midpoint = (len(season_episodes) + 1) // 2
            season_episodes = (
                season_episodes[:midpoint]
                if part == 1
                else season_episodes[midpoint:]
            )
        selected = [
            episode for episode in season_episodes
            if isinstance(episode.get("rating"), (int, float))
        ]
        if not selected:
            continue
        years = [
            int(episode["year"])
            for episode in selected
            if episode.get("year") not in (None, "")
        ]
        season_year = min(years) if years else None
        if season_year is not None and season_year != wanted_year:
            continue
        score = round(
            sum(float(episode["rating"]) for episode in selected) / len(selected),
            1,
        )
        if not 0 <= score <= 10:
            continue
        exact.append({
            "imdbId": imdb_id,
            "title": "%s: Season %d" % (
                str(hit.get("l") or "").strip(),
                season,
            ),
            "seriesTitle": str(hit.get("l") or "").strip(),
            "season": season,
            "year": season_year,
            "type": str(hit.get("qid") or "").casefold(),
            "score": score,
            "episodeCount": len(selected),
            "ratingMethod": "MEAN_EPISODE_RATINGS",
            "url": "https://www.imdb.com/title/%s/" % imdb_id,
        })
    if len(exact) == 1:
        return {
            "status": "matched",
            "score": exact[0]["score"],
            "candidate": exact[0],
            "matchMode": "season",
        }
    if len(exact) > 1:
        distinct = {
            (item["imdbId"], item["score"])
            for item in exact
        }
        if len(distinct) == 1:
            return {
                "status": "matched",
                "score": exact[0]["score"],
                "candidate": exact[0],
                "matchMode": "season",
            }
        return {
            "status": "ambiguous",
            "score": None,
            "reason": "MULTIPLE_SEASON_CANDIDATES",
            "candidates": exact,
        }
    return {"status": "no_match", "score": None, "reason": "NO_SEASON_MATCH"}


def lookup_imdb_by_id(
    imdb_id: str,
    media_type: str,
    year=None,
    season: int | None = None,
    part: int | None = None,
    allow_type_mismatch: bool = False,
) -> dict:
    """Resolve a known IMDb title/season override and verify it first."""
    if not re.fullmatch(r"tt\d{5,}", imdb_id or ""):
        return {"status": "no_match", "score": None, "reason": "INVALID_IMDB_ID"}
    try:
        wanted_year = int(str(year)[:4]) if year not in (None, "") else None
    except (TypeError, ValueError):
        wanted_year = None
    try:
        detail = _imdb_graphql_title(imdb_id)
    except Exception as exc:
        return {
            "status": "error",
            "score": None,
            "reason": "DETAIL_LOOKUP_FAILED",
            "errorType": type(exc).__name__,
        }
    title = str((detail.get("titleText") or {}).get("text") or "").strip()
    detail_type = str((detail.get("titleType") or {}).get("id") or "").casefold()
    detail_year = (detail.get("releaseYear") or {}).get("year")
    allowed = (
        {"movie", "tvmovie"}
        if media_type == "movie"
        else {"tvseries", "tvminiseries"}
    )
    if detail_type not in allowed and not allow_type_mismatch:
        return {"status": "no_match", "score": None, "reason": "TYPE_MISMATCH"}
    if season is not None:
        if wanted_year is None:
            return {"status": "no_match", "score": None, "reason": "MISSING_YEAR"}
        return _lookup_imdb_season_candidates(
            [{"id": imdb_id, "l": title, "qid": detail_type}],
            season,
            wanted_year,
            {detail_type},
            part=part,
        )
    if wanted_year is not None and detail_year not in (None, wanted_year):
        return {"status": "no_match", "score": None, "reason": "YEAR_MISMATCH"}
    rating = (detail.get("ratingsSummary") or {}).get("aggregateRating")
    if isinstance(rating, bool):
        rating = None
    try:
        score = round(float(rating), 1)
    except (TypeError, ValueError):
        return {"status": "no_match", "score": None, "reason": "NO_RATING"}
    if not 0 <= score <= 10:
        return {"status": "no_match", "score": None, "reason": "INVALID_RATING"}
    return {
        "status": "matched",
        "score": score,
        "candidate": {
            "imdbId": imdb_id,
            "title": title,
            "year": detail_year or wanted_year,
            "type": detail_type,
            "score": score,
            "url": "https://www.imdb.com/title/%s/" % imdb_id,
        },
    }


BANGUMI_SEARCH_URL = "https://api.bgm.tv/v0/search/subjects?limit=20&offset=0"
ANILIST_GRAPHQL_URL = "https://graphql.anilist.co"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"


def _post_json_url(
    url: str,
    payload: dict,
    proxy: str | None = None,
    timeout: float = 20.0,
) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "User-Agent": _HTTP_UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    effective_proxy = proxy or _configured_https_proxy()
    handler = urllib.request.ProxyHandler(
        {"http": effective_proxy, "https": effective_proxy}
        if effective_proxy
        else {}
    )
    opener = urllib.request.build_opener(handler)
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))


def lookup_bangumi(
    title: str,
    year=None,
    season: int | None = None,
    proxy: str | None = None,
) -> dict:
    """Resolve one exact Bangumi anime entry and its 0-10 rating."""
    query = str(title or "").strip()
    try:
        wanted_year = int(str(year)[:4]) if year not in (None, "") else None
    except (TypeError, ValueError):
        wanted_year = None
    if not query or wanted_year is None:
        return {"status": "no_match", "score": None, "reason": "MISSING_IDENTITY"}
    wanted_full = _rt_normalize_title(query)
    parsed_query = extract_season(query)
    wanted_base = _rt_normalize_title(parsed_query["title"])
    try:
        payload = _post_json_url(
            BANGUMI_SEARCH_URL,
            {"keyword": parsed_query["title"], "filter": {"type": [2]}},
            proxy=proxy,
        )
    except Exception as exc:
        return {
            "status": "error",
            "score": None,
            "reason": "LOOKUP_FAILED",
            "errorType": type(exc).__name__,
        }
    exact = []
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or "")
        try:
            item_year = int(date[:4])
        except (TypeError, ValueError):
            continue
        if item_year != wanted_year:
            continue
        names = [
            str(item.get("name") or ""),
            str(item.get("name_cn") or ""),
        ]
        full_match = any(_rt_normalize_title(name) == wanted_full for name in names)
        season_match = False
        for name in names:
            candidate = extract_season(name)
            if _rt_normalize_title(candidate["title"]) != wanted_base:
                continue
            if season is None:
                season_match = candidate["season"] is None
            else:
                season_match = candidate["season"] == season
            if season_match:
                break
        if not full_match and not season_match:
            continue
        rating = item.get("rating") or {}
        try:
            score = round(float(rating.get("score")), 1)
        except (TypeError, ValueError):
            continue
        if not 0 <= score <= 10:
            continue
        exact.append({
            "bangumiId": item.get("id"),
            "title": str(item.get("name_cn") or item.get("name") or "").strip(),
            "originalTitle": str(item.get("name") or "").strip(),
            "year": item_year,
            "season": extract_season(item.get("name_cn") or item.get("name"))["season"],
            "score": score,
            "url": "https://bgm.tv/subject/%s" % item.get("id"),
        })
    if len(exact) == 1:
        return {
            "status": "matched",
            "score": exact[0]["score"],
            "candidate": exact[0],
        }
    if len(exact) > 1:
        return {
            "status": "ambiguous",
            "score": None,
            "reason": "MULTIPLE_EXACT_CANDIDATES",
            "candidates": exact,
        }
    return {"status": "no_match", "score": None, "reason": "NO_EXACT_MATCH"}


def lookup_anime_aliases(
    title: str,
    year=None,
    season: int | None = None,
    proxy: str | None = None,
) -> dict:
    """Use AniList names to bridge Chinese/Japanese titles to English releases."""
    query = str(title or "").strip()
    if not query:
        return {"status": "no_match", "aliases": []}
    try:
        wanted_year = int(str(year)[:4]) if year not in (None, "") else None
    except (TypeError, ValueError):
        wanted_year = None
    graphql = (
        "query ($search: String) { Page(perPage: 20) { media("
        "search: $search, type: ANIME, sort: SEARCH_MATCH) { id "
        "title { romaji english native } synonyms startDate { year } } } }"
    )
    try:
        payload = _post_json_url(
            ANILIST_GRAPHQL_URL,
            {
                "query": graphql,
                "variables": {"search": query},
            },
            proxy=proxy,
        )
    except Exception as exc:
        return {
            "status": "error",
            "aliases": [],
            "reason": "LOOKUP_FAILED",
            "errorType": type(exc).__name__,
        }
    wanted_full = _rt_normalize_title(query)
    wanted_base = _rt_normalize_title(extract_season(query)["title"])
    aliases = []
    for media in ((payload.get("data") or {}).get("Page") or {}).get("media", []):
        if not isinstance(media, dict):
            continue
        start = media.get("startDate") or {}
        try:
            item_year = int(start.get("year"))
        except (TypeError, ValueError):
            continue
        if wanted_year is not None and item_year != wanted_year:
            continue
        title_obj = media.get("title") or {}
        names = [
            title_obj.get("english"),
            title_obj.get("romaji"),
            title_obj.get("native"),
            *(media.get("synonyms") or []),
        ]
        names = [str(name).strip() for name in names if str(name or "").strip()]
        accepted = False
        for name in names:
            parsed = extract_season(name)
            if _rt_normalize_title(name) == wanted_full:
                accepted = True
                break
            if season is not None and parsed["season"] == season:
                accepted = True
                break
            if _rt_normalize_title(parsed["title"]) != wanted_base:
                continue
            if season is None or parsed["season"] == season:
                accepted = True
                break
        if accepted:
            aliases.extend(names)
    aliases = list(dict.fromkeys(aliases))
    return {
        "status": "matched" if aliases else "no_match",
        "aliases": aliases,
        "reason": None if aliases else "NO_EXACT_MATCH",
    }


def _wikidata_json(params: dict, proxy: str | None = None) -> dict:
    query = urllib.parse.urlencode({
        **params,
        "format": "json",
        "origin": "*",
    })
    effective_proxy = proxy or _configured_https_proxy()
    handler = urllib.request.ProxyHandler(
        {"http": effective_proxy, "https": effective_proxy}
        if effective_proxy
        else {}
    )
    opener = urllib.request.build_opener(handler)
    last_error = None
    _assert_host_not_restricted(WIKIDATA_API_URL)
    for attempt in range(3):
        request = urllib.request.Request(
            WIKIDATA_API_URL + "?" + query,
            headers={
                "User-Agent": _HTTP_UA + " personal-media-vault/2.5.7",
                "Accept": "application/json",
            },
        )
        try:
            with opener.open(request, timeout=20) as response:
                return json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            _raise_for_access_restriction(exc, WIKIDATA_API_URL)
            last_error = exc
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
        except (OSError, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
    raise last_error


def _wikidata_value(entity: dict, property_id: str):
    try:
        return entity["claims"][property_id][0]["mainsnak"]["datavalue"]["value"]
    except (KeyError, IndexError, TypeError):
        return None


def _wikidata_year(entity: dict) -> int | None:
    for property_id in ("P577", "P580", "P571", "P574"):
        value = _wikidata_value(entity, property_id)
        if isinstance(value, dict):
            match = re.match(r"[+-](\d{4,})", str(value.get("time") or ""))
            if match:
                return int(match.group(1))
    return None


def lookup_wikidata(
    title: str,
    media_type: str,
    year=None,
    proxy: str | None = None,
) -> dict:
    """Resolve a film/TV item to stable external IDs through Wikidata."""
    query = str(title or "").strip()
    try:
        wanted_year = int(str(year)[:4]) if year not in (None, "") else None
    except (TypeError, ValueError):
        wanted_year = None
    if not query or media_type not in {"movie", "teleplay"}:
        return {"status": "no_match", "reason": "MISSING_IDENTITY"}
    search_language = (
        "ja"
        if re.search(r"[\u3040-\u30ff]", query)
        else "en"
        if re.search(r"[A-Za-z]", query)
        else "zh"
    )
    try:
        search = _wikidata_json({
            "action": "wbsearchentities",
            "search": query,
            "language": search_language,
            "uselang": search_language,
            "type": "item",
            "limit": "10",
        }, proxy=proxy)
    except Exception as exc:
        return {
            "status": "error",
            "reason": "LOOKUP_FAILED",
            "errorType": type(exc).__name__,
        }
    ids = [
        str(item.get("id") or "")
        for item in search.get("search", [])
        if re.fullmatch(r"Q\d+", str(item.get("id") or ""))
    ][:10]
    if not ids:
        return {"status": "no_match", "reason": "NO_SEARCH_RESULT"}
    try:
        entities_payload = _wikidata_json({
            "action": "wbgetentities",
            "ids": "|".join(ids),
            "props": "labels|aliases|claims",
            "languages": "zh|zh-cn|zh-hans|zh-hant|ja|en",
        }, proxy=proxy)
    except Exception as exc:
        return {
            "status": "error",
            "reason": "DETAIL_LOOKUP_FAILED",
            "errorType": type(exc).__name__,
        }
    wanted_title = _rt_normalize_title(query)
    movie_types = {"Q11424"}
    tv_types = {"Q5398426", "Q1259759", "Q117467246"}
    allowed = movie_types if media_type == "movie" else tv_types
    exact = []
    for qid in ids:
        entity = (entities_payload.get("entities") or {}).get(qid) or {}
        labels = entity.get("labels") or {}
        aliases = entity.get("aliases") or {}
        names = [
            value.get("value")
            for value in labels.values()
            if isinstance(value, dict)
        ]
        for values in aliases.values():
            if isinstance(values, list):
                names.extend(
                    value.get("value")
                    for value in values
                    if isinstance(value, dict)
                )
        if not any(_rt_normalize_title(name) == wanted_title for name in names):
            continue
        instance = _wikidata_value(entity, "P31") or {}
        instance_id = str(instance.get("id") or "")
        if instance_id and instance_id not in allowed:
            continue
        entity_year = _wikidata_year(entity)
        if wanted_year is not None and entity_year is not None and entity_year != wanted_year:
            continue
        imdb_id = str(_wikidata_value(entity, "P345") or "").strip()
        rt_id = str(_wikidata_value(entity, "P1258") or "").strip()
        metacritic_id = str(_wikidata_value(entity, "P1712") or "").strip()
        if not imdb_id and not rt_id and not metacritic_id:
            continue
        exact.append({
            "wikidataId": qid,
            "imdbId": imdb_id or None,
            "rottenTomatoesId": rt_id or None,
            "metacriticId": metacritic_id or None,
            "englishTitle": str((labels.get("en") or {}).get("value") or "").strip(),
            "year": entity_year,
            "type": media_type,
        })
    if len(exact) == 1:
        return {"status": "matched", "candidate": exact[0]}
    if len(exact) > 1:
        return {
            "status": "ambiguous",
            "reason": "MULTIPLE_EXACT_CANDIDATES",
            "candidates": exact,
        }
    return {"status": "no_match", "reason": "NO_EXACT_MATCH"}


def lookup_wikidata_by_id(
    wikidata_id: str,
    media_type: str,
    proxy: str | None = None,
) -> dict:
    """Refresh claims from an already resolved Wikidata item ID."""
    qid = str(wikidata_id or "").strip()
    if not re.fullmatch(r"Q\d+", qid) or media_type not in {"movie", "teleplay"}:
        return {"status": "no_match", "reason": "INVALID_WIKIDATA_ID"}
    try:
        payload = _wikidata_json({
            "action": "wbgetentities",
            "ids": qid,
            "props": "labels|aliases|claims",
            "languages": "zh|zh-cn|zh-hans|zh-hant|ja|en",
        }, proxy=proxy)
    except Exception as exc:
        return {
            "status": "error",
            "reason": "LOOKUP_FAILED",
            "errorType": type(exc).__name__,
        }
    entity = (payload.get("entities") or {}).get(qid) or {}
    if not entity or entity.get("missing") is not None:
        return {"status": "no_match", "reason": "ENTITY_NOT_FOUND"}
    instance = _wikidata_value(entity, "P31") or {}
    instance_id = str(instance.get("id") or "")
    allowed = (
        {"Q11424"}
        if media_type == "movie"
        else {"Q5398426", "Q1259759", "Q117467246"}
    )
    if instance_id and instance_id not in allowed:
        return {"status": "no_match", "reason": "TYPE_MISMATCH"}
    imdb_id = str(_wikidata_value(entity, "P345") or "").strip()
    rt_id = str(_wikidata_value(entity, "P1258") or "").strip()
    metacritic_id = str(_wikidata_value(entity, "P1712") or "").strip()
    if not imdb_id and not rt_id and not metacritic_id:
        return {"status": "no_match", "reason": "NO_EXTERNAL_ID"}
    label = str(
        ((entity.get("labels") or {}).get("en") or {}).get("value") or ""
    ).strip()
    return {
        "status": "matched",
        "candidate": {
            "wikidataId": qid,
            "imdbId": imdb_id or None,
            "rottenTomatoesId": rt_id or None,
            "metacriticId": metacritic_id or None,
            "englishTitle": label,
            "year": _wikidata_year(entity),
            "type": media_type,
        },
    }


def _metacritic_path(
    metacritic_id: str,
    media_type: str,
    season: int | None = None,
) -> str | None:
    value = str(metacritic_id or "").strip().strip("/")
    if not value or value.startswith(("http://", "https://")) or ".." in value:
        return None
    if "/" not in value:
        value = "%s/%s" % (
            "movie" if media_type == "movie" else "tv",
            value,
        )
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        return None
    if season is not None and not re.search(r"/season-\d+$", value):
        value += "/season-%d" % season
    return value.rstrip("/")


def lookup_external_ids(
    candidate: dict,
    *,
    season: int | None = None,
    part: int | None = None,
    year=None,
    fields: set[str] | None = None,
) -> dict:
    """Fetch ratings using stable IDs instead of searching by localized titles."""
    requested = set(fields or {
        "imdbScore",
        "rottenTomatoesScore",
        "metacriticScore",
    })
    result = {
        "imdbScore": None,
        "rottenTomatoesScore": None,
        "metacriticScore": None,
        "errors": [],
    }
    imdb_id = str(candidate.get("imdbId") or "").strip()
    if imdb_id and "imdbScore" in requested and season is not None:
        resolved = lookup_imdb_by_id(
            imdb_id,
            str(candidate.get("type") or ""),
            year,
            season=season,
            part=part,
        )
        if resolved.get("status") == "matched":
            result["imdbScore"] = resolved.get("score")
        elif resolved.get("status") == "error":
            result["errors"].append({
                "source": "imdb",
                "errorType": resolved.get("errorType") or resolved.get("reason"),
            })
    elif imdb_id and "imdbScore" in requested:
        try:
            detail = _imdb_graphql_title(imdb_id)
            rating = (detail.get("ratingsSummary") or {}).get("aggregateRating")
            score = round(float(rating), 1)
            if 0 <= score <= 10:
                result["imdbScore"] = score
        except Exception as exc:
            result["errors"].append({"source": "imdb", "errorType": type(exc).__name__})
    rt_id = str(candidate.get("rottenTomatoesId") or "").strip().strip("/")
    if rt_id and "rottenTomatoesScore" in requested and season is None:
        try:
            page = _rt_text("https://www.rottentomatoes.com/" + rt_id)
            match = re.search(
                r'"criticsScore":\{"averageRating":"[^"]*".{0,800}?'
                r'"scorePercent":"(\d{1,3})%"',
                page,
            )
            if match:
                score = int(match.group(1))
                if 0 <= score <= 100:
                    result["rottenTomatoesScore"] = score
        except Exception as exc:
            result["errors"].append({"source": "rt", "errorType": type(exc).__name__})
    metacritic_id = str(candidate.get("metacriticId") or "").strip().strip("/")
    if metacritic_id and "metacriticScore" in requested:
        path = _metacritic_path(
            metacritic_id,
            str(candidate.get("type") or ""),
            season,
        )
        try:
            if not path:
                raise ValueError("INVALID_METACRITIC_ID")
            page = _metacritic_text("https://www.metacritic.com/" + path + "/")
            match = re.search(r'"ratingValue"\s*:\s*"?(\d{1,3})', page)
            if match:
                score = int(match.group(1))
                if 1 <= score <= 100:
                    result["metacriticScore"] = score
        except Exception as exc:
            result["errors"].append({"source": "metacritic", "errorType": type(exc).__name__})
    return result


def strip_tags(html: str, sep: str = " / ") -> str:
    """去标签取文本：</a>、<br> 之类当分隔符，最后反转义、压空格。"""
    text = re.sub(r"(?i)</a\s*>", sep, html)
    text = re.sub(r"(?i)<br\s*/?>", sep, text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?:\s*/\s*)+", " / ", text).strip(" /")
    return text


def split_multi(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\s*/\s*", text or "") if p.strip()]
    out = []
    for p in parts:
        if p not in out:
            out.append(p)
    return out


def fetch_game_detail(subject_id: str) -> dict:
    """解析豆瓣游戏条目页（插件 DoubanGameLoadHandler 用的就是这条路）。"""
    if not subject_id:
        return {}
    url = "https://www.douban.com/game/%s/" % subject_id
    try:
        raw = douban_get(url, referer="https://www.douban.com/", timeout=20)
    except Exception:
        return {}
    t = raw.decode("utf-8", errors="replace")

    out: dict = {"url": url}

    m = re.search(r"<title>(.*?)</title>", t, re.S)
    if m:
        name = re.sub(r"\s*\(豆瓣\)\s*$", "", strip_tags(m.group(1), " ")).strip()
        out["title"] = clean_title(name)

    m = (re.search(r'rating_num[^>]*property="v:average"[^>]*>\s*([\d.]+)', t)
         or re.search(r'property="v:average"[^>]*>\s*([\d.]+)', t))
    if m:
        out["score"] = as_float(m.group(1))

    info: dict[str, str] = {}
    for dt, dd in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", t, re.S):
        label = strip_tags(dt, " ").rstrip(":：").strip()
        if label and label not in info:
            info[label] = strip_tags(dd)
    out["info"] = info

    m = (re.search(r'id="link-report".*?<p>(.*?)</p>', t, re.S)
         or re.search(r'<div class="mod item-desc".*?<p>(.*?)</p>', t, re.S))
    if m:
        out["intro"] = strip_tags(m.group(1), "\n")

    m = (re.search(r'<div class="pic">.*?<img[^>]+src="([^"]+)"', t, re.S)
         or re.search(r'<img[^>]+src="([^"]+/lpic/[^"]+)"', t, re.S))
    if m:
        out["cover"] = m.group(1)

    return out


def _ensure_func_placeholder():
    pass


def grab_first_cover(vault: Path, name: str,
                     candidates: list[tuple[str, str | None]],
                     prefer_rel: str = "") -> str:
    """按顺序试每个候选地址，第一个通过图片校验的就落盘。"""
    seen = set()
    for url, referer in candidates:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            raw = download_image(url, referer)
        except Exception as e:  # noqa: BLE001
            print("[cover] 放弃 %s -> %r" % (url[:70], e), file=sys.stderr)
            log_image_failure("cover_candidate", url, e)
            continue
        ext = mimetypes.guess_extension(sniff_image(raw)) or ".jpg"
        if ext in (".jpe", ".jpeg"):
            ext = ".jpg"
        return _write_attachment(vault, name, ext, raw, prefer_rel=prefer_rel)
    return ""


def fetch_douban_detail(subject_id: str, target_type: str) -> dict:
    """尽力而为地补齐元数据；失败就返回空 dict，不影响主流程。"""
    kind = "book" if target_type == "book" else "movie"
    url = HOST_REXXAR + "/rexxar/api/v2/%s/%s" % (kind, subject_id)
    try:
        raw = douban_get(url, referer="https://m.douban.com/", timeout=6)
        d = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}

    def names(key):
        val = d.get(key) or []
        out = []
        for x in val if isinstance(val, list) else []:
            if isinstance(x, dict) and x.get("name"):
                out.append(str(x["name"]))
            elif isinstance(x, str) and x.strip():
                out.append(x.strip())
        return out

    rating = d.get("rating") or {}
    pic = d.get("pic") or {}
    cover = ""
    if isinstance(pic, dict):
        cover = pic.get("large") or pic.get("normal") or ""
    pub = normalize_date(d.get("pubdate"))
    year = str(d.get("year") or "").strip()

    out = {
        "title": str(d.get("title") or "").strip(),
        "originalTitle": str(d.get("original_title") or "").strip(),
        "intro": str(d.get("intro") or "").strip(),
        "score": as_float(rating.get("value")) if isinstance(rating, dict) else None,
        "cover": cover or "",
        "year": year,
        "datePublished": pub,
    }
    if kind == "book":
        out["authors"] = names("author")
        out["publisher"] = str(d.get("publisher") or "").strip()
        out["pages"] = d.get("pages")
    else:
        out["directors"] = names("directors")
        out["actors"] = names("actors")[:6]
        out["genres"] = [str(g) for g in (d.get("genres") or []) if g]
        out["countries"] = [str(c) for c in (d.get("countries") or []) if c]
    return out


def best_cover_for_title(lib: "Library", title: str, t: str) -> str:
    """按标题重新找封面：Steam 2x 竖版 → Steam 1x → Bangumi → 豆瓣 lpic。"""
    cands: list[tuple[str, str | None]] = []
    clean = clean_title(title)
    en = english_name_of(title)

    if t == "game":
        cands += steam_cover_candidates([x for x in (clean, title, en) if x])
        cands += bangumi_cover_candidates(clean or title)

    queries = [clean, title]
    first = None
    for q in queries:
        if not q:
            continue
        try:
            results = search_douban(q, t)
        except Exception:
            results = []
        if results:
            first = results[0]
            break

    if first:
        if t == "game":
            g = fetch_game_detail(str(first.get("id") or ""))
            page_cover = g.get("cover")
            if page_cover:
                cands.append((page_cover, "https://www.douban.com/game/%s/" % first.get("id")))
        cover = upgrade_douban_image(str(first.get("cover") or ""))
        if cover:
            cands.append((cover, "https://www.douban.com/"))

    return grab_first_cover(lib.vault, safe_filename(title), cands)


def upgrade_games(lib: "Library", progress=None, force_covers: bool = False) -> dict:
    """批量清洗游戏标题、换高清封面、抓 Metacritic 评分。"""
    game_dir = lib.vault / MEDIA_DIR_NAME / "game"
    files = sorted(game_dir.glob("*.md")) if game_dir.is_dir() else []

    # 开跑前先探一下上游：不通就别让用户干等
    # 封面源探测：不通就整批跳过（避免每张图白等）
    global _CDN_DOWN, _BGM_DOWN
    def _probe_cover():
        try:
            raw = download_image(
                "https://steamcdn-a.akamaihd.net/steam/apps/814380/library_600x900.jpg")
            return bool(sniff_image(raw))
        except Exception:
            return False
    if not call_with_timeout(_probe_cover, 8, False):
        _CDN_DOWN = True
    def _probe_bgm():
        try:
            url = ("https://api.bgm.tv/search/subject/%E9%80%86%E8%BD%AC%E8%A3%81%E5%88%A4"
                   "?type=4&responseGroup=small")
            d = http_json(url, timeout=6, headers={"User-Agent": "mediavault/1.0", "Accept": "application/json"})
            return bool(d.get("list"))
        except Exception:
            return False
    if not call_with_timeout(_probe_bgm, 8, False):
        _BGM_DOWN = True

    steam_ok = bool(call_with_timeout(
        lambda: steam_search_appid("Sekiro: Shadows Die Twice")[0], 8))
    mc_ok = call_with_timeout(
        lambda: fetch_metacritic_score(None, "Sekiro: Shadows Die Twice"), 8) is not None
    if progress:
        progress(0, len(files), "上游检查",
                 "Steam %s / Metacritic %s" % ("可用" if steam_ok else "不通",
                                                "可用" if mc_ok else "不通"))
    if not steam_ok and not mc_ok:
        stats = {"total": len(files), "title": 0, "cover": 0, "score": 0,
                 "unchanged": 0, "failed": len(files), "offline": True, "changes": []}
        return stats
    if files:
        lib.create_snapshot_backup("upgrade-games")
    lib.log.event("batch_upgrade_started", count=len(files), operation="upgrade-games")
    stats = {"total": len(files), "title": 0, "cover": 0, "score": 0,
             "unchanged": 0, "failed": 0}
    changes: list[dict] = []

    for i, path in enumerate(files, 1):
        rel = str(path.relative_to(lib.vault)).replace("\\", "/")
        try:
            rec = read_note(path, lib.vault)
            if not rec:
                stats["failed"] += 1
                continue
            old_title = rec["title"]
            en = english_name_of(old_title)
            new_title = clean_title(old_title)

            # 别名表优先（不依赖豆瓣）；没有才去豆瓣条目页找原名
            if not en:
                en = TITLE_EN_ALIAS.get(old_title) or TITLE_EN_ALIAS.get(new_title) or ""
            if not en:
                sid = str(rec.get("id") or "").strip()
                if sid:
                    try:
                        info = (fetch_game_detail(sid) or {}).get("info") or {}
                        cand = (info.get("原名") or "").strip()
                        en = english_name_of(cand) or (cand if re.search(r"[A-Za-z]", cand) else "")
                    except Exception:
                        en = ""

            appid, official = call_with_timeout(
                lambda: steam_search_appid(en or new_title), 8, (None, None)) or (None, None)
            if not appid and en and new_title:
                appid, official = call_with_timeout(
                    lambda: steam_search_appid(new_title), 8, (None, None)) or (None, None)
            if official and has_cjk(official) and not has_cjk(new_title):
                new_title = clean_title(official)

            score = call_with_timeout(
                lambda: fetch_metacritic_score(appid, en, new_title), 8)

            cands = call_with_timeout(
                lambda: steam_cover_candidates([x for x in (en, new_title) if x]), 10, []) or []
            cands += call_with_timeout(
                lambda: bangumi_cover_candidates(new_title), 8, []) or []
            cur_img = str(rec.get("image") or "")
            image = ""
            skip_cover = False
            before_mtime = 0.0
            if cur_img:
                try:
                    before_mtime = (lib.vault / cur_img).stat().st_mtime
                except OSError:
                    before_mtime = 0.0
            if cur_img and not force_covers:
                try:
                    fp = lib.vault / cur_img
                    if fp.is_file() and fp.stat().st_size > 60 * 1024:
                        skip_cover = True
                except OSError:
                    pass
            if cands and not skip_cover:
                prefer = cur_img if cur_img.startswith("attachments/") else ""
                image = call_with_timeout(
                    lambda: grab_first_cover(lib.vault, safe_filename(new_title), cands,
                                             prefer_rel=prefer), 12, "") or ""

            payload: dict = {}
            if new_title and new_title != old_title:
                payload["title"] = new_title
            if score is not None:
                cur_score = rec.get("score")
                cur_meta = rec.get("metacriticScore")
                try:
                    changed = cur_score is None or abs(float(cur_score) - float(score)) > 1e-6
                except (TypeError, ValueError):
                    changed = True
                try:
                    meta_changed = cur_meta is None or abs(float(cur_meta) - float(score)) > 1e-6
                except (TypeError, ValueError):
                    meta_changed = True
                changed = changed or meta_changed
                if changed:
                    payload["score"] = score
                    payload["metacriticScore"] = score
            if image and image != rec.get("image"):
                payload["image"] = image

            if not payload:
                stats["unchanged"] += 1
                if progress:
                    progress(i, len(files), old_title, "无变化")
                continue

            payload["revision"] = rec["revision"]
            try:
                lib.update(rel, payload)
            except Exception:
                if image and image != rec.get("image"):
                    try:
                        lib.resolve_attachment(image).unlink(missing_ok=True)
                    except (OSError, ValueError):
                        pass
                raise
            if "title" in payload:
                stats["title"] += 1
            if image:
                try:
                    after_mtime = (lib.vault / image).stat().st_mtime
                except OSError:
                    after_mtime = 0.0
                if image != rec.get("image") or after_mtime != before_mtime:
                    stats["cover"] += 1
            if "score" in payload:
                stats["score"] += 1
            changes.append({"file": path.name, "from": old_title,
                            "to": new_title, "score": score,
                            "cover": bool(image)})
            if progress:
                progress(i, len(files), new_title,
                         "评分 %s%s" % (score if score is not None else "—",
                                        " 新封面" if image else ""))
        except Exception as e:  # noqa: BLE001
            stats["failed"] += 1
            if progress:
                progress(i, len(files), path.stem, "失败: %s" % str(e)[:40])

    stats["changes"] = changes
    lib.log.event(
        "batch_upgrade_finished",
        operation="upgrade-games",
        total=stats["total"],
        failed=stats["failed"],
    )
    return stats


def fix_image(lib: "Library", rel: str, expected_revision=None) -> dict:
    """修复封面：网络地址 → 存到本地；本地文件损坏/缺失 → 按标题重新找一张。"""
    global _CDN_FAILS, _CDN_DOWN, _STEAM_FAILS, _STEAM_DOWN, _BGM_FAILS, _BGM_DOWN
    _CDN_FAILS = 0
    _CDN_DOWN = False
    _STEAM_FAILS = 0
    _STEAM_DOWN = False
    _BGM_FAILS = 0
    _BGM_DOWN = False

    lib.load_if_changed()
    rel = normalize_vault_relative_path(rel)
    with lib._path_lock(rel):
        target = lib._note_target(rel)
        if not target.is_file():
            raise ValueError("文件不存在")

        raw = target.read_bytes()
        lib._check_revision(target, rel, expected_revision)
        parsed_frontmatter = parse_frontmatter(raw)
        if not parsed_frontmatter.ok:
            message = "；".join(item.message for item in parsed_frontmatter.diagnostics)
            raise ValueError("Frontmatter 不在可安全修改的子集内：%s" % message)
        fm = {key: field.value for key, field in parsed_frontmatter.fields.items()}
        identity = normalize_identity(fm.get("schemaVersion"), fm.get("mediaId"))
        if identity["schemaFuture"]:
            raise ValueError("该笔记使用未来 schemaVersion，当前版本只读")
        if not identity["writeAllowed"]:
            raise ValueError("该笔记的 schemaVersion 或 mediaId 异常，当前版本只读")
        cached = next((item for item in lib.items if item.get("path") == rel), None)
        if cached and cached.get("mediaIdDuplicate"):
            raise ValueError("该笔记与其他笔记重复使用 mediaId，当前版本只读")
        old = fm.get("image")
        old = old.strip() if isinstance(old, str) else ""

        rec = read_note(target, lib.vault)
        title = rec["title"] if rec else target.stem
        t = rec["type"] if rec else "movie"

        # 已经是本地文件：先判断它到底是不是一张能用的图
        if old and not old.startswith("http"):
            p = lib.resolve_attachment(old)
            if p.is_file() and p.stat().st_size > 1500:
                head = p.read_bytes()[:64]
                if sniff_image(head):
                    raise ValueError("封面是正常的，不需要修复")

        new_path = ""
        if old.startswith("http"):
            new_path = save_remote_image(lib.vault, safe_filename(title), old)
        if not new_path:
            new_path = best_cover_for_title(lib, title, t)
        if not new_path:
            raise ValueError("没找到可用的封面，可以手动换一张")

        patch_result = patch_frontmatter(raw, {"image": new_path})
        if not patch_result.ok:
            message = "；".join(item.message for item in patch_result.diagnostics)
            raise ValueError("Frontmatter Patch 失败：%s" % message)
        text, nl, bom = decode_note(patch_result.data)
        new_text, _ = sync_callout_result(text, {"image": new_path}, t)
        new_raw = encode_note(new_text, nl, bom)

        def verify():
            if target.read_bytes() != new_raw:
                raise NoteWriteError("写入后内容校验失败")
            if read_note(target, lib.vault) is None:
                raise NoteWriteError("写入后笔记无法解析")
            if target.read_bytes() != new_raw:
                raise NoteWriteError("校验期间笔记再次发生变化")

        try:
            try:
                atomic_write_note(
                    target,
                    new_raw,
                    lib.backup_root,
                    rel,
                    verify,
                    expected_revision=content_revision(raw),
                )
            except ConcurrentModificationError as exc:
                latest = read_note(target, lib.vault) or {
                    "path": rel,
                    "revision": content_revision(target.read_bytes()),
                }
                lib.log.event("external_modification_conflict", level="warning", path=rel)
                raise RevisionConflict(latest) from exc
        except Exception:
            try:
                lib.resolve_attachment(new_path).unlink(missing_ok=True)
            except (OSError, ValueError):
                pass
            raise

        rec2 = read_note(target, lib.vault)
        if rec2 is None:
            raise NoteWriteError("写入后笔记无法读取")
        lib._cache_item(rel, rec2)
        lib.log.event("image_repaired", path=rel, image=new_path)
        return rec2


def find_existing(lib: "Library", title: str, t: str) -> dict | None:
    folder = lib.media_root / t
    if not folder.exists():
        return None
    want = clean_title(title).strip().lower()
    for p in folder.rglob("*.md"):
        rec = read_note(p, lib.vault)
        if rec and clean_title(rec["title"]).strip().lower() == want:
            return rec
    return None


def import_from_douban(lib: "Library", payload: dict) -> dict:
    global _CDN_FAILS, _CDN_DOWN, _STEAM_FAILS, _STEAM_DOWN, _BGM_FAILS, _BGM_DOWN
    _CDN_FAILS = 0
    _CDN_DOWN = False
    _STEAM_FAILS = 0
    _STEAM_DOWN = False
    _BGM_FAILS = 0
    _BGM_DOWN = False

    target_type = str(payload.get("type") or "movie").strip().lower()
    if target_type not in CREATE_TYPES:
        target_type = "movie"
    subject_id = str(payload.get("id") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("缺少标题")

    existing = find_existing(lib, title, target_type)
    if existing:
        queued = _queue_rating_job(lib, existing)
        return dict(existing, existed=True, ratingJob=queued)

    # ---- 游戏：解析条目页 + Steam 封面 + Metacritic ----
    if target_type == "game":
        g = fetch_game_detail(subject_id) if subject_id else {}
        info = g.get("info") or {}
        genres = split_multi(info.get("类型", ""))
        platforms = split_multi(info.get("平台", ""))

        raw_title = g.get("title") or title
        merged_title = clean_title(raw_title)
        en_name = english_name_of(raw_title) or english_name_of(title)

        appid, official_name = steam_search_appid(en_name or merged_title)
        if not appid and en_name and merged_title:
            appid, official_name = steam_search_appid(merged_title)
        if official_name and has_cjk(official_name) and not has_cjk(merged_title):
            merged_title = clean_title(official_name)

        publisher = info.get("发行商") or info.get("开发商") or ""
        date_pub = normalize_date(
            info.get("发行日期") or info.get("预计上市时间") or payload.get("year"))

        # 评分：游戏一律用 Metacritic（0–100）
        metacritic_score = fetch_metacritic_score(appid, en_name, merged_title)
        douban_score = as_float(payload.get("score"))
        score = metacritic_score if metacritic_score is not None else douban_score

        subject_url = ("https://www.douban.com/game/%s/" % subject_id) if subject_id else "https://www.douban.com/"
        cands: list[tuple[str, str | None]] = []
        cands += steam_cover_candidates([x for x in (en_name, merged_title) if x])
        cands += bangumi_cover_candidates(merged_title)
        cover = upgrade_douban_image(g.get("cover") or str(payload.get("cover") or "").strip())
        if cover:
            cands.append((cover, subject_url))
        image_path = grab_first_cover(lib.vault, safe_filename(merged_title), cands)

        rec = create_note(lib, {
            "type": "game",
            "title": merged_title,
            "score": score,
            "metacriticScore": metacritic_score,
            "doubanScore": douban_score,
            "status": False,
            "datePublished": date_pub,
            "desc": g.get("intro") or "",
            "creator": publisher,
            "tags": genres,
            "platform": platforms,
            "imagePath": image_path or None,
            "imageUrl": None if image_path else (cover or None),
            "imageReferer": subject_url,
        })
        rec["enriched"] = bool(g)
        return rec

    detail = fetch_douban_detail(subject_id, target_type) if subject_id else {}

    merged_title = clean_title(detail.get("title") or title)
    year = detail.get("year") or str(payload.get("year") or "").strip()
    date_pub = detail.get("datePublished") or normalize_date(year)

    score = detail.get("score")
    if score is None:
        score = as_float(payload.get("score"))

    cover = detail.get("cover") or str(payload.get("cover") or "").strip()
    cover = upgrade_douban_image(cover)

    subject_url = ("https://movie.douban.com/subject/%s/" % subject_id) if subject_id else "https://www.douban.com/"

    new_payload = {
        "type": target_type,
        "title": merged_title,
        "originalTitle": detail.get("originalTitle") or "",
        "score": score,
        "doubanScore": score,
        "status": False,
        "datePublished": date_pub,
        "desc": detail.get("intro") or str(payload.get("desc") or "").strip(),
        "imageUrl": cover or None,
        "imageReferer": subject_url,
    }

    if target_type == "book":
        authors = detail.get("authors") or []
        new_payload["creator"] = " / ".join(authors) if authors else str(payload.get("sub") or "").strip()
        new_payload["publisher"] = detail.get("publisher") or ""
        new_payload["pages"] = detail.get("pages")
        new_payload["tags"] = []
    else:
        directors = detail.get("directors") or []
        new_payload["creator"] = " / ".join(directors)
        new_payload["actor"] = " / ".join(detail.get("actors") or [])
        new_payload["country"] = detail.get("countries") or []
        new_payload["tags"] = detail.get("genres") or []

    rec = create_note(lib, new_payload)
    queued = _queue_rating_job(lib, rec)
    rec["ratingJob"] = queued
    rec["enriched"] = bool(detail)
    return rec


# ------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "MediaVault/2.0"

    def log_message(self, fmt, *args):  # 安静一点
        if "--verbose" in sys.argv:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---------- helpers
    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _json(self, obj, code: int = 200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, data, "application/json; charset=utf-8")

    def _error(self, msg: str, code: int = 400):
        self._json({"ok": False, "error": msg}, code)

    def _request_origin(self) -> str:
        port = self.server.server_address[1]
        return "http://127.0.0.1:%d" % port

    def _validate_request(self, write: bool = False) -> bool:
        port = self.server.server_address[1]
        allowed_hosts = {
            "127.0.0.1:%d" % port,
            "localhost:%d" % port,
        }
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in allowed_hosts:
            self._error("无效的 Host", 403)
            return False
        if not write:
            return True

        origin = (self.headers.get("Origin") or "").strip().rstrip("/")
        client = (self.headers.get("X-MediaVault-Client") or "").strip()
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        allowed_origins = {
            self._request_origin(),
            self._request_origin().replace("127.0.0.1", "localhost", 1),
        }
        if origin and origin not in allowed_origins:
            self._error("写入请求来源不受信任", 403)
            return False
        if not origin and client != APP_ID and fetch_site != "same-origin":
            self._error("写入请求缺少可信来源标记", 403)
            return False
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            self._error("写入请求必须使用 application/json", 415)
            return False
        return True

    # ---------- routes
    def do_GET(self):
        if not self._validate_request(False):
            return
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if route in ("/", "/index.html"):
            return self._static("index.html", "text/html; charset=utf-8")
        if route.startswith("/static/"):
            name = route[len("/static/"):]
            if "/" in name or ".." in name:
                return self._error("bad path", 400)
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if name.endswith(".js"):
                ctype = "text/javascript; charset=utf-8"
            if name.endswith(".css"):
                ctype = "text/css; charset=utf-8"
            return self._static(name, ctype)

        if route == "/api/library":
            if qs.get("refresh"):
                LIB.load()
            items = LIB.snapshot()
            return self._json({"ok": True, "count": len(items), "items": items,
                               "vault": LIB.vault.name, "doubanLogin": douban_logged_in(),
                               "obsidian": OBSIDIAN_MODE, "root": str(LIB.vault),
                               "app": APP_ID, "version": APP_VERSION,
                               "warnings": LIB.load_warnings})

        if route == "/api/ratings":
            path = (qs.get("path", [""])[0] or "").strip() or None
            if path:
                path = normalize_vault_relative_path(path)
            queue = RATING_QUEUE or RatingJobQueue(LIB.data_dir)
            return self._json(queue.snapshot(path))

        if route == "/api/douban/session":
            return self._json(douban_session_status())

        if route == "/api/search":
            q = (qs.get("q", [""])[0] or "").strip()
            t = (qs.get("type", ["movie"])[0] or "movie").strip().lower()
            if not q:
                return self._json({"ok": True, "items": [], "type": t})
            try:
                items = search_douban(q, t)
                LIB.log.event(
                    "metadata_search",
                    type=t,
                    resultCount=len(items),
                )
                return self._json({"ok": True, "items": items, "type": t})
            except Exception as e:  # noqa: BLE001
                LIB.log.event(
                    "metadata_search_failed",
                    level="warning",
                    type=t,
                    errorType=type(e).__name__,
                )
                return self._json({"ok": False, "items": [], "error": "豆瓣搜索失败：%s" % e})

        if route == "/api/diag":
            return self._diag(qs)

        if route == "/api/migration/scan":
            try:
                from migration import public_scan_result

                return self._json({
                    "ok": True,
                    "readOnly": True,
                    "scan": public_scan_result(LIB.vault),
                })
            except Exception as exc:  # noqa: BLE001
                LIB.log.event(
                    "migration_scan_failed",
                    level="error",
                    errorType=type(exc).__name__,
                )
                return self._json({
                    "ok": False,
                    "readOnly": True,
                    "error": "迁移只读扫描失败",
                }, 500)

        if route == "/api/migration/reports":
            name = (qs.get("name", [""])[0] or "").strip()
            if (
                not name
                or "/" in name
                or "\\" in name
                or ".." in name
                or Path(name).suffix.lower() not in {".json", ".md"}
            ):
                return self._error("bad report name", 400)
            root = (LIB.data_dir / "migration" / "reports").resolve()
            target = (root / name).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                return self._error("bad report name", 400)
            if not target.is_file():
                return self._error("report not found", 404)
            try:
                raw = target.read_bytes()
            except OSError:
                return self._error("report read failed", 500)
            ctype = (
                "application/json; charset=utf-8"
                if target.suffix.lower() == ".json"
                else "text/markdown; charset=utf-8"
            )
            return self._send(200, raw, ctype, "no-store")

        if route == "/media":
            return self._media(qs.get("p", [""])[0])

        if route == "/api/ping":
            return self._json({
                "ok": True,
                "app": APP_ID,
                "version": APP_VERSION,
                "protocolVersion": APP_PROTOCOL_VERSION,
                "instanceId": APP_INSTANCE_ID,
                "vaultRoot": str(LIB.vault.resolve()) if LIB is not None else "",
            })

        return self._error("not found", 404)

    def do_POST(self):
        if not self._validate_request(True):
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in (
            "/api/item", "/api/create", "/api/import", "/api/fix-image",
            "/api/quit", "/api/delete", "/api/upgrade-games", "/api/backup",
            "/api/retry-ratings", "/api/douban/session", "/api/douban/logout",
        ):
            return self._error("not found", 404)
        try:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._error("Content-Length 无效", 400)
            if length <= 0 or length > 24_000_000:
                return self._error("请求体为空或过大", 400)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                return self._error("请求体必须是 JSON 对象", 400)

            if parsed.path == "/api/upgrade-games":
                force = payload.get("force", False)
                if not isinstance(force, bool):
                    return self._error("force 必须是布尔值", 400)
                res = upgrade_games(LIB, force_covers=force)
                return self._json({"ok": True, **res})

            if parsed.path == "/api/backup":
                raw_label = payload.get("label") or "manual"
                if not isinstance(raw_label, str):
                    return self._error("备份标签必须是字符串", 400)
                label = raw_label.strip()[:80]
                target = LIB.create_snapshot_backup(label, include_attachments=True)
                return self._json({
                    "ok": True,
                    "backup": target.name,
                    "count": sum(1 for _ in target.rglob("*.md")),
                    "fileCount": sum(
                        1 for p in target.rglob("*")
                        if p.is_file() and p.name != "manifest.json"
                    ),
                })

            if parsed.path == "/api/douban/session":
                cookie = payload.get("cookie")
                try:
                    path = save_douban_session(LIB.data_dir, cookie)
                except DoubanAuthError as exc:
                    return self._error(str(exc), 400)
                _HEADER_CACHE["key"] = None
                _HEADER_CACHE["headers"] = None
                LIB.log.event(
                    "douban_session_saved",
                    source="standalone",
                    stored=path.name,
                )
                return self._json(douban_session_status())

            if parsed.path == "/api/douban/logout":
                try:
                    cleared = clear_douban_session(LIB.data_dir)
                except DoubanAuthError as exc:
                    return self._error(str(exc), 500)
                _HEADER_CACHE["key"] = None
                _HEADER_CACHE["headers"] = None
                LIB.log.event(
                    "douban_session_cleared",
                    source="standalone",
                    changed=cleared,
                )
                return self._json(douban_session_status())

            if parsed.path == "/api/delete":
                raw_rel = payload.get("path")
                if not isinstance(raw_rel, str):
                    return self._error("path 必须是字符串", 400)
                rel = raw_rel.strip()
                if not rel:
                    return self._error("缺少 path", 400)
                revision = payload.get("revision")
                if revision is not None and not isinstance(revision, str):
                    return self._error("revision 必须是字符串", 400)
                result = LIB.delete_with_details(rel, expected_revision=revision)
                return self._json({
                    "ok": bool(result.get("ok")),
                    "deleted": rel if result.get("ok") else None,
                    "trash": result.get("trash"),
                    "attachment": result.get("attachment"),
                    "attachmentMoved": bool(result.get("attachmentMoved")),
                    "attachmentShared": bool(result.get("attachmentShared")),
                })

            if parsed.path == "/api/quit":
                LIB.log.event("app_stopping", source="shell")
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            if parsed.path == "/api/fix-image":
                rel = payload.get("path")
                if not isinstance(rel, str):
                    return self._error("path 必须是字符串", 400)
                if not rel:
                    return self._error("缺少 path", 400)
                revision = payload.get("revision")
                if revision is not None and not isinstance(revision, str):
                    return self._error("revision 必须是字符串", 400)
                return self._json({
                    "ok": True,
                    "item": fix_image(LIB, rel, expected_revision=revision),
                })

            if parsed.path == "/api/create":
                rec = create_note(LIB, payload)
                queued = (
                    _queue_rating_job(LIB, rec)
                    if payload.get("autoRatings") is True
                    else None
                )
                return self._json({
                    "ok": True,
                    "item": rec,
                    "ratingJob": queued,
                })

            if parsed.path == "/api/import":
                rec = import_from_douban(LIB, payload)
                return self._json({"ok": True, "item": rec})

            if parsed.path == "/api/retry-ratings":
                rel = payload.get("path")
                if not isinstance(rel, str):
                    return self._error("path 必须是字符串", 400)
                rel = normalize_vault_relative_path(rel)
                item = next(
                    (entry for entry in LIB.snapshot() if entry.get("path") == rel),
                    None,
                )
                if item is None:
                    return self._error("文件不存在", 404)
                job = _queue_rating_job(LIB, item)
                return self._json({"ok": True, "ratingJob": job})

            rel = payload.get("path")
            if not isinstance(rel, str):
                return self._error("path 必须是字符串", 400)
            if not rel:
                return self._error("缺少 path", 400)
            rec = LIB.update(rel, payload)
            if rec is None:
                return self._error("没有可更新的字段", 400)
            return self._json({"ok": True, "item": rec})
        except RevisionConflict as e:
            return self._json({
                "ok": False,
                "error": str(e),
                "conflict": True,
                "latest": e.current,
            }, 409)
        except json.JSONDecodeError:
            return self._error("JSON 解析失败", 400)
        except UnicodeDecodeError:
            return self._error("请求体不是有效的 UTF-8", 400)
        except ValueError as e:
            return self._error(str(e), 400)
        except Exception as e:  # noqa: BLE001
            request_id = uuid.uuid4().hex[:10]
            LIB.log.event(
                "api_exception",
                level="error",
                requestId=request_id,
                route=urllib.parse.urlparse(self.path).path,
                errorType=type(e).__name__,
            )
            print("[api:%s] %s" % (request_id, type(e).__name__), file=sys.stderr)
            return self._error("服务器内部错误（编号 %s）" % request_id, 500)

    def _diag(self, qs):
        """自检：真正打一次豆瓣，把状态码 / 返回前几十字节 / 用了哪些请求头 都报出来。"""
        q = (qs.get("q", ["星际穿越"])[0] or "星际穿越").strip()
        kind = (qs.get("type", ["movie"])[0] or "movie").strip().lower()
        out = {"ok": False, "query": q, "type": kind, "hasCookie": douban_logged_in()}
        try:
            headers = douban_headers()
            out["headers"] = sorted(headers.keys())
            if kind == "game":
                url = HOST_WWW + "/j/search?q=%s&start=0&cat=3114" % urllib.parse.quote(q)
                raw = douban_get(url, referer=HOST_WWW + "/")
                out["bytes"] = len(raw)
                text = raw.decode("utf-8", errors="replace")
                out["sample"] = text[:300]
                out["resultBlocks"] = text.count('class="result"')
                parser = _SearchHtmlParser()
                parser.feed(text)
                out["items"] = len(parser.items)
                out["ok"] = len(parser.items) > 0
            else:
                url = HOST_MOVIE + "/j/subject_suggest?q=" + urllib.parse.quote(q)
                raw = douban_get(url, referer=HOST_MOVIE + "/")
                out["bytes"] = len(raw)
                out["sample"] = raw[:160].decode("utf-8", errors="replace")
                data = json.loads(raw.decode("utf-8", errors="replace"))
                out["items"] = len(data) if isinstance(data, list) else 0
                out["ok"] = isinstance(data, list)
            out["url"] = url
        except Exception as e:  # noqa: BLE001
            out["error"] = "%s: %s" % (type(e).__name__, e)
        return self._json(out)

    # ---------- static / media
    def _static(self, name: str, ctype: str):
        p = (WEB_DIR / name)
        if not p.is_file():
            return self._error("not found", 404)
        body = p.read_bytes()
        # 前端资源一律不缓存，避免改了代码浏览器还拿旧的
        cache = "no-cache, no-store, must-revalidate"
        return self._send(200, body, ctype, cache)

    def _media(self, rel: str):
        if not rel:
            return self._error("missing p", 400)

        # 远程图片走代理：带上豆瓣需要的 Cookie/Referer，避免 403
        scheme = (urllib.parse.urlsplit(rel).scheme or "").lower()
        if scheme in ("http", "https"):
            try:
                host = (urllib.parse.urlparse(rel).hostname or "").lower()
                if host.endswith("douban.com") or host.endswith("doubanio.com"):
                    request_headers = douban_headers()
                    request_headers["Referer"] = "https://movie.douban.com/"
                else:
                    # 外部图床：不带任何 Cookie，只用普通 UA
                    request_headers = {"User-Agent": DOUBAN_UA}
                raw, _ = fetch_remote_bytes(
                    rel,
                    headers=request_headers,
                    max_bytes=12 * 1024 * 1024,
                    connect_timeout=5,
                    read_timeout=12,
                )
            except UnsafeRemoteURLError as exc:
                log_image_failure("media_proxy", rel, exc)
                return self._error(str(exc), 403)
            except Exception as exc:
                log_image_failure("media_proxy", rel, exc)
                return self._error("image fetch failed", 502)
            ctype = sniff_image(raw)
            if not ctype:
                return self._error("远程内容不是有效图片", 415)
            return self._send(200, raw, ctype, "max-age=3600")

        try:
            target = LIB.resolve_attachment(urllib.parse.unquote(rel))
        except (OSError, ValueError):
            return self._error("bad path", 400)
        if not target.is_file():
            return self._error("not found", 404)
        ext = target.suffix.lower()
        expected_ctype = MIME_EXTRA.get(ext)
        if ext not in ALLOWED_IMAGE_EXTS or not expected_ctype:
            return self._error("附件不是允许的图片类型", 415)
        try:
            if target.stat().st_size > MAX_LOCAL_IMAGE_BYTES:
                return self._error("图片超过大小限制", 413)
            body = target.read_bytes()
        except OSError:
            return self._error("read error", 500)
        ctype = sniff_image(body)
        if not ctype or ctype != expected_ctype:
            return self._error("附件内容与图片类型不符", 415)
        return self._send(200, body, ctype, "max-age=86400")


class MediaVaultHTTPServer(ThreadingHTTPServer):
    """Exclusive local listener; never bind the same port in two instances."""

    allow_reuse_address = False
    daemon_threads = True


def bind_http_server(start: int):
    for port in range(start, start + 30):
        try:
            httpd = MediaVaultHTTPServer(("127.0.0.1", port), Handler)
            return httpd, int(httpd.server_address[1])
        except OSError:
            continue
    raise SystemExit("找不到可用端口")


def start_server(vault=None, port_pref: int = 8765, quiet: bool = False):
    """启动 HTTP 服务并返回 (httpd, port, url)。供窗口版和命令行版共用。"""
    global LIB, OBSIDIAN_MODE, MEDIA_DIR_NAME

    if vault:
        vault = Path(vault).resolve()
    elif is_obsidian_vault(DEFAULT_VAULT):
        vault = DEFAULT_VAULT
    else:
        vault = default_data_root()

    OBSIDIAN_MODE = is_obsidian_vault(vault)
    if not OBSIDIAN_MODE:
        MEDIA_DIR_NAME = STANDALONE_DIR_NAME
        ensure_library(vault)
    elif not (vault / MEDIA_DIR_NAME).exists():
        raise SystemExit("在 %s 下找不到 %s 目录，请用 --vault 指定库根目录" % (vault, MEDIA_DIR_NAME))

    LIB = Library(vault, data_dir=data_directory_for_vault(vault))
    t0 = time.time()
    items = LIB.load()
    elapsed = (time.time() - t0) * 1000
    LIB.log.event(
        "app_started",
        version=APP_VERSION,
        vaultName=vault.name,
        obsidian=OBSIDIAN_MODE,
        itemCount=len(items),
    )
    start_rating_worker(LIB)
    httpd, port = bind_http_server(port_pref)
    url = "http://127.0.0.1:%d/" % port

    if not quiet:
        print("=" * 52)
        print("  影音库 · 本地网页版")
        print("  库目录：%s" % vault)
        print("  已载入：%d 篇（%.0f ms）" % (len(items), elapsed))
        print("  地址：  %s" % url)
        print("=" * 52)
    return httpd, port, url


def main():
    args = sys.argv[1:]
    if "--migrate-vault" in args or any(
        flag in args
        for flag in ("--scan", "--dry-run", "--apply", "--rollback")
    ):
        from migrate_vault import main as migration_main

        raise SystemExit(migration_main(args))

    port_pref = 8765
    vault = None
    open_browser = True
    upgrade_flag = False
    force_covers = False
    for i, a in enumerate(args):
        if a == "--vault" and i + 1 < len(args):
            vault = args[i + 1]
        elif a == "--port" and i + 1 < len(args):
            port_pref = int(args[i + 1])
        elif a == "--no-browser":
            open_browser = False
        elif a == "--upgrade-games":
            upgrade_flag = True
        elif a == "--force-covers":
            force_covers = True

    if upgrade_flag:
        _root = Path(vault).resolve() if vault else DEFAULT_VAULT
        global LIB, OBSIDIAN_MODE
        OBSIDIAN_MODE = is_obsidian_vault(_root)
        LIB = Library(_root, data_dir=data_directory_for_vault(_root))
        LIB.load()
        print("开始批量升级游戏（目录：%s）" % (_root / MEDIA_DIR_NAME / "game"))

        def _show(i, total, name, note):
            print("  [%3d/%3d] %-34s %s" % (i, total, str(name)[:32], note))

        res = upgrade_games(LIB, progress=_show, force_covers=force_covers)
        if res.get("offline"):
            print("上游（Steam / Metacritic）都连不上，本次没有改动任何笔记。")
            print("网络恢复后重跑：python server.py --upgrade-games --vault <库路径>")
        else:
            print("完成：共 %(total)d 个，改标题 %(title)d，换封面 %(cover)d，"
                  "写评分 %(score)d，无变化 %(unchanged)d，失败 %(failed)d" % res)
        return

    httpd, port, url = start_server(vault=vault, port_pref=port_pref)
    print("  关闭：  在这个窗口按 Ctrl+C")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        stop_rating_worker()
        httpd.server_close()
        if LIB is not None:
            LIB.log.event("app_stopped")


if __name__ == "__main__":
    main()
