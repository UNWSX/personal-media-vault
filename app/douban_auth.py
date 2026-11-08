from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


SESSION_DIR_NAME = "secrets"
SESSION_FILE_NAME = "douban-session.bin"
MAX_COOKIE_BYTES = 16 * 1024
CRYPTPROTECT_UI_FORBIDDEN = 0x1
_ENTROPY = b"media-vault/douban-session/v1"


class DoubanAuthError(ValueError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    ), buffer


def _dpapi_transform(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise DoubanAuthError("豆瓣登录态加密仅支持 Windows")
    data_in, data_in_buffer = _blob(data)
    entropy, entropy_buffer = _blob(_ENTROPY)
    data_out = DATA_BLOB()
    crypt = ctypes.windll.crypt32
    kernel = ctypes.windll.kernel32
    function = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    description = ctypes.c_wchar_p() if not protect else None
    result = function(
        ctypes.byref(data_in),
        description,
        ctypes.byref(entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(data_out),
    )
    # Keep Python buffers alive until the API call returns.
    _ = data_in_buffer, entropy_buffer
    if not result:
        raise DoubanAuthError("Windows 无法加密或解密豆瓣登录态")
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        if data_out.pbData:
            kernel.LocalFree(data_out.pbData)


def normalize_cookie(value) -> str:
    if not isinstance(value, str):
        raise DoubanAuthError("Cookie 必须是字符串")
    raw = value.strip()
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
    if not raw:
        raise DoubanAuthError("Cookie 不能为空")
    if len(raw.encode("utf-8")) > MAX_COOKIE_BYTES:
        raise DoubanAuthError("Cookie 过大")
    if any(ch in raw for ch in ("\r", "\n", "\x00")):
        raise DoubanAuthError("Cookie 包含无效字符")
    parts = []
    names = set()
    for part in raw.split(";"):
        item = part.strip()
        if not item:
            continue
        if "=" not in item:
            raise DoubanAuthError("Cookie 格式无效")
        name, _ = item.split("=", 1)
        name = name.strip()
        if not name:
            raise DoubanAuthError("Cookie 名称为空")
        names.add(name)
        parts.append(item)
    if "dbcl2" not in names:
        raise DoubanAuthError("没有检测到豆瓣登录 Cookie（缺少 dbcl2）")
    return "; ".join(parts)


def session_path(data_dir: Path) -> Path:
    return Path(data_dir) / SESSION_DIR_NAME / SESSION_FILE_NAME


def save_session(data_dir: Path, cookie: str) -> Path:
    normalized = normalize_cookie(cookie)
    target = session_path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "version": 1,
            "cookie": normalized,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    protected = _dpapi_transform(payload, protect=True)
    temp = target.with_name(target.name + ".tmp")
    try:
        with open(temp, "wb") as handle:
            handle.write(protected)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return target


def load_session(data_dir: Path) -> dict | None:
    target = session_path(data_dir)
    try:
        protected = target.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DoubanAuthError("无法读取豆瓣登录态") from exc
    try:
        raw = _dpapi_transform(protected, protect=False)
        payload = json.loads(raw.decode("utf-8"))
        cookie = normalize_cookie(payload.get("cookie"))
        if payload.get("version") != 1:
            raise DoubanAuthError("豆瓣登录态版本不受支持")
        return {
            "version": 1,
            "cookie": cookie,
            "path": target,
        }
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise DoubanAuthError("豆瓣登录态内容损坏") from exc


def clear_session(data_dir: Path) -> bool:
    target = session_path(data_dir)
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DoubanAuthError("无法清除豆瓣登录态") from exc
