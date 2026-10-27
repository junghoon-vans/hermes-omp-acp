"""Exercise the installed wheel and native ACP catalog without model inference."""

import json
import os
import shutil
import tempfile
from importlib.metadata import distribution
from pathlib import Path

command = os.environ.get("OMP_INTEGRATION_COMMAND") or shutil.which("omp")
if not command:
    raise RuntimeError("Install OMP or set OMP_INTEGRATION_COMMAND")
command = str(Path(command).resolve())
preserve = {
    "PATH", "PYTHONPATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL",
    "SYSTEMROOT", "BUN_INSTALL", "PI_PACKAGE_DIR",
}
environment = {key: value for key, value in os.environ.items() if key in preserve}
with tempfile.TemporaryDirectory(prefix="hermes-discovery-") as temporary:
    root = Path(temporary)
    hermes_home = root / "hermes"
    hermes_home.mkdir()
    agent_dir = root / ".omp" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "models.yml").write_text(json.dumps({"providers": {"fixture": {
        "baseUrl": "http://127.0.0.1:9", "api": "openai-completions", "auth": "none",
        "models": [{"id": "catalog-only", "name": "Catalog only", "reasoning": False,
                    "contextWindow": 128000, "maxTokens": 4096}],
    }}}))
    (hermes_home / "config.yaml").write_text(json.dumps({"plugins": {
        "enabled": ["omp-acp-provider"],
        "entries": {"omp-acp-provider": {"settings": {
            "command": command,
            "args": ["acp", "--cwd", str(root), "--session-dir", str(root / "sessions"),
                     "--no-title", "--no-lsp", "--no-extensions", "--no-skills", "--no-rules"],
            "mcp_enabled": False,
        }}},
    }}))
    os.environ.clear()
    os.environ.update(environment)
    os.environ.update({"HOME": temporary, "HERMES_HOME": str(hermes_home),
                       "PI_CODING_AGENT_DIR": str(agent_dir)})

    from providers import get_provider_profile

    import hermes_omp_acp.provider

    installed = distribution("hermes-omp-acp")
    expected = installed.locate_file("hermes_omp_acp/provider.py").resolve()
    actual = Path(hermes_omp_acp.provider.__file__).resolve()
    assert actual == expected, f"Imported source instead of installed wheel: {actual}"
    entry = next(item for item in installed.entry_points
                 if item.group == "hermes_agent.plugins" and item.name == "omp-acp-provider")
    assert entry.load() is hermes_omp_acp.provider.register
    profile = get_provider_profile("omp-acp")
    assert profile is not None, "Hermes did not discover the enabled installed entry point"
    models = profile.fetch_models(timeout=30)
    assert "fixture/catalog-only" in (models or []), f"Native ACP discovery failed: {models}"
    print(f"Installed import: {actual}")
    print(f"Hermes entry-point discovery + native ACP catalog: {models}")
    print("No completion/model call; PM-managed Git-plugin admission is not exercised.")
