import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hermes_omp_acp.tool import run_task
from hermes_omp_acp.tool_plugin import register
from hermes_omp_acp.workspaces import WorkspaceStore
from tools.registry import registry
from tools.interrupt import is_interrupted
from agent.interrupt_control import InterruptControlMixin
from agent.tool_executor import _registered_tool_worker, _run_sequential_tool_execution_middleware

PEER = Path(__file__).with_name("task_acp_peer.py").resolve()


class Session(InterruptControlMixin):
    def __init__(self):
        self._execution_thread_id = None
        self._tool_worker_threads = set()
        self._tool_worker_threads_lock = threading.Lock()
        self._active_children = []
        self._active_children_lock = threading.Lock()
        self._pending_redirect_lock = threading.Lock()
        self.quiet_mode = True
        self._interrupt_requested = False
        self._checkpoint_mgr = SimpleNamespace(enabled=False)
        self._tool_guardrails = SimpleNamespace(
            before_call=lambda *_: SimpleNamespace(allows_execution=True))
        self.tool_progress_callback = None
        self.tool_start_callback = None

    def _touch_activity(self, _label):
        pass


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="omp-tool-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Tool Test")
        self.git("config", "user.email", "tool@example.test")
        (self.repo / "tracked").write_text("base")
        (self.repo / ".gitignore").write_text("ignored\n")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        self.base_commit = self.git("rev-parse", "HEAD")
        (self.repo / "tracked").write_text("dirty")
        (self.repo / "untracked").write_text("keep")
        (self.repo / "ignored").write_text("keep ignored")
        self.source_status = self.git("status", "--porcelain", "--ignored")
        self.signals = self.root / "signals"
        self.signals.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.agent_dir = self.root / "agent"
        self.agent_dir.mkdir()
        self.environment = patch.dict(os.environ, {"HERMES_HOME": str(self.home), "PI_CODING_AGENT_DIR": str(self.agent_dir)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.settings = {"model_id": "fixture/model", "allowed_roots": [str(self.root)],
                         "command": sys.executable, "args": [str(PEER), "normal", str(self.signals)],
                         "mcp_enabled": False, "timeout_seconds": 10}
        self.store_root = self.root / "store"
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.addCleanup(self.pool.shutdown)
        self.sessions = []
        self.addCleanup(self.stop_sessions)
        self.scope = str(self.home.resolve())
        self.addCleanup(lambda: registry.deregister("omp_task", scope=self.scope))
        # Exercise the real plugin registration function and real registry. The
        # packaging suite separately exercises discovery and PluginContext.
        ctx = SimpleNamespace(get_config=lambda key, default=None: self.settings.get(key, default),
                              register_tool=lambda **kw: registry.register(**kw, scope=self.scope))
        register(ctx)

    def stop_sessions(self):
        for session in self.sessions:
            session.interrupt(hard_cancel=True)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True, timeout=30).stdout.strip()

    def store(self):
        return WorkspaceStore(self.store_root, [str(self.root)])

    def args(self, number=1, goal="goal"):
        return {"repository": str(self.repo), "goal": goal, "task_id": f"{number:032x}"}

    def start(self, arguments=None, *, executor=False):
        session = Session()
        self.sessions.append(session)
        def dispatch(arguments):
            return registry.dispatch("omp_task", arguments, scope=self.scope)
        def actual_dispatch():
            if executor:
                return _run_sequential_tool_execution_middleware(
                    session, function_name="omp_task", function_args=arguments or self.args(),
                    effective_task_id="integration", tool_call_id="omp-call",
                    execute=dispatch).result
            with _registered_tool_worker(session):
                return dispatch(arguments or self.args())
        from plugins.plugin_storage import plugin_data_dir
        self.store_root = plugin_data_dir("omp-task") / "workspaces"
        return session, self.pool.submit(actual_dispatch)

    def records(self):
        path = self.signals / "protocol.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines(keepends=True) if line.endswith("\n")]

    def wait_record(self, method="session/prompt", goal=None):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            for record in self.records():
                if record["method"] == method and (goal is None or goal == record.get("text")):
                    return record
            time.sleep(0.01)
        self.fail(f"Missing peer barrier {method} {goal}: {self.records()}")

    def release(self, record):
        (self.signals / f"continue-{record['pid']}").touch()

    def assert_reaped(self, pid):
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_dispatch_retains_reuses_files_fresh_session_and_preserves_source(self):
        arguments = self.args()
        _, future = self.start(arguments)
        first = self.wait_record()
        self.release(first)
        result = json.loads(future.result(15))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["response"], "goal")
        self.assertEqual(result["base_commit"], self.base_commit)
        path = Path(result["path"])
        self.assertEqual(first["actual_cwd"], str(path))
        self.assertEqual(first["requested_cwd"], str(path))
        self.assertEqual((path / "tracked").read_text(), "base")
        (path / "retained").write_text("retained edit")
        arguments["goal"] = "second goal"
        _, second_future = self.start(arguments)
        second = self.wait_record(goal="second goal")
        self.release(second)
        reused = json.loads(second_future.result(15))
        self.assertEqual(reused["path"], result["path"])
        self.assertEqual(reused["branch"], result["branch"])
        self.assertNotEqual(first["pid"], second["pid"])
        self.assertEqual((path / "retained").read_text(), "retained edit")
        self.assertEqual(self.git("status", "--porcelain", "--ignored"), self.source_status)
        self.assertEqual((self.repo / "tracked").read_text(), "dirty")
        self.assertEqual((self.repo / "untracked").read_text(), "keep")
        self.assertEqual((self.repo / "ignored").read_text(), "keep ignored")
        self.assert_reaped(first["pid"])
        self.assert_reaped(second["pid"])

    def test_real_executor_hard_stop_reaps_before_lock_reuse_and_leaves_peer_unaffected(self):
        stopped, future = self.start(self.args(1, "stop me"), executor=True)
        first = self.wait_record(goal="stop me")
        _, peer_future = self.start(self.args(2, "keep running"), executor=True)
        peer = self.wait_record(goal="keep running")
        with self.assertRaisesRegex(RuntimeError, "in use"):
            with self.store().acquire(self.args(1)):
                self.fail("acquired active workspace")
        for _ in range(5):
            stopped.interrupt(hard_cancel=True)
        result = json.loads(future.result(15))
        self.assertEqual(result["status"], "cancelled", result)
        self.assert_reaped(first["pid"])
        self.release(first)  # A killed peer must never perform its delayed write.
        with self.store().acquire(self.args(1)) as workspace:
            self.assertFalse((workspace.path / "after-prompt.txt").exists())
        self.assertFalse(peer_future.done())
        self.release(peer)
        self.assertEqual(json.loads(peer_future.result(15))["status"], "success")
        self.assert_reaped(peer["pid"])
        _, reuse = self.start(self.args(1, "reuse after stop"))
        record = self.wait_record(goal="reuse after stop")
        self.release(record)
        self.assertEqual(json.loads(reuse.result(15))["status"], "success")

    def test_registry_stop_honors_interrupt_context_from_originating_thread(self):
        from tools.interrupt import acting_for_tid, set_interrupt

        session = Session()
        self.sessions.append(session)
        owner_tid = threading.get_ident()
        session._execution_thread_id = owner_tid
        self.addCleanup(set_interrupt, False, owner_tid)
        self.addCleanup(setattr, session, "_execution_thread_id", None)
        from plugins.plugin_storage import plugin_data_dir
        self.store_root = plugin_data_dir("omp-task") / "workspaces"

        def dispatch():
            token = acting_for_tid.set(owner_tid)
            try:
                return registry.dispatch("omp_task", self.args(), scope=self.scope)
            finally:
                acting_for_tid.reset(token)

        future = self.pool.submit(dispatch)
        record = self.wait_record()
        session.interrupt(hard_cancel=True)
        result = json.loads(future.result(15))
        self.assertEqual(result["status"], "cancelled", result)
        self.assert_reaped(record["pid"])
        self.release(record)
        with self.store().acquire(self.args()) as workspace:
            self.assertFalse((workspace.path / "after-prompt.txt").exists())
        set_interrupt(False, owner_tid)
        session._execution_thread_id = None

    def test_stop_during_model_selection_never_prompts_and_reaps_before_unlock(self):
        self.settings["args"] = [str(PEER), "timeout", str(self.signals)]
        session, future = self.start()
        record = self.wait_record("session/set_config_option")
        session.interrupt(hard_cancel=True)
        result = json.loads(future.result(15))
        self.assertEqual(result["status"], "cancelled", result)
        self.assert_reaped(record["pid"])
        self.assertFalse(any(row["method"] == "session/prompt" for row in self.records()))
        with self.store().acquire(self.args()) as workspace:
            self.assertEqual(str(workspace.path), result["path"])

    def test_tool_runs_without_advanced_delegation_modules_or_configuration(self):
        with patch.dict(sys.modules, {"agent.delegation_provider": None,
                                     "agent.delegation_registry": None}):
            _, future = self.start()
            record = self.wait_record()
            self.release(record)
            result = json.loads(future.result(15))
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["response"], "goal")
        self.assert_reaped(record["pid"])

    def test_stop_during_workspace_acquisition_joins_before_releasing_lock(self):
        acquired, proceed = threading.Event(), threading.Event()
        original = WorkspaceStore.acquire
        @contextmanager
        def blocked(store, metadata):
            with original(store, metadata) as workspace:
                acquired.set()
                if not proceed.wait(15):
                    raise RuntimeError("test barrier expired")
                yield workspace
        with patch.object(WorkspaceStore, "acquire", blocked):
            session, future = self.start()
            try:
                self.assertTrue(acquired.wait(15))
                session.interrupt(hard_cancel=True)
                self.assertFalse(future.done())
                with self.assertRaisesRegex(RuntimeError, "in use"):
                    with original(self.store(), self.args()):
                        pass
            finally:
                proceed.set()
            result = json.loads(future.result(15))
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.records(), [])
        with self.store().acquire(self.args()) as workspace:
            self.assertTrue(workspace.path.is_dir())

    def test_stop_during_spawn_reaps_child_before_unlock(self):
        from hermes_omp_acp import _session, tool
        spawned, proceed, observed = threading.Event(), threading.Event(), threading.Event()
        original = _session.spawn_agent_process
        pid = []
        original_stop = tool._Invocation.stop
        def stop(invocation):
            original_stop(invocation)
            observed.set()
        @asynccontextmanager
        async def blocked(*args, **kwargs):
            async with original(*args, **kwargs) as pair:
                pid.append(pair[1].pid)
                spawned.set()
                deadline = time.monotonic() + 15
                while not proceed.is_set() and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                yield pair
        with patch.object(_session, "spawn_agent_process", blocked), patch.object(tool._Invocation, "stop", stop):
            session, future = self.start()
            try:
                self.assertTrue(spawned.wait(15))
                session.interrupt(hard_cancel=True)
                self.assertTrue(observed.wait(5))
                self.assertFalse(future.done())
                with self.assertRaisesRegex(RuntimeError, "in use"):
                    with self.store().acquire(self.args()):
                        pass
            finally:
                proceed.set()
            result = json.loads(future.result(15))
        self.assertEqual(result["status"], "cancelled", result)
        self.assert_reaped(pid[0])
        with self.store().acquire(self.args()) as workspace:
            self.assertFalse((workspace.path / "after-prompt.txt").exists())

    def test_fail_closed_repository_policy_and_caller_launch_injection(self):
        for changes in ({"repository": None}, {"repository": "."}, {"model": "other"},
                        {"command": "other"}, {"task_id": "../escape"}):
            with self.subTest(changes=changes):
                arguments = {**self.args(), **changes}
                result = json.loads(run_task(arguments, settings=self.settings,
                    store_root=self.store_root, interrupted=lambda: False))
                self.assertEqual(result["status"], "error")
        for policy in ([], None, [str(self.home)]):
            result = json.loads(run_task(self.args(), settings={**self.settings, "allowed_roots": policy},
                store_root=self.store_root, interrupted=lambda: False))
            self.assertEqual(result["status"], "error")
        self.assertEqual(self.records(), [])

    def test_strict_model_rejection_and_timeout_retain_workspace_without_prompt(self):
        for number, mode, model in ((1, "normal", "absent"), (2, "reject", "fixture/model"),
                                   (3, "mismatch", "fixture/model"), (4, "no-model-exposure", "fixture/model"),
                                   (5, "timeout", "fixture/model")):
            with self.subTest(mode=mode):
                self.settings.update(model_id=model, args=[str(PEER), mode, str(self.signals)], timeout_seconds=2)
                _, future = self.start(self.args(number))
                result = json.loads(future.result(15))
                self.assertEqual(result["status"], "error", result)
                self.assertTrue(Path(result["path"]).is_dir())
                with self.store().acquire(self.args(number)) as workspace:
                    self.assertFalse((workspace.path / "after-prompt.txt").exists())
        self.assertFalse(any(row["method"] == "session/prompt" for row in self.records()))
        for row in self.records():
            self.assert_reaped(row["pid"])

    def test_prompt_timeout_reaps_before_unlock_and_retains_partial_files(self):
        self.settings["timeout_seconds"] = 3
        _, future = self.start()
        record = self.wait_record()
        with self.assertRaisesRegex(RuntimeError, "in use"):
            with self.store().acquire(self.args()):
                pass
        result = json.loads(future.result(15))
        self.assertEqual(result["status"], "error", result)
        self.assertIn("Timed out", result["error"])
        self.assert_reaped(record["pid"])
        self.release(record)
        with self.store().acquire(self.args()) as workspace:
            self.assertEqual(str(workspace.path), result["path"])
            self.assertFalse((workspace.path / "after-prompt.txt").exists())

    def test_omitted_task_identity_creates_distinct_retained_workspaces(self):
        arguments = {"repository": str(self.repo), "goal": "first anonymous"}
        _, first_future = self.start(arguments)
        first = self.wait_record(goal="first anonymous")
        self.release(first)
        first_result = json.loads(first_future.result(15))
        arguments["goal"] = "second anonymous"
        _, second_future = self.start(arguments)
        second = self.wait_record(goal="second anonymous")
        self.release(second)
        second_result = json.loads(second_future.result(15))
        self.assertEqual(first_result["status"], "success", first_result)
        self.assertEqual(second_result["status"], "success", second_result)
        self.assertRegex(first_result["task_id"], r"^[0-9a-f]{32}$")
        self.assertNotEqual(first_result["task_id"], second_result["task_id"])
        self.assertNotEqual(first_result["path"], second_result["path"])
        self.assertTrue(Path(first_result["path"]).is_dir())
        self.assertTrue(Path(second_result["path"]).is_dir())

    def test_registered_launch_and_mcp_enabled_and_disabled(self):
        config = self.agent_dir / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"workspace-tools": {"command": sys.executable,
                         "args": ["-c", "pass"], "env": {"WORKSPACE_MCP": "fixture"}}}}))
        self.settings["mcp_enabled"] = True
        _, future = self.start(self.args(1, "mcp enabled"))
        record = self.wait_record(goal="mcp enabled")
        self.release(record)
        self.assertEqual(json.loads(future.result(15))["status"], "success")
        first = next(row for row in self.records() if row["method"] == "session/new")
        self.assertEqual(first["argv"], [str(PEER), "normal", str(self.signals)])
        self.assertEqual(first["actual_cwd"], first["requested_cwd"])
        self.assertEqual(first["mcp_servers"][0]["name"], "workspace-tools")
        self.assertEqual({item["name"]: item["value"] for item in first["mcp_servers"][0]["env"]}, {"WORKSPACE_MCP": "fixture"})
        config.write_text("invalid json")
        self.settings["mcp_enabled"] = False
        _, future = self.start(self.args(2, "mcp disabled"))
        record = self.wait_record(goal="mcp disabled")
        self.release(record)
        self.assertEqual(json.loads(future.result(15))["status"], "success")
        latest = [row for row in self.records() if row["method"] == "session/new"][-1]
        self.assertEqual(latest["mcp_servers"], [])

    def test_uuid_omission_and_preexisting_interrupt_never_spawn(self):
        result = json.loads(run_task({"repository": str(self.repo), "goal": "goal"},
                           settings=self.settings, store_root=self.store_root, interrupted=lambda: True))
        self.assertEqual(result["status"], "cancelled")
        self.assertRegex(result["task_id"], r"^[0-9a-f]{32}$")
        self.assertFalse(self.store_root.exists())
        self.assertEqual(self.records(), [])
