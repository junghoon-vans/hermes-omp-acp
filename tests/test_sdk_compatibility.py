import asyncio
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

from hermes_omp_acp._session import ACPRuntime
from hermes_omp_acp.errors import ACPError
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
                with self.assertRaises(ACPError) as raised:
                    client.chat.completions.create(
                        model="fixture/model",
                        messages=[{"role": "user", "content": "Do the task"}],
                        timeout=5,
                    )
                elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5, "The transport failure was replaced by the completion timeout")
        self.assertEqual(raised.exception.phase, "prompt")
        self.assertTrue(raised.exception.prompt_dispatched)
        self.assertIsInstance(raised.exception.__cause__, ConnectionError)
        pid = int((self.home / "sdk-compat-eof.ready").read_text(encoding="ascii"))
        self.assert_process_reaped(pid)

    def test_live_child_stdout_eof_during_initialize_fails_and_reaps_child(self):
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": self.peer_args("eof-initialize")}):
            with closing(self.client()) as client:
                started = time.monotonic()
                with self.assertRaises(ACPError) as raised:
                    client.chat.completions.create(
                        model="fixture/model",
                        messages=[{"role": "user", "content": "Do the task"}],
                        timeout=5,
                    )
                elapsed = time.monotonic() - started

        self.assertLess(elapsed, 5)
        self.assertEqual(raised.exception.phase, "initialize")
        self.assertFalse(raised.exception.prompt_dispatched)
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

    def test_request_after_observed_eof_is_not_dispatched(self):
        async def scenario():
            runtime = ACPRuntime(
                acp_command=sys.executable,
                acp_args=[str(Path(__file__).with_name("sdk_compat_peer.py")), "eof-after-session"],
                acp_cwd=str(self.home),
                mcp_enabled=False,
            )
            # Wait at the real session boundary so EOF is known before the next
            # request, rather than relying on subprocess scheduling.
            async with runtime._session() as state:
                async with asyncio.timeout(2):
                    while state.stdout.failure is None:
                        await asyncio.sleep(0.01)
                self.assertIsNone(state.process.returncode)
                with self.assertRaises(RuntimeError) as raised:
                    await asyncio.wait_for(runtime._request(
                        state,
                        "session/set_model",
                        state.connection.set_session_model(
                            model_id="fixture/model", session_id="fixture",
                        ),
                    ), timeout=1)
                self.assertEqual(raised.exception.method, "session/set_model")
                self.assertFalse(raised.exception.prompt_dispatched)
                process = state.process
            self.assertIsNotNone(process.returncode)

        asyncio.run(scenario())
        self.assertFalse((self.home / "sdk-compat-post-eof.request").exists())
