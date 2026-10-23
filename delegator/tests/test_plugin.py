import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
NAME = "hermes_omp_delegator_behavior"
SPEC = importlib.util.spec_from_file_location(NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
PLUGIN = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = PLUGIN
SPEC.loader.exec_module(PLUGIN)


class State:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class Context:
    plugin_id = "omp-acp-delegator"

    def __init__(self, repository):
        self.state = State()
        self.settings = {
            "allowed_roots": [str(repository)],
            "omp_command": str(ROOT / "tests/fixtures/acp_peer.py"),
            "timeout": 20,
        }
        self.tools = {}
        self.hooks = {}
        self.commands = {}
        self.cleanup = None

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_tool(self, *, name, handler, **_):
        self.tools[name] = handler

    def register_hook(self, name, handler):
        self.hooks[name] = handler

    def register_command(self, name, *, handler, **_):
        self.commands[name] = handler

    def on_unload(self, callback):
        self.cleanup = callback


class PluginBehavior(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Plugin test")
        self.git("config", "user.email", "plugin@example.invalid")
        (self.repository / "original.txt").write_text("preserve\n")
        self.git("add", "original.txt")
        self.git("commit", "-qm", "initial")
        self.data = self.root / "profile/plugin-data/omp-acp-delegator"
        storage = types.ModuleType("plugins.plugin_storage")
        storage.plugin_data_dir = self.data_dir
        package = types.ModuleType("plugins")
        package.__path__ = []
        self.module_patch = patch.dict(sys.modules, {"plugins": package, "plugins.plugin_storage": storage})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        self.environment = patch.dict(os.environ, {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.ctx = Context(self.repository)
        PLUGIN.register(self.ctx)
        self.addCleanup(self.ctx.cleanup)

    def data_dir(self, _):
        self.data.mkdir(parents=True, exist_ok=True)
        return self.data

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repository), *args], text=True).strip()

    def call(self, name, *, session="session-a", **args):
        return json.loads(self.ctx.tools[name]({"repository": str(self.repository), **args}, session_id=session))

    def test_compaction_retains_task_but_a_new_session_does_not_inherit_it(self):
        (self.repository / "original.txt").write_text("user's uncommitted work\n")
        first = self.call("omp_task", prompt="first change")
        self.assertEqual(first["status"], "completed", first)
        self.ctx.hooks["post_compaction"](old_session_id="session-a", session_id="session-b", kind="compression")
        second = self.call("omp_task", session="session-b", prompt="follow-up change")
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(second["worktree"], first["worktree"])
        self.assertEqual((Path(second["worktree"]) / "result.txt").read_text(), "1:first change\n2:follow-up change\n")
        self.ctx.hooks["on_session_reset"](session_id="session-b")
        fresh = self.call("omp_status", session="session-c")
        self.assertEqual(fresh["status"], "not_started")
        self.assertEqual((self.repository / "original.txt").read_text(), "user's uncommitted work\n")

    def test_cancellation_command_cannot_cancel_another_sessions_run(self):
        result = {}
        thread = threading.Thread(target=lambda: result.update(self.call("omp_task", prompt="sleep")), daemon=True)
        thread.start()
        def stop():
            self.ctx.cleanup()
            thread.join(10)

        self.addCleanup(stop)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if list((self.data / "tasks/worktrees").glob("*/sleep.started")) or not thread.is_alive():
                break
            time.sleep(0.02)
        self.assertTrue(thread.is_alive(), result)
        self.assertTrue(list((self.data / "tasks/worktrees").glob("*/sleep.started")), result)
        stranger = self.call("omp_cancel", session="session-other")
        self.assertEqual(stranger["status"], "not_running")
        self.assertEqual(self.call("omp_status")["status"], "running")
        cancelled = json.loads(self.ctx.commands["omp-cancel"](str(self.repository), session_id="session-a"))
        self.assertEqual(cancelled["status"], "cancellation_requested")
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], "cancelled", result)

    def test_reset_event_cancels_the_old_session_when_the_payload_names_the_new_one(self):
        result = {}
        thread = threading.Thread(target=lambda: result.update(self.call("omp_task", prompt="sleep")), daemon=True)
        thread.start()

        def stop():
            self.ctx.cleanup()
            thread.join(10)

        self.addCleanup(stop)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if list((self.data / "tasks/worktrees").glob("*/sleep.started")) or not thread.is_alive():
                break
            time.sleep(0.02)
        self.assertTrue(list((self.data / "tasks/worktrees").glob("*/sleep.started")), result)
        self.ctx.hooks["on_session_reset"](session_id="new-session", old_session_id="session-a")
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], "cancelled", result)
        self.assertEqual(self.call("omp_status", session="new-session")["status"], "not_started")

    def test_missing_session_identity_does_not_create_task_state(self):
        result = self.call("omp_task", session=None, prompt="write")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.data.exists())


if __name__ == "__main__":
    unittest.main()
