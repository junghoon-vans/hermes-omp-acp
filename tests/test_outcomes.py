import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.error_classifier import classify_api_error
from providers import get_provider_profile

from hermes_omp_acp.provider import register


class CompletionOutcomeTests(unittest.TestCase):
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
        os.environ["HERMES_OMP_ACP_ARGS"] = shlex.join([
            str(Path(__file__).with_name("outcomes_peer.py")), mode,
        ])
        return self.profile.create_client(acp_cwd=str(self.home))

    @staticmethod
    def request():
        return {
            "model": "fixture/model",
            "messages": [{"role": "user", "content": "Complete the task"}],
            "timeout": 5,
        }

    def assert_outcome(self, response, mode):
        choice = response.choices[0]
        self.assertEqual(choice.finish_reason, "length" if mode == "max_tokens" else "stop")
        self.assertEqual(response.acp_stop_reason, mode)
        self.assertFalse(choice.message.tool_calls)
        if mode == "refusal":
            self.assertEqual(choice.message.content, "I cannot proceed.")
            self.assertEqual(choice.message.refusal, "I cannot proceed.")
        else:
            self.assertEqual(choice.message.content, "Partial answer.")
            self.assertIsNone(choice.message.refusal)

    def test_sync_and_async_terminal_results_preserve_their_meaning(self):
        async def async_completion(mode):
            with closing(self.client(mode)) as client:
                return await client.chat.completions.create(**self.request())

        for mode in ("end_turn", "max_tokens", "refusal"):
            with self.subTest(mode=mode, caller="sync"):
                with closing(self.client(mode)) as client:
                    self.assert_outcome(client.chat.completions.create(**self.request()), mode)
            with self.subTest(mode=mode, caller="async"):
                self.assert_outcome(asyncio.run(async_completion(mode)), mode)

    def test_buffered_stream_preserves_refusal_and_length(self):
        async def async_stream(mode):
            with closing(self.client(mode)) as client:
                stream = await client.chat.completions.create(**self.request(), stream=True)
                return [chunk async for chunk in stream]

        for mode in ("max_tokens", "refusal"):
            for asynchronous in (False, True):
                with self.subTest(mode=mode, asynchronous=asynchronous):
                    if asynchronous:
                        chunks = asyncio.run(async_stream(mode))
                    else:
                        with closing(self.client(mode)) as client:
                            chunks = list(client.chat.completions.create(**self.request(), stream=True))
                    choices = [choice for chunk in chunks for choice in chunk.choices]
                    self.assertEqual(choices[-1].finish_reason, "length" if mode == "max_tokens" else "stop")
                    self.assertEqual("".join(choice.delta.content or "" for choice in choices),
                                     "I cannot proceed." if mode == "refusal" else "Partial answer.")
                    if mode == "refusal":
                        self.assertEqual("".join(choice.delta.refusal or "" for choice in choices),
                                         "I cannot proceed.")

    def test_cancelled_partial_output_cannot_become_a_tool_call(self):
        async def async_cancel(mode):
            with closing(self.client(mode)) as client:
                await client.chat.completions.create(**self.request(), stream=True)

        for mode in ("cancelled", "cancelled-tools"):
            with self.subTest(mode=mode, caller="sync"):
                with closing(self.client(mode)) as client:
                    with self.assertRaises(InterruptedError):
                        client.chat.completions.create(**self.request())
            with self.subTest(mode=mode, caller="async-stream"):
                with self.assertRaises(InterruptedError):
                    asyncio.run(async_cancel(mode))

    def test_turn_request_limit_is_terminal_in_the_real_hermes_classifier(self):
        with closing(self.client("max_turn_requests")) as client:
            with self.assertRaises(RuntimeError) as error:
                client.chat.completions.create(**self.request())
        self.assertIn("max_turn_requests", str(error.exception))
        verdict = classify_api_error(error.exception, provider="omp-acp", model="fixture/model")
        self.assertFalse(verdict.retryable)
        self.assertFalse(verdict.should_fallback)

    def test_only_successful_complete_tool_calls_are_executable(self):
        with closing(self.client("end_turn-tools")) as client:
            response = client.chat.completions.create(**self.request())
        self.assertEqual(response.choices[0].finish_reason, "tool_calls")
        tool_call = response.choices[0].message.tool_calls[0]
        self.assertEqual(tool_call.function.name, "lookup")
        self.assertEqual(json.loads(tool_call.function.arguments), {"query": "value"})
        with closing(self.client("max_tokens-incomplete")) as client:
            truncated = client.chat.completions.create(**self.request())
        self.assertEqual(truncated.choices[0].finish_reason, "length")
        self.assertFalse(truncated.choices[0].message.tool_calls)


if __name__ == "__main__":
    unittest.main()
