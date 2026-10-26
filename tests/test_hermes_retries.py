"""Keep dispatched ACP failures terminal through Hermes's real delegated watchdog."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class HermesRetryPolicyTests(unittest.TestCase):
    def run_failed_turn(self, mode):
        with tempfile.TemporaryDirectory(prefix="hermes-acp-retry-") as temporary:
            root = Path(temporary)
            home = root / "hermes"
            home.mkdir()
            peer = Path(__file__).with_name("acp_error_peer.py").resolve()
            (home / "config.yaml").write_text(json.dumps({
                "model": {"provider": "omp-acp", "default": "fixture/model",
                          "base_url": "acp://omp", "api_mode": "chat_completions"},
                "providers": {"omp-acp": {"stale_timeout_seconds": 0.5, "request_timeout_seconds": 1.5}},
                "plugins": {
                    "enabled": ["omp-acp-provider"],
                    "entries": {"omp-acp-provider": {"settings": {
                        "command": sys.executable, "args": [str(peer), mode], "mcp_enabled": False,
                    }}},
                },
                "memory": {"memory_enabled": False, "user_profile_enabled": False},
            }))
            environment = {key: value for key, value in os.environ.items()
                           if key in {"PATH", "PYTHONPATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL",
                                      "SYSTEMROOT", "HERMES_BUNDLED_LOCALES"}}
            environment.update({
                "HOME": str(root), "HERMES_HOME": str(home),
                "PI_CODING_AGENT_DIR": str(root / "omp"), "PYTHONDONTWRITEBYTECODE": "1",
            })
            script = """
import json
from contextlib import closing
from pathlib import Path
import sys
from run_agent import AIAgent

with closing(AIAgent(provider="omp-acp", model="fixture/model", api_key="omp-acp",
                     base_url="acp://omp", platform="subagent", quiet_mode=True,
                     max_iterations=4, enabled_toolsets=[])) as agent:
    result = agent.run_conversation("Execute the task once and report its outcome.")
Path(sys.argv[1]).write_text(json.dumps({
    "completed": result["completed"], "failure_reason": result.get("failure_reason"),
}))
"""
            result_path = root / "result.json"
            process = subprocess.run(
                [sys.executable, "-P", "-c", script, str(result_path)],
                cwd=root, env=environment, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            result = json.loads(result_path.read_text())
            self.assertFalse(result["completed"])
            receipts = (root / "error-peer.prompt-count").read_text(encoding="ascii").splitlines()
            self.assertEqual(receipts, ["1"], "Hermes replayed a prompt after watchdog error translation")
            self.assertEqual(result["failure_reason"], "format_error")

    def test_watchdog_preserves_terminal_rpc_failure_classification(self):
        self.run_failed_turn("rpc-error")

    def test_watchdog_does_not_replay_a_timed_out_dispatched_prompt(self):
        self.run_failed_turn("hang-prompt")


if __name__ == "__main__":
    unittest.main()
