import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.error_classifier import classify_api_error
from providers import get_provider_profile

from hermes_omp_acp.errors import ACPError, ACPTimeoutError
from hermes_omp_acp.provider import register


class RuntimeErrorClassificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.agent_dir = self.home / "omp"
        self.error_peer = Path(__file__).with_name("acp_error_peer.py")
        self.normal_peer = Path(__file__).with_name("acp_peer.py")
        self.environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.home),
            "PI_CODING_AGENT_DIR": str(self.agent_dir),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("HERMES_OMP_ACP_MCP_ENABLED", None)
        os.environ.pop("HERMES_OMP_ACP_STREAM_LIMIT_BYTES", None)
        register()
        self.profile = get_provider_profile("omp-acp")

    def _client(self, mode, *, peer=None, mcp_enabled=False):
        peer = peer or self.error_peer
        args = [str(peer)]
        if mode:
            args.append(mode)
        return self.profile.create_client(
            command=sys.executable,
            args=args,
            acp_cwd=str(self.home),
            mcp_enabled=mcp_enabled,
        )

    def _complete(self, client, *, model=None, timeout=5):
        return client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Do the task once."}],
            timeout=timeout,
        )

    @staticmethod
    def _classify(error, model=""):
        return classify_api_error(error, provider="omp-acp", model=model)

    def _assert_reaped(self, pid, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                pass
            time.sleep(0.01)
        self.fail(f"ACP error peer process {pid} was not reaped")

    def test_unoffered_model_is_terminal_before_prompt_dispatch(self):
        client = self._client("config", peer=self.normal_peer)
        try:
            with self.assertRaises(ACPError) as raised:
                self._complete(client, model="fixture/missing")
        finally:
            client.close()
        error = raised.exception
        self.assertEqual(error.phase, "model_selection")
        self.assertIs(error.prompt_dispatched, False)
        self.assertEqual(error.failure_kind, "deterministic")
        self.assertIsNone(error.cause)
        classified = self._classify(error, "fixture/missing")
        self.assertEqual(classified.reason.value, "format_error")
        self.assertFalse(classified.retryable)
        self.assertFalse(classified.should_fallback)
        self.assertFalse((self.home / "prompt-executed").exists())

    def test_missing_executable_is_terminal_and_preserves_launch_cause(self):
        client = self.profile.create_client(
            command=str(self.home / "missing-omp"),
            args=[],
            acp_cwd=str(self.home),
            mcp_enabled=False,
        )
        try:
            with self.assertRaises(ACPError) as raised:
                self._complete(client)
        finally:
            client.close()
        error = raised.exception
        self.assertEqual(error.phase, "launch")
        self.assertIsInstance(error.cause, FileNotFoundError)
        classified = self._classify(error)
        self.assertFalse(classified.retryable)
        self.assertFalse(classified.should_fallback)

    def test_unsupported_mcp_transport_is_terminal_before_prompt_dispatch(self):
        self.agent_dir.mkdir()
        (self.agent_dir / "mcp.json").write_text(
            '{"mcpServers":{"remote":{"type":"http","url":"http://127.0.0.1:1/mcp"}}}',
            encoding="utf-8",
        )
        client = self._client("mcp-error", mcp_enabled=True)
        try:
            with self.assertRaises(ACPError) as raised:
                self._complete(client)
        finally:
            client.close()
        error = raised.exception
        self.assertEqual(error.phase, "initialize")
        self.assertIs(error.prompt_dispatched, False)
        classified = self._classify(error)
        self.assertFalse(classified.retryable)
        self.assertFalse(classified.should_fallback)
        self.assertFalse((self.home / "error-peer.prompt-count").exists())
        self._assert_reaped(int((self.home / "error-peer.pid").read_text(encoding="ascii")))

    def test_rpc_error_keeps_method_code_and_original_cause(self):
        client = self._client("rpc-error")
        try:
            with self.assertRaises(ACPError) as raised:
                self._complete(client)
        finally:
            client.close()
        error = raised.exception
        self.assertEqual(error.phase, "prompt")
        self.assertEqual(error.method, "session/prompt")
        self.assertEqual(error.rpc_code, -32017)
        self.assertIs(error.prompt_dispatched, True)
        self.assertEqual(error.failure_kind, "deterministic")
        self.assertEqual(getattr(error.cause, "code", None), -32017)
        classified = self._classify(error)
        self.assertFalse(classified.retryable)
        self.assertFalse(classified.should_fallback)
        self.assertEqual(classified.error_context["rpc_code"], -32017)
        self.assertEqual((self.home / "error-peer.prompt-count").read_text().splitlines(), ["1"])
        self._assert_reaped(int((self.home / "error-peer.pid").read_text(encoding="ascii")))

    def test_post_dispatch_timeout_is_terminal_and_outcome_unknown(self):
        client = self._client("hang-prompt")
        try:
            with self.assertRaises(ACPTimeoutError) as raised:
                self._complete(client, timeout=1)
        finally:
            client.close()
        error = raised.exception
        self.assertEqual(error.phase, "prompt")
        self.assertEqual(error.method, "session/prompt")
        self.assertIs(error.prompt_dispatched, True)
        self.assertEqual(error.failure_kind, "outcome_unknown")
        classified = self._classify(error)
        self.assertFalse(classified.retryable)
        self.assertFalse(classified.should_fallback)
        self.assertEqual((self.home / "error-peer.prompt-count").read_text().splitlines(), ["1"])
        self._assert_reaped(int((self.home / "error-peer.pid").read_text(encoding="ascii")))

    def test_pre_dispatch_timeout_is_retryable_without_fallback(self):
        client = self._client("slow-initialize")
        try:
            with self.assertRaises(ACPTimeoutError) as raised:
                self._complete(client, timeout=0.25)
        finally:
            client.close()
        error = raised.exception
        self.assertEqual(error.phase, "initialize")
        self.assertEqual(error.method, "initialize")
        self.assertIs(error.prompt_dispatched, False)
        self.assertEqual(error.failure_kind, "pre_dispatch_transport")
        classified = self._classify(error)
        self.assertTrue(classified.retryable)
        self.assertFalse(classified.should_fallback)
        self._assert_reaped(int((self.home / "error-peer.pid").read_text(encoding="ascii")))


if __name__ == "__main__":
    unittest.main()
