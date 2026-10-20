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


if __name__ == "__main__":
    unittest.main()
