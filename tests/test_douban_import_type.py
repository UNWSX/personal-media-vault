from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import server  # noqa: E402


class DoubanImportTypeTests(unittest.TestCase):
    def _library(self, temp):
        vault = Path(temp)
        for media_type in ("movie", "teleplay", "book", "game"):
            (vault / "02-电影阅读" / media_type).mkdir(parents=True)
        (vault / "attachments").mkdir()
        return server.Library(vault)

    def test_detail_uses_authoritative_tv_fields(self):
        raw = json.dumps({
            "title": "A TV Season",
            "original_title": "A Show Season 1",
            "type": "tv",
            "subtype": "tv",
            "is_tv": True,
            "episodes_count": 8,
            "year": "2020",
            "pubdate": ["2020-01-01"],
            "rating": {"value": 8.1},
        }).encode("utf-8")
        with mock.patch.object(server, "douban_get", return_value=raw):
            detail = server.fetch_douban_detail("123", "movie")
        self.assertEqual(detail["mediaType"], "teleplay")
        self.assertEqual(detail["originalTitle"], "A Show Season 1")
        self.assertEqual(detail["episodesCount"], 8)

    def test_movie_selection_is_stored_as_episode_show(self):
        with tempfile.TemporaryDirectory() as temp:
            lib = self._library(temp)
            detail = {
                "mediaType": "teleplay",
                "title": "绝命毒师 第一季",
                "originalTitle": "Breaking Bad Season 1",
                "year": "2008",
                "datePublished": "2008-01-20",
                "score": 9.0,
                "cover": "",
                "intro": "",
                "directors": [],
                "actors": [],
                "countries": [],
                "genres": [],
            }
            with (
                mock.patch.object(server, "fetch_douban_detail", return_value=detail),
                mock.patch.object(server, "_queue_rating_job", return_value=None),
            ):
                item = server.import_from_douban(lib, {
                    "id": "2373195",
                    "title": "绝命毒师 第一季",
                    "type": "movie",
                })
                self.assertFalse(
                    (lib.media_root / "movie" / "绝命毒师 第一季.md").exists()
                )
        self.assertEqual(item["type"], "teleplay")
        self.assertIn("/teleplay/", item["path"])

    def test_existing_wrong_movie_note_is_moved_to_episode_show(self):
        with tempfile.TemporaryDirectory() as temp:
            lib = self._library(temp)
            wrong = lib.media_root / "movie" / "绝命毒师 第一季.md"
            wrong.write_text(
                "---\n"
                "id: 2373195\n"
                "title: 绝命毒师 第一季\n"
                "type: movie\n"
                "datePublished: 2008-01-20\n"
                "doubanScore: 9.0\n"
                "status: false\n"
                "---\n\n"
                "> [!info]+ 基本信息\n"
                "> **🎬 标题**：《绝命毒师 第一季》\n",
                encoding="utf-8",
            )
            detail = {
                "mediaType": "teleplay",
                "title": "绝命毒师 第一季",
                "originalTitle": "Breaking Bad Season 1",
                "year": "2008",
                "datePublished": "2008-01-20",
                "score": 9.0,
                "cover": "",
                "intro": "",
                "directors": [],
                "actors": [],
                "countries": [],
                "genres": [],
            }
            with (
                mock.patch.object(server, "fetch_douban_detail", return_value=detail),
                mock.patch.object(server, "_queue_rating_job", return_value=None),
            ):
                item = server.import_from_douban(lib, {
                    "id": "2373195",
                    "title": "绝命毒师 第一季",
                    "type": "movie",
                })
            self.assertTrue(item["existed"])
            self.assertTrue(item["mediaTypeRepaired"])
            self.assertEqual(item["type"], "teleplay")
            self.assertFalse(wrong.exists())
            repaired = lib.media_root / "teleplay" / wrong.name
            self.assertTrue(repaired.exists())
            text = repaired.read_text(encoding="utf-8")
            self.assertIn("type: teleplay", text)
            self.assertIn("> **📺 标题**", text)
            self.assertEqual(
                len(list((lib.media_root / "movie").glob("*.md"))) +
                len(list((lib.media_root / "teleplay").glob("*.md"))),
                1,
            )

    def test_book_and_game_imports_keep_their_authoritative_types(self):
        with tempfile.TemporaryDirectory() as temp:
            lib = self._library(temp)
            book_detail = {
                "mediaType": "book",
                "title": "测试图书",
                "originalTitle": "",
                "year": "2024",
                "datePublished": "2024-01-01",
                "score": 8.0,
                "cover": "",
                "intro": "",
                "authors": ["作者"],
                "publisher": "出版社",
                "pages": 100,
            }
            with (
                mock.patch.object(
                    server, "fetch_douban_detail", return_value=book_detail
                ),
                mock.patch.object(server, "_queue_rating_job", return_value=None),
            ):
                book = server.import_from_douban(lib, {
                    "id": "100",
                    "title": "测试图书",
                    "type": "book",
                })
            self.assertEqual(book["type"], "book")
            self.assertIn("/book/", book["path"])

            with (
                mock.patch.object(server, "fetch_game_detail", return_value={}),
                mock.patch.object(
                    server, "steam_search_appid", return_value=(None, "")
                ),
                mock.patch.object(
                    server, "fetch_metacritic_score", return_value=None
                ),
                mock.patch.object(server, "grab_first_cover", return_value=""),
                mock.patch.object(server, "_queue_rating_job", return_value=None),
            ):
                game = server.import_from_douban(lib, {
                    "id": "200",
                    "title": "测试游戏",
                    "type": "game",
                    "score": 8.0,
                })
            self.assertEqual(game["type"], "game")
            self.assertIn("/game/", game["path"])


if __name__ == "__main__":
    unittest.main()
