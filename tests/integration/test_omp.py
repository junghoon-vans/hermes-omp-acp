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
            "plugins": {"enabled": ["omp-acp"]},
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
            "HERMES_OMP_ACP_COMMAND": str(Path(command).resolve()),
            "HERMES_OMP_ACP_ARGS": shlex.join([
                "acp", "--session-dir", str(self.root / "sessions"),
                "--no-title", "--no-lsp", "--no-extensions", "--no-skills", "--no-rules",
            ]),
        })
        isolation = patch.dict(os.environ, environment, clear=True)
        isolation.start()
        self.addCleanup(isolation.stop)
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

    def test_hermes_cli_discovers_the_provider_and_returns_native_tool_output(self):
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
        self.assertEqual(result.stdout.strip(), self.token)
        self.assert_native_read_and_model_selection()


if __name__ == "__main__":
    unittest.main()
