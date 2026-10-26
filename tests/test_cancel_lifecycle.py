"""Public-client and Hermes cancellation-hook lifecycle regressions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest

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

    def test_request_timeout_notifies_the_remote_prompt(self):
        client = self._client()
        try:
            with self.assertRaises(TimeoutError):
                client.chat.completions.create(
                    model="fixture/model",
                    messages=[{"role": "user", "content": "wait for the request deadline"}],
                    timeout=2,
                )
            self.assertTrue((self.home / "cancel-peer.prompt-ready").exists())
            self.assertTrue((self.home / "cancel-peer.cancel-received").exists())
            self._assert_reaped(self._peer_pid())
        finally:
            client.close()

    def test_async_task_cancellation_notifies_the_remote_prompt(self):
        async def exercise():
            client = self._client()
            request = asyncio.create_task(client.chat.completions.create(
                model="fixture/model",
                messages=[{"role": "user", "content": "wait for task cancellation"}],
                timeout=10,
            ))
            try:
                await asyncio.to_thread(self._wait_for_marker, "cancel-peer.prompt-ready")
                pid = self._peer_pid()
                request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(request, timeout=5)
                self.assertTrue((self.home / "cancel-peer.cancel-received").exists())
                self._assert_reaped(pid)
            finally:
                client.close()
                await asyncio.gather(request, return_exceptions=True)

        asyncio.run(exercise())

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
        environment = {
            key: value for key, value in os.environ.items()
            if key in {"PATH", "PYTHONPATH", "TMPDIR", "TMP", "TEMP", "LANG",
                       "LC_ALL", "SYSTEMROOT", "HERMES_BUNDLED_LOCALES"}
        }
        environment.update({
            "HOME": str(self.home),
            "HERMES_HOME": str(hermes_home),
            "PI_CODING_AGENT_DIR": str(omp_home),
            "HERMES_OMP_ACP_COMMAND": sys.executable,
            "HERMES_OMP_ACP_ARGS": shlex.join([str(_PEER), "cooperative"]),
            "HERMES_OMP_ACP_MCP_ENABLED": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        script = """
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import time
from run_agent import AIAgent

with closing(AIAgent(
    api_key="fixture", base_url="acp://omp", provider="omp-acp",
    model="fixture/model", platform="cli", cwd=str(Path.cwd()),
    enabled_toolsets=[], quiet_mode=True, skip_context_files=True,
    skip_memory=True, skip_background_review=True,
)) as agent, ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(agent.run_conversation, "wait until the ACP prompt is active")
    deadline = time.monotonic() + 5
    while not Path("cancel-peer.prompt-ready").exists():
        if future.done():
            raise AssertionError(f"Hermes returned before prompt dispatch: {future.result()!r}")
        if time.monotonic() >= deadline:
            raise TimeoutError("The ACP prompt did not start.")
        time.sleep(0.01)
    accepted = agent.interrupt(hard_cancel=True)
    result = future.result(timeout=10)
Path("interrupt-result.json").write_text(json.dumps({
    "accepted": accepted, "completed": result["completed"],
}))
"""
        process = subprocess.run(
            [sys.executable, "-P", "-c", script],
            cwd=self.home, env=environment, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads((self.home / "interrupt-result.json").read_text())
        self.assertTrue(result["accepted"])
        self.assertFalse(result["completed"])
        self.assertEqual(
            (self.home / "cancel-peer.prompt-count").read_text(encoding="ascii").splitlines(),
            ["prompt"],
        )
        self.assertTrue((self.home / "cancel-peer.cancel-received").exists())
        self._assert_reaped(self._peer_pid())


if __name__ == "__main__":
    unittest.main()
