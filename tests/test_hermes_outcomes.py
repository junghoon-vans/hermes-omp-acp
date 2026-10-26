"""Exercise terminal ACP outcomes through the real Hermes conversation loop."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class HermesOutcomeTests(unittest.TestCase):
    def run_turn(self, mode):
        with tempfile.TemporaryDirectory(prefix="hermes-acp-outcome-") as temporary:
            root = Path(temporary)
            home = root / "hermes"
            home.mkdir()
            peer = Path(__file__).with_name("outcomes_peer.py").resolve()
            (home / "config.yaml").write_text(json.dumps({
                "model": {"provider": "omp-acp", "default": "fixture/model",
                          "base_url": "acp://omp", "api_mode": "chat_completions"},
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
            result_path = root / "result.json"
            script = """
import json
from contextlib import closing
from pathlib import Path
import sys
from run_agent import AIAgent

with closing(AIAgent(provider="omp-acp", model="fixture/model", api_key="omp-acp",
                     base_url="acp://omp", quiet_mode=True, max_iterations=4)) as agent:
    result = agent.run_conversation("Complete the task and report the outcome.")
Path(sys.argv[1]).write_text(json.dumps({
    "completed": result["completed"], "response": result["final_response"],
    "failure_reason": result.get("failure_reason"),
    "tool_messages": [message for message in result.get("messages", []) if message.get("role") == "tool"],
}))
"""
            process = subprocess.run(
                [sys.executable, "-P", "-c", script, str(result_path)],
                cwd=root, env=environment, capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            result = json.loads(result_path.read_text())
            receipts = (root / "prompt-receipts").read_text(encoding="ascii").splitlines()
            self.assertEqual(receipts, ["received"], "Hermes replayed an already-dispatched ACP turn")
            self.assertEqual(result["tool_messages"], [], "A terminal partial response executed a tool")
            return result

    def test_cancelled_tool_output_stops_the_turn_without_replay(self):
        result = self.run_turn("cancelled-tools")
        self.assertFalse(result["completed"])

    def test_turn_request_limit_is_not_a_length_continuation(self):
        result = self.run_turn("max_turn_requests")
        self.assertFalse(result["completed"])

    def test_refusal_is_displayed_without_fallback_or_replay(self):
        result = self.run_turn("refusal")
        self.assertTrue(result["completed"])


if __name__ == "__main__":
    unittest.main()
