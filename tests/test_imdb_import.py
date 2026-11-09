from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import batch_imdb  # noqa: E402
import server  # noqa: E402


MOVIE = """---
id: "35593344"
title: 奥本海默
type: movie
score: 8.8
status: true
datePublished: 2023-07-21
---

> **📝 状态**：已看

正文必须保持不变。
"""


class IMDbLookupTests(unittest.TestCase):
    def test_movie_requires_exact_title_year_and_type(self):
        suggestions = {
            "d": [{
                "id": "tt15398776",
                "l": "Oppenheimer",
                "y": 2023,
                "qid": "movie",
            }]
        }
        detail = {
            "id": "tt15398776",
            "titleText": {"text": "Oppenheimer"},
            "releaseYear": {"year": 2023},
            "titleType": {"id": "movie"},
            "ratingsSummary": {"aggregateRating": 8.2},
        }
        with mock.patch.object(
            server, "_imdb_suggestion_json", return_value=suggestions
        ), mock.patch.object(server, "_imdb_graphql_title", return_value=detail):
            result = server.lookup_imdb("Oppenheimer", "movie", 2023)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["score"], 8.2)
        self.assertEqual(result["candidate"]["imdbId"], "tt15398776")

    def test_tv_series_type_is_supported(self):
        suggestions = {
            "d": [{"id": "tt9140554", "l": "Loki", "y": 2021, "qid": "tvSeries"}]
        }
        detail = {
            "titleText": {"text": "Loki"},
            "releaseYear": {"year": 2021},
            "titleType": {"id": "tvSeries"},
            "ratingsSummary": {"aggregateRating": 8.2},
        }
        with mock.patch.object(
            server, "_imdb_suggestion_json", return_value=suggestions
        ), mock.patch.object(server, "_imdb_graphql_title", return_value=detail):
            result = server.lookup_imdb("Loki", "teleplay", 2021)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["score"], 8.2)

    def test_wrong_year_is_not_queried(self):
        suggestions = {
            "d": [{"id": "tt1160419", "l": "Dune", "y": 1984, "qid": "movie"}]
        }
        with mock.patch.object(
            server, "_imdb_suggestion_json", return_value=suggestions
        ), mock.patch.object(server, "_imdb_graphql_title") as detail:
            result = server.lookup_imdb("Dune", "movie", 2021)
        self.assertEqual(result["status"], "no_match")
        detail.assert_not_called()

    def test_popular_exact_candidate_disambiguates_same_title_and_year(self):
        suggestions = {
            "d": [
                {"id": "tt1000001", "l": "Same Name", "y": 2026, "qid": "movie", "rank": 8},
                {"id": "tt1000002", "l": "Same Name", "y": 2026, "qid": "movie", "rank": 700},
                {"id": "tt1000003", "l": "Same Name", "y": 2026, "qid": "movie", "rank": 30000},
            ]
        }
        details = {
            "tt1000001": {
                "titleText": {"text": "Same Name"},
                "releaseYear": {"year": 2026},
                "titleType": {"id": "movie"},
                "ratingsSummary": {"aggregateRating": 8.4},
            },
            "tt1000002": {
                "titleText": {"text": "Same Name"},
                "releaseYear": {"year": 2026},
                "titleType": {"id": "movie"},
                "ratingsSummary": {"aggregateRating": 2.1},
            },
            "tt1000003": {
                "titleText": {"text": "Same Name"},
                "releaseYear": {"year": 2026},
                "titleType": {"id": "movie"},
                "ratingsSummary": {"aggregateRating": 3.2},
            },
        }
        with mock.patch.object(
            server, "_imdb_suggestion_json", return_value=suggestions
        ), mock.patch.object(
            server,
            "_imdb_graphql_title",
            side_effect=lambda imdb_id: details[imdb_id],
        ):
            result = server.lookup_imdb("Same Name", "movie", 2026)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["candidate"]["imdbId"], "tt1000001")
        self.assertEqual(result["matchMode"], "popularity_rank")

    def test_close_popularity_ranks_remain_ambiguous(self):
        suggestions = {
            "d": [
                {"id": "tt2000001", "l": "Close Match", "y": 2026, "qid": "movie", "rank": 100},
                {"id": "tt2000002", "l": "Close Match", "y": 2026, "qid": "movie", "rank": 120},
            ]
        }
        detail = {
            "titleText": {"text": "Close Match"},
            "releaseYear": {"year": 2026},
            "titleType": {"id": "movie"},
            "ratingsSummary": {"aggregateRating": 7.0},
        }
        with mock.patch.object(
            server, "_imdb_suggestion_json", return_value=suggestions
        ), mock.patch.object(server, "_imdb_graphql_title", return_value=detail):
            result = server.lookup_imdb("Close Match", "movie", 2026)
        self.assertEqual(result["status"], "ambiguous")

    def test_tv_season_uses_episode_rating_average(self):
        suggestions = {
            "d": [{
                "id": "tt2861424",
                "l": "Rick and Morty",
                "y": 2013,
                "qid": "tvSeries",
            }]
        }
        episodes = [
            {"season": 2, "year": 2015, "rating": 8.0},
            {"season": 2, "year": 2015, "rating": 9.0},
            {"season": 3, "year": 2017, "rating": 7.0},
        ]
        with mock.patch.object(
            server, "_imdb_suggestion_json", return_value=suggestions
        ), mock.patch.object(
            server, "_imdb_series_episodes", return_value=episodes
        ):
            result = server.lookup_imdb(
                "Rick and Morty",
                "teleplay",
                2015,
                season=2,
            )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["score"], 8.5)
        self.assertEqual(result["candidate"]["season"], 2)


class IMDbBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        media = self.vault / "02-电影阅读" / "movie"
        media.mkdir(parents=True)
        self.note = media / "奥本海默.md"
        self.note.write_text(MOVIE, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_and_apply_preserve_body(self):
        lookup = {
            "status": "matched",
            "score": 8.2,
            "candidate": {
                "imdbId": "tt15398776",
                "title": "Oppenheimer",
                "year": 2023,
                "type": "movie",
                "score": 8.2,
                "url": "https://www.imdb.com/title/tt15398776/",
            },
        }
        with mock.patch.object(
            batch_imdb,
            "_douban_title_candidate",
            return_value=("Oppenheimer", "douban_search"),
        ), mock.patch.object(batch_imdb, "lookup_imdb", return_value=lookup):
            plan = batch_imdb.build_plan(self.vault, wait_seconds=0)
        before_body = MOVIE.split("---\n", 2)[2]
        report = batch_imdb.apply_plan(
            self.vault,
            plan,
            "APPLY " + plan["planId"],
        )
        self.assertEqual(report["stats"]["updated"], 1)
        after = self.note.read_text(encoding="utf-8")
        self.assertIn("imdbScore: 8.2", after)
        self.assertEqual(after.split("---\n", 2)[2], before_body)

    def test_existing_imdb_score_is_skipped(self):
        self.note.write_text(
            MOVIE.replace("status: true\n", "status: true\nimdbScore: 8.1\n"),
            encoding="utf-8",
        )
        with mock.patch.object(batch_imdb, "lookup_imdb") as lookup:
            plan = batch_imdb.build_plan(self.vault, wait_seconds=0)
        self.assertEqual(plan["stats"]["skipped_existing"], 1)
        lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
