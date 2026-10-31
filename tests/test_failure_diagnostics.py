"""End-to-end safety regressions for subprocess failure diagnostics."""

import asyncio
import os
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_omp_acp.client import OmpACPClient
from hermes_omp_acp.errors import ACPError, ACPTimeoutError


_PEER = Path(__file__).with_name("diagnostic_peer.py")


class FailureDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.home),
            "PI_CODING_AGENT_DIR": str(self.home / "omp"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("HERMES_OMP_ACP_MCP_ENABLED", "HERMES_OMP_ACP_STREAM_LIMIT_BYTES"):
            os.environ.pop(name, None)

    def _client(self, mode, *, stream_limit_bytes=16 * 1024 * 1024):
        return OmpACPClient(
            command=sys.executable,
            args=[str(_PEER), mode],
            acp_cwd=str(self.home),
            mcp_enabled=False,
            stream_limit_bytes=stream_limit_bytes,
        )

    @staticmethod
    def _complete(client, *, timeout=5):
        return client.chat.completions.create(
            model=None,
            messages=[{"role": "user", "content": "Complete the task once."}],
            timeout=timeout,
        )

    def test_oversized_stderr_is_suppressed_after_child_finishes_writing(self):
        for mode in ("oversized-newline", "oversized-no-newline"):
            with self.subTest(mode=mode):
                (self.home / "diagnostic-peer-complete").unlink(missing_ok=True)
                client = self._client(mode)
                with patch("agent.redact._REDACT_ENABLED", False):
                    try:
                        with self.assertRaises(ACPError) as raised:
                            self._complete(client)
                    finally:
                        client.close()

                self.assertTrue((self.home / "diagnostic-peer-complete").is_file())
                error = raised.exception
                error_text = str(error)
                rendered = "".join(traceback.format_exception(error))
                self.assertEqual(error.phase, "prompt")
                self.assertEqual(error.exit_code, 17)
                self.assertIn("exit code 17", error_text)
                for secret in (
                    "OVERSIZED-DIAGNOSTIC-SENTINEL",
                    "OVERFLOW-CONTEXT-TAIL-SECRET-99887766",
                ):
                    self.assertNotIn(secret, error_text)
                    self.assertNotIn(secret, rendered)

    def test_bounded_stderr_preserves_safe_text_and_exit_context(self):
        (self.home / "diagnostic-peer-complete").unlink(missing_ok=True)
        client = self._client("bounded-stderr")
        try:
            with self.assertRaises(ACPError) as raised:
                self._complete(client)
        finally:
            client.close()

        error = raised.exception
        self.assertTrue((self.home / "diagnostic-peer-complete").is_file())
        self.assertIn("safe bounded diagnostic: child detail", str(error))
        self.assertEqual(error.phase, "prompt")
        self.assertEqual(error.exit_code, 17)
        self.assertIn("exit code 17", str(error))

    def test_exit_without_stderr_keeps_status_in_message_and_fields(self):
        client = self._client("empty-stderr")
        try:
            with self.assertRaises(ACPError) as raised:
                self._complete(client)
        finally:
            client.close()
        self.assertEqual(raised.exception.phase, "prompt")
        self.assertEqual(raised.exception.exit_code, 17)
        self.assertIn("exit code 17", str(raised.exception))

    def test_split_multiline_credentials_are_redacted_when_optional_redaction_is_disabled(self):
        (self.home / "diagnostic-peer-complete").unlink(missing_ok=True)
        client = self._client("split-secrets")
        with patch("agent.redact._REDACT_ENABLED", False):
            try:
                with self.assertRaises(ACPError) as raised:
                    self._complete(client)
            finally:
                client.close()

        error_text = str(raised.exception)
        rendered = "".join(traceback.format_exception(raised.exception))
        self.assertTrue((self.home / "diagnostic-peer-complete").is_file())
        self.assertIn("safe diagnostic line", error_text)
        self.assertEqual(raised.exception.exit_code, 17)
        for secret in ("fixture-secret-value-123456789", "mnopqrstuvwxyz123456"):
            self.assertNotIn(secret, error_text)
            self.assertNotIn(secret, rendered)

    def test_stderr_utf8_split_across_writes_is_decoded_after_eof(self):
        (self.home / "diagnostic-peer-complete").unlink(missing_ok=True)
        client = self._client("utf8-stderr")
        try:
            with self.assertRaises(ACPError) as raised:
                self._complete(client)
        finally:
            client.close()

        error = raised.exception
        self.assertTrue((self.home / "diagnostic-peer-complete").is_file())
        self.assertEqual(error.phase, "prompt")
        self.assertEqual(error.exit_code, 17)
        self.assertIn("safe UTF-8 diagnostic: café and snowman ☃", str(error))

    def test_request_error_message_and_data_are_safe_in_public_traceback(self):
        from acp.exceptions import RequestError

        client = self._client("rpc-secret")
        try:
            with self.assertRaises(ACPError) as raised:
                self._complete(client)
        finally:
            client.close()

        error = raised.exception
        rendered = "".join(traceback.format_exception(error))
        self.assertEqual(error.phase, "prompt")
        self.assertEqual(error.method, "session/prompt")
        self.assertEqual(error.rpc_code, -32023)
        self.assertIsNone(error.__context__)
        for snapshot in (error.cause, error.__cause__):
            self.assertIsInstance(snapshot, RequestError)
            self.assertEqual(snapshot.code, -32023)
            self.assertIsNone(getattr(snapshot, "data", None))
            self.assertIsNone(snapshot.__cause__)
            self.assertIsNone(snapshot.__context__)
        for secret in (
            "fixture-secret-value-123456789",
            "mnopqrstuvwxyz123456",
            "API_KEY=",
            "access_token",
            "second line",
            "payload-only-secret-99887766",
        ):
            self.assertNotIn(secret, rendered)

    def test_async_rpc_error_does_not_retain_raw_implicit_context(self):
        async def exercise():
            client = self._client("rpc-secret")
            try:
                with self.assertRaises(ACPError) as raised:
                    await self._complete(client)
                return raised.exception
            finally:
                client.close()

        error = asyncio.run(exercise())
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.cause.data)
        self.assertIsNone(error.__cause__.data)
        self.assertEqual(error.rpc_code, -32023)

    def test_catalog_rpc_error_does_not_retain_raw_context_or_data(self):
        client = self._client("catalog-rpc-secret")
        try:
            with self.assertRaises(ACPError) as raised:
                client.list_models(timeout_seconds=5)
        finally:
            client.close()
        error = raised.exception
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.cause.data)
        self.assertIsNone(error.__cause__.data)
        self.assertEqual(error.rpc_code, -32023)
        self.assertEqual(error.phase, "session")

    def test_overlong_frame_with_newline_uses_configured_stream_limit(self):
        self._assert_frame_limit_error(
            "overlong-newline",
            configured_limit=2048,
            expected_limit=2048,
        )

    def test_overlong_frame_without_newline_uses_environment_override(self):
        with patch.dict(os.environ, {"HERMES_OMP_ACP_STREAM_LIMIT_BYTES": "1536"}):
            self._assert_frame_limit_error(
                "overlong-no-newline",
                configured_limit=8192,
                expected_limit=1536,
            )

    def _assert_frame_limit_error(self, mode, *, configured_limit, expected_limit):
        (self.home / "diagnostic-frame-complete").unlink(missing_ok=True)
        client = self._client(mode, stream_limit_bytes=configured_limit)
        with self.assertLogs(level="ERROR") as logs:
            try:
                with self.assertRaises(ACPError) as raised:
                    self._complete(client)
            finally:
                client.close()

        error = raised.exception
        message = str(error)
        rendered = "".join(traceback.format_exception(error))
        self.assertTrue((self.home / "diagnostic-frame-complete").is_file())
        self.assertEqual(error.phase, "prompt")
        self.assertIn(f"stream_limit_bytes={expected_limit}", message)
        self.assertIn("plugins.entries.omp-acp-provider.settings.stream_limit_bytes", message)
        self.assertIn("HERMES_OMP_ACP_STREAM_LIMIT_BYTES", message)
        self.assertIn("then restart Hermes or its gateway", message)
        self.assertIn("Request timeout is a separate control.", message)
        self.assertNotIn("FRAME-PAYLOAD-SENTINEL", rendered)
        self.assertNotIn("FRAME-PAYLOAD-SENTINEL", "\n".join(logs.output))

    def test_real_timeout_does_not_advertise_frame_limit_remediation(self):
        with patch.dict(os.environ, {"HERMES_OMP_ACP_STREAM_LIMIT_BYTES": "1536"}):
            client = self._client("wait-for-timeout", stream_limit_bytes=8192)
            try:
                with self.assertRaises(ACPTimeoutError) as raised:
                    self._complete(client, timeout=1.5)
            finally:
                client.close()

        error = raised.exception
        self.assertEqual(error.phase, "prompt")
        self.assertNotIn("stream_limit_bytes", str(error))
        self.assertNotIn("HERMES_OMP_ACP_STREAM_LIMIT_BYTES", str(error))

    def test_collector_retains_complete_utf8_block_at_raw_byte_limit(self):
        from hermes_omp_acp._session import _STDERR_LIMIT_BYTES, _StderrDiagnostics

        collector = _StderrDiagnostics()
        payload = "é" * (_STDERR_LIMIT_BYTES // len("é".encode("utf-8")))
        raw = payload.encode("utf-8")
        self.assertEqual(len(raw), _STDERR_LIMIT_BYTES)
        chunks = (raw[:2048], raw[2048:5001], raw[5001:])
        retained = 0
        for chunk in chunks:
            collector.feed(chunk)
            retained += len(chunk)
            self.assertEqual(collector.retained_bytes, retained)
            self.assertLessEqual(collector.retained_bytes, _STDERR_LIMIT_BYTES)
        collector.finish()
        self.assertEqual(collector.text(), payload)
        self.assertEqual(collector.retained_bytes, _STDERR_LIMIT_BYTES)

    def test_collector_discards_the_entire_block_after_raw_overflow(self):
        from hermes_omp_acp._session import _STDERR_LIMIT_BYTES, _StderrDiagnostics

        collector = _StderrDiagnostics()
        first = b"retained-prefix-" + b"x" * (_STDERR_LIMIT_BYTES // 2 - 16)
        second = b"y" * (_STDERR_LIMIT_BYTES - len(first))
        for chunk, expected in ((first, len(first)), (second, _STDERR_LIMIT_BYTES)):
            collector.feed(chunk)
            self.assertEqual(collector.retained_bytes, expected)
            self.assertLessEqual(collector.retained_bytes, _STDERR_LIMIT_BYTES)
        collector.feed(b"overflow-tail-secret")
        self.assertEqual(collector.retained_bytes, 0)
        collector.feed(b"later bytes cannot restore a suffix")
        self.assertEqual(collector.retained_bytes, 0)
        collector.finish()
        text = collector.text()
        self.assertIn("omitted", text)
        self.assertNotIn("retained-prefix", text)
        self.assertNotIn("overflow-tail-secret", text)

    def test_incomplete_collector_never_exposes_unfinished_bytes(self):
        from hermes_omp_acp._session import _StderrDiagnostics

        collector = _StderrDiagnostics()
        collector.feed(b"incomplete-diagnostic-secret")
        self.assertEqual(collector.retained_bytes, len(b"incomplete-diagnostic-secret"))
        text = collector.text()
        self.assertIn("omitted", text)
        self.assertNotIn("incomplete-diagnostic-secret", text)

    def test_collector_suppresses_the_block_if_redaction_raises(self):
        from hermes_omp_acp._session import _StderrDiagnostics

        collector = _StderrDiagnostics()
        collector.feed(b"safe-before-redactor-failure")
        collector.finish()
        with patch(
            "hermes_omp_acp.errors.redact_sensitive_text",
            side_effect=RuntimeError("redactor failure"),
        ):
            text = collector.text()
        self.assertIn("suppressed", text)
        self.assertNotIn("safe-before-redactor-failure", text)


if __name__ == "__main__":
    unittest.main()
