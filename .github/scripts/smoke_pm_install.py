"""Install this exact Git revision through official Hermes PM in disposable homes.

Run with a clean Python 3.14 interpreter, not the wheel-test environment.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--hermes-source", type=Path, required=True)
args = parser.parse_args()
repo = Path(__file__).resolve().parents[2]
upstream = args.hermes_source.resolve()


def run(command, *, cwd, env, capture=False):
    print("+", " ".join(map(str, command)), flush=True)
    result = subprocess.run(
        list(map(str, command)), cwd=cwd, env=env, check=True,
        text=True, stdout=subprocess.PIPE if capture else None,
        stdin=subprocess.DEVNULL, timeout=1800,
    )
    return result.stdout.strip() if capture else None


with tempfile.TemporaryDirectory(prefix="hermes-pm-install-") as temporary:
    root = Path(temporary)
    home = root / "home"
    home.mkdir()
    hermes_home = home / ".hermes"
    hermes_home.mkdir()
    source = root / "hermes-agent"
    # Do not inherit credentials, provider configuration, Python paths or PM state.
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"}}
    env.update(HOME=str(home), HERMES_HOME=str(hermes_home),
               XDG_CONFIG_HOME=str(home / ".config"),
               XDG_CACHE_HOME=str(home / ".cache"),
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0", PYTHONUNBUFFERED="1")
    git = lambda *parts, cwd=repo: run(["git", *parts], cwd=cwd, env=env, capture=True)
    revision = git("rev-parse", "HEAD")
    upstream_revision = git("rev-parse", "HEAD", cwd=upstream)
    git("clone", "--depth", "1", upstream.as_uri(), str(source))
    git("checkout", "--detach", upstream_revision, cwd=source)
    # A real Git remote, not a directory copy or preinstalled provider wheel.
    remote = root / "hermes-omp-acp.git"
    git("clone", "--bare", "--depth", "1", repo.as_uri(), str(remote))
    original = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                for name in ("pyproject.toml", "uv.lock")}
    cli = [sys.executable, str(source / "hermes")]
    # --extra alone assumes the required tool closure is already provisioned.
    # Omit only optional browser/computer-use payloads; keep all required pins.
    run([*cli, "pm", "install", "--tools-only", "--without", "agent-browser",
         "--without", "cua-driver"], cwd=source, env=env)
    run([*cli, "pm", "install", "--extra", "acp"], cwd=source, env=env)

    def probe(code):
        return run([sys.executable, "-c", (
            "from pathlib import Path; "
            "from pm.environments import activate_dependencies; "
            "activate_dependencies(Path.cwd()); " + code
        )], cwd=source, env=env, capture=True)

    print(probe(
        "from importlib.metadata import distributions; "
        "assert not any(d.metadata['Name'] == 'hermes-omp-acp' for d in distributions()), "
        "'Provider already installed before Git admission'; "
        "from providers import get_provider_profile; "
        "assert get_provider_profile('omp-acp') is None; "
        "print('Clean official PM baseline: provider absent')"
    ), flush=True)
    # Official confirmation for this trusted local source's CAUTION scan.
    # Scanning stays on; dangerous verdicts and dependency conflicts still fail.
    run([*cli, "plugins", "install", remote.as_uri(), "--ref", revision,
         "--enable", "--yes-deps", "--force"], cwd=source, env=env)
    # CLI may return zero after refusing admission; inspect committed state, not exit alone.
    metadata = json.loads((hermes_home / "plugins" / ".install-metadata.json").read_text())
    record = metadata["omp-acp-provider"]
    assert record["revision"] == revision and record["pinned"] is True, record
    installed = hermes_home / "plugins" / "omp-acp-provider"
    assert git("rev-parse", "HEAD", cwd=installed) == revision
    run([*cli, "plugins", "enable", "omp-acp-provider"], cwd=source, env=env)
    run([*cli, "plugins", "list", "--plain", "--no-bundled"], cwd=source, env=env)
    print(probe(
        "import tomllib; from importlib.metadata import distribution, version; "
        "from packaging.requirements import Requirement; "
        "from packaging.version import Version; "
        "from hermes_cli.config import load_config; "
        "assert 'omp-acp-provider' in load_config()['plugins']['enabled']; "
        "sdk = Version(version('agent-client-protocol')); "
        "provider = distribution('hermes-omp-acp'); "
        "upstream = tomllib.loads(Path('pyproject.toml').read_text()); "
        "requirements = [Requirement(r) for r in "
        "[*upstream['project']['optional-dependencies']['acp'], *provider.requires] "
        "if Requirement(r).name == 'agent-client-protocol']; "
        "assert len(requirements) >= 2; "
        "assert all(sdk in r.specifier for r in requirements), requirements; "
        "from providers import get_provider_profile; "
        "profile = get_provider_profile('omp-acp'); "
        "assert profile is not None, 'Provider unavailable after fresh process startup'; "
        "import hermes_omp_acp.provider as module; "
        "entry = next(e for e in provider.entry_points "
        "if e.group == 'hermes_agent.plugins' and e.name == 'omp-acp-provider'); "
        "assert entry.load() is module.register; "
        "print(f'Fresh startup provider: {profile}; import: {module.__file__}'); "
        "print(f'Original Hermes + plugin requirements: {requirements}; SDK: {sdk}')"
    ), flush=True)
    for name, digest in original.items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
    print(f"PASS: official PM Git admission/install/enable/fresh discovery at {revision}")
    print("No inference, model credentials, gateway service, or live profile used.")
