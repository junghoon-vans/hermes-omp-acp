import os
import shlex
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from providers import get_provider_profile

from hermes_omp_acp.provider import register


class SdkCompatibilityTests(unittest.TestCase):
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
        os.environ.pop("HERMES_OMP_ACP_MCP_ENABLED", None)
        os.environ.pop("HERMES_OMP_ACP_STREAM_LIMIT_BYTES", None)
        self.addCleanup(environment.stop)
        register()
        self.profile = get_provider_profile("omp-acp")

    def client(self):
        return self.profile.create_client(acp_cwd=str(self.home))

    def peer_args(self, mode):
        return shlex.join([str(Path(__file__).with_name("sdk_compat_peer.py")), mode])

    @staticmethod
    def process_is_alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def assert_process_reaped(self, pid, timeout=5):
        deadline = time.monotonic() + timeout
        while self.process_is_alive(pid):
            if time.monotonic() >= deadline:
                self.fail(f"ACP peer process {pid} was not reaped")
            time.sleep(0.01)

    def test_live_child_stdout_eof_fails_completion_before_timeout_and_reaps_child(self):
        peer_args = self.peer_args("eof-live")
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": peer_args}):
            with closing(self.client()) as client:
                started = time.monotonic()
                with self.assertRaises(RuntimeError):
                    client.chat.completions.create(
                        model="fixture/model",
                        messages=[{"role": "user", "content": "Do the task"}],
                        timeout=5,
                    )
                elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5, "The transport failure was replaced by the completion timeout")
        pid = int((self.home / "sdk-compat-eof.ready").read_text(encoding="ascii"))
        self.assert_process_reaped(pid)

    def test_normal_completion_survives_peer_exit_after_prompt_response(self):
        peer_args = self.peer_args("exit-after-response")
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": peer_args}):
            with closing(self.client()) as client:
                response = client.chat.completions.create(
                    model="fixture/model",
                    messages=[{"role": "user", "content": "Do the task"}],
                    timeout=5,
                )

        self.assertEqual(response.choices[0].message.content, "completed response")
        self.assertEqual(response.choices[0].finish_reason, "stop")
        pid = int((self.home / "sdk-compat-exit.ready").read_text(encoding="ascii"))
        self.assert_process_reaped(pid)
