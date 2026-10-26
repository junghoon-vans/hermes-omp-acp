import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest
from contextlib import closing
from unittest.mock import patch

from agent.error_classifier import classify_api_error
from agent.runtime_cwd import reset_session_cwd, set_session_cwd
from providers import get_provider_profile

from hermes_omp_acp.provider import register


class ProtocolNegotiationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.home),
            "PI_CODING_AGENT_DIR": str(self.home / "omp"),
            "HERMES_OMP_ACP_COMMAND": sys.executable,
        })
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("HERMES_OMP_ACP_STREAM_LIMIT_BYTES", None)
        os.environ.pop("HERMES_OMP_ACP_MCP_ENABLED", None)
        register()
        self.profile = get_provider_profile("omp-acp")

    def client(self, mode):
        args = shlex.join([str(Path(__file__).with_name("protocol_peer.py")), mode])
        os.environ["HERMES_OMP_ACP_ARGS"] = args
        return self.profile.create_client(acp_cwd=str(self.home))

    def assert_peer_reaped(self):
        pid = int((self.home / "peer-pid").read_text(encoding="ascii"))
        deadline = time.monotonic() + 3
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            if time.monotonic() >= deadline:
                self.fail(f"Protocol negotiation left child process {pid} running")
            time.sleep(0.01)

    def test_supported_version_accepts_unknown_optional_capabilities(self):
        with closing(self.client("supported-version")) as client:
            response = client.chat.completions.create(
                model="fixture/model",
                messages=[{"role": "user", "content": "Complete the task"}],
                timeout=5,
            )
        self.assertEqual(response.choices[0].message.content, "first second")
        self.assertTrue((self.home / "session-created").exists())
        self.assertTrue((self.home / "prompt-executed").exists())
        self.assert_peer_reaped()

    def test_incompatible_or_invalid_version_cannot_create_a_session(self):
        for mode in ("unsupported-version", "missing-version", "invalid-version"):
            with self.subTest(mode=mode):
                (self.home / "session-created").unlink(missing_ok=True)
                (self.home / "prompt-executed").unlink(missing_ok=True)
                with closing(self.client(mode)) as client:
                    with self.assertRaises((RuntimeError, ValueError)) as error:
                        client.chat.completions.create(
                            model="fixture/model",
                            messages=[{"role": "user", "content": "Must not execute"}],
                            timeout=5,
                        )
                verdict = classify_api_error(error.exception, provider="omp-acp", model="fixture/model")
                self.assertFalse(verdict.retryable)
                self.assertFalse(verdict.should_fallback)
                self.assertFalse((self.home / "session-created").exists())
                self.assertFalse((self.home / "prompt-executed").exists())
                self.assert_peer_reaped()

    def test_model_discovery_rejects_incompatible_negotiation(self):
        token = set_session_cwd(str(self.home))
        try:
            with patch.dict(os.environ, {
                "HERMES_OMP_ACP_ARGS": shlex.join([
                    str(Path(__file__).with_name("protocol_peer.py")), "unsupported-version",
                ]),
            }):
                self.assertIsNone(self.profile.fetch_models(timeout=5))
        finally:
            reset_session_cwd(token)
        self.assertFalse((self.home / "session-created").exists())
        self.assert_peer_reaped()


if __name__ == "__main__":
    unittest.main()
