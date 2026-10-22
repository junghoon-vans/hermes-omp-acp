import importlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "acp_peer.py"

# Load the worker as a package without initializing the production plugin entrypoint.
PACKAGE_NAME = "hermes_omp_delegator"
PACKAGE = types.ModuleType(PACKAGE_NAME)
PACKAGE.__path__ = [str(ROOT)]
PACKAGE.__package__ = PACKAGE_NAME
sys.modules[PACKAGE_NAME] = PACKAGE
Worker = importlib.import_module(f"{PACKAGE_NAME}.worker").Worker


class WorkerBehavior(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Worker test")
        self.git("config", "user.email", "worker@example.invalid")
        (self.repository / "original.txt").write_text("preserve\n")
        self.git("add", "original.txt")
        self.git("commit", "-qm", "initial")
        self.original_branch = self.git("branch", "--show-current")
        self.config = {
            "command": [sys.executable, str(FIXTURE)],
            "git": shutil.which("git"),
            "state_dir": str(self.root / "state"),
            "allowed_roots": [str(self.repository)],
            "timeout": 12,
        }
        self.worker = Worker(self.config)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repository), *args], text=True).strip()

    def run_task(self, prompt, *, conversation="conversation", cancelled=None, **kwargs):
        return self.worker.run(
            conversation,
            str(self.repository),
            prompt,
            cancelled=cancelled or (lambda: False),
            **kwargs,
        )

    def run_in_thread(self, worker, prompt, *, conversation, cancelled):
        outcome = {}
        errors = []

        def run():
            try:
                outcome["result"] = worker.run(
                    conversation,
                    str(self.repository),
                    prompt,
                    cancelled=cancelled,
                )
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, outcome, errors

    def wait_for_sleep_worktree(self, timeout=8):
        deadline = time.monotonic() + timeout
        worktrees = self.root / "state" / "worktrees"
        while time.monotonic() < deadline:
            for marker in worktrees.glob("*/sleep.started"):
                worktree = marker.parent
                if (worktree / "shell.pid").exists() and (worktree / "grandchild.pid").exists():
                    return worktree
            time.sleep(0.01)
        self.fail(f"Fixture did not start its shell child within {timeout} seconds.")

    def assert_process_stopped(self, pid):
        state = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            text=True,
            capture_output=True,
        )
        self.assertTrue(
            state.returncode != 0 or state.stdout.strip().startswith("Z"),
            f"process {pid} is still running: {state.stdout.strip()}",
        )

    def test_follow_up_loads_session_and_preserves_dirty_original_checkout(self):
        (self.repository / "original.txt").write_text("user's uncommitted work\n")

        first = self.run_task("first change")
        second = self.run_task("follow-up change")

        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["session_id"], first["session_id"])
        self.assertEqual(second["worktree"], first["worktree"])
        self.assertEqual(
            (Path(second["worktree"]) / "result.txt").read_text(),
            "1:first change\n2:follow-up change\n",
        )
        self.assertEqual(second["tool_calls"][-1]["rawOutput"]["exit_code"], 0)
        self.assertEqual((self.repository / "original.txt").read_text(), "user's uncommitted work\n")
        self.assertFalse((self.repository / "result.txt").exists())
        self.assertEqual(self.git("branch", "--show-current"), self.original_branch)

    def test_permission_request_is_cancelled_without_mutation(self):
        result = self.run_task("permission-needed")

        self.assertEqual(result["status"], "permission_required")
        worktree = Path(result["worktree"])
        self.assertFalse((worktree / "result.txt").exists())
        self.assertNotIn("result.txt", result["changes"])
        with self.assertRaises(RuntimeError):
            self.run_task("write")

    def test_symlink_escape_is_rejected_before_creating_task_state(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.repository / "escape"
        link.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(ValueError):
            self.worker.status("owner", str(link))

        self.assertEqual(list((self.root / "state").glob("*.json")), [])

    def test_crash_is_retained_and_not_blindly_retried(self):
        result = self.run_task("crash")

        self.assertEqual(result["status"], "failed")
        with self.assertRaises(RuntimeError):
            self.run_task("write")
        continued = self.run_task("recover", resume_interrupted=True)
        self.assertEqual(continued["session_id"], result["session_id"])
        self.assertEqual((Path(continued["worktree"]) / "result.txt").read_text(), "1:recover\n")

    def test_max_tokens_stop_reason_is_not_success(self):
        result = self.run_task("max_tokens")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stop_reason"], "max_tokens")

    def test_live_status_and_cancellation_kill_shell_descendants(self):
        cancellation = threading.Event()
        thread, outcome, errors = self.run_in_thread(
            self.worker,
            "sleep",
            conversation="cancel",
            cancelled=cancellation.is_set,
        )
        self.addCleanup(thread.join, 8)
        self.addCleanup(cancellation.set)
        worktree = self.wait_for_sleep_worktree()
        shell_pid = int((worktree / "shell.pid").read_text())
        grandchild_pid = int((worktree / "grandchild.pid").read_text())

        status_result = {}
        status_errors = []

        def read_status():
            try:
                status_result["value"] = self.worker.status("cancel", str(self.repository))
            except BaseException as error:
                status_errors.append(error)

        status_thread = threading.Thread(target=read_status, daemon=True)
        status_thread.start()
        try:
            status_thread.join(1)
            self.assertFalse(status_thread.is_alive(), "status blocked behind the active run")
            self.assertFalse(status_errors, status_errors)
            self.assertEqual(status_result["value"]["status"], "running")
        finally:
            cancellation.set()
            thread.join(8)
            status_thread.join(2)

        self.assertFalse(thread.is_alive(), "cancelled worker did not finish")
        self.assertFalse(errors, errors)
        self.assertIn("result", outcome)
        result = outcome["result"]
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse((worktree / "escaped").exists())
        self.assert_process_stopped(shell_pid)
        self.assert_process_stopped(grandchild_pid)
        retained = self.worker.status("cancel", str(self.repository))
        self.assertEqual(retained["status"], "cancelled")
        self.assertEqual(retained["session_id"], result["session_id"])

    def test_timeout_kills_shell_descendants(self):
        timeout_config = {**self.config, "timeout": 5}
        timeout_worker = Worker(timeout_config)
        fallback_cancellation = threading.Event()
        thread, outcome, errors = self.run_in_thread(
            timeout_worker,
            "sleep",
            conversation="timeout",
            cancelled=fallback_cancellation.is_set,
        )
        self.addCleanup(thread.join, 8)
        self.addCleanup(fallback_cancellation.set)
        worktree = self.wait_for_sleep_worktree()
        shell_pid = int((worktree / "shell.pid").read_text())
        grandchild_pid = int((worktree / "grandchild.pid").read_text())

        thread.join(10)
        self.assertFalse(thread.is_alive(), "timed-out worker did not finish")
        self.assertFalse(errors, errors)
        self.assertIn("result", outcome)
        result = outcome["result"]
        self.assertEqual(result["status"], "failed")
        self.assertFalse((worktree / "escaped").exists())
        self.assert_process_stopped(shell_pid)
        self.assert_process_stopped(grandchild_pid)

    def test_tool_failure_is_retained_as_failed(self):
        result = self.run_task("tool-failure")

        self.assertEqual(result["status"], "completed_with_errors")
        self.assertTrue(any(call["status"] == "failed" for call in result["tool_calls"]))
        failed_call = next(call for call in result["tool_calls"] if call["status"] == "failed")
        self.assertEqual(failed_call["rawOutput"]["exit_code"], 23)
        self.assertFalse((Path(result["worktree"]) / "result.txt").exists())

    def test_conversations_do_not_share_worktree_or_session(self):
        first = self.run_task("first", conversation="first-conversation")
        second = self.run_task("second", conversation="another-thread")

        self.assertNotEqual(first["worktree"], second["worktree"])
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertEqual((Path(first["worktree"]) / "result.txt").read_text(), "1:first\n")
        self.assertEqual((Path(second["worktree"]) / "result.txt").read_text(), "1:second\n")


if __name__ == "__main__":
    unittest.main()
