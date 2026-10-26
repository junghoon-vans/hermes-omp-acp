"""Opt-in checks for metadata and entry points in built distributions."""

from email.parser import BytesParser
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


DIST_DIR_ENV = "HERMES_OMP_DIST_DIR"
ENTRYPOINT_ENV = "HERMES_OMP_CHECK_INSTALLED_ENTRYPOINT"


@unittest.skipUnless(os.environ.get(DIST_DIR_ENV), f"set {DIST_DIR_ENV} to check built distributions")
class PackagingArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root))
        from utils import fast_safe_load

        manifest = fast_safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
        cls.version = manifest["version"]
        cls.description = manifest["description"]

        dist_dir = Path(os.environ[DIST_DIR_ENV])
        wheels = sorted(dist_dir.glob("hermes_omp_acp-*.whl"))
        sdists = sorted(dist_dir.glob("hermes_omp_acp-*.tar.gz"))
        if len(wheels) != 1:
            raise AssertionError(f"expected one hermes_omp_acp wheel in {dist_dir}, found {len(wheels)}")
        if len(sdists) != 1:
            raise AssertionError(f"expected one hermes_omp_acp sdist in {dist_dir}, found {len(sdists)}")
        cls.wheel = wheels[0]
        cls.sdist = sdists[0]
        cls.root = root

    def assert_metadata_matches_manifest(self, metadata, artifact):
        self.assertEqual(metadata["Version"], self.version, f"{artifact} version")
        self.assertEqual(metadata["Summary"], self.description, f"{artifact} description")

    def test_built_wheel_metadata_matches_plugin_manifest(self):
        with zipfile.ZipFile(self.wheel) as wheel:
            metadata_files = [
                name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
            ]
            self.assertEqual(len(metadata_files), 1, "wheel must contain one dist-info/METADATA")
            metadata = BytesParser().parsebytes(wheel.read(metadata_files[0]))

        self.assert_metadata_matches_manifest(metadata, self.wheel.name)

    def test_built_sdist_metadata_matches_plugin_manifest(self):
        with tarfile.open(self.sdist, "r:gz") as archive:
            metadata_files = [
                member
                for member in archive.getmembers()
                if member.isfile() and Path(member.name).name == "PKG-INFO"
            ]
            self.assertEqual(len(metadata_files), 1, "sdist must contain one PKG-INFO")
            metadata_file = archive.extractfile(metadata_files[0])
            self.assertIsNotNone(metadata_file)
            metadata = BytesParser().parsebytes(metadata_file.read())

        self.assert_metadata_matches_manifest(metadata, self.sdist.name)

    def test_standalone_tool_directory_plugin_is_packaged_in_sdist(self):
        with tarfile.open(self.sdist, "r:gz") as archive:
            manifest_members = [
                member for member in archive.getmembers()
                if member.isfile() and member.name.endswith("plugins/omp-task/plugin.yaml")
            ]
            wrapper_members = [
                member for member in archive.getmembers()
                if member.isfile() and member.name.endswith("plugins/omp-task/__init__.py")
            ]
        self.assertEqual(len(manifest_members), 1, "sdist must include the tool directory manifest")
        self.assertEqual(len(wrapper_members), 1, "sdist must include the tool directory wrapper")

    @unittest.skipUnless(
        os.environ.get(ENTRYPOINT_ENV) == "1",
        f"set {ENTRYPOINT_ENV}=1 in the installed Hermes runtime to check both wheel entry points",
    )
    def test_installed_wheel_imports_and_registers_both_plugin_entrypoints(self):
        script = r"""
import importlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import time
from concurrent.futures import ThreadPoolExecutor

expected_version, expected_description, source_root = sys.argv[1:4]
distribution = importlib.metadata.distribution("hermes-omp-acp")
if distribution.version != expected_version:
    raise AssertionError(f"installed version {distribution.version!r} != {expected_version!r}")
if distribution.metadata["Summary"] != expected_description:
    raise AssertionError("installed description does not match plugin.yaml")

with tempfile.TemporaryDirectory(prefix="hermes-omp-packaging-") as temp_home:
    home = Path(temp_home)
    repository = home / "repository"
    repository.mkdir()
    signals = home / "signals"
    signals.mkdir()
    agent_dir = home / "omp"
    agent_dir.mkdir()
    os.environ["PI_CODING_AGENT_DIR"] = str(agent_dir)
    for args in (["init", "-q"], ["config", "user.name", "Package Test"],
                 ["config", "user.email", "package@example.invalid"]):
        subprocess.run(["git", "-C", str(repository), *args], check=True)
    (repository / "tracked").write_text("base")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "initial"], check=True)
    (repository / "tracked").write_text("dirty source")
    peer = Path(source_root) / "tests" / "task_acp_peer.py"
    (home / "config.yaml").write_text(json.dumps({"plugins": {
        "enabled": ["omp-acp-provider", "omp-task"],
        "entries": {"omp-task": {"settings": {
            "model_id": "fixture/model", "allowed_roots": [str(repository)],
            "command": sys.executable, "args": [str(peer), "normal", str(signals)],
            "mcp_enabled": False, "timeout_seconds": 15,
        }}},
    }}))
    os.environ["HERMES_HOME"] = str(home)
    # Match Hermes CLI bootstrap: config is initialized before PluginManager.
    # Importing plugins first hits an upstream config/provider discovery cycle.
    import hermes_cli.config

    import hermes_omp_acp

    package_dir = Path(distribution.locate_file("hermes_omp_acp")).resolve()
    module_path = Path(hermes_omp_acp.__file__).resolve()
    if not module_path.is_relative_to(package_dir):
        raise AssertionError(f"package imported outside the installed distribution: {module_path}")
    if module_path.is_relative_to(Path(source_root).resolve() / "hermes_omp_acp"):
        raise AssertionError(f"package imported from the source checkout: {module_path}")

    plugin_entrypoints = [
        entrypoint
        for entrypoint in distribution.entry_points
        if entrypoint.group == "hermes_agent.plugins"
        and entrypoint.name in {"omp-acp-provider", "omp-task"}
    ]
    if len(plugin_entrypoints) != 2 or {entrypoint.name for entrypoint in plugin_entrypoints} != {
        "omp-acp-provider", "omp-task"
    }:
        raise AssertionError(f"unexpected plugin entry points: {[ep.name for ep in plugin_entrypoints]}")
    entrypoints = {entrypoint.name: entrypoint for entrypoint in plugin_entrypoints}
    if entrypoints["omp-acp-provider"].value != "hermes_omp_acp.provider:register":
        raise AssertionError("the existing provider entry point target changed")
    if entrypoints["omp-task"].value != "hermes_omp_acp.tool_plugin":
        raise AssertionError("omp-task must target its module, not a callable")

    provider_register = entrypoints["omp-acp-provider"].load()
    if not callable(provider_register):
        raise AssertionError("provider entry point did not load a callable")
    required_provider_args = [
        parameter for parameter in inspect.signature(provider_register).parameters.values()
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ) and parameter.default is inspect.Parameter.empty
    ]
    if required_provider_args:
        raise AssertionError("provider entry point must remain zero-argument")

    tool_module = entrypoints["omp-task"].load()
    if not isinstance(tool_module, types.ModuleType):
        raise AssertionError("omp-task entry point did not load a module")
    if not callable(getattr(tool_module, "register", None)):
        raise AssertionError("omp-task module does not expose register(ctx)")
    tool_module_path = Path(tool_module.__file__).resolve()
    if not tool_module_path.is_relative_to(package_dir):
        raise AssertionError(f"tool entry point imported outside the installed distribution: {tool_module_path}")
    if tool_module_path.is_relative_to(Path(source_root).resolve() / "hermes_omp_acp"):
        raise AssertionError(f"tool entry point imported from the source checkout: {tool_module_path}")

    from hermes_cli.plugins import PluginManager
    manager = PluginManager()
    manager.discover_and_load()
    plugins = {plugin["name"]: plugin for plugin in manager.list_plugins()}
    provider = plugins.get("omp-acp-provider")
    if provider is None or provider["kind"] != "model-provider" or not provider["enabled"] or provider["error"]:
        raise AssertionError(f"provider entry point was mishandled by the general loader: {provider!r}")
    task = plugins.get("omp-task")
    if task is None or not task["enabled"] or task["error"] or task["tools"] != 1:
        raise AssertionError(f"general tool entry point did not register omp_task: {task!r}")

    from providers import get_provider_profile
    profile = get_provider_profile("omp-acp")
    if profile is None or profile.name != "omp-acp":
        raise AssertionError("the provider registry did not discover omp-acp-provider")

    from tools.registry import registry
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(registry.dispatch, "omp_task",
            {"repository": str(repository), "goal": "installed tool request"},
            scope=str(home.resolve()))
        deadline = time.monotonic() + 20
        prompt = None
        while time.monotonic() < deadline:
            protocol = signals / "protocol.jsonl"
            if protocol.exists():
                rows = [json.loads(line) for line in protocol.read_text().splitlines(keepends=True)
                        if line.endswith("\n")]
                prompt = next((row for row in rows if row["method"] == "session/prompt"), None)
                if prompt:
                    (signals / f"continue-{prompt['pid']}").touch()
                    break
            if future.done():
                raise AssertionError(f"installed tool stopped before prompt: {future.result()}")
            time.sleep(0.01)
        if prompt is None:
            raise AssertionError("installed tool did not reach its ACP prompt barrier")
        result = json.loads(future.result(timeout=20))
    if result["status"] != "success" or result["response"] != "installed tool request":
        raise AssertionError(result)
    if result["path"] != prompt["requested_cwd"] or result["path"] != prompt["actual_cwd"]:
        raise AssertionError("installed tool did not launch in its retained worktree")
    if (repository / "tracked").read_text() != "dirty source":
        raise AssertionError("installed tool modified the source checkout")
    try:
        os.kill(prompt["pid"], 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("installed tool returned before reaping its ACP peer")
"""
        result = subprocess.run(
            [
                sys.executable,
                "-P",
                "-c",
                script,
                self.version,
                self.description,
                str(self.root),
            ],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    @unittest.skipUnless(
        os.environ.get(ENTRYPOINT_ENV) == "1",
        f"set {ENTRYPOINT_ENV}=1 in the installed Hermes runtime to check the directory plugin wrapper",
    )
    def test_installed_wheel_loads_standalone_directory_tool_plugin(self):
        script = r"""
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

source_root = Path(sys.argv[1]).resolve()
distribution = importlib.metadata.distribution("hermes-omp-acp")
package_dir = Path(distribution.locate_file("hermes_omp_acp")).resolve()

with tempfile.TemporaryDirectory(prefix="hermes-omp-directory-plugin-") as temp_home:
    home = Path(temp_home)
    plugins = home / "plugins"
    plugins.mkdir()
    plugin_dir = plugins / "omp-task"
    shutil.copytree(source_root / "plugins" / "omp-task", plugin_dir)
    repository = home / "repository"
    repository.mkdir()
    (home / "config.yaml").write_text(json.dumps({"plugins": {
        "enabled": ["omp-task"],
        "entries": {"omp-task": {"settings": {
            "model_id": "fixture/model", "allowed_roots": [str(repository)],
            "command": sys.executable, "args": ["acp"],
            "mcp_enabled": False, "timeout_seconds": 15,
        }}},
    }}))
    os.environ["HERMES_HOME"] = str(home)
    import hermes_cli.config
    import hermes_omp_acp
    from hermes_cli.plugins import PluginManager

    package_path = Path(hermes_omp_acp.__file__).resolve()
    if not package_path.is_relative_to(package_dir):
        raise AssertionError(f"tool implementation imported outside the installed distribution: {package_path}")
    if package_path.is_relative_to(source_root / "hermes_omp_acp"):
        raise AssertionError(f"tool implementation imported from the source checkout: {package_path}")

    manager = PluginManager()
    manager.discover_and_load()
    task = next((plugin for plugin in manager.list_plugins() if plugin["name"] == "omp-task"), None)
    if task is None or task["source"] != "user" or not task["enabled"] or task["error"] or task["tools"] != 1:
        raise AssertionError(f"directory wrapper did not register omp_task: {task!r}")
    wrapper_module = sys.modules.get("hermes_plugins.omp_task")
    if wrapper_module is None or Path(wrapper_module.__file__).resolve() != plugin_dir / "__init__.py":
        raise AssertionError("Hermes did not load the installed directory wrapper")
"""
        result = subprocess.run(
            [
                sys.executable,
                "-P",
                "-c",
                script,
                str(self.root),
            ],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
