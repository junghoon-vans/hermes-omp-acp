import asyncio
from concurrent.futures import ThreadPoolExecutor
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

    def test_peer_exit_reports_its_failure_instead_of_waiting_for_a_timeout(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "exit"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}), closing(self.client()) as client:
            with self.assertRaises(RuntimeError) as error:
                client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Do the task"}], timeout=2,
                )
        self.assertIn("fixture peer failed during initialization", str(error.exception))

    def test_model_discovery_remains_available_inside_an_async_caller(self):
        async def exercise():
            self.assertIn("fixture/model", self.profile.fetch_models(timeout=5) or [])

        asyncio.run(exercise())

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


    def test_acp_permission_requests_are_cancelled(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "permission"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            with closing(self.client()) as client:
                response = client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Request a protected action"}],
                    timeout=5,
                )
        self.assertEqual(response.choices[0].message.content, "permission outcome: cancelled")

    def test_filesystem_callbacks_allow_safe_reads_and_reject_unsafe_access(self):
        secret = "fixture-read-secret-123456789"
        (self.home / "allowed.txt").write_text(f"read-ok\nAPI_KEY={secret}\n", encoding="utf-8")
        outside_read_path = self.home.parent / f"{self.home.name}-outside-read.txt"
        outside_read_path.write_text("outside-read-secret", encoding="utf-8")
        outside_write_path = self.home.parent / f"{self.home.name}-outside-write.txt"
        protected_write_path = self.home / ".env"
        for path in (outside_read_path, outside_write_path, protected_write_path):
            self.addCleanup(path.unlink, missing_ok=True)

        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "filesystem"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            with closing(self.client()) as client:
                response = client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Inspect the fixture files"}],
                    timeout=5,
                )

        content = response.choices[0].message.content
        self.assertIn("read-ok", content)
        self.assertNotIn(secret, content)
        self.assertIn("outside_read_denied=True", content)
        self.assertNotIn("outside-read-secret", content)
        self.assertIn("protected_write_denied=True", content)
        self.assertIn("outside_write_denied=True", content)
        self.assertFalse(protected_write_path.exists())
        self.assertFalse(outside_write_path.exists())

    def test_reasoning_and_hermes_tool_calls_survive_completion_conversion(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "conversion"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            with closing(self.client()) as client:
                response = client.chat.completions.create(
                    model="fixture/model", messages=[{"role": "user", "content": "Look something up"}],
                    timeout=5,
                )

        choice = response.choices[0]
        self.assertEqual(choice.finish_reason, "tool_calls")
        self.assertEqual(choice.message.content, "Before the call.\nAfter the call.")
        self.assertEqual(choice.message.reasoning_content, "The agent's private reasoning.")
        self.assertEqual(len(choice.message.tool_calls), 1)
        tool_call = choice.message.tool_calls[0]
        self.assertEqual(tool_call.id, "fixture-call")
        self.assertEqual(tool_call.function.name, "lookup")
        self.assertEqual(tool_call.function.arguments, '{"query":"hermes"}')

    def test_concurrent_completions_on_one_client_keep_sessions_isolated(self):
        args = shlex.join([str(Path(__file__).with_name("acp_peer.py")), "isolation"])
        with patch.dict(os.environ, {"HERMES_OMP_ACP_ARGS": args}):
            with closing(self.client()) as client:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    alpha = executor.submit(
                        client.chat.completions.create,
                        model="fixture/model", messages=[{"role": "user", "content": "request alpha"}], timeout=5,
                    )
                    beta = executor.submit(
                        client.chat.completions.create,
                        model="fixture/model", messages=[{"role": "user", "content": "request beta"}], timeout=5,
                    )
                    alpha_response = alpha.result(timeout=8)
                    beta_response = beta.result(timeout=8)

        self.assertEqual(alpha_response.choices[0].message.content, "reply to alpha")
        self.assertEqual(beta_response.choices[0].message.content, "reply to beta")

if __name__ == "__main__":
    unittest.main()
