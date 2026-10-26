"""Retain one isolated Git worktree for each Hermes OMP task."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid


_TASK_ID = re.compile(r"[0-9a-f]{32}\Z")
_COMMIT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_MANIFEST_FIELDS = {"repository", "task_id", "path", "branch", "base_commit"}


@dataclass(frozen=True)
class Workspace:
    path: Path
    repository: Path
    branch: str
    base_commit: str


def _canonical_allowed_roots(allowed_roots: list[str]) -> tuple[Path, ...]:
    if not isinstance(allowed_roots, list) or not allowed_roots:
        raise ValueError("allowed_roots must be a non-empty list of absolute directory paths.")

    roots = []
    for value in allowed_roots:
        if not isinstance(value, str) or not value:
            raise ValueError("Every allowed repository root must be a non-empty absolute path.")
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("Every allowed repository root must be absolute.")
        try:
            root = path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError(f"Allowed repository root does not exist: {value}") from error
        if not root.is_dir():
            raise ValueError(f"Allowed repository root is not a directory: {value}")
        roots.append(root)
    return tuple(roots)


def _git(git: str, cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            [
                str(git), "-C", str(cwd),
                "-c", "core.hooksPath=/dev/null",
                "-c", "core.fsmonitor=false",
                *args,
            ],
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except OSError as error:
        raise RuntimeError(f"Could not run Git: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Git {' '.join(args[:2])} timed out.") from error
    if result.returncode:
        command = " ".join(args[:2])
        raise RuntimeError(f"Git {command} failed: {result.stderr.strip()}")
    return result.stdout.rstrip("\n")


def validate_repository(value: str | os.PathLike[str], allowed_roots: list[str], git: str = "git") -> Path:
    """Return the canonical Git repository root after enforcing the configured boundary."""
    roots = _canonical_allowed_roots(allowed_roots)
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError("Repository must be an absolute path to a Git repository root.")
    try:
        requested = Path(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Repository must be an absolute path to a Git repository root.") from error
    if not requested.is_absolute():
        raise ValueError("Repository path must be absolute.")
    try:
        repository = requested.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("Repository path does not exist.") from error
    if not repository.is_dir():
        raise ValueError("Repository path must be a directory.")
    if not any(repository.is_relative_to(root) for root in roots):
        raise ValueError("Repository is outside the configured repository roots.")

    try:
        top_level = Path(_git(git, repository, "rev-parse", "--show-toplevel")).resolve(strict=True)
        head = _git(git, repository, "rev-parse", "--verify", "HEAD^{commit}")
    except (RuntimeError, OSError) as error:
        raise ValueError("Repository must be a Git repository with a valid HEAD commit.") from error
    if top_level != repository:
        raise ValueError("Specify the repository root, not a subdirectory.")
    if not _COMMIT_ID.fullmatch(head):
        raise ValueError("Repository HEAD is not a valid commit.")
    return repository


class WorkspaceStore:
    def __init__(self, root: Path, allowed_roots: list[str], git: str = "git"):
        self.allowed_roots = _canonical_allowed_roots(allowed_roots)
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if not self.root.is_dir():
            raise ValueError("Workspace store root must be a directory.")
        self.git = str(git)

    @contextmanager
    def acquire(self, metadata: Mapping[str, object]):
        if not isinstance(metadata, Mapping):
            raise ValueError("Workspace metadata must be a mapping.")
        task_id = metadata.get("task_id")
        if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
            raise ValueError("Workspace task_id must be exactly 32 lowercase hexadecimal characters.")
        repository_value = metadata.get("repository")
        repository = validate_repository(repository_value, [str(root) for root in self.allowed_roots], self.git)

        task_dir = self.root / task_id
        try:
            task_dir.mkdir(mode=0o700)
            created_task_dir = True
        except FileExistsError:
            created_task_dir = False
        if not self._is_real_directory(task_dir):
            raise RuntimeError("Workspace task directory is missing, replaced, or not a real directory.")

        lock_path = task_dir / "workspace.lock"
        lock_flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        lock_fd = os.open(lock_path, lock_flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                raise RuntimeError("Workspace lock is not a regular file.")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("This OMP task already has a workspace in use.") from None

            manifest_path = task_dir / "state.json"
            if self._path_kind(manifest_path) == "missing":
                if not created_task_dir:
                    raise RuntimeError("Existing workspace state is missing; refusing to recreate it.")
                workspace, manifest = self._create(task_id, repository, task_dir)
                self._save_manifest(manifest_path, task_dir, manifest)
            else:
                manifest = self._load_manifest(manifest_path)
                workspace = self._reuse(task_id, repository, task_dir, manifest)
            yield workspace
        finally:
            os.close(lock_fd)

    @staticmethod
    def _path_kind(path: Path) -> str:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return "missing"
        except OSError as error:
            raise RuntimeError(f"Could not inspect workspace path {path}.") from error
        if stat.S_ISLNK(info.st_mode):
            return "symlink"
        if stat.S_ISREG(info.st_mode):
            return "file"
        if stat.S_ISDIR(info.st_mode):
            return "directory"
        return "other"

    @classmethod
    def _is_real_directory(cls, path: Path) -> bool:
        return cls._path_kind(path) == "directory"

    def _create(self, task_id: str, repository: Path, task_dir: Path) -> tuple[Workspace, dict[str, str]]:
        worktree = task_dir / "tree"
        if self._path_kind(worktree) != "missing":
            raise RuntimeError("Unexpected workspace worktree path already exists; refusing to replace it.")
        base_commit = _git(self.git, repository, "rev-parse", "--verify", "HEAD^{commit}")
        if not _COMMIT_ID.fullmatch(base_commit):
            raise RuntimeError("Repository HEAD is not a valid commit.")
        branch = f"hermes/omp-{task_id}"
        _git(self.git, repository, "worktree", "add", "-b", branch, str(worktree), base_commit)

        manifest = {
            "repository": str(repository),
            "task_id": task_id,
            "path": str(worktree),
            "branch": branch,
            "base_commit": base_commit,
        }
        workspace = self._workspace_from_manifest(manifest)
        self._verify_worktree(workspace)
        return workspace, manifest

    def _load_manifest(self, path: Path) -> dict[str, str]:
        if self._path_kind(path) != "file":
            raise RuntimeError("Workspace state is corrupt or replaced; refusing to recreate it.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                if os.fstat(stream.fileno()).st_size > 16384:
                    raise RuntimeError("Workspace state is corrupt or too large.")
                data = json.load(stream)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Workspace state is corrupt or unreadable.") from error
        if not isinstance(data, dict) or set(data) != _MANIFEST_FIELDS:
            raise RuntimeError("Workspace state is corrupt or incomplete.")
        if not all(isinstance(value, str) for value in data.values()):
            raise RuntimeError("Workspace state is corrupt or incomplete.")
        return data

    def _workspace_from_manifest(self, manifest: dict[str, str]) -> Workspace:
        return Workspace(
            path=Path(manifest["path"]),
            repository=Path(manifest["repository"]),
            branch=manifest["branch"],
            base_commit=manifest["base_commit"],
        )

    def _reuse(
        self,
        task_id: str,
        repository: Path,
        task_dir: Path,
        manifest: dict[str, str],
    ) -> Workspace:
        expected_path = task_dir / "tree"
        expected_branch = f"hermes/omp-{task_id}"
        if (
            manifest["task_id"] != task_id
            or manifest["repository"] != str(repository)
            or manifest["path"] != str(expected_path)
            or manifest["branch"] != expected_branch
            or not _COMMIT_ID.fullmatch(manifest["base_commit"])
        ):
            raise RuntimeError("Retained workspace ownership does not match the requested task.")
        workspace = self._workspace_from_manifest(manifest)
        try:
            resolved_base = _git(self.git, repository, "rev-parse", "--verify", f"{workspace.base_commit}^{{commit}}")
        except RuntimeError as error:
            raise RuntimeError("Retained workspace base commit is invalid.") from error
        if resolved_base != workspace.base_commit:
            raise RuntimeError("Retained workspace base commit is invalid.")
        self._verify_worktree(workspace)
        return workspace

    def _verify_worktree(self, workspace: Workspace) -> None:
        path = workspace.path
        if not self._is_real_directory(path):
            raise RuntimeError("Retained workspace worktree is missing or replaced.")
        try:
            resolved_path = path.resolve(strict=True)
            top_level = Path(_git(self.git, path, "rev-parse", "--show-toplevel")).resolve(strict=True)
            branch = _git(self.git, path, "symbolic-ref", "--quiet", "--short", "HEAD")
            source_common = self._common_dir(workspace.repository)
            worktree_common = self._common_dir(path)
            registered = self._worktree_is_registered(workspace.repository, path, workspace.branch)
        except (OSError, RuntimeError) as error:
            raise RuntimeError("Retained workspace worktree could not be verified.") from error
        if (
            resolved_path != path
            or top_level != path
            or branch != workspace.branch
            or source_common != worktree_common
            or not registered
        ):
            raise RuntimeError("Retained workspace worktree was replaced or no longer belongs to this task.")

    def _common_dir(self, cwd: Path) -> Path:
        value = Path(_git(self.git, cwd, "rev-parse", "--git-common-dir"))
        if not value.is_absolute():
            value = cwd / value
        return value.resolve(strict=True)

    def _worktree_is_registered(self, repository: Path, path: Path, branch: str) -> bool:
        output = _git(self.git, repository, "worktree", "list", "--porcelain", "-z")
        records = []
        record: dict[str, str] = {}
        for item in output.split("\0"):
            if not item:
                if record:
                    records.append(record)
                    record = {}
                continue
            key, separator, value = item.partition(" ")
            record[key] = value if separator else ""
        if record:
            records.append(record)
        expected = path.resolve(strict=True)
        expected_branch = f"refs/heads/{branch}"
        for item in records:
            candidate = item.get("worktree")
            if not candidate:
                continue
            try:
                candidate_path = Path(candidate).resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if candidate_path == expected and item.get("branch") == expected_branch:
                return True
        return False

    @staticmethod
    def _save_manifest(path: Path, task_dir: Path, manifest: dict[str, str]) -> None:
        temporary = task_dir / f".state-{uuid.uuid4().hex}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(manifest, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
