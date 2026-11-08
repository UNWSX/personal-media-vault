from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import server  # noqa: E402
import batch_enhanced_ratings as batch  # noqa: E402
from media_seasons import chinese_number, extract_season  # noqa: E402


class SeasonParsingTests(unittest.TestCase):
    def test_chinese_and_english_seasons(self):
        self.assertEqual(extract_season("瑞克和莫蒂第四季")["season"], 4)
        self.assertEqual(extract_season("洛基 S02")["season"], 2)
        self.assertEqual(
            extract_season("Attack on Titan Season 3 Part 2")["season"],
            3,
        )
        self.assertEqual(
            extract_season("无职转生Ⅱ到了异世界就拿出真本事Part.2"),
            {"title": "无职转生", "season": 2, "part": 2},
        )
        self.assertEqual(chinese_number("十二"), 12)


class BangumiAndAliasTests(unittest.TestCase):
    def setUp(self):
        self._rating_queue = server.RATING_QUEUE

    def tearDown(self):
        server.RATING_QUEUE = self._rating_queue

    def test_bangumi_exact_season_score(self):
        payload = {
            "data": [{
                "id": 118335,
                "name": "進撃の巨人 Season 2",
                "name_cn": "进击的巨人 第二季",
                "date": "2017-04-01",
                "rating": {"score": 8.1},
            }]
        }
        with mock.patch.object(server, "_post_json_url", return_value=payload):
            result = server.lookup_bangumi(
                "进击的巨人 第二季",
                2017,
                season=2,
            )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["score"], 8.1)

    def test_bangumi_retries_transient_network_failure(self):
        payload = {
            "data": [{
                "id": 118335,
                "name": "進撃の巨人 Season 2",
                "name_cn": "进击的巨人 第二季",
                "date": "2017-04-01",
                "rating": {"score": 8.1},
            }]
        }
        with mock.patch.object(
            server,
            "_post_json_url",
            side_effect=[OSError("temporary"), payload],
        ), mock.patch.object(server.time, "sleep"):
            result = server.lookup_bangumi(
                "进击的巨人 第二季",
                2017,
                season=2,
            )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["score"], 8.1)

    def test_anilist_bridges_japanese_to_english(self):
        payload = {
            "data": {
                "Page": {
                    "media": [{
                        "title": {
                            "english": "Attack on Titan Season 2",
                            "romaji": "Shingeki no Kyojin Season 2",
                            "native": "進撃の巨人 Season 2",
                        },
                        "synonyms": ["进击的巨人 第二季"],
                        "startDate": {"year": 2017},
                    }]
                }
            }
        }
        with mock.patch.object(server, "_post_json_url", return_value=payload):
            result = server.lookup_anime_aliases(
                "進撃の巨人 Season 2",
                2017,
                season=2,
            )
        self.assertEqual(result["status"], "matched")
        self.assertIn("Attack on Titan Season 2", result["aliases"])

    def test_anilist_retries_transient_network_failure(self):
        payload = {
            "data": {
                "Page": {
                    "media": [{
                        "title": {
                            "english": "Attack on Titan Season 2",
                            "romaji": "Shingeki no Kyojin Season 2",
                            "native": "進撃の巨人 Season 2",
                        },
                        "synonyms": ["进击的巨人 第二季"],
                        "startDate": {"year": 2017},
                    }]
                }
            }
        }
        with mock.patch.object(
            server,
            "_post_json_url",
            side_effect=[OSError("temporary"), payload],
        ), mock.patch.object(server.time, "sleep"):
            result = server.lookup_anime_aliases(
                "進撃の巨人 Season 2",
                2017,
                season=2,
            )
        self.assertEqual(result["status"], "matched")

    def test_imdb_suggestion_retries_transient_network_failure(self):
        payload = {
            "d": [{
                "id": "tt13293588",
                "l": "Mushoku Tensei: Jobless Reincarnation",
                "y": 2021,
                "qid": "tvSeries",
            }]
        }
        with mock.patch.object(
            server,
            "http_json",
            side_effect=[OSError("temporary"), payload],
        ), mock.patch.object(server.time, "sleep"):
            result = server._imdb_suggestion_json("Mushoku Tensei")
        self.assertEqual(result["d"][0]["id"], "tt13293588")

    def test_anime_alias_lookup_tries_original_title_after_first_miss(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            lib = server.Library(vault)
            cache_state = {
                "imdb": {},
                "rotten": {},
                "bangumi": {},
                "anilist": {},
                "wikidata": {},
                "metacritic": {},
            }
            with (
                mock.patch.object(
                    batch,
                    "_douban_title_candidate",
                    return_value=("", ""),
                ),
                mock.patch.object(
                    batch,
                    "lookup_bangumi",
                    return_value={
                        "status": "matched",
                        "score": 8.0,
                        "candidate": {
                            "originalTitle": "日本語タイトル Season 3",
                        },
                    },
                ),
                mock.patch.object(
                    batch,
                    "lookup_anime_aliases",
                    side_effect=[
                        {"status": "no_match", "aliases": []},
                        {
                            "status": "matched",
                            "aliases": [
                                "English Anime Title Season 3",
                            ],
                        },
                    ],
                ) as aliases,
            ):
                identity = batch._build_identity(
                    {
                        "type": "teleplay",
                        "title": "中文动画标题",
                        "year": 2026,
                        "tags": ["动画"],
                    },
                    {"originalTitle": "日本語タイトル Season 3"},
                    lib,
                    None,
                    cache_state,
                    None,
                    0,
                    True,
                    False,
                    False,
                    True,
                    True,
                )
        self.assertGreaterEqual(aliases.call_count, 2)
        self.assertIn(
            (
                "English Anime Title Season 3",
                "anilist_alias",
            ),
            identity["queries"],
        )

    def test_verified_anime_series_overrides(self):
        cases = {
            "灵笼第二季": ("tt10827146", 2),
            "雾山五行": ("tt12953630", 1),
            "雾山五行·犀川幻紫林": ("tt12953630", 2),
            "我的三体之章北海传": ("tt8829226", 3),
            "我的三体之罗辑传": ("tt8829226", 2),
            "鬼灭之刃：无限列车篇": ("tt9335498", 2),
            "无职转生Ⅲ 到了异世界就拿出真本事": ("tt13293588", 3),
        }
        for title, (imdb_id, season) in cases.items():
            with self.subTest(title=title):
                override = batch.resolve_imdb_override(title)
                self.assertIsNotNone(override)
                self.assertEqual(override["imdbId"], imdb_id)
                self.assertEqual(override["season"], season)

    def test_imdb_season_accepts_year_inside_multi_year_run(self):
        episodes = [{
            "id": "tt1",
            "season": 2,
            "episode": number,
            "year": year,
            "rating": rating,
        } for number, year, rating in (
            (1, 2024, 9.0),
            (2, 2025, 9.4),
        )]
        with mock.patch.object(
            server,
            "_imdb_series_episodes",
            return_value=episodes,
        ):
            result = server._lookup_imdb_season_candidates(
                [{"id": "tt10827146", "l": "Ling Cage", "qid": "tvSeries"}],
                2,
                2025,
                {"tvseries"},
            )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["score"], 9.2)

    def test_anime_detection_accepts_extended_tag_labels(self):
        self.assertTrue(batch._is_anime({"tags": ["日本动画"]}))
        self.assertTrue(batch._is_anime({"tags": ["TV Anime"]}))
        self.assertEqual(
            server._missing_external_fields({
                "type": "teleplay",
                "imdbScore": 8.0,
                "rottenTomatoesScore": 90,
                "metacriticScore": 80,
                "bangumiScore": None,
                "tags": ["日本动画"],
            }),
            ["bangumiScore"],
        )

    def test_wikidata_resolves_stable_imdb_and_rt_ids(self):
        search = {"search": [{"id": "Q69082264", "label": "中国机长"}]}
        entities = {
            "entities": {
                "Q69082264": {
                    "labels": {
                        "zh": {"value": "中国机长"},
                        "en": {"value": "The Captain"},
                    },
                    "aliases": {},
                    "claims": {
                        "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q11424"}}}}],
                        "P577": [{"mainsnak": {"datavalue": {"value": {
                            "time": "+2019-09-21T00:00:00Z"
                        }}}}],
                        "P345": [{"mainsnak": {"datavalue": {
                            "value": "tt10218664"
                        }}}],
                        "P1258": [{"mainsnak": {"datavalue": {
                            "value": "m/the_captain_2019"
                        }}}],
                        "P1712": [{"mainsnak": {"datavalue": {
                            "value": "the-captain-2019"
                        }}}],
                    },
                }
            }
        }
        with mock.patch.object(
            server,
            "_wikidata_json",
            side_effect=[search, entities],
        ):
            result = server.lookup_wikidata(
                "中国机长",
                "movie",
                2019,
            )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["candidate"]["imdbId"], "tt10218664")
        self.assertEqual(
            result["candidate"]["rottenTomatoesId"],
            "m/the_captain_2019",
        )
        self.assertEqual(
            result["candidate"]["metacriticId"],
            "the-captain-2019",
        )

    def test_wikidata_can_refresh_claims_from_existing_qid(self):
        entities = {
            "entities": {
                "Q130700148": {
                    "labels": {
                        "en": {"value": "Oppenheimer"},
                    },
                    "claims": {
                        "P31": [{"mainsnak": {"datavalue": {"value": {
                            "id": "Q11424"
                        }}}}],
                        "P577": [{"mainsnak": {"datavalue": {"value": {
                            "time": "+2023-07-21T00:00:00Z"
                        }}}}],
                        "P345": [{"mainsnak": {"datavalue": {
                            "value": "tt15398776"
                        }}}],
                        "P1712": [{"mainsnak": {"datavalue": {
                            "value": "oppenheimer"
                        }}}],
                    },
                }
            }
        }
        with mock.patch.object(
            server,
            "_wikidata_json",
            return_value=entities,
        ):
            result = server.lookup_wikidata_by_id(
                "Q130700148",
                "movie",
            )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["candidate"]["year"], 2023)
        self.assertEqual(result["candidate"]["metacriticId"], "oppenheimer")

    def test_external_ids_fetch_scores_without_title_search(self):
        with mock.patch.object(
            server,
            "_imdb_graphql_title",
            return_value={"ratingsSummary": {"aggregateRating": 6.3}},
        ), mock.patch.object(
            server,
            "_rt_text",
            return_value=(
                '"criticsScore":{"averageRating":"6.4",'
                '"scorePercent":"75%"}'
            ),
        ), mock.patch.object(
            server,
            "_metacritic_text",
            return_value='"ratingValue":72',
        ):
            result = server.lookup_external_ids({
                "imdbId": "tt10218664",
                "rottenTomatoesId": "m/the_captain_2019",
                "metacriticId": "the-captain-2019",
                "type": "movie",
            })
        self.assertEqual(result["imdbScore"], 6.3)
        self.assertEqual(result["rottenTomatoesScore"], 75)
        self.assertEqual(result["metacriticScore"], 72)

    def test_external_ids_use_metacritic_id_for_tv_season(self):
        with mock.patch.object(
            server,
            "_metacritic_text",
            return_value='"ratingValue": 84',
        ) as fetch_page:
            result = server.lookup_external_ids(
                {
                    "metacriticId": "the-last-of-us",
                    "type": "teleplay",
                },
                season=1,
                year=2023,
                fields={"metacriticScore"},
            )
        self.assertEqual(result["metacriticScore"], 84)
        fetch_page.assert_called_once_with(
            "https://www.metacritic.com/tv/the-last-of-us/season-1/"
        )

    def test_metacritic_movie_exact_year(self):
        payload = {
            "data": {
                "items": [{
                    "type": "movie",
                    "title": "Dune: Part Two",
                    "slug": "dune-part-two",
                    "premiereYear": 2024,
                    "releaseDate": "2024-03-01",
                    "criticScoreSummary": {"score": 79},
                }]
            }
        }
        with mock.patch.object(server, "http_json", return_value=payload):
            result = server.lookup_metacritic(
                "Dune: Part Two",
                "movie",
                2024,
            )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["score"], 79)

    def test_metacritic_tv_season_reads_season_page(self):
        payload = {
            "data": {
                "items": [{
                    "type": "season",
                    "title": "The Last of Us : Season 1",
                    "slug": "the-last-of-us/season-1",
                    "releaseDate": "",
                    "premiereYear": 0,
                    "criticScoreSummary": {"score": None},
                }]
            }
        }
        with mock.patch.object(server, "http_json", return_value=payload), \
                mock.patch.object(
                    server,
                    "_metacritic_text",
                    return_value='"ratingValue":84',
                ):
            result = server.lookup_metacritic(
                "The Last of Us",
                "teleplay",
                2023,
                season=1,
            )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["score"], 84)

    def test_metacritic_tv_season_rejects_lookalike_series(self):
        payload = {
            "data": {
                "items": [{
                    "type": "season",
                    "title": "Daredevil: Born Again : Season 1",
                    "slug": "daredevil-born-again/season-1",
                    "releaseDate": "2025-03-04",
                    "premiereYear": 2025,
                    "criticScoreSummary": {"score": 69},
                }]
            }
        }
        with mock.patch.object(server, "http_json", return_value=payload):
            result = server.lookup_metacritic(
                "Daredevil",
                "teleplay",
                2015,
                season=1,
            )
        self.assertEqual(result["status"], "no_match")

    def test_metacritic_zero_is_treated_as_missing(self):
        payload = {
            "data": {
                "items": [{
                    "type": "movie",
                    "title": "Detective Chinatown",
                    "slug": "detective-chinatown",
                    "premiereYear": 2015,
                    "releaseDate": "2015-12-31",
                    "criticScoreSummary": {"score": 0},
                }]
            }
        }
        with mock.patch.object(server, "http_json", return_value=payload):
            result = server.lookup_metacritic(
                "Detective Chinatown",
                "movie",
                2015,
            )
        self.assertEqual(result["status"], "no_match")

    def test_metacritic_only_plan_prefers_wikidata_stable_id(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            media = vault / "02-电影阅读" / "movie"
            media.mkdir(parents=True)
            (media / "奥本海默.md").write_text(
                "---\n"
                "id: \"1\"\n"
                "title: 奥本海默\n"
                "originalTitle: Oppenheimer\n"
                "type: movie\n"
                "year: 2023\n"
                "status: true\n"
                "---\n\n正文保持不变。\n",
                encoding="utf-8",
            )
            wikidata = {
                "status": "matched",
                "candidate": {
                    "wikidataId": "Q130700148",
                    "imdbId": "tt15398776",
                    "metacriticId": "oppenheimer",
                    "englishTitle": "Oppenheimer",
                    "year": 2023,
                    "type": "movie",
                },
            }
            with mock.patch.object(
                batch,
                "_douban_title_candidate",
                return_value=("Oppenheimer", "douban_search"),
            ), mock.patch.object(
                batch,
                "lookup_wikidata",
                return_value=wikidata,
            ) as wikidata_lookup, mock.patch.object(
                batch,
                "lookup_external_ids",
                return_value={
                    "imdbScore": None,
                    "rottenTomatoesScore": None,
                    "metacriticScore": 90,
                    "errors": [],
                },
            ) as external_lookup, mock.patch.object(
                batch,
                "lookup_metacritic",
            ) as title_lookup:
                plan = batch.build_plan(
                    vault,
                    metacritic_only=True,
                    wait_seconds=0,
                )
        self.assertEqual(plan["entries"][0]["updates"]["metacriticScore"], 90)
        self.assertEqual(
            plan["entries"][0]["metacriticLookup"]["source"],
            "wikidata_metacritic_id",
        )
        wikidata_lookup.assert_called()
        external_lookup.assert_called()
        title_lookup.assert_not_called()

    def test_create_then_worker_applies_automatic_ratings(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "02-电影阅读" / "movie").mkdir(parents=True)
            (vault / "attachments").mkdir()
            lib = server.Library(vault)
            server.RATING_QUEUE = server.RatingJobQueue(lib.data_dir)
            with mock.patch.object(
                server,
                "auto_lookup_external_ratings",
                return_value={
                    "imdbScore": 8.2,
                    "rottenTomatoesScore": 93,
                    "metacriticScore": 90,
                },
            ):
                created = server.create_note(lib, {
                    "type": "movie",
                    "title": "自动评分测试",
                    "status": False,
                    "autoRatings": True,
                })
                self.assertIsNone(created["imdbScore"])
                job = server._queue_rating_job(lib, created)
                server._process_rating_job(lib, job)
                updated = next(
                    item for item in lib.snapshot()
                    if item["path"] == created["path"]
                )
        self.assertEqual(updated["imdbScore"], 8.2)
        self.assertEqual(updated["rottenTomatoesScore"], 93)
        self.assertEqual(updated["metacriticScore"], 90)

    def test_auto_ratings_use_the_same_results_as_batch_tool(self):
        cases = (
            (
                "无职转生Ⅱ到了异世界就拿出真本事Part.1",
                "2023-07-02",
                2,
                1,
                7.6,
            ),
            (
                "无职转生Ⅱ到了异世界就拿出真本事Part2",
                "2024-04-07",
                2,
                2,
                7.9,
            ),
            (
                "无职转生Ⅲ 到了异世界就拿出真本事",
                "2026-07-04",
                3,
                None,
                7.9,
            ),
        )
        for title, date_published, expected_season, expected_part, score in cases:
            with self.subTest(title=title), tempfile.TemporaryDirectory() as temp:
                vault = Path(temp)
                (vault / "02-电影阅读" / "teleplay").mkdir(parents=True)
                (vault / "attachments").mkdir()
                lib = server.Library(vault)
                created = server.create_note(lib, {
                    "type": "teleplay",
                    "title": title,
                    "datePublished": date_published,
                    "tags": ["动画"],
                    "status": False,
                })
                with (
                    mock.patch.object(
                        batch,
                        "lookup_imdb_by_id",
                        return_value={"status": "matched", "score": score},
                    ) as imdb_by_id,
                    mock.patch.object(
                        batch,
                        "lookup_imdb",
                        return_value={"status": "no_match", "score": None},
                    ),
                    mock.patch.object(
                        batch,
                        "lookup_wikidata",
                        return_value={"status": "no_match"},
                    ),
                    mock.patch.object(
                        batch,
                        "lookup_rotten_tomatoes_season",
                        return_value={"status": "no_match", "score": None},
                    ),
                    mock.patch.object(
                        batch,
                        "lookup_metacritic",
                        return_value={"status": "no_match", "score": None},
                    ),
                    mock.patch.object(
                        batch,
                        "lookup_bangumi",
                        return_value={"status": "no_match", "score": None},
                    ),
                    mock.patch.object(
                        batch,
                        "lookup_anime_aliases",
                        return_value={"status": "no_match", "aliases": []},
                    ),
                ):
                    batch_plan = batch.build_plan(
                        vault,
                        only_path=created["path"],
                        wait_seconds=0,
                    )
                    automatic = server.auto_lookup_external_ratings(
                        {
                            "_itemPath": created["path"],
                            "type": "teleplay",
                            "title": title,
                        },
                        lib=lib,
                    )
                self.assertEqual(
                    automatic,
                    batch_plan["entries"][0]["updates"],
                )
                self.assertEqual(automatic, {"imdbScore": score})
                args, kwargs = imdb_by_id.call_args
                self.assertEqual(args[0], "tt13293588")
                self.assertEqual(args[1], "teleplay")
                self.assertEqual(kwargs["season"], expected_season)
                self.assertEqual(kwargs["part"], expected_part)

    def test_worker_schedules_retry_when_sources_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "02-电影阅读" / "movie").mkdir(parents=True)
            (vault / "attachments").mkdir()
            lib = server.Library(vault)
            created = server.create_note(lib, {
                "type": "movie",
                "title": "离线评分测试",
                "status": False,
            })
            queue = mock.Mock()
            server.RATING_QUEUE = queue
            job = {
                "path": created["path"],
                "fields": ["imdbScore", "metacriticScore", "rottenTomatoesScore"],
                "attempts": 0,
            }
            with mock.patch.object(
                server,
                "auto_lookup_external_ratings",
                return_value={},
            ):
                server._process_rating_job(lib, job)
        args = queue.finish.call_args
        self.assertEqual(args.args[1], "retry")
        self.assertEqual(args.kwargs["error"], "NO_NEW_SCORE")
        self.assertGreater(args.kwargs["next_attempt"], time.time())

    def test_douban_import_enables_automatic_ratings(self):
        with tempfile.TemporaryDirectory() as temp:
            vault = Path(temp)
            (vault / "02-电影阅读" / "movie").mkdir(parents=True)
            (vault / "attachments").mkdir()
            lib = server.Library(vault)
            detail = {
                "title": "奥本海默",
                "originalTitle": "Oppenheimer",
                "year": "2023",
                "datePublished": "2023-07-21",
                "score": 8.8,
                "cover": "",
                "intro": "",
                "directors": [],
                "actors": [],
                "countries": [],
                "genres": [],
            }
            with mock.patch.object(
                server,
                "fetch_douban_detail",
                return_value=detail,
            ), mock.patch.object(
                server,
                "_queue_rating_job",
                return_value={"status": "queued"},
            ) as queue_job:
                created = server.import_from_douban(lib, {
                    "id": "35593344",
                    "title": "奥本海默",
                    "type": "movie",
                })
        self.assertIsNone(created["imdbScore"])
        self.assertIsNone(created["rottenTomatoesScore"])
        self.assertIsNone(created["metacriticScore"])
        self.assertEqual(created["originalTitle"], "Oppenheimer")
        self.assertEqual(created["ratingJob"]["status"], "queued")
        queue_job.assert_called_once()

    def test_cached_wikidata_id_is_refreshed_for_new_metacritic_id(self):
        cache = {
            "movie|2023|oppenheimer": {
                "status": "matched",
                "candidate": {
                    "wikidataId": "Q130700148",
                    "imdbId": "tt15398776",
                    "englishTitle": "Oppenheimer",
                    "year": 2023,
                    "type": "movie",
                },
                "scores": {"imdbScore": 8.2},
            },
        }
        refreshed_candidate = {
            "status": "matched",
            "candidate": {
                "wikidataId": "Q130700148",
                "imdbId": "tt15398776",
                "metacriticId": "oppenheimer",
                "englishTitle": "Oppenheimer",
                "year": 2023,
                "type": "movie",
            },
        }
        with mock.patch.object(
            batch,
            "lookup_wikidata",
        ) as title_search, mock.patch.object(
            batch,
            "lookup_wikidata_by_id",
            return_value=refreshed_candidate,
        ) as id_lookup, mock.patch.object(
            batch,
            "lookup_external_ids",
            return_value={
                "imdbScore": None,
                "rottenTomatoesScore": None,
                "metacriticScore": 90,
                "errors": [],
            },
        ):
            result = batch._lookup_wikidata_queries(
                {"type": "movie", "year": 2023},
                [("Oppenheimer", "original_title")],
                cache,
                None,
                {"wikidata": cache, "imdb": {}, "rotten": {}, "bangumi": {}, "anilist": {}, "metacritic": {}},
                None,
                0,
                score_fields={"metacriticScore"},
            )
        self.assertEqual(result["scores"]["metacriticScore"], 90)
        title_search.assert_not_called()
        id_lookup.assert_called_once()

    def test_cached_failure_is_retried(self):
        cache = {
            "lookup|movie|2024||dune":
                {"status": "no_match", "score": None},
        }

        def lookup(query):
            return {
                "status": "matched",
                "score": 79,
                "candidate": {"title": "Dune: Part Two"},
            }

        result = batch._lookup_queries(
            lookup,
            [("Dune", "stored_alias")],
            cache,
            "movie|2024|",
            None,
            {},
            0,
        )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["score"], 79)


if __name__ == "__main__":
    unittest.main()
