import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from hermes_omp_acp.workspaces import WorkspaceStore, validate_repository


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temporary = Path(temporary.name)
        self.allowed = self.temporary / "repositories"
        self.allowed.mkdir()
        self.repository = self.make_repository("project")
        self.store_root = self.temporary / "workspaces"
        self.store = WorkspaceStore(self.store_root, [str(self.allowed)])

    def git(self, cwd, *args):
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        ).stdout.strip()

    def make_repository(self, name):
        repository = self.allowed / name
        repository.mkdir()
        subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True, timeout=30)
        self.git(repository, "config", "user.name", "Workspace Test")
        self.git(repository, "config", "user.email", "workspace@example.test")
        (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
        self.git(repository, "add", "tracked.txt")
        self.git(repository, "commit", "-m", "initial")
        return repository.resolve()

    @staticmethod
    def metadata(task_id, repository):
        return {"task_id": task_id, "repository": str(repository)}

    def test_validate_repository_requires_a_root_inside_allowed_roots_with_head(self):
        self.assertEqual(validate_repository(str(self.repository), [str(self.allowed)]), self.repository)
        with self.assertRaises(ValueError):
            validate_repository(str(self.temporary), [str(self.allowed)])
        with self.assertRaises(ValueError):
            validate_repository(str(self.repository / "tracked.txt"), [str(self.allowed)])

    def test_validate_repository_rejects_symlink_escape_and_missing_head(self):
        outside = self.temporary / "outside"
        outside.mkdir()
        link = self.allowed / "escape"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            validate_repository(str(link), [str(self.allowed)])

        empty = self.allowed / "empty"
        empty.mkdir()
        subprocess.run(["git", "-C", str(empty), "init", "-q"], check=True, timeout=30)
        with self.assertRaises(ValueError):
            validate_repository(str(empty), [str(self.allowed)])

    def test_constructor_rejects_malformed_allowed_roots(self):
        for roots in ([], ["relative/path"], [""]):
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                WorkspaceStore(self.temporary / "invalid", roots)
        with self.assertRaises(ValueError):
            WorkspaceStore(self.temporary / "invalid", None)

    def test_acquire_requires_a_generated_format_task_id_and_repository(self):
        for task_id in ("", "A" * 32, "a" * 31, "g" * 32):
            with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                with self.store.acquire(self.metadata(task_id, self.repository)):
                    self.fail("Invalid task IDs must be refused")
        with self.assertRaises(ValueError):
            with self.store.acquire({"task_id": "5" * 32, "repository": "relative/repository"}):
                self.fail("Relative repositories must be refused")

    def test_source_dirty_files_are_preserved_without_copying_them_to_worktree(self):
        tracked = self.repository / "tracked.txt"
        tracked.write_text("source dirty\n", encoding="utf-8")
        (self.repository / "untracked.txt").write_text("source only\n", encoding="utf-8")

        with self.store.acquire(self.metadata("a" * 32, self.repository)) as workspace:
            self.assertEqual(tracked.read_text(encoding="utf-8"), "source dirty\n")
            self.assertEqual((workspace.path / "tracked.txt").read_text(encoding="utf-8"), "base\n")
            self.assertFalse((workspace.path / "untracked.txt").exists())

    def test_reused_task_keeps_uncommitted_edits_and_commits(self):
        metadata = self.metadata("b" * 32, self.repository)
        with self.store.acquire(metadata) as first:
            first.path.joinpath("tracked.txt").write_text("retained edit\n", encoding="utf-8")
            self.git(first.path, "add", "tracked.txt")
            self.git(first.path, "commit", "-m", "task change")
            original_base = first.base_commit

        with self.store.acquire(metadata) as reused:
            self.assertEqual(reused.path, first.path)
            self.assertEqual(reused.repository, self.repository)
            self.assertEqual(reused.branch, first.branch)
            self.assertEqual(reused.base_commit, original_base)
            self.assertEqual((reused.path / "tracked.txt").read_text(encoding="utf-8"), "retained edit\n")
            self.assertEqual(self.git(reused.path, "log", "-1", "--format=%s"), "task change")

    def test_different_tasks_receive_distinct_worktrees_and_branches(self):
        with self.store.acquire(self.metadata("c" * 32, self.repository)) as first:
            first_path = first.path
            first_branch = first.branch
        with self.store.acquire(self.metadata("d" * 32, self.repository)) as second:
            self.assertNotEqual(second.path, first_path)
            self.assertNotEqual(second.branch, first_branch)
            self.assertTrue(second.path.is_dir())

    def test_same_task_id_cannot_be_reused_for_a_different_repository(self):
        other = self.make_repository("other")
        metadata = self.metadata("e" * 32, self.repository)
        with self.store.acquire(metadata):
            pass
        with self.assertRaises(RuntimeError):
            with self.store.acquire(self.metadata("e" * 32, other)):
                self.fail("A task ID must not change repository ownership")

    def test_missing_or_replaced_worktree_is_never_recreated(self):
        for task_id, replace in (("f" * 32, False), ("1" * 32, True)):
            metadata = self.metadata(task_id, self.repository)
            with self.store.acquire(metadata) as workspace:
                path = workspace.path
            shutil.rmtree(path)
            if replace:
                path.mkdir()
            with self.subTest(replace=replace), self.assertRaises(RuntimeError):
                with self.store.acquire(metadata):
                    self.fail("An unknown or missing worktree must not be recreated")

    def test_missing_or_corrupt_manifest_is_never_recreated(self):
        missing = self.metadata("2" * 32, self.repository)
        with self.store.acquire(missing):
            pass
        (self.store_root / missing["task_id"] / "state.json").unlink()
        with self.assertRaises(RuntimeError):
            with self.store.acquire(missing):
                self.fail("Missing ownership state must not be recreated")

        corrupt = self.metadata("3" * 32, self.repository)
        with self.store.acquire(corrupt):
            pass
        state = self.store_root / corrupt["task_id"] / "state.json"
        state.write_text(json.dumps({"task_id": corrupt["task_id"]}), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            with self.store.acquire(corrupt):
                self.fail("Corrupt ownership state must not be recreated")

    def test_lock_refuses_concurrent_acquire_from_another_process(self):
        metadata = self.metadata("4" * 32, self.repository)
        package_root = Path(__file__).resolve().parents[1]
        code = f"""
import sys
sys.path.insert(0, {str(package_root)!r})
from hermes_omp_acp.workspaces import WorkspaceStore
store = WorkspaceStore({str(self.store_root)!r}, [{str(self.allowed)!r}])
try:
    with store.acquire({metadata!r}):
        raise SystemExit(3)
except RuntimeError as error:
    if 'already has a workspace in use' not in str(error):
        raise
else:
    raise SystemExit(4)
"""
        with self.store.acquire(metadata):
            process = subprocess.run(
                [sys.executable, "-c", code],
                check=False,
                text=True,
                capture_output=True,
                timeout=10,
                env=os.environ.copy(),
            )
        self.assertEqual(process.returncode, 0, process.stderr)


if __name__ == "__main__":
    unittest.main()
