from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))

import douban_auth  # noqa: E402


class DoubanAuthTests(unittest.TestCase):
    def test_normalize_cookie(self):
        raw = 'Cookie: ll=abc; bid=def; dbcl2="1:test"; ck=xyz'
        self.assertEqual(
            douban_auth.normalize_cookie(raw),
            'll=abc; bid=def; dbcl2="1:test"; ck=xyz',
        )

    def test_invalid_cookie_is_rejected(self):
        for raw in ("", "ll=abc", "dbcl2=ok\nCookie-Injection=yes", 123):
            with self.subTest(raw=raw):
                with self.assertRaises(douban_auth.DoubanAuthError):
                    douban_auth.normalize_cookie(raw)

    def test_session_is_encrypted_and_round_trips(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cookie = "ll=abc; bid=def; dbcl2=secret; ck=xyz"
            target = douban_auth.save_session(root, cookie)
            protected = target.read_bytes()
            self.assertNotIn(b"dbcl2", protected)
            self.assertNotIn(b"secret", protected)
            loaded = douban_auth.load_session(root)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["cookie"], cookie)

    def test_clear_session_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            douban_auth.save_session(root, "dbcl2=ok; bid=def")
            self.assertTrue(douban_auth.clear_session(root))
            self.assertFalse(douban_auth.clear_session(root))


if __name__ == "__main__":
    unittest.main()
