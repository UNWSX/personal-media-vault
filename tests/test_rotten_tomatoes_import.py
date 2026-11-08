from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import batch_rotten_tomatoes as batch  # noqa: E402
import server  # noqa: E402


MOVIE = """---
id: "35593344"
title: 奥本海默
type: movie
score: 8.8
status: true
---

> **📝 状态**：已看

正文必须保持不变。
"""


class RottenTomatoesBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        media = self.vault / "02-电影阅读" / "movie"
        media.mkdir(parents=True)
        self.note = media / "奥本海默.md"
        self.note.write_text(MOVIE, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_dry_run_matches_and_apply_preserves_rest_of_note(self):
        lookup = {
            "status": "matched",
            "score": 93,
            "candidate": {
                "title": "Oppenheimer",
                "year": 2023,
                "type": "movie",
                "score": 93,
                "url": "https://www.rottentomatoes.com/m/oppenheimer_2023",
            },
        }
        with mock.patch.object(
            batch,
            "_douban_title_candidate",
            return_value=("Oppenheimer", "douban_search"),
        ), mock.patch.object(batch, "lookup_rotten_tomatoes", return_value=lookup):
            plan = batch.build_plan(self.vault, wait_seconds=0)
        self.assertEqual(plan["stats"]["matched"], 1)
        before_body = MOVIE.split("---\n", 2)[2]
        report = batch.apply_plan(
            self.vault,
            plan,
            "APPLY " + plan["planId"],
        )
        self.assertEqual(report["stats"]["updated"], 1)
        after = self.note.read_text(encoding="utf-8")
        self.assertIn("rottenTomatoesScore: 93", after)
        self.assertEqual(after.split("---\n", 2)[2], before_body)
        self.assertTrue(Path(report["backupPath"]).is_dir())

    def test_external_edit_after_preview_is_not_overwritten(self):
        lookup = {
            "status": "matched",
            "score": 93,
            "candidate": {
                "title": "Oppenheimer",
                "year": 2023,
                "type": "movie",
                "score": 93,
                "url": "https://www.rottentomatoes.com/m/oppenheimer_2023",
            },
        }
        with mock.patch.object(
            batch,
            "_douban_title_candidate",
            return_value=("Oppenheimer", "douban_search"),
        ), mock.patch.object(batch, "lookup_rotten_tomatoes", return_value=lookup):
            plan = batch.build_plan(self.vault, wait_seconds=0)
        self.note.write_text(MOVIE + "\nObsidian 外部新增。\n", encoding="utf-8")
        report = batch.apply_plan(
            self.vault,
            plan,
            "APPLY " + plan["planId"],
        )
        self.assertEqual(report["stats"]["conflict"], 1)
        current = self.note.read_text(encoding="utf-8")
        self.assertIn("Obsidian 外部新增。", current)
        self.assertNotIn("rottenTomatoesScore:", current)

    def test_existing_note_score_is_skipped_without_lookup(self):
        self.note.write_text(
            MOVIE.replace("status: true\n", "status: true\nrottenTomatoesScore: 91\n"),
            encoding="utf-8",
        )
        with mock.patch.object(batch, "lookup_rotten_tomatoes") as lookup:
            plan = batch.build_plan(self.vault, wait_seconds=0)
        self.assertEqual(plan["stats"]["skipped_existing"], 1)
        lookup.assert_not_called()

    def test_plan_rejects_wrong_confirmation(self):
        plan = {
            "planFormatVersion": 1,
            "planId": "x",
            "vault": str(self.vault.resolve()),
            "entries": [],
        }
        with self.assertRaises(ValueError):
            batch.apply_plan(self.vault, plan, "APPLY wrong")

    def test_douban_search_original_title_is_extracted(self):
        self.assertEqual(
            batch._parse_douban_original_title(
                "原名:Oppenheimer / 克里斯托弗·诺兰 / 基里安·墨菲 / 2023"
            ),
            "Oppenheimer",
        )

    def test_douban_id_match_does_not_depend_on_parser_year(self):
        fm = {"id": "1418190"}
        item = {
            "title": "007：大战皇家赌场",
            "type": "movie",
            "year": 2006,
        }
        result = [{
            "id": "1418190",
            "year": "",
            "sub": "原名:Casino Royale / 马丁·坎贝尔 / 2006",
        }]
        with mock.patch.object(batch, "search_douban", return_value=result):
            title, source = batch._douban_title_candidate(
                mock.Mock(),
                item,
                fm,
                {},
            )
        self.assertEqual(title, "Casino Royale")
        self.assertEqual(source, "douban_search")

    def test_rotten_tomatoes_season_uses_season_page_score(self):
        suggestions = {
            "hits": [{
                "title": "Rick and Morty",
                "type": "tv",
                "releaseYear": 2013,
                "vanity": "rick_and_morty",
            }]
        }
        page = (
            '<title>Rick and Morty: Season 4 | Rotten Tomatoes</title>'
            '"dateCreated":"2019-11-10",'
            '"criticsScore":{"averageRating":"8.30",'
            '"scorePercent":"96%"}'
        )
        with mock.patch.object(server, "_rt_request_json", return_value=suggestions), \
                mock.patch.object(server, "_rt_text", return_value=page):
            result = server.lookup_rotten_tomatoes_season(
                "Rick and Morty", 4, 2019
            )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["score"], 96)


if __name__ == "__main__":
    unittest.main()
