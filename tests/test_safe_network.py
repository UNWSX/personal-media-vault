from __future__ import annotations

import socket
import sys
import unittest
import json
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import safe_network  # noqa: E402


class SafeNetworkTests(unittest.TestCase):
    def test_allowlist_is_exact(self):
        safe_network.validate_remote_image_url(
            "https://img3.doubanio.com/view/photo.png"
        )
        safe_network.validate_remote_image_url(
            "https://asset.doubanio.com/view/photo.png"
        )
        safe_network.validate_remote_image_url(
            "https://lain.bgm.tv/pic/cover/l/example.jpg"
        )
        rejected = [
            "https://doubanio.com/view/photo.png",
            "https://evil.doubanio.com/view/photo.png",
            "https://img3.doubanio.com.evil.example/photo.png",
            "https://img3.doubanio.com:444/photo.png",
            "file:///tmp/photo.png",
            "https://user:pass@img3.doubanio.com/photo.png",
        ]
        for url in rejected:
            with self.subTest(url=url):
                with self.assertRaises(safe_network.UnsafeRemoteURLError):
                    safe_network.validate_remote_image_url(url)

    def test_private_or_mixed_dns_results_are_rejected(self):
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        mixed = private + [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]
        for result in (private, mixed):
            with self.subTest(result=result):
                with mock.patch.object(safe_network.socket, "getaddrinfo", return_value=result):
                    with mock.patch.object(
                        safe_network,
                        "_resolve_via_trusted_doh",
                        side_effect=safe_network.UnsafeRemoteURLError("blocked"),
                    ) as fallback:
                        with self.assertRaises(safe_network.UnsafeRemoteURLError):
                            safe_network.resolve_public_addresses("img3.doubanio.com", 443)
                fallback.assert_called_once_with("img3.doubanio.com")

    def test_connection_uses_the_pinned_ip_not_hostname(self):
        fake = mock.Mock()
        with mock.patch.object(safe_network.socket, "create_connection", return_value=fake) as connect:
            safe_network._connect_pinned("93.184.216.34", 443, 5)
        connect.assert_called_once_with(("93.184.216.34", 443), timeout=5)

    def test_resolution_is_validated_before_connection_and_not_repeated(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        fake_conn = mock.Mock()
        fake_conn.sock = mock.Mock()
        fake_response = mock.Mock()
        fake_response.status = 200
        fake_response.getheader.side_effect = lambda name: (
            "image/png" if name == "Content-Type" else None
        )
        fake_response.read.return_value = b"\x89PNG\r\n\x1a\n" + b"x" * 100
        fake_conn.getresponse.return_value = fake_response

        conn_cls = mock.Mock(return_value=fake_conn)
        with mock.patch.object(safe_network.socket, "getaddrinfo", return_value=public) as resolve:
            with mock.patch.object(safe_network, "_PinnedHTTPSConnection", conn_cls):
                data, ctype = safe_network.fetch_remote_bytes(
                    "https://img3.doubanio.com/photo.png"
                )
        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertEqual(ctype, "image/png")
        self.assertEqual(resolve.call_count, 1)
        conn_cls.assert_called_once_with(
            "img3.doubanio.com", 443, "93.184.216.34", 5.0
        )

    def test_redirect_to_unlisted_host_is_rejected(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        fake_conn = mock.Mock()
        fake_conn.sock = mock.Mock()
        redirect = mock.Mock()
        redirect.status = 302
        redirect.getheader.return_value = "http://127.0.0.1/private"
        fake_conn.getresponse.return_value = redirect
        with mock.patch.object(safe_network.socket, "getaddrinfo", return_value=public):
            with mock.patch.object(safe_network, "_PinnedHTTPSConnection", return_value=fake_conn):
                with self.assertRaises(safe_network.UnsafeRemoteURLError):
                    safe_network.fetch_remote_bytes(
                        "https://img3.doubanio.com/photo.png"
                    )

    def test_response_size_limit(self):
        response = mock.Mock()
        response.getheader.return_value = "100"
        response.read.return_value = b"x" * 11
        with self.assertRaises(safe_network.UnsafeRemoteURLError):
            safe_network._read_limited(response, 10)

    def test_trusted_doh_fallback_returns_validated_public_ip(self):
        fake_response = mock.Mock()
        fake_response.status = 200
        fake_response.getheader.return_value = "application/dns-json"
        fake_response.read.return_value = json.dumps({
            "Status": 0,
            "Answer": [{"type": 1, "data": "93.184.216.34"}],
        }).encode()
        fake_conn = mock.Mock()
        fake_conn.sock = mock.Mock()
        fake_conn.getresponse.return_value = fake_response
        with mock.patch.object(
            safe_network.http.client,
            "HTTPSConnection",
            return_value=fake_conn,
        ):
            addresses = safe_network._resolve_via_trusted_doh("img3.doubanio.com")
        self.assertEqual(addresses, ("93.184.216.34",))


if __name__ == "__main__":
    unittest.main()
