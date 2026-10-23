"""Exercise the installed Hermes host with the existing SDK agent fixture; no model calls."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import tempfile


PLUGIN = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true", help="Load the installed wheel instead of the source directory.")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="omp-hermes-smoke-") as temporary:
        root = Path(temporary).resolve()
        home = root / "home"
        plugins = home / "plugins"
        plugins.mkdir(parents=True)
        if not args.installed:
            (plugins / "omp-acp-delegator").symlink_to(PLUGIN, target_is_directory=True)
        bundled = root / "empty-bundled"
        bundled.mkdir()
        repository = root / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        (repository / "original.txt").write_text("preserve\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "original.txt"], check=True)
        subprocess.run([
            "git", "-C", str(repository), "-c", "user.name=Compatibility smoke",
            "-c", "user.email=smoke@example.invalid", "-c", "commit.gpgsign=false",
            "commit", "-qm", "initial",
        ], check=True)
        executable = root / "fixture-omp"
        executable.write_text(
            f"#!{sys.executable}\nimport os, sys\nos.execv(sys.executable, "
            f"[sys.executable, {str(PLUGIN / 'tests/fixtures/acp_peer.py')!r}])\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        (home / "config.yaml").write_text(json.dumps({"plugins": {
            "enabled": ["omp-acp-delegator"],
            "entries": {"omp-acp-delegator": {"settings": {
                "allowed_roots": [str(repository)], "omp_command": str(executable), "timeout": 30,
            }}},
        }}), encoding="utf-8")
        os.environ["HERMES_HOME"] = str(home)
        os.environ["HERMES_BUNDLED_PLUGINS"] = str(bundled)

        from hermes_cli.plugins import VALID_HOOKS, get_plugin_command_handler, get_plugin_manager
        from tools.registry import registry

        manager = get_plugin_manager()
        manager.discover_and_load()
        running_thread = None

        def call(name, session, **arguments):
            result = registry.dispatch(name, {"repository": str(repository), **arguments}, scope=manager.scope_key, session_id=session)
            return json.loads(result) if isinstance(result, str) else result

        try:
            first = call("omp_task", "parent", prompt="first")
            assert first["status"] == "completed", first
            second = call("omp_task", "parent", prompt="second")
            assert second["status"] == "completed", second
            assert second["session_id"] == first["session_id"]
            assert second["worktree"] == first["worktree"]
            worktree = Path(first["worktree"])
            assert (worktree / "result.txt").read_text() == "1:first\n2:second\n"
            assert not (repository / "result.txt").exists()
            assert call("omp_status", "unrelated")["status"] == "not_started"

            if "post_compaction" in VALID_HOOKS:
                manager.invoke_hook("post_compaction", session_id="child", old_session_id="parent")
                continued = call("omp_task", "child", prompt="third")
                assert continued["status"] == "completed", continued
                assert continued["session_id"] == first["session_id"]
                assert continued["worktree"] == first["worktree"]
                assert (worktree / "result.txt").read_text() == "1:first\n2:second\n3:third\n"
                continuity = "compaction ID continuity verified"
            else:
                assert call("omp_status", "child")["status"] == "not_started"
                continuity = "post_compaction unavailable; basic delegation verified"

            running = {}
            def run_task():
                running["result"] = call("omp_task", "cancellable", prompt="sleep")

            running_thread = threading.Thread(target=run_task)
            running_thread.start()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = call("omp_status", "cancellable")
                if status["status"] == "running" and (Path(status["worktree"]) / "sleep.started").exists():
                    break
                time.sleep(0.01)
            else:
                raise AssertionError(f"Cancellation fixture did not start: {status}")

            handler = get_plugin_command_handler("omp-cancel")
            try:
                from hermes_cli.plugin_command_dispatch import invoke_plugin_command
            except ModuleNotFoundError as error:
                if error.name != "hermes_cli.plugin_command_dispatch":
                    raise
                assert json.loads(handler(str(repository)))["status"] == "failed"
                assert call("omp_cancel", "unrelated")["status"] == "not_running"
                assert call("omp_status", "cancellable")["status"] == "running"
                cancelled = call("omp_cancel", "cancellable")
                cancellation = "slash context unavailable; tool cancellation verified"
            else:
                foreign = json.loads(invoke_plugin_command(
                    handler, str(repository), session_id="unrelated",
                    session_key="unrelated", source=None, platform="cli",
                ))
                assert foreign["status"] == "not_running", foreign
                assert call("omp_status", "cancellable")["status"] == "running"
                cancelled = json.loads(invoke_plugin_command(
                    handler, str(repository), session_id="cancellable",
                    session_key="cancellable", source=None, platform="cli",
                ))
                cancellation = "official slash-handler cancellation verified"
            assert cancelled["status"] == "cancellation_requested", cancelled
            running_thread.join(timeout=10)
            assert not running_thread.is_alive(), "Cancelled task did not finish"
            assert running["result"]["status"] == "cancelled", running
            assert call("omp_status", "cancellable")["status"] == "cancelled"
            print(f"Hermes native host: {continuity}; {cancellation}; follow-up and session isolation verified")
        finally:
            manager.unload()
            if running_thread is not None:
                running_thread.join(timeout=10)


if __name__ == "__main__":
    main()
