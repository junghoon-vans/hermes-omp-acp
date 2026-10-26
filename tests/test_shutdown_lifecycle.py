import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from providers import get_provider_profile

from hermes_omp_acp.provider import register


class ShutdownLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.peer = Path(__file__).with_name("acp_shutdown_peer.py")
        self.environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.home),
            "PI_CODING_AGENT_DIR": str(self.home / "omp"),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("HERMES_OMP_ACP_MCP_ENABLED", None)
        os.environ.pop("HERMES_OMP_ACP_STREAM_LIMIT_BYTES", None)
        register()
        self.profile = get_provider_profile("omp-acp")

    def _client(self, mode):
        return self.profile.create_client(
            command=sys.executable,
            args=[str(self.peer), mode],
            acp_cwd=str(self.home),
            mcp_enabled=False,
        )

    def _peer_pid(self, timeout=5):
        marker = self.home / "shutdown-peer.pid"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if marker.exists():
                return int(marker.read_text(encoding="ascii"))
            time.sleep(0.01)
        self.fail("ACP shutdown peer did not finish session negotiation")

    @staticmethod
    def _assert_reaped(test_case, pid, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                pass
            time.sleep(0.01)
        test_case.fail(f"ACP shutdown peer process {pid} was not reaped")

    def _prompt(self, client, *, timeout):
        return client.chat.completions.create(
            model=None,
            messages=[{"role": "user", "content": "x" * (4 * 1024 * 1024)}],
            timeout=timeout,
        )

    def test_unread_buffered_stdin_is_bounded_and_ignoring_child_is_reaped(self):
        client = self._client("unread-stdin")
        started = time.monotonic()
        try:
            with self.assertRaises(TimeoutError):
                self._prompt(client, timeout=1.0)
            elapsed = time.monotonic() - started
            pid = self._peer_pid()
        finally:
            client.close()
        self.assertLess(elapsed, 8.0)
        self._assert_reaped(self, pid)

    def test_connection_failure_remains_primary_when_cleanup_crosses_deadline(self):
        client = self._client("closed-stdin")
        started = time.monotonic()
        try:
            with self.assertRaises(RuntimeError) as raised:
                self._prompt(client, timeout=1.0)
            elapsed = time.monotonic() - started
            pid = self._peer_pid()
        finally:
            client.close()
        self.assertIsInstance(raised.exception.__cause__, (BrokenPipeError, ConnectionError))
        self.assertLess(elapsed, 8.0)
        self._assert_reaped(self, pid)


if __name__ == "__main__":
    unittest.main()
