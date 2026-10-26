import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
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
        self.omp_command = str(Path(command).resolve())
        # Hermes may redirect tempfile globally into this test's profile.
        self.addCleanup(setattr, tempfile, "tempdir", tempfile.tempdir)
        temporary = tempfile.TemporaryDirectory(prefix="hermes-native-omp-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.token = "NATIVE_READ_" + secrets.token_hex(8)
        self.probe = self.root / "probe.txt"
        self.probe.write_text(self.token + "\n")
        self.endpoint = ModelEndpoint(self.probe)
        self.addCleanup(self.endpoint.close)
        self.omp_args = [
            "acp", "--session-dir", str(self.root / "sessions with spaces"),
            "--no-title", "--no-lsp", "--no-extensions", "--no-skills", "--no-rules",
        ]
        agent_dir = self.root / ".omp" / "agent"
        agent_dir.mkdir(parents=True)
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
            "plugins": {
                "enabled": ["omp-acp-provider"],
                "entries": {"omp-acp-provider": {"settings": {
                    "command": self.omp_command,
                    "args": self.omp_args,
                    "mcp_enabled": True,
                }}},
            },
            "memory": {"memory_enabled": False, "user_profile_enabled": False},
        }))
        preserve = {
            "PATH", "PYTHONPATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "SYSTEMROOT",
            "HERMES_PYTHON", "HERMES_NODE", "HERMES_REVISION", "HERMES_INSTALL_ROOT",
            "HERMES_BUNDLED_LOCALES", "HERMES_OPTIONAL_MCPS", "HERMES_OPTIONAL_SKILLS",
            "HERMES_TUI_DIR", "HERMES_WEB_DIST", "PI_PACKAGE_DIR", "BUN_INSTALL",
        }
        environment = {key: value for key, value in os.environ.items() if key in preserve}
        environment.update({
            "HOME": str(self.root), "HERMES_HOME": str(hermes_home),
            "PI_CODING_AGENT_DIR": str(agent_dir), "PYTHONDONTWRITEBYTECODE": "1",
        })
        isolation = patch.dict(os.environ, environment, clear=True)
        isolation.start()
        self.addCleanup(isolation.stop)
        from hermes_logging import _reset_queued_handlers

        self.addCleanup(_reset_queued_handlers)
        self.profile = get_provider_profile("omp-acp")
        self.assertIsNotNone(self.profile, "Hermes did not discover the installed provider")
        # Provider discovery is process-global; each fixture owns fresh launch paths.
        self.configure()

    def configure(self, **settings):
        from hermes_omp_acp.provider import register

        config_path = Path(os.environ["HERMES_HOME"]) / "config.yaml"
        config = json.loads(config_path.read_text())
        config["plugins"]["entries"]["omp-acp-provider"]["settings"].update(settings)
        config_path.write_text(json.dumps(config))
        register()
        self.profile = get_provider_profile("omp-acp")

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

    def test_provider_uses_the_mcp_server_from_the_omp_agent_directory(self):
        self.endpoint.tool_name = "mcp__probe_token"
        agent_dir = Path(os.environ["PI_CODING_AGENT_DIR"])
        # A user-local executable must work even when a service omits ~/.local/bin.
        local_bin = self.root / ".local" / "bin"
        local_bin.mkdir(parents=True)
        command = local_bin / "token-mcp"
        command.symlink_to(Path(sys.executable).resolve())
        (agent_dir / "mcp.json").write_text(json.dumps({"mcpServers": {
            "probe": {
                "command": "token-mcp",
                "args": [str(Path(__file__).with_name("token_server.py")), str(self.probe)],
            },
        }}))
        self.configure(mcp_enabled=False)
        with patch.dict(os.environ, {"HERMES_OMP_ACP_MCP_ENABLED": "true"}):
            with closing(self.profile.create_client(acp_cwd=str(self.root))) as client:
                response = client.chat.completions.create(
                    model="fixture/model-b", timeout=40,
                    messages=[{"role": "user", "content": "Read the token through the probe MCP server."}],
                )
        self.assertEqual(response.choices[0].message.content, self.token)
        self.assert_native_read_and_model_selection()

    def test_default_omp_config_respects_disabled_server_precedence(self):
        self.endpoint.tool_name = "mcp__probe_token"
        marker = self.root / "blocked-server-started"
        config = {
            "mcpServers": {
                "probe": {
                    "enabled": False, "command": sys.executable,
                    "args": [str(Path(__file__).with_name("token_server.py")), str(self.probe)],
                },
                "blocked": {
                    "enabled": False, "command": sys.executable,
                    "args": ["-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('started')"],
                },
                "inactive": {"enabled": False},
            },
            "enabledServers": ["probe", "blocked"],
            "disabledServers": ["blocked"],
        }
        (self.root / ".omp" / "agent" / "mcp.json").write_text(json.dumps(config))
        with patch.dict(os.environ):
            os.environ.pop("PI_CODING_AGENT_DIR")
            with closing(self.profile.create_client(acp_cwd=str(self.root))) as client:
                response = client.chat.completions.create(
                    model="fixture/model-b", timeout=40,
                    messages=[{"role": "user", "content": "Read the token through the probe MCP server."}],
                )
        self.assertEqual(response.choices[0].message.content, self.token)
        self.assertFalse(marker.exists(), "A denied server must not be started by OMP")
        self.assert_native_read_and_model_selection()

    def test_disabled_mcp_does_not_start_configured_servers(self):
        marker = self.root / "mcp-server-started"
        agent_dir = Path(os.environ["PI_CODING_AGENT_DIR"])
        (agent_dir / "mcp.json").write_text(json.dumps({"mcpServers": {
            "blocked": {
                "command": sys.executable,
                "args": ["-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('started')"],
            },
        }}))
        self.configure(mcp_enabled=False)
        with closing(self.profile.create_client(acp_cwd=str(self.root))) as client:
            response = client.chat.completions.create(
                model="fixture/model-b", timeout=40,
                messages=[{"role": "user", "content": f"Read {self.probe} and return only its contents."}],
            )
        self.assertEqual(response.choices[0].message.content, self.token)
        self.assertFalse(marker.exists(), "Disabling forwarding must prevent MCP server startup")
        self.assert_native_read_and_model_selection()

    def test_unknown_model_does_not_run_native_omp_inference(self):
        with closing(self.profile.create_client(acp_cwd=str(self.root))) as client:
            with self.assertRaisesRegex(RuntimeError, "not offered"):
                client.chat.completions.create(
                    model="fixture/not-listed", timeout=20,
                    messages=[{"role": "user", "content": f"Read {self.probe} and return its contents."}],
                )
        self.assertEqual(self.endpoint.requests, [],
                         "An unknown model must not run inference on OMP's default model")

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

    def _workspace_tool(self):
        from types import SimpleNamespace
        from hermes_omp_acp.tool_plugin import register
        from tools.registry import registry

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
        models_path = Path(os.environ["PI_CODING_AGENT_DIR"]) / "models.yml"
        models = json.loads(models_path.read_text())
        models["providers"]["fixture"]["baseUrl"] = endpoint.url
        models_path.write_text(json.dumps(models))
        self.task_settings = {
            "model_id": "fixture/model-b", "allowed_roots": [str(repository)],
            "command": self.omp_command, "args": self.omp_args[:],
            "mcp_enabled": True, "timeout_seconds": 60,
        }
        scope = str(Path(os.environ["HERMES_HOME"]).resolve())
        register(SimpleNamespace(
            get_config=lambda key, default=None: self.task_settings.get(key, default),
            register_tool=lambda **kwargs: registry.register(**kwargs, scope=scope),
        ))
        self.addCleanup(lambda: registry.deregister("omp_task", scope=scope))

        def invoke(task_id=None):
            arguments = {"repository": str(repository), "goal": "Read, change and read back probe.txt"}
            if task_id is not None:
                arguments["task_id"] = task_id
            result = json.loads(registry.dispatch("omp_task", arguments, scope=scope))
            self.assertEqual(result["status"], "success", result)
            self.assertEqual(result["response"], modified)
            return result

        return repository, modified, endpoint, invoke

    def _assert_workspace_change(self, result, repository, modified):
        worktree = Path(result["path"])
        self.assertEqual((repository / "probe.txt").read_text(), self.token + "\n")
        self.assertEqual((worktree / "probe.txt").read_text(), modified + "\n")
        sessions = [
            json.loads(line)
            for path in (self.root / "sessions with spaces").rglob("*.jsonl")
            for line in path.read_text().splitlines()
            if line.strip()
        ]
        self.assertTrue(any(record.get("type") == "session" and
                            Path(record.get("cwd", "/")).resolve() == worktree.resolve()
                            for record in sessions))
        return worktree

    def test_omp_task_changes_only_retained_worktree_and_reuses_task_files(self):
        repository, modified, endpoint, invoke = self._workspace_tool()
        first = invoke()
        worktree = self._assert_workspace_change(first, repository, modified)
        (worktree / "retained.txt").write_text("retained")
        second = invoke(first["task_id"])
        self.assertEqual(second["path"], first["path"])
        self.assertEqual((worktree / "retained.txt").read_text(), "retained")
        self.assertFalse(endpoint.errors, endpoint.errors)
        self.assertEqual({request["model"] for request in endpoint.requests}, {"model-b"})

    def test_concurrent_omp_tasks_keep_distinct_owned_worktrees(self):
        from concurrent.futures import ThreadPoolExecutor

        repository, modified, endpoint, invoke = self._workspace_tool()
        # Initialize this disposable OMP profile before overlapping requests.
        # OMP 15.7.6 switches a brand-new SQLite auth store to WAL before
        # installing its busy timeout; simultaneous first opens can fail there.
        from hermes_omp_acp._session import ACPRuntime

        with closing(ACPRuntime(command=self.omp_command, args=self.omp_args,
                                acp_cwd=str(self.root), mcp_enabled=False)) as runtime:
            models = asyncio.run(runtime.list_models(20))
        self.assertIn("fixture/model-b", models)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(invoke) for _ in range(2)]
            results = [future.result(90) for future in futures]
        self.assertFalse(endpoint.errors, endpoint.errors)
        paths = [self._assert_workspace_change(result, repository, modified) for result in results]
        self.assertNotEqual(paths[0], paths[1])


if __name__ == "__main__":
    unittest.main()
