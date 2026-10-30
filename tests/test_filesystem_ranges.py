"""ACP filesystem range and redaction regressions."""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from acp.exceptions import RequestError
from agent.redact import redact_sensitive_text
from hermes_omp_acp._session import _RequestContext, _SessionCallbacks


class FilesystemRangeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"HERMES_HOME": str(self.root / "home")})
        environment.start()
        self.addCleanup(environment.stop)
        self.path = self.root / "fixture.txt"
        self.callbacks = _SessionCallbacks(
            str(self.root), allow_file_requests=True, request_context=_RequestContext(),
        )

    def read(self, **params):
        return asyncio.run(self.callbacks.read_text_file(
            path=str(self.path), session_id="fixture", **params,
        )).content

    def test_limit_applies_with_omitted_first_and_later_start(self):
        self.path.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
        for params, expected in (
            ({"limit": 2}, "one\ntwo\n"),
            ({"line": 1, "limit": 2}, "one\ntwo\n"),
            ({"line": 2, "limit": 2}, "two\nthree\n"),
            ({"line": 5, "limit": 2}, "five\n"),
            ({"line": 6, "limit": 2}, ""),
            ({"line": 2}, "two\nthree\nfour\nfive\n"),
            ({"line": 0, "limit": 1}, "one\n"),
            ({"line": -1, "limit": 1}, "one\n"),
            ({"line": 2, "limit": 0}, "two\nthree\nfour\nfive\n"),
            ({"line": 2, "limit": -1}, "two\nthree\nfour\nfive\n"),
            ({}, "one\ntwo\nthree\nfour\nfive\n"),
            ({"line": 10**30, "limit": 2}, ""),
            ({"line": 2, "limit": 10**30}, "two\nthree\nfour\nfive\n"),
        ):
            with self.subTest(params=params):
                self.assertEqual(self.read(**params), expected)

    def test_bom_unicode_newlines_and_unterminated_eof(self):
        self.path.write_bytes("\ufeff한글\r\nsecond\rthird\nfourth\vlast".encode("utf-8"))
        self.assertEqual(self.read(limit=2), "한글\nsecond\n")
        self.assertEqual(self.read(line=3, limit=2), "third\nfourth\v")
        self.assertEqual(self.read(line=5, limit=2), "last")
        self.assertEqual(self.read(), "한글\nsecond\nthird\nfourth\vlast")
        self.path.write_bytes(b"")
        self.assertEqual(self.read(limit=2), "")
        self.path.unlink()
        self.assertEqual(self.read(limit=2), "")

    def test_bounded_range_does_not_decode_large_unrequested_tail(self):
        with self.path.open("wb") as stream:
            stream.write(b"one\ntwo\nthree\n")
            stream.write(b"unused\n" * 1_000_000)
            stream.write(b"\xff")
        self.assertEqual(self.read(limit=2), "one\ntwo\n")
        self.assertEqual(self.read(line=2, limit=2), "two\nthree\n")
        with self.assertRaises(RequestError):
            self.read()

    def test_redaction_keeps_multiline_context_and_is_forced(self):
        # Synthetic credentials only. Redact once after joining the range, not
        # separately per line: control-split tokens need the complete context.
        sensitive = "API_KEY=" + "fixture-secret-value-123456789\n sk-abc\nmnopqrstuvwxyz123456\n"
        self.path.write_text("safe\n" + sensitive + "outside\n", encoding="utf-8")
        with patch("agent.redact._REDACT_ENABLED", False):
            expected = redact_sensitive_text(sensitive, force=True)
            self.assertNotIn("fixture-secret-value-123456789", expected)
            self.assertNotIn("mnopqrstuvwxyz123456", expected)
            self.assertEqual(self.read(line=2, limit=3), expected)
            first = self.read(limit=2)
            self.assertNotIn("fixture-secret-value-123456789", first)
            self.assertTrue(first.startswith("safe\n"))
            self.assertNotIn("outside", first)


if __name__ == "__main__":
    unittest.main()
