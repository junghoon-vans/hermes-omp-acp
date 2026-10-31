"""Public-client regressions for EOF preceding process exit, without inference."""

import asyncio
import os
from pathlib import Path
import socket
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch

from hermes_omp_acp.client import OmpACPClient
from hermes_omp_acp.errors import ACPCancelledError, ACPError, ACPTimeoutError


class EOFDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
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
        os.environ.pop("HERMES_OMP_ACP_STREAM_LIMIT_BYTES", None)

    async def _exercise(self, mode, *, interrupt=None):
        loop = asyncio.get_running_loop()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.setblocking(False)
            client = OmpACPClient(
                command=sys.executable,
                args=[str(Path(__file__).with_name("eof_diagnostic_peer.py")),
                      mode, str(listener.getsockname()[1])],
                acp_cwd=str(self.home),
                mcp_enabled=False,
            )
            request = asyncio.ensure_future(client.chat.completions.create(
                model=None,
                messages=[{"role": "user", "content": "Complete once."}],
                timeout=5,
            ))
            control = None
            release = None
            try:
                control, _ = await asyncio.wait_for(loop.sock_accept(listener), 3)
                control.setblocking(False)
                self.assertEqual(await asyncio.wait_for(loop.sock_recv(control, 1), 3), b"E")
                if mode.startswith("exit-"):
                    async def release_exit():
                        # Deliberately hold the confirmed EOF-before-exit window
                        # open for 100ms. This models delayed child teardown,
                        # not probabilistic scheduling of EOF against exit.
                        ready = loop.create_future()
                        timer = loop.call_later(0.1, ready.set_result, None)
                        try:
                            await ready
                            await loop.sock_sendall(control, b"X")
                            self.assertEqual(await loop.sock_recv(control, 1), b"X")
                        finally:
                            timer.cancel()
                    release = asyncio.create_task(release_exit())
                if interrupt == "cancel":
                    client.cancel()
                deadline_seen = asyncio.Event()
                deadline_timer = None
                try:
                    if interrupt == "deadline":
                        # Arm the caller's deadline only after the EOF receipt,
                        # so interpreter startup cannot consume its budget.
                        deadline_timer = loop.call_later(0.1, deadline_seen.set)
                        async with asyncio.timeout(4):
                            await asyncio.wait_for(request, 0.1)
                    else:
                        await asyncio.wait_for(asyncio.shield(request), 4)
                except (ACPError, ACPTimeoutError, ACPCancelledError) as error:
                    if interrupt == "deadline":
                        self.assertTrue(deadline_seen.is_set(), "The caller deadline must actually expire")
                    if release is not None:
                        await asyncio.wait_for(release, 2)
                    return error
                finally:
                    if deadline_timer is not None:
                        deadline_timer.cancel()
                self.fail("A peer that never answered the prompt must not succeed")
            finally:
                if release is not None and not release.done():
                    release.cancel()
                    await asyncio.gather(release, return_exceptions=True)
                if not request.done():
                    request.cancel()
                await asyncio.gather(request, return_exceptions=True)
                client.close()
                if control is not None:
                    control.close()

    def _assert_prompt_failure(self, error):
        self.assertEqual(error.phase, "prompt")
        self.assertEqual(error.method, "session/prompt")
        self.assertIs(error.prompt_dispatched, True)
        self.assertNotIn("stream_limit_bytes", str(error))
        self.assertNotIn("HERMES_OMP_ACP_STREAM_LIMIT_BYTES", str(error))

    async def test_eof_before_exit_preserves_safe_stderr_and_exit_17(self):
        error = await self._exercise("exit-safe")
        self._assert_prompt_failure(error)
        self.assertIsInstance(error, ACPError)
        with self.subTest(field="exit_code"):
            self.assertEqual(error.exit_code, 17)
        with self.subTest(field="safe_stderr"):
            self.assertIn("safe diagnostic after prompt", str(error))
        with self.subTest(field="exit_message"):
            self.assertIn("exit code 17", str(error))

    async def test_eof_before_exit_redacts_secrets_without_losing_safe_context(self):
        with patch("agent.redact._REDACT_ENABLED", False):
            error = await self._exercise("exit-secrets")
        rendered = "".join(traceback.format_exception(error))
        self.assertNotIn("fixture-eof-secret-123456789", rendered)
        with self.subTest(field="exit_code"):
            self.assertEqual(error.exit_code, 17)
        with self.subTest(field="safe_stderr"):
            self.assertIn("safe diagnostic after prompt", str(error))

    async def test_eof_after_huge_stderr_drain_keeps_exit_without_payload(self):
        for mode in ("exit-huge-newline", "exit-huge-no-newline"):
            with self.subTest(mode=mode):
                with patch("agent.redact._REDACT_ENABLED", False):
                    error = await self._exercise(mode)
                self._assert_prompt_failure(error)
                rendered = "".join(traceback.format_exception(error))
                self.assertNotIn("fixture-eof-secret-123456789", rendered)
                self.assertNotIn("EOF-OVERSIZED-SECRET", rendered)
                self.assertEqual(error.exit_code, 17)
                self.assertIn("exit code 17", str(error))

    async def test_eof_with_live_child_is_bounded_without_fabricated_exit(self):
        for mode in ("both-closed", "stdout-only"):
            with self.subTest(mode=mode):
                error = await self._exercise(mode)
                self._assert_prompt_failure(error)
                self.assertIsInstance(error, ACPError)
                self.assertIsNone(error.exit_code)
                self.assertEqual(error.failure_kind, "outcome_unknown")
                self.assertNotIn("exit code", str(error))

    async def test_cancel_during_eof_stderr_drain_remains_cancellation(self):
        for mode in ("stdout-only", "both-closed"):
            with self.subTest(mode=mode):
                error = await self._exercise(mode, interrupt="cancel")
                self._assert_prompt_failure(error)
                self.assertIsInstance(error, ACPCancelledError)
                self.assertEqual(error.failure_kind, "cancelled")
                self.assertIsNone(error.exit_code)

    async def test_caller_deadline_during_eof_drain_keeps_primary_transport_error(self):
        # EOF is already the primary failure. A caller deadline expiring during
        # diagnostic drain must not replace it with a timeout or frame error.
        for mode in ("stdout-only", "both-closed"):
            with self.subTest(mode=mode):
                error = await self._exercise(mode, interrupt="deadline")
                self._assert_prompt_failure(error)
                self.assertIsInstance(error, ACPError)
                self.assertEqual(error.failure_kind, "outcome_unknown")
                self.assertIsNone(error.exit_code)


if __name__ == "__main__":
    unittest.main()
