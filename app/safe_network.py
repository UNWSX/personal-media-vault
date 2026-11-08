from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import urllib.parse


# Explicit hosts only. No wildcard or suffix matching.
IMAGE_HOST_ALLOWLIST = frozenset({
    "img1.doubanio.com",
    "img2.doubanio.com",
    "img3.doubanio.com",
    "img4.doubanio.com",
    "img5.doubanio.com",
    "img6.doubanio.com",
    "img7.doubanio.com",
    "img8.doubanio.com",
    "img9.doubanio.com",
    "asset.doubanio.com",
    "shared.akamai.steamstatic.com",
    "steamcdn-a.akamaihd.net",
    "lain.bgm.tv",
})

DEFAULT_MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_REDIRECTS = 5
TRUSTED_DOH_IP = "223.5.5.5"


class UnsafeRemoteURLError(ValueError):
    pass


def _normalize_url(url: str) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeRemoteURLError("远程图片地址格式无效") from exc
    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeRemoteURLError("远程图片只允许 HTTP 或 HTTPS")
    if parsed.username or parsed.password:
        raise UnsafeRemoteURLError("远程图片地址不能包含认证信息")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or host not in IMAGE_HOST_ALLOWLIST:
        raise UnsafeRemoteURLError("远程图片域名不在白名单中")
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    if port not in (80, 443):
        raise UnsafeRemoteURLError("远程图片端口不受支持")
    if (parsed.scheme.lower() == "https" and port != 443) or (
            parsed.scheme.lower() == "http" and port != 80):
        raise UnsafeRemoteURLError("远程图片端口与协议不匹配")
    return parsed._replace(netloc=host)


def validate_remote_image_url(url: str) -> urllib.parse.SplitResult:
    return _normalize_url(str(url or "").strip())


def _validated_addresses(values) -> tuple[str, ...]:
    addresses = []
    for address in values:
        address = str(address).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise UnsafeRemoteURLError("远程图片域名解析结果无效") from exc
        if not ip.is_global:
            raise UnsafeRemoteURLError("远程图片域名解析到了非公网地址")
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise UnsafeRemoteURLError("远程图片域名没有可用地址")
    return tuple(addresses)


def _resolve_via_trusted_doh(host: str) -> tuple[str, ...]:
    """系统 DNS 返回代理假地址或被污染时，用固定公网 DoH 重新解析。"""
    context = ssl.create_default_context()
    conn = http.client.HTTPSConnection(TRUSTED_DOH_IP, 443, timeout=5, context=context)
    try:
        path = "/resolve?name=%s&type=A" % urllib.parse.quote(host)
        conn.request("GET", path, headers={
            "Accept": "application/dns-json",
            "User-Agent": "MediaVault/2.0",
            "Accept-Encoding": "identity",
        })
        if conn.sock is not None:
            conn.sock.settimeout(5)
        response = conn.getresponse()
        if response.status != 200:
            raise UnsafeRemoteURLError("可信 DNS 返回 HTTP %d" % response.status)
        data = _read_limited(response, 256 * 1024)
        parsed = json.loads(data.decode("utf-8"))
        if parsed.get("Status") != 0:
            raise UnsafeRemoteURLError("可信 DNS 未返回有效解析结果")
        values = [
            answer.get("data")
            for answer in parsed.get("Answer") or []
            if answer.get("type") == 1
        ]
        return _validated_addresses(values)
    except UnsafeRemoteURLError:
        raise
    except Exception as exc:
        raise UnsafeRemoteURLError("远程图片域名解析失败") from exc
    finally:
        conn.close()


def resolve_public_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError:
        return _resolve_via_trusted_doh(host)

    values = [info[4][0] for info in infos]
    try:
        return _validated_addresses(values)
    except UnsafeRemoteURLError:
        return _resolve_via_trusted_doh(host)


def _connect_pinned(ip: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((ip, port), timeout=timeout)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, pinned_ip: str, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self):
        self.sock = _connect_pinned(self._pinned_ip, self.port, self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, pinned_ip: str, timeout: float):
        context = ssl.create_default_context()
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self):
        raw = _connect_pinned(self._pinned_ip, self.port, self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def _read_limited(response: http.client.HTTPResponse, limit: int) -> bytes:
    length = response.getheader("Content-Length")
    if length:
        try:
            if int(length) > limit:
                raise UnsafeRemoteURLError("远程图片超过大小限制")
        except ValueError:
            pass
    data = response.read(limit + 1)
    if len(data) > limit:
        raise UnsafeRemoteURLError("远程图片超过大小限制")
    return data


def fetch_remote_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    connect_timeout: float = 5.0,
    read_timeout: float = 12.0,
) -> tuple[bytes, str]:
    """请求远程图片；每一跳都先校验并固定 DNS 结果，再连接该 IP。"""
    current = url
    safe_headers = {
        k: v for k, v in (headers or {}).items()
        if k.lower() not in {
            "host", "proxy-authorization", "proxy-authenticate",
            "connection", "transfer-encoding", "content-length",
        }
    }
    safe_headers.setdefault("User-Agent", "MediaVault/2.0")
    safe_headers.setdefault("Accept", "image/*,*/*;q=0.8")
    safe_headers.setdefault("Accept-Encoding", "identity")

    for redirect_count in range(MAX_REDIRECTS + 1):
        parsed = validate_remote_image_url(current)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        addresses = resolve_public_addresses(host, port)
        pinned_ip = addresses[0]
        conn_cls = _PinnedHTTPSConnection if parsed.scheme.lower() == "https" else _PinnedHTTPConnection
        conn = conn_cls(host, port, pinned_ip, connect_timeout)
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            conn.request("GET", path, headers=safe_headers)
            if conn.sock is not None:
                conn.sock.settimeout(read_timeout)
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                if redirect_count >= MAX_REDIRECTS:
                    raise UnsafeRemoteURLError("远程图片重定向次数过多")
                location = response.getheader("Location")
                response.close()
                if not location:
                    raise UnsafeRemoteURLError("远程图片重定向缺少目标地址")
                next_url = urllib.parse.urljoin(current, location)
                next_host = (urllib.parse.urlsplit(next_url).hostname or "").lower().rstrip(".")
                if next_host != host:
                    for sensitive in ("cookie", "authorization", "referer", "origin"):
                        for key in list(safe_headers):
                            if key.lower() == sensitive:
                                safe_headers.pop(key, None)
                current = next_url
                continue
            if response.status < 200 or response.status >= 300:
                raise UnsafeRemoteURLError("远程图片服务器返回 HTTP %d" % response.status)
            data = _read_limited(response, max_bytes)
            ctype = response.getheader("Content-Type") or ""
            return data, ctype
        finally:
            conn.close()
    raise UnsafeRemoteURLError("远程图片请求失败")
