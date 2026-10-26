"""Public-client and Hermes cancellation-hook lifecycle regressions."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from hermes_omp_acp.client import OmpACPClient
from hermes_omp_acp.errors import ACPCancelledError


_PEER = Path(__file__).with_name("acp_cancel_peer.py")


class CancellationLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def _client(self, mode="cooperative"):
        return OmpACPClient(
            acp_command=sys.executable,
            acp_args=[str(_PEER), mode],
            acp_cwd=str(self.home),
            mcp_enabled=False,
        )

    def _wait_for_marker(self, name, timeout=5):
        marker = self.home / name
        deadline = time.monotonic() + timeout
        while not marker.exists():
            if time.monotonic() >= deadline:
                self.fail(f"Timed out waiting for ACP peer marker {name}")
            time.sleep(0.01)
        return marker

    def _peer_pid(self):
        return int((self.home / "cancel-peer.pid").read_text(encoding="ascii"))

    @staticmethod
    def _process_is_alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _assert_reaped(self, pid, timeout=5):
        deadline = time.monotonic() + timeout
        while self._process_is_alive(pid):
            if time.monotonic() >= deadline:
                self.fail(f"ACP peer process {pid} was not reaped")
            time.sleep(0.01)

    def _start_completion(self, client, executor):
        return executor.submit(
            client.chat.completions.create,
            model="fixture/model",
            messages=[{"role": "user", "content": "wait for cancellation"}],
            timeout=10,
        )

    def test_cross_thread_cancel_is_confirmed_and_client_can_be_reused(self):
        client = self._client()
        executor = ThreadPoolExecutor(max_workers=1)
        future = self._start_completion(client, executor)
        try:
            self._wait_for_marker("cancel-peer.prompt-ready")
            pid = self._peer_pid()
            client.cancel()
            with self.assertRaises(ACPCancelledError):
                future.result(timeout=5)
            self.assertTrue((self.home / "cancel-peer.cancel-received").exists())
            self._assert_reaped(pid)

            response = client.chat.completions.create(
                model="fixture/model",
                messages=[{"role": "user", "content": "independent follow-up"}],
                timeout=5,
            )
            self.assertEqual(
                response.choices[0].message.content,
                "completion after cancellation",
            )
        finally:
            client.close()
            executor.shutdown(wait=True, cancel_futures=True)

    def test_noncooperative_prompt_is_escalated_and_reaped(self):
        client = self._client("noncooperative")
        executor = ThreadPoolExecutor(max_workers=1)
        future = self._start_completion(client, executor)
        try:
            self._wait_for_marker("cancel-peer.prompt-ready")
            pid = self._peer_pid()
            client.cancel()
            with self.assertRaises(ACPCancelledError):
                future.result(timeout=6)
            self._assert_reaped(pid)
        finally:
            client.close()
            executor.shutdown(wait=True, cancel_futures=True)

    def test_startup_cancel_and_duplicate_close_are_bounded(self):
        client = self._client("startup")
        executor = ThreadPoolExecutor(max_workers=1)
        future = self._start_completion(client, executor)
        try:
            self._wait_for_marker("cancel-peer.startup-ready")
            pid = self._peer_pid()
            client.cancel()
            client.cancel()
            client.close()
            client.close()
            with self.assertRaises(ACPCancelledError):
                future.result(timeout=6)
            self._assert_reaped(pid)
        finally:
            client.close()
            executor.shutdown(wait=True, cancel_futures=True)

    def test_hermes_interrupt_dispatches_the_real_client_cancel_hook(self):
        hermes_home = self.home / "hermes"
        omp_home = self.home / "omp"
        hermes_home.mkdir()
        omp_home.mkdir()
        (hermes_home / "config.yaml").write_text(json.dumps({
            "model": {
                "provider": "omp-acp",
                "default": "fixture/model",
                "base_url": "acp://omp",
                "api_mode": "chat_completions",
            },
            "plugins": {
                "enabled": ["omp-acp-provider"],
                "entries": {"omp-acp-provider": {"settings": {}}},
            },
            "memory": {"memory_enabled": False, "user_profile_enabled": False},
        }), encoding="utf-8")
        environment = patch.dict(os.environ, {
            "HERMES_HOME": str(hermes_home),
            "PI_CODING_AGENT_DIR": str(omp_home),
            "HERMES_OMP_ACP_COMMAND": sys.executable,
            "HERMES_OMP_ACP_ARGS": shlex.join([str(_PEER), "cooperative"]),
            "HERMES_OMP_ACP_MCP_ENABLED": "false",
        })
        environment.start()
        self.addCleanup(environment.stop)

        from hermes_omp_acp.provider import register
        from run_agent import AIAgent

        register()
        agent = AIAgent(
            api_key="fixture",
            base_url="acp://omp",
            provider="omp-acp",
            model="fixture/model",
            platform="cli",
            enabled_toolsets=[],
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            skip_background_review=True,
        )
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(agent.run_conversation, "wait until the ACP prompt is active")
        try:
            self._wait_for_marker("cancel-peer.prompt-ready")
            pid = self._peer_pid()
            self.assertTrue(agent.interrupt(hard_cancel=True))
            result = future.result(timeout=10)
            from agent.conversation_loop import INTERRUPT_WAITING_FOR_MODEL_PREFIX
            self.assertTrue(result["final_response"].startswith(INTERRUPT_WAITING_FOR_MODEL_PREFIX))
            self.assertTrue((self.home / "cancel-peer.cancel-received").exists())
            self._assert_reaped(pid)
        finally:
            agent.close()
            executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
