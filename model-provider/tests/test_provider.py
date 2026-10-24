import asyncio
from contextlib import closing
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.auxiliary_client import _to_async_client
from providers import get_provider_profile

from hermes_omp_acp.provider import register


class ProviderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.home),
            "HERMES_OMP_ACP_COMMAND": sys.executable,
            "HERMES_OMP_ACP_ARGS": shlex.join([str(Path(__file__).with_name("acp_peer.py"))]),
        })
        environment.start()
        self.addCleanup(environment.stop)
        register()
        self.profile = get_provider_profile("omp-acp")

    def client(self):
        return self.profile.create_client(acp_cwd=str(self.home))

    def test_missing_executable_does_not_break_the_model_picker(self):
        with patch.dict(os.environ, {"HERMES_OMP_ACP_COMMAND": str(self.home / "missing-omp")}):
            self.assertIsNone(self.profile.fetch_models(timeout=1))

    def test_async_auxiliary_completion_does_not_block_the_event_loop(self):
        async def exercise():
            with closing(self.client()) as client:
                auxiliary, model = _to_async_client(client, "fixture/model")
                heartbeat = asyncio.create_task(asyncio.sleep(0.01))
                response = await auxiliary.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": "A short response"}], timeout=5,
                )
                self.assertTrue(heartbeat.done(), "The subprocess blocked Hermes's async event loop")
                self.assertEqual(response.choices[0].finish_reason, "stop")
        asyncio.run(exercise())

    def test_async_stream_preserves_the_completion_and_stop_boundary(self):
        async def exercise():
            with closing(self.client()) as client:
                auxiliary, model = _to_async_client(client, "fixture/model")
                stream = await auxiliary.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": "A short response"}], stream=True, timeout=5,
                )
                chunks = [chunk async for chunk in stream]
                choices = [choice for chunk in chunks for choice in chunk.choices]
                content = "".join(choice.delta.content or "" for choice in choices)
                self.assertEqual(content, "first second")
                self.assertEqual(choices[-1].finish_reason, "stop")
        asyncio.run(exercise())

    def test_model_selection_failure_prevents_prompt_execution(self):
        cases = (
            ("config", "fixture/missing", RuntimeError, "not offered"),
            ("config", "copilot-acp", RuntimeError, "not offered"),
            ("reject", "fixture/model", RuntimeError, "session/set_config_option failed"),
            ("mismatch", "fixture/model", RuntimeError, "selected"),
            ("timeout", "fixture/model", TimeoutError, "Timed out"),
            ("legacy-reject", "fixture/model", RuntimeError, "session/set_model failed"),
            ("legacy", "fixture/missing", RuntimeError, "not offered"),
        )
        for mode, model, error, message in cases:
            with self.subTest(mode=mode, model=model):
                args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), mode])
                (self.home / "prompt-executed").unlink(missing_ok=True)
                with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
                    with closing(self.client()) as client:
                        with self.assertRaisesRegex(error, message):
                            client.chat.completions.create(
                                model=model, messages=[{"role": "user", "content": "Do the task"}],
                                timeout=1 if mode == "timeout" else 5,
                            )
                self.assertFalse((self.home / "prompt-executed").exists(),
                                 "A failed model selection must not execute the task")

    def test_legacy_model_selection_completes_the_task(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "legacy"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            with closing(self.client()) as client:
                response = client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Do the task"}],
                    timeout=5,
                )
        self.assertEqual(response.choices[0].message.content, "first second")


if __name__ == "__main__":
    unittest.main()
