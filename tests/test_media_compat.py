from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import media_compat  # noqa: E402


class MediaCompatTests(unittest.TestCase):
    def test_legacy_false_maps_without_claiming_progress(self):
        movie = media_compat.normalize_status("movie", False, None)
        game = media_compat.normalize_status("game", False, None)
        self.assertEqual(movie["progressStatus"], "planned")
        self.assertEqual(movie["statusGroup"], "not_started")
        self.assertTrue(movie["progressStatusValid"])
        self.assertEqual(movie["progressStatusSource"], "legacy")
        self.assertEqual(game["progressStatus"], "backlog")
        self.assertEqual(game["statusGroup"], "not_started")

    def test_identity_and_future_schema_are_reported(self):
        legacy = media_compat.normalize_identity(None, None)
        self.assertTrue(legacy["writeAllowed"])
        self.assertFalse(legacy["schemaVersionValid"])

        valid = media_compat.normalize_identity(
            2, "c3b9e4a8-7d21-4f8a-9e1b-3f2d1e0a9b8c"
        )
        self.assertTrue(valid["mediaIdValid"])
        self.assertTrue(valid["schemaVersionValid"])
        uppercase = media_compat.normalize_identity(
            2, "C3B9E4A8-7D21-4F8A-9E1B-3F2D1E0A9B8C"
        )
        self.assertTrue(uppercase["mediaIdValid"])

        future = media_compat.normalize_identity(3, "bad")
        self.assertTrue(future["schemaFuture"])
        self.assertFalse(future["writeAllowed"])
        self.assertIn("INVALID_MEDIA_ID", future["identityWarnings"])

        fractional = media_compat.normalize_identity(Decimal("2.5"), None)
        self.assertFalse(fractional["writeAllowed"])
        self.assertEqual(fractional["schemaVersion"], None)
        self.assertIn("INVALID_SCHEMA_VERSION", fractional["identityWarnings"])

        quoted = media_compat.normalize_identity("2", None)
        self.assertFalse(quoted["writeAllowed"])
        self.assertIn("INVALID_SCHEMA_VERSION", quoted["identityWarnings"])

    def test_status_conflict_prefers_progress_and_warns(self):
        result = media_compat.normalize_status("game", False, "completed")
        self.assertEqual(result["progressStatus"], "completed")
        self.assertTrue(result["status"])
        self.assertIn("STATUS_FIELD_MISMATCH", result["warnings"])

    def test_unknown_progress_is_not_overwritten(self):
        result = media_compat.normalize_status("book", True, "shelved")
        self.assertEqual(result["progressStatus"], "shelved")
        self.assertIsNone(result["status"])
        self.assertFalse(result["progressStatusValid"])
        self.assertEqual(result["statusGroup"], "unknown")

    def test_game_norm_score_priority(self):
        rating = media_compat.normalize_ratings(
            "game",
            {"score": "91", "metacriticScore": "88", "myScore": "9.5"},
        )
        self.assertEqual(rating["normScore"], 8.8)
        self.assertEqual(rating["scoreSource"], "metacritic")
        self.assertEqual(rating["myScore"], 9.5)

    def test_game_legacy_score_fallbacks(self):
        high = media_compat.normalize_ratings("game", {"score": "85"})
        low = media_compat.normalize_ratings("game", {"score": "8.5"})
        self.assertEqual(high["normScore"], 8.5)
        self.assertEqual(high["scoreSource"], "metacritic")
        self.assertEqual(low["normScore"], 8.5)
        self.assertEqual(low["scoreSource"], "metacritic")

    def test_movie_uses_douban_then_legacy(self):
        self.assertEqual(
            media_compat.normalize_ratings(
                "movie", {"score": "8.1", "doubanScore": "8.6"}
            )["normScore"],
            8.6,
        )
        self.assertEqual(
            media_compat.normalize_ratings("movie", {"score": "8.4"})["normScore"],
            8.4,
        )
        self.assertEqual(
            media_compat.normalize_ratings("movie", {"score": "8.4"})["scoreSource"],
            "douban",
        )
        self.assertIsNone(
            media_compat.normalize_ratings("movie", {"score": "85"})["normScore"]
        )

    def test_invalid_ratings_do_not_become_zero(self):
        result = media_compat.normalize_ratings(
            "movie", {"score": "NaN", "doubanScore": "10.2", "myScore": "8.3"}
        )
        self.assertIsNone(result["score"])
        self.assertIsNone(result["doubanScore"])
        self.assertIsNone(result["myScore"])
        self.assertIsNone(result["normScore"])
        self.assertTrue(result["ratingWarnings"])

    def test_status_write_matrix(self):
        updates = media_compat.build_item_updates("game", {}, {"progressStatus": "playing"})
        self.assertEqual(updates["progressStatus"], "playing")
        self.assertFalse(updates["status"])

        updates = media_compat.build_item_updates("game", {}, {"status": True})
        self.assertEqual(updates["progressStatus"], "completed")
        self.assertTrue(updates["status"])

        with self.assertRaises(ValueError):
            media_compat.build_item_updates(
                "game", {}, {"progressStatus": "playing", "status": True}
            )

    def test_primary_rating_write_syncs_legacy_score(self):
        updates = media_compat.build_item_updates(
            "movie", {}, {"doubanScore": "8.6", "score": "1.0"}
        )
        self.assertEqual(updates["doubanScore"], 8.6)
        self.assertEqual(updates["score"], 8.6)

        updates = media_compat.build_item_updates(
            "game", {}, {"metacriticScore": 92, "score": 1}
        )
        self.assertEqual(updates["metacriticScore"], 92)
        self.assertEqual(updates["score"], 92)

        legacy = media_compat.build_item_updates(
            "movie", {}, {"score": "8.4"}
        )
        self.assertEqual(legacy["score"], 8.4)
        self.assertEqual(legacy["doubanScore"], 8.4)

    def test_rotten_tomatoes_rating_is_separate(self):
        result = media_compat.normalize_ratings(
            "movie", {"rottenTomatoesScore": 87, "myScore": 9.5}
        )
        self.assertEqual(result["rottenTomatoesScore"], 87)
        self.assertEqual(result["myScore"], 9.5)
        with self.assertRaises(ValueError):
            media_compat.build_item_updates(
                "movie", {}, {"rottenTomatoesScore": 101}
            )

    def test_personal_score_never_touches_external_score(self):
        updates = media_compat.build_item_updates("movie", {}, {"myScore": 9.5})
        self.assertEqual(updates, {"myScore": 9.5})

    def test_personal_score_requires_half_step(self):
        with self.assertRaises(ValueError):
            media_compat.build_item_updates("movie", {}, {"myScore": 8.3})

    def test_tags_and_genre_are_read_merged_but_not_rewritten(self):
        tags = media_compat.normalize_tags(
            {"tags": ["RPG", "#收藏"], "genre": ["RPG", "动作"]}
        )
        self.assertEqual(tags, ["RPG", "收藏", "动作"])
        self.assertNotIn("tags", media_compat.build_item_updates(
            "game", {}, {"tags": ["RPG"]}
        ))

    def test_music_stays_on_legacy_status_model(self):
        updates = media_compat.build_item_updates("music", {}, {"status": True})
        self.assertEqual(updates, {"status": True})
        completed = media_compat.normalize_status("music", True, None)
        todo = media_compat.normalize_status("music", False, None)
        self.assertEqual(completed["statusGroup"], "completed")
        self.assertEqual(todo["statusGroup"], "not_started")
        with self.assertRaises(ValueError):
            media_compat.build_item_updates(
                "music", {}, {"progressStatus": "completed"}
            )


if __name__ == "__main__":
    unittest.main()
