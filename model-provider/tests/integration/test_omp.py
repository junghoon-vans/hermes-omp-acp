import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from providers import get_provider_profile

from model_endpoint import ModelEndpoint


class NativeOmpTests(unittest.TestCase):
    def setUp(self):
        command = os.environ.get("OMP_INTEGRATION_COMMAND") or shutil.which("omp")
        if not command:
            raise RuntimeError("Install OMP or set OMP_INTEGRATION_COMMAND before running integration tests")
        temporary = tempfile.TemporaryDirectory(prefix="hermes-native-omp-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.token = "NATIVE_READ_" + secrets.token_hex(8)
        self.probe = self.root / "probe.txt"
        self.probe.write_text(self.token + "\n")
        self.endpoint = ModelEndpoint(self.probe)
        self.addCleanup(self.endpoint.close)
        agent_dir = self.root / "omp"
        agent_dir.mkdir()
        (agent_dir / "models.yml").write_text(json.dumps({"providers": {"fixture": {
            "baseUrl": self.endpoint.url, "api": "openai-completions", "auth": "none",
            "models": [{"id": name, "name": name, "reasoning": False,
                        "contextWindow": 128000, "maxTokens": 4096}
                       for name in ("model-a", "model-b")],
        }}}))
        hermes_home = self.root / "hermes"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(json.dumps({
            "model": {"provider": "omp-acp", "default": "fixture/model-b",
                      "base_url": "acp://omp", "api_mode": "chat_completions"},
            "plugins": {"enabled": ["omp-acp-provider"]},
            "memory": {"memory_enabled": False, "user_profile_enabled": False},
        }))
        preserve = {
            "PATH", "PYTHONPATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "SYSTEMROOT",
            "HERMES_PYTHON", "HERMES_NODE", "HERMES_REVISION", "HERMES_INSTALL_ROOT",
            "HERMES_BUNDLED_LOCALES", "HERMES_OPTIONAL_MCPS", "HERMES_OPTIONAL_SKILLS",
            "HERMES_TUI_DIR", "HERMES_WEB_DIST", "PI_PACKAGE_DIR", "BUN_INSTALL",
            "HERMES_ADVANCED_DELEGATION_SOURCE",
        }
        environment = {key: value for key, value in os.environ.items() if key in preserve}
        environment.update({
            "HOME": str(self.root), "HERMES_HOME": str(hermes_home),
            "PI_CODING_AGENT_DIR": str(agent_dir), "PYTHONDONTWRITEBYTECODE": "1",
            "HERMES_OMP_ACP_COMMAND": str(Path(command).resolve()),
            "HERMES_OMP_ACP_ARGS": shlex.join([
                "acp", "--session-dir", str(self.root / "sessions"),
                "--no-title", "--no-lsp", "--no-extensions", "--no-skills", "--no-rules",
            ]),
        })
        isolation = patch.dict(os.environ, environment, clear=True)
        isolation.start()
        self.addCleanup(isolation.stop)
        from hermes_logging import _reset_queued_handlers

        self.addCleanup(_reset_queued_handlers)
        self.profile = get_provider_profile("omp-acp")
        self.assertIsNotNone(self.profile, "Hermes did not discover the installed provider")

    def assert_native_read_and_model_selection(self):
        self.assertFalse(self.endpoint.errors, self.endpoint.errors)
        self.assertEqual({request["model"] for request in self.endpoint.requests}, {"model-b"})
        self.assertNotIn(self.token, json.dumps(self.endpoint.requests[0]))
        self.assertTrue(any(message["role"] == "tool" and self.token in json.dumps(message)
                            for request in self.endpoint.requests for message in request["messages"]),
                        "OMP did not return the read result to the model")
        self.assertEqual(self.probe.read_text(), self.token + "\n")

    def test_async_provider_selects_the_model_and_reads_a_file_through_native_omp(self):
        models = self.profile.fetch_models(timeout=20)
        self.assertTrue({"fixture/model-a", "fixture/model-b"}.issubset(set(models or [])))

        async def exercise():
            with closing(self.profile.create_client(acp_cwd=str(self.root))) as client:
                stream = await client.chat.completions.create(
                    model="fixture/model-b", stream=True, timeout=40,
                    messages=[{"role": "user", "content": f"Read {self.probe} and return only its contents."}],
                )
                choices = [choice async for chunk in stream for choice in chunk.choices]
                self.assertEqual("".join(choice.delta.content or "" for choice in choices), self.token)
                self.assertEqual(choices[-1].finish_reason, "stop")
        asyncio.run(exercise())
        self.assert_native_read_and_model_selection()

    def _stop_gateway(self):
        script = """
from gateway.status import get_running_pid
from hermes_cli.gateway import stop_profile_gateway

if get_running_pid() is not None:
    stop_profile_gateway()
assert get_running_pid() is None, "The test gateway did not stop"
"""
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=self.root,
            text=True, capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_hermes_cli_discovers_the_provider_and_returns_native_tool_output(self):
        self.addCleanup(self._stop_gateway)
        from hermes_cli.models import list_available_providers

        provider = next((entry for entry in list_available_providers() if entry["id"] == "omp-acp"), None)
        self.assertIsNotNone(provider, "The model picker hides the installed provider")
        self.assertTrue(provider["authenticated"], "The model picker cannot use the OMP executable")
        result = subprocess.run([
            sys.executable, "-c", "from hermes_cli.main import main; main()", "chat",
            "--provider", "omp-acp", "--model", "fixture/model-b", "--oneshot", "-Q",
            "--max-turns", "4", "-q", f"Read {self.probe} and return only its contents.",
        ], cwd=self.root, text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(self.token, result.stdout.splitlines())
        self.assert_native_read_and_model_selection()

    def test_gateway_style_agent_resolves_omitted_launch_arguments(self):
        result_path = self.root / "agent-result.json"
        script = """
import json
from contextlib import closing
from pathlib import Path
import sys
from run_agent import AIAgent

with closing(AIAgent(provider="omp-acp", model="fixture/model-b",
                     api_key="omp-acp", base_url="acp://omp",
                     quiet_mode=True, max_iterations=4)) as agent:
    result = agent.run_conversation(sys.argv[1])
Path(sys.argv[2]).write_text(json.dumps({
    "completed": result["completed"], "response": result["final_response"],
}))
"""
        result = subprocess.run([
            sys.executable, "-c", script,
            f"Read {self.probe} and return only its contents.", str(result_path),
        ], cwd=self.root, text=True, capture_output=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        response = json.loads(result_path.read_text())
        self.assertTrue(response["completed"], result.stdout + result.stderr)
        self.assertEqual(response["response"], self.token)
        self.assert_native_read_and_model_selection()

    def _workspace_routes(self, count=1):
        source = os.environ.get("HERMES_ADVANCED_DELEGATION_SOURCE")
        if not source:
            raise RuntimeError("Set HERMES_ADVANCED_DELEGATION_SOURCE to the official advanced-delegation plugin")
        repository = self.root / "repository"
        repository.mkdir()
        (repository / "probe.txt").write_text(self.token + "\n")
        for arguments in (
            ["init", "--quiet"],
            ["add", "probe.txt"],
            ["-c", "user.name=OMP integration", "-c", "user.email=check@example.invalid",
             "commit", "--quiet", "-m", "initial"],
        ):
            subprocess.run(["git", "-C", str(repository), *arguments], check=True)
        modified = "NATIVE_READ_" + secrets.token_hex(8)
        endpoint = ModelEndpoint(Path("probe.txt"), replacement=modified)
        self.addCleanup(endpoint.close)
        models_path = self.root / "omp/models.yml"
        models = json.loads(models_path.read_text())
        models["providers"]["fixture"]["baseUrl"] = endpoint.url
        models_path.write_text(json.dumps(models))

        config_path = Path(os.environ["HERMES_HOME"]) / "config.yaml"
        config = json.loads(config_path.read_text())
        config["delegation"] = {
            "backend": "omp-worktree",
            "fallback_providers": [],
            "backends": {
                "omp-worktree": {"allowed_roots": [str(repository)]},
                "model_map": {"default": "coder", "options": [{
                    "name": "coder", "description": "Native OMP repository work",
                    "provider": "omp-acp", "model": "fixture/model-b", "fallback_providers": [],
                }]},
            },
        }
        config["plugins"]["enabled"].append("advanced-delegation")
        config_path.write_text(json.dumps(config))
        plugins = config_path.parent / "plugins"
        plugins.mkdir()
        (plugins / "advanced-delegation").symlink_to(source, target_is_directory=True)
        os.environ["TERMINAL_CWD"] = str(self.root)
        from hermes_cli.plugins import get_plugin_manager
        from tools.delegate_tool_routes import resolve_task_routes

        manager = get_plugin_manager()
        manager.discover_and_load(force=True)
        self.addCleanup(manager.unload)
        routes, error = resolve_task_routes([
            {"goal": "Read, change and read back probe.txt", "option": "coder",
             "repository": str(repository)}
            for _ in range(count)
        ], config["delegation"], None)
        self.assertIsNone(error, {"error": error, "plugins": manager.list_plugins()})
        self.assertFalse([route.error for route in routes if route.error])
        return repository, modified, endpoint, routes

    def _assert_workspace_change(self, route, repository, modified):
        from plugins.plugin_storage import plugin_data_dir

        metadata = route.creds["request_overrides"]["extra_body"]["hermes_omp_acp"]
        state = json.loads((plugin_data_dir("omp-acp-provider") / "workspaces" /
                            metadata["task_id"] / "state.json").read_text())
        worktree = Path(state["path"])
        self.assertEqual((repository / "probe.txt").read_text(), self.token + "\n")
        self.assertEqual((worktree / "probe.txt").read_text(), modified + "\n")
        sessions = [
            json.loads(line)
            for path in (self.root / "sessions").rglob("*.jsonl")
            for line in path.read_text().splitlines()
            if line.strip()
        ]
        self.assertTrue(any(record.get("type") == "session" and
                            Path(record.get("cwd", "/")).resolve() == worktree.resolve()
                            for record in sessions))
        return worktree

    def test_coder_agent_changes_only_its_provider_owned_worktree(self):
        from run_agent import AIAgent

        repository, modified, endpoint, routes = self._workspace_routes()
        with closing(AIAgent(
            **routes[0].creds, quiet_mode=True, max_iterations=2, enabled_toolsets=[],
            skip_context_files=True, skip_memory=True, skip_background_review=True,
        )) as child:
            result = child.run_conversation("Read, change and read back probe.txt")
        self.assertTrue(result["completed"], result)
        self.assertEqual(result["final_response"], modified)
        self.assertFalse(endpoint.errors, endpoint.errors)
        self._assert_workspace_change(routes[0], repository, modified)

    def test_concurrent_completions_keep_distinct_worktrees_on_one_provider_client(self):
        repository, modified, endpoint, routes = self._workspace_routes(count=2)

        async def exercise():
            with closing(self.profile.create_client(acp_cwd=str(self.root))) as client:
                return await asyncio.gather(*[
                    client.chat.completions.create(
                        model="fixture/model-b", timeout=60,
                        messages=[{"role": "user", "content": "Read, change and read back probe.txt"}],
                        **route.creds["request_overrides"],
                    )
                    for route in routes
                ])

        responses = asyncio.run(exercise())
        self.assertEqual([response.choices[0].message.content for response in responses], [modified, modified])
        self.assertFalse(endpoint.errors, endpoint.errors)
        paths = [self._assert_workspace_change(route, repository, modified) for route in routes]
        self.assertNotEqual(paths[0], paths[1])


if __name__ == "__main__":
    unittest.main()
