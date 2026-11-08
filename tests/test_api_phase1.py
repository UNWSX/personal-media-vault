from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import server  # noqa: E402


class PhaseOneApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        (self.vault / ".obsidian").mkdir()
        (self.vault / "attachments").mkdir()
        media = self.vault / "02-电影阅读" / "movie"
        media.mkdir(parents=True)
        (media / "api.md").write_text(
            "---\n"
            "title: API\n"
            "type: movie\n"
            "score: 8\n"
            'scoreStar: "⭐⭐⭐⭐"\n'
            "status: false\n"
            "---\n\n"
            "> **📝 状态**：未看\n\n"
            "正文\n",
            encoding="utf-8",
        )

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.httpd, self.port, _ = server.start_server(
            vault=self.vault, port_pref=port, quiet=True
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, payload=None):
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["X-MediaVault-Client"] = "media-vault"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_ping_library_and_revision_conflict(self):
        status, ping = self.request("/api/ping")
        self.assertEqual(status, 200)
        self.assertEqual(ping["app"], "media-vault")
        self.assertEqual(ping["version"], "2.0.0")
        self.assertEqual(ping["protocolVersion"], 1)
        self.assertRegex(ping["instanceId"], r"^[0-9a-f]{32}$")
        self.assertEqual(Path(ping["vaultRoot"]), self.vault.resolve())

        status, library = self.request("/api/library")
        self.assertEqual(status, 200)
        self.assertEqual(library["count"], 1)
        item = library["items"][0]
        self.assertEqual(item["progressStatus"], "planned")
        self.assertEqual(item["normScore"], 8.0)
        self.assertEqual(item["scoreSource"], "douban")
        self.assertEqual(item["scoreStar"], "⭐⭐⭐⭐")
        revision = item["revision"]

        status, saved = self.request("/api/item", {
            "path": "02-电影阅读/movie/api.md",
            "revision": revision,
            "status": True,
        })
        self.assertEqual(status, 200)
        self.assertTrue(saved["item"]["status"])

        status, conflict = self.request("/api/item", {
            "path": "02-电影阅读/movie/api.md",
            "revision": revision,
            "comment": "旧修订不应覆盖",
        })
        self.assertEqual(status, 409)
        self.assertTrue(conflict["conflict"])
        self.assertNotIn("comment:", (self.vault / "02-电影阅读" / "movie" / "api.md").read_text(encoding="utf-8"))

    def test_progress_and_rating_compatibility_updates(self):
        _, library = self.request("/api/library")
        item = library["items"][0]
        status, completed = self.request("/api/item", {
            "path": item["path"],
            "revision": item["revision"],
            "progressStatus": "completed",
        })
        self.assertEqual(status, 200)
        self.assertTrue(completed["item"]["status"])
        self.assertEqual(completed["item"]["progressStatus"], "completed")
        text = (self.vault / item["path"]).read_text(encoding="utf-8")
        self.assertIn("> **📝 状态**：已看", text)

        status, rated = self.request("/api/item", {
            "path": item["path"],
            "revision": completed["item"]["revision"],
            "doubanScore": 8.7,
            "myScore": 9.5,
        })
        self.assertEqual(status, 200)
        self.assertEqual(rated["item"]["doubanScore"], 8.7)
        self.assertEqual(rated["item"]["score"], 8.7)
        self.assertEqual(rated["item"]["myScore"], 9.5)
        self.assertEqual(rated["item"]["normScore"], 8.7)
        self.assertIn(
            'scoreStar: "⭐⭐⭐⭐"',
            (self.vault / item["path"]).read_text(encoding="utf-8"),
        )

        status, invalid = self.request("/api/item", {
            "path": item["path"],
            "revision": rated["item"]["revision"],
            "progressStatus": "planned",
            "status": True,
        })
        self.assertEqual(status, 400)
        self.assertFalse(invalid["ok"])

    def test_invalid_schema_is_serialized_and_write_blocked(self):
        invalid_path = self.vault / "02-电影阅读" / "movie" / "invalid-schema.md"
        invalid_path.write_text(
            "---\n"
            "schemaVersion: 2.5\n"
            'mediaId: "c3b9e4a8-7d21-4f8a-9e1b-3f2d1e0a9b8c"\n'
            "title: 无效版本\n"
            "type: movie\n"
            "status: false\n"
            "---\n\n正文\n",
            encoding="utf-8",
        )
        status, library = self.request("/api/library")
        self.assertEqual(status, 200)
        item = next(
            row for row in library["items"]
            if row["path"].endswith("invalid-schema.md")
        )
        self.assertEqual(item["schemaVersionRaw"], 2.5)
        self.assertFalse(item["writeAllowed"])

        status, result = self.request("/api/item", {
            "path": item["path"],
            "revision": item["revision"],
            "status": True,
        })
        self.assertEqual(status, 400)
        self.assertFalse(result["ok"])
        self.assertIn("status: false", invalid_path.read_text(encoding="utf-8"))

    def test_api_rejects_untrusted_origin_and_bad_content_type(self):
        data = json.dumps({"path": "02-电影阅读/movie/api.md", "status": True}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/item",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Origin": "https://evil.example",
            },
        )
        try:
            urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 403)
        else:
            self.fail("不可信 Origin 应被拒绝")

        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/item",
            data=b"{}",
            headers={
                "Content-Type": "text/plain",
                "X-MediaVault-Client": "media-vault",
            },
        )
        try:
            urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 415)
        else:
            self.fail("text/plain 写入请求应被拒绝")

    def test_same_origin_legacy_client_can_still_write(self):
        _, library = self.request("/api/library")
        item = library["items"][0]
        data = json.dumps({
            "path": item["path"],
            "revision": item["revision"],
            "finishTime": "2026-11-07",
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/item",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{self.port}",
            },
        )
        with urllib.request.urlopen(req, timeout=3) as response:
            result = json.loads(response.read().decode("utf-8"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["item"]["finishTime"], "2026-11-07")

    def test_server_can_shut_down_through_authenticated_local_request(self):
        status, data = self.request("/api/quit", {})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def test_douban_session_is_encrypted_and_can_be_cleared(self):
        status, initial = self.request("/api/douban/session")
        self.assertEqual(status, 200)
        self.assertFalse(initial["loggedIn"])

        cookie = "ll=abc; bid=def; dbcl2=secret; ck=xyz"
        status, saved = self.request(
            "/api/douban/session",
            {"cookie": cookie},
        )
        self.assertEqual(status, 200)
        self.assertTrue(saved["loggedIn"])
        self.assertEqual(saved["source"], "standalone")
        secret_path = self.vault / ".media-app" / "secrets" / "douban-session.bin"
        self.assertTrue(secret_path.is_file())
        self.assertNotIn(b"dbcl2", secret_path.read_bytes())
        self.assertEqual(
            server.douban_headers()["Cookie"],
            cookie,
        )

        status, cleared = self.request("/api/douban/logout", {})
        self.assertEqual(status, 200)
        self.assertFalse(cleared["loggedIn"])
        self.assertFalse(secret_path.exists())

    def test_douban_session_rejects_non_login_cookie(self):
        status, result = self.request(
            "/api/douban/session",
            {"cookie": "bid=anonymous"},
        )
        self.assertEqual(status, 400)
        self.assertFalse(result["ok"])
        self.assertIn("dbcl2", result["error"])

    def test_douban_login_controls_do_not_flash_before_session_check(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/",
            timeout=3,
        ) as response:
            html = response.read().decode("utf-8")
        self.assertIn('id="dbSession" hidden', html)
        self.assertRegex(html, r'id="dbLoginBtn"[^>]*hidden')
        self.assertRegex(html, r'id="dbImportBtn"[^>]*hidden')

    def test_media_cannot_read_config_or_disguised_markdown(self):
        config = self.vault / ".obsidian" / "private.json"
        config.write_text('{"token":"secret"}', encoding="utf-8")
        status, _ = self.request("/media?p=" + urllib.parse.quote(".obsidian/private.json"))
        self.assertEqual(status, 400)

        disguised = self.vault / "attachments" / "fake.png"
        disguised.write_text("# not an image", encoding="utf-8")
        status, _ = self.request("/media?p=attachments%2Ffake.png")
        self.assertEqual(status, 415)

        valid = self.vault / "attachments" / "valid.png"
        valid.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/media?p=attachments%2Fvalid.png",
            timeout=3,
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "image/png")

        status, _ = self.request(
            "/media?p=" + urllib.parse.quote("http://127.0.0.1/private.png")
        )
        self.assertEqual(status, 403)

    def test_host_and_note_path_are_validated(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/ping",
            headers={"Host": "evil.example"},
        )
        try:
            urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 403)
        else:
            self.fail("错误 Host 应被拒绝")

        status, data = self.request("/api/delete", {
            "path": "../01-工作学习/private.md",
        })
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_manual_snapshot_backup(self):
        image = self.vault / "attachments" / "keep.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
        status, data = self.request("/api/backup", {"label": "manual-test"})
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["fileCount"], 2)
        snapshot = self.vault / ".media-app" / "backups" / "snapshots" / data["backup"]
        self.assertTrue((snapshot / "02-电影阅读" / "movie" / "api.md").is_file())
        self.assertEqual((snapshot / "attachments" / "keep.png").read_bytes(), image.read_bytes())
        self.assertTrue((snapshot / "manifest.json").is_file())

    def test_migration_web_endpoints_are_read_only(self):
        note = self.vault / "02-电影阅读" / "movie" / "api.md"
        before = note.read_bytes()
        before_mtime = note.stat().st_mtime_ns
        status, result = self.request("/api/migration/scan")
        self.assertEqual(status, 200)
        self.assertTrue(result["readOnly"])
        self.assertEqual(result["scan"]["summary"]["files"], 1)
        self.assertEqual(note.read_bytes(), before)
        self.assertEqual(note.stat().st_mtime_ns, before_mtime)

        reports = self.vault / ".media-app" / "migration" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "preview-test.json").write_text(
            '{"ok":true}',
            encoding="utf-8",
        )
        status, report = self.request(
            "/api/migration/reports?name=preview-test.json"
        )
        self.assertEqual(status, 200)
        self.assertTrue(report["ok"])

        status, _ = self.request("/api/migration/apply", {
            "plan": "forbidden",
        })
        self.assertEqual(status, 404)
        self.assertEqual(note.read_bytes(), before)

    def test_create_and_recoverable_delete(self):
        status, created = self.request("/api/create", {
            "title": "新建测试",
            "type": "movie",
            "status": False,
            "rottenTomatoesScore": 87,
        })
        self.assertEqual(status, 200)
        item = created["item"]
        created_path = self.vault / item["path"]
        self.assertTrue(created_path.is_file())
        self.assertIn("title: 新建测试", created_path.read_text(encoding="utf-8"))
        self.assertEqual(item["schemaVersion"], 2)
        self.assertRegex(item["mediaId"], r"^[0-9a-f-]{36}$")
        self.assertEqual(item["progressStatus"], "planned")
        self.assertEqual(item["rottenTomatoesScore"], 87)

        status, deleted = self.request("/api/delete", {
            "path": item["path"],
            "revision": item["revision"],
        })
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])
        self.assertFalse(created_path.exists())
        self.assertTrue(any((self.vault / ".media-app" / "trash").glob("*新建测试.md")))


class MetadataSearchTests(unittest.TestCase):
    def test_douban_plugin_cookie_content_is_reused_locally(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            plugin = vault / ".obsidian" / "plugins" / "obsidian-douban-plugin"
            plugin.mkdir(parents=True)
            (plugin / "data.json").write_text(
                json.dumps({
                    "loginCookiesContent": "bid=test; dbcl2=test",
                    "loginHeadersContent": "{}",
                }),
                encoding="utf-8",
            )
            previous_lib = server.LIB
            try:
                server.LIB = server.Library(vault)
                server._HEADER_CACHE["headers"] = None
                headers = server.douban_headers()
            finally:
                server.LIB = previous_lib
                server._HEADER_CACHE["headers"] = None
        self.assertEqual(headers["Cookie"], "bid=test; dbcl2=test")

    def test_rotten_tomatoes_lookup_uses_matching_meter_score(self):
        payload = {
            "hits": [
                {
                    "title": "Another Film",
                    "type": "movie",
                    "releaseYear": 2024,
                    "rottenTomatoes": {"criticsScore": 30},
                },
                {
                    "title": "Dune: Part Two",
                    "type": "movie",
                    "releaseYear": 2024,
                    "rottenTomatoes": {"criticsScore": 92},
                },
            ]
        }
        with mock.patch.object(server, "_rt_request_json", return_value=payload):
            score = server.fetch_rotten_tomatoes_score(
                "Dune: Part Two",
                "movie",
                2024,
            )
        self.assertEqual(score, 92)

    def test_rotten_tomatoes_rejects_wrong_year_and_ambiguous_match(self):
        wrong_year = {
            "hits": [{
                "title": "Dune",
                "type": "movie",
                "releaseYear": 1984,
                "rottenTomatoes": {"criticsScore": 91},
            }]
        }
        with mock.patch.object(server, "_rt_request_json", return_value=wrong_year):
            result = server.lookup_rotten_tomatoes("Dune", "movie", 2021)
        self.assertEqual(result["status"], "no_match")

        ambiguous = {
            "hits": [
                {
                    "title": "Dune",
                    "type": "movie",
                    "releaseYear": 2021,
                    "emsId": "one",
                    "rottenTomatoes": {"criticsScore": 83},
                },
                {
                    "title": "Dune",
                    "type": "movie",
                    "releaseYear": 2021,
                    "emsId": "two",
                    "rottenTomatoes": {"criticsScore": 84},
                },
            ]
        }
        with mock.patch.object(server, "_rt_request_json", return_value=ambiguous):
            result = server.lookup_rotten_tomatoes("Dune", "movie", 2021)
        self.assertEqual(result["status"], "ambiguous")

    def test_plugin_web_search_is_preferred(self):
        expected = [{"id": "1", "title": "插件结果", "type": "movie"}]
        with mock.patch.object(server, "search_douban_html", return_value=expected) as web:
            with mock.patch.object(server, "douban_get") as suggest:
                self.assertEqual(server.search_douban("测试", "movie"), expected)
        web.assert_called_once_with("测试", "1002", "movie")
        suggest.assert_not_called()

    def test_subject_suggest_is_only_fallback_when_plugin_search_is_empty(self):
        payload = json.dumps([
            {"id": "2", "title": "备用结果", "year": "2026", "img": "", "sub_title": ""}
        ]).encode()
        with mock.patch.object(server, "search_douban_html", return_value=[]):
            with mock.patch.object(server, "douban_get", return_value=payload) as suggest:
                result = server.search_douban("备用", "movie")
        self.assertEqual(result[0]["title"], "备用结果")
        suggest.assert_called_once()

    def test_plugin_search_error_falls_back_without_hiding_exception(self):
        payload = b"[]"
        with mock.patch.object(server, "search_douban_html", side_effect=OSError("blocked")):
            with mock.patch.object(server, "douban_get", return_value=payload):
                self.assertEqual(server.search_douban("空", "book"), [])


if __name__ == "__main__":
    unittest.main()
