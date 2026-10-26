import asyncio
from contextlib import asynccontextmanager, contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from hermes_omp_acp.client import OmpACPClient
from hermes_omp_acp.workspaces import WorkspaceStore


PEER = Path(__file__).with_name("workspace_acp_peer.py")


class WorkspaceClientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="workspace-client-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.allowed = self.root / "repositories"
        self.allowed.mkdir()
        self.repository = self.allowed / "project"
        self.repository.mkdir()
        self.git(self.repository, "init", "-q")
        self.git(self.repository, "config", "user.name", "Workspace Client Test")
        self.git(self.repository, "config", "user.email", "workspace-client@example.test")
        (self.repository / "tracked.txt").write_text("base\n", encoding="utf-8")
        self.git(self.repository, "add", "tracked.txt")
        self.git(self.repository, "commit", "-m", "initial")

        self.home = self.root / "home"
        self.home.mkdir()
        self.agent_dir = self.root / "agent"
        self.agent_dir.mkdir()
        environment = patch.dict(os.environ, {
            "HERMES_HOME": str(self.home),
            "PI_CODING_AGENT_DIR": str(self.agent_dir),
        })
        environment.start()
        for name in (
            "HERMES_OMP_ACP_COMMAND",
            "HERMES_OMP_ACP_ARGS",
            "HERMES_OMP_ACP_MCP_ENABLED",
        ):
            os.environ.pop(name, None)
        self.addCleanup(environment.stop)
        (self.home / "config.yaml").write_text(json.dumps({
            "delegation": {
                "backend": "omp-worktree",
                "backends": {"omp-worktree": {"allowed_roots": [str(self.allowed)]}},
            },
        }), encoding="utf-8")

        from plugins.plugin_storage import plugin_data_dir

        self.store = WorkspaceStore(
            plugin_data_dir("omp-acp-provider") / "workspaces",
            [str(self.allowed)],
        )
        self.signal_dir = self.root / "signals"
        self.signal_dir.mkdir()
        self.clients = []
        self.addCleanup(self.close_clients)

    def close_clients(self):
        for client in self.clients:
            client.close()

    def make_client(self, mode="normal"):
        client = OmpACPClient(
            acp_command=sys.executable,
            acp_args=[str(PEER), mode, str(self.signal_dir)],
            acp_cwd=str(self.root),
        )
        self.clients.append(client)
        return client

    def make_registered_client(self, *, mode, mcp_enabled):
        from importlib.util import find_spec

        if find_spec("agent.delegation_provider") is None or find_spec("agent.delegation_registry") is None:
            self.skipTest("This Hermes release lacks the delegation APIs required by omp-worktree registration")
        from hermes_omp_acp.provider import register
        from providers import get_provider_profile

        (self.home / "config.yaml").write_text(json.dumps({
            "delegation": {
                "backend": "omp-worktree",
                "backends": {"omp-worktree": {"allowed_roots": [str(self.allowed)]}},
            },
            "plugins": {
                "enabled": ["omp-acp-provider"],
                "entries": {
                    "omp-acp-provider": {
                        "settings": {
                            "command": sys.executable,
                            "args": [str(PEER.resolve()), mode, str(self.signal_dir)],
                            "mcp_enabled": mcp_enabled,
                        },
                    },
                },
            },
        }), encoding="utf-8")
        register()
        client = get_provider_profile("omp-acp").create_client(acp_cwd=str(self.root))
        self.clients.append(client)
        return client

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        ).stdout.strip()

    def metadata(self, number):
        return {"repository": str(self.repository), "task_id": f"{number:032x}"}

    def complete(self, client, metadata, prompt, *, model="fixture/model", timeout=5):
        return client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
            extra_body={"hermes_omp_acp": metadata},
        )

    def records(self):
        log = self.signal_dir / "protocol.jsonl"
        if not log.exists():
            return []
        complete_lines = [line for line in log.read_text(encoding="utf-8").splitlines(keepends=True) if line.endswith("\n")]
        return [json.loads(line) for line in complete_lines]

    async def wait_for_record(self, predicate, timeout=8):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            for record in self.records():
                if predicate(record):
                    return record
            await asyncio.sleep(0.01)
        self.fail("ACP peer did not record the expected request")

    async def wait_for_prompt(self, text):
        return await self.wait_for_record(
            lambda record: record.get("method") == "session/prompt" and text in record.get("text", ""),
        )

    def release_prompt(self, record):
        (self.signal_dir / f"continue-{record['pid']}").touch()

    async def complete_workspace_request(self, client, metadata, prompt):
        task = asyncio.create_task(self.complete(client, metadata, prompt))
        try:
            session = await self.wait_for_record(
                lambda record: record.get("method") == "session/new",
            )
            prompt_record = await self.wait_for_prompt(prompt)
            self.release_prompt(prompt_record)
            return session, await task
        finally:
            if not task.done():
                task.cancel()
                for record in self.records():
                    if record.get("method") == "session/prompt":
                        self.release_prompt(record)
                await asyncio.gather(task, return_exceptions=True)


    @staticmethod
    def process_is_alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    async def wait_for_process_exit(self, pid, timeout=3):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if not self.process_is_alive(pid):
                return
            await asyncio.sleep(0.01)
        self.fail(f"ACP subprocess {pid} remained alive")

    def test_real_acp_subprocess_uses_and_retains_the_git_worktree(self):
        client = self.make_client()
        metadata = self.metadata(1)
        prompt = "workspace-success"

        async def exercise():
            task = asyncio.create_task(self.complete(client, metadata, prompt))
            try:
                record = await self.wait_for_prompt(prompt)
                self.release_prompt(record)
                response = await task
                return record, response
            finally:
                if not task.done():
                    task.cancel()
                    for record in self.records():
                        if record.get("method") == "session/prompt":
                            self.release_prompt(record)
                    await asyncio.gather(task, return_exceptions=True)

        record, response = asyncio.run(exercise())
        self.assertIn(prompt, response.choices[0].message.content)
        self.assertEqual(record["actual_cwd"], record["requested_cwd"])
        workspace_path = Path(record["requested_cwd"])
        self.assertEqual(self.git(workspace_path, "rev-parse", "--show-toplevel"), str(workspace_path))
        self.assertEqual((workspace_path / "tracked.txt").read_text(encoding="utf-8"), "base\n")
        self.assertEqual((workspace_path / "after-prompt.txt").read_text(encoding="utf-8"), "delayed write")
        self.assertEqual((self.repository / "tracked.txt").read_text(encoding="utf-8"), "base\n")
        with self.store.acquire(metadata) as workspace:
            self.assertEqual(workspace.path, workspace_path)
            self.assertTrue(workspace.path.is_dir())

    def test_registered_settings_reach_request_runtime_and_forward_mcp_with_workspace_cwd(self):
        (self.agent_dir / "mcp.json").write_text(json.dumps({
            "mcpServers": {
                "workspace-tools": {
                    "command": sys.executable,
                    "args": ["-c", "pass"],
                    "env": {"WORKSPACE_MCP": "fixture"},
                },
            },
        }), encoding="utf-8")
        client = self.make_registered_client(mode="registered-settings", mcp_enabled=True)
        metadata = self.metadata(2)
        prompt = "registered-workspace-mcp"

        session, response = asyncio.run(self.complete_workspace_request(client, metadata, prompt))

        self.assertIn(prompt, response.choices[0].message.content)
        self.assertEqual(session["command"], sys.executable)
        self.assertEqual(session["argv"], [
            str(PEER.resolve()),
            "registered-settings",
            str(self.signal_dir),
        ])
        self.assertEqual(len(session["mcp_servers"]), 1)
        server = session["mcp_servers"][0]
        self.assertEqual(server["name"], "workspace-tools")
        self.assertEqual(server["command"], sys.executable)
        self.assertEqual(server["args"], ["-c", "pass"])
        self.assertEqual(
            {entry["name"]: entry["value"] for entry in server["env"]},
            {"WORKSPACE_MCP": "fixture"},
        )
        self.assertEqual(session["actual_cwd"], session["requested_cwd"])
        workspace_path = Path(session["requested_cwd"])
        self.assertEqual(self.git(workspace_path, "rev-parse", "--show-toplevel"), str(workspace_path))
        with self.store.acquire(metadata) as workspace:
            self.assertEqual(workspace.path, workspace_path)

    def test_registered_disabled_mcp_skips_invalid_config_in_workspace_runtime(self):
        (self.agent_dir / "mcp.json").write_text('{"mcpServers": broken', encoding="utf-8")
        client = self.make_registered_client(mode="registered-disabled", mcp_enabled=False)
        metadata = self.metadata(3)
        prompt = "registered-disabled-mcp"

        session, response = asyncio.run(self.complete_workspace_request(client, metadata, prompt))

        self.assertIn(prompt, response.choices[0].message.content)
        self.assertEqual(session["command"], sys.executable)
        self.assertEqual(session["argv"], [
            str(PEER.resolve()),
            "registered-disabled",
            str(self.signal_dir),
        ])
        self.assertEqual(session["mcp_servers"], [])
        self.assertEqual(session["actual_cwd"], session["requested_cwd"])
        workspace_path = Path(session["requested_cwd"])
        with self.store.acquire(metadata) as workspace:
            self.assertEqual(workspace.path, workspace_path)


    def test_model_selection_failures_never_reach_prompt(self):
        cases = (
            ("normal", "fixture/not-offered", RuntimeError, 5, False),
            ("reject", "fixture/model", RuntimeError, 5, True),
            ("mismatch", "fixture/model", RuntimeError, 5, True),
            ("timeout", "fixture/model", TimeoutError, 2, True),
            ("no-model-exposure", "fixture/model", RuntimeError, 5, False),
        )
        for number, (mode, model, error, timeout, expects_selection) in enumerate(cases, start=10):
            with self.subTest(mode=mode):
                client = self.make_client(mode)
                before = len(self.records())
                with self.assertRaises(error):
                    self.complete(
                        client,
                        self.metadata(number),
                        f"must-not-prompt-{number}",
                        model=model,
                        timeout=timeout,
                    )
                records = self.records()[before:]
                self.assertFalse(
                    any(record.get("method") == "session/prompt" for record in records),
                    f"ACP prompt was sent after model selection failed in {mode!r}",
                )
                self.assertTrue(any(record.get("method") == "session/new" for record in records))
                if expects_selection:
                    self.assertTrue(any(record.get("method") == "session/set_config_option" for record in records))

    def test_cancelling_one_request_does_not_interrupt_a_concurrent_request(self):
        client = self.make_client()
        requests = (
            (self.metadata(20), "isolated-request-alpha"),
            (self.metadata(21), "isolated-request-beta"),
        )

        async def exercise():
            tasks = [asyncio.create_task(self.complete(client, metadata, prompt)) for metadata, prompt in requests]
            records = {}
            try:
                for _, prompt in requests:
                    records[prompt] = await self.wait_for_prompt(prompt)
                tasks[0].cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await tasks[0]
                self.release_prompt(records[requests[0][1]])
                await self.wait_for_process_exit(records[requests[0][1]]["pid"])
                self.assertFalse(
                    (Path(records[requests[0][1]]["requested_cwd"]) / "after-prompt.txt").exists(),
                )
                self.release_prompt(records[requests[1][1]])
                return records, await tasks[1]
            finally:
                for record in records.values():
                    self.release_prompt(record)
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        records, response = asyncio.run(exercise())
        alpha, beta = (prompt for _, prompt in requests)
        self.assertIn(beta, response.choices[0].message.content)
        self.assertNotIn(alpha, response.choices[0].message.content)
        paths = []
        for metadata, prompt in requests:
            record = records[prompt]
            with self.store.acquire(metadata) as workspace:
                paths.append(workspace.path)
                self.assertEqual(record["requested_cwd"], str(workspace.path))
                self.assertEqual(record["actual_cwd"], str(workspace.path))
                delayed_write = workspace.path / "after-prompt.txt"
                if prompt == alpha:
                    self.assertFalse(delayed_write.exists())
                else:
                    self.assertEqual(delayed_write.read_text(encoding="utf-8"), "delayed write")
        self.assertNotEqual(paths[0], paths[1])

    def test_async_cancellation_after_prompt_kills_process_and_preserves_reusable_worktree(self):
        client = self.make_client()
        metadata = self.metadata(30)
        prompt = "cancel-after-prompt"

        async def exercise():
            task = asyncio.create_task(self.complete(client, metadata, prompt))
            record = await self.wait_for_prompt(prompt)
            task.cancel()
            await asyncio.sleep(0)
            if not task.done():
                task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            # Let an uncancelled peer pass its prompt gate. A surviving child would
            # now make the delayed workspace write, independent of scheduler timing.
            self.release_prompt(record)
            await self.wait_for_process_exit(record["pid"])
            await asyncio.sleep(0.05)
            return record

        record = asyncio.run(exercise())
        workspace_path = Path(record["requested_cwd"])
        self.assertFalse((workspace_path / "after-prompt.txt").exists())
        with self.store.acquire(metadata) as workspace:
            self.assertEqual(workspace.path, workspace_path)
            self.assertEqual(self.git(workspace.path, "rev-parse", "--show-toplevel"), str(workspace.path))

    def test_cancellation_before_workspace_acquire_releases_the_request(self):
        client = self.make_client()
        metadata = self.metadata(40)
        entered = threading.Event()
        resume = threading.Event()
        original_acquire = WorkspaceStore.acquire

        @contextmanager
        def paused_acquire(store, request_metadata):
            entered.set()
            if not resume.wait(5):
                raise TimeoutError("workspace acquire test barrier expired")
            with original_acquire(store, request_metadata) as workspace:
                yield workspace

        async def exercise():
            task = asyncio.create_task(self.complete(client, metadata, "cancel-before-acquire"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5), "request never reached workspace acquisition")
                task.cancel()
                await asyncio.sleep(0)
            finally:
                resume.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)

        with patch.object(WorkspaceStore, "acquire", paused_acquire):
            asyncio.run(exercise())
        self.assertFalse(any(record.get("method") == "session/prompt" for record in self.records()))
        with self.store.acquire(metadata) as workspace:
            self.assertTrue(workspace.path.is_dir())

    def test_cancellation_racing_with_spawn_does_not_start_a_peer(self):
        from hermes_omp_acp import _session

        client = self.make_client()
        metadata = self.metadata(41)
        entered = threading.Event()
        resume = threading.Event()
        spawned = []
        original_spawn = _session.spawn_agent_process

        @asynccontextmanager
        async def paused_spawn(*args, **kwargs):
            entered.set()
            while not resume.is_set():
                await asyncio.sleep(0.01)
            async with original_spawn(*args, **kwargs) as pair:
                spawned.append(pair[1])
                yield pair

        async def exercise():
            task = asyncio.create_task(self.complete(client, metadata, "cancel-before-spawn"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5), "request never reached ACP spawn")
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=5)
            finally:
                resume.set()

        with patch.object(_session, "spawn_agent_process", paused_spawn):
            asyncio.run(exercise())
        self.assertEqual(spawned, [], "A peer process was spawned after cancellation won the startup race")
        self.assertFalse(any(record.get("method") == "session/prompt" for record in self.records()))
        with self.store.acquire(metadata) as workspace:
            self.assertTrue(workspace.path.is_dir())


if __name__ == "__main__":
    unittest.main()
