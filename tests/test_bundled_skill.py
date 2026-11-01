import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from hermes_omp_acp.skills_plugin import register

_RUNTIME_INSPECTION = r'''
import json
from importlib import metadata, resources
from pathlib import Path

from providers import get_provider_profile

# Keep provider discovery first: importing hermes_cli.plugins imports config, whose
# import-time provider enumeration can otherwise complete the one-shot provider scan
# before the plugin enablement readers are available.
profile_before = get_provider_profile("omp-acp")

from hermes_cli.plugins import get_plugin_manager
from tools.skills_tool import skill_view

resource = resources.files("hermes_omp_acp").joinpath("skills", "omp-acp", "SKILL.md")
assert resource.is_file(), f"Missing package resource: {resource}"
resource_path = Path(str(resource)).resolve()
resource_content = resource.read_text(encoding="utf-8")
distribution = metadata.distribution("hermes-omp-acp")
direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
is_editable = bool(direct_url.get("dir_info", {}).get("editable"))
resource_record = "hermes_omp_acp/skills/omp-acp/SKILL.md"
recorded_files = {item.as_posix() for item in distribution.files or ()}
if not is_editable:
    assert resource_record in recorded_files, (
        f"{resource_record} is missing from the installed wheel RECORD"
    )
    installed_resource_path = Path(distribution.locate_file(resource_record)).resolve()
    assert installed_resource_path.is_file(), (
        f"Installed wheel does not contain {resource_record}"
    )
    assert resource_path == installed_resource_path, (
        f"importlib.resources resolved {resource_path}, not the installed wheel resource "
        f"{installed_resource_path}"
    )

manager = get_plugin_manager()
manager.discover_and_load()
profile_after = get_provider_profile("omp-acp")
view = json.loads(skill_view("omp-acp-skills:omp-acp", preprocess=False))
print(json.dumps({
    "resource_path": str(resource_path),
    "resource_content": resource_content,
    "editable": is_editable,
    "recorded_resource": resource_record in recorded_files,
    "view": view,
    "provider_before": profile_before.name if profile_before is not None else None,
    "provider_after": profile_after.name if profile_after is not None else None,
}))
'''


class BundledSkillTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hermes-omp-acp-bundled-skill-")
        self.addCleanup(temporary.cleanup)
        self.temp_root = Path(temporary.name)

    def _configure_home(self, name, *, enable_skill=True, disable_skill=False):
        home = self.temp_root / name
        home.mkdir(parents=True)

        enabled = ["omp-acp-provider"]
        if enable_skill:
            enabled.append("omp-acp-skills")
        config = {"plugins": {"enabled": enabled, "disabled": []}}
        if disable_skill:
            config["plugins"]["disabled"].append("omp-acp-skills")
        (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
        return home

    def _inspect_home(self, home):
        environment = os.environ.copy()
        environment.update({
            "HERMES_HOME": str(home),
            "HOME": str(home),
            "PI_CODING_AGENT_DIR": str(home / "omp"),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        completed = subprocess.run(
            [sys.executable, "-P", "-c", _RUNTIME_INSPECTION],
            cwd=self.temp_root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"Hermes runtime failed for {home}:\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        try:
            result = json.loads(completed.stdout)
            result["_runtime_stderr"] = completed.stderr
            return result
        except json.JSONDecodeError as error:
            self.fail(
                f"Hermes runtime returned invalid JSON for {home}: {error}\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )

    def _assert_provider_survives_general_discovery(self, result):
        stderr = result.get("_runtime_stderr", "")
        diagnostic = (
            f"provider before={result.get('provider_before')!r}, "
            f"after={result.get('provider_after')!r}\n"
            f"Hermes stderr (last 4 KiB):\n{stderr[-4096:]}"
        )
        self.assertEqual(result["provider_before"], "omp-acp", diagnostic)
        self.assertEqual(result["provider_after"], "omp-acp", diagnostic)

    def _assert_no_local_skill_copy(self, home):
        local_skills = home / "skills"
        for skill_dir in ("omp-acp", "omp-acp-skills"):
            self.assertFalse(
                (local_skills / skill_dir / "SKILL.md").exists(),
                f"Plugin skill was copied into profile-local skills: {skill_dir}",
            )
        self.assertFalse(
            (local_skills / "omp-acp-skills" / "omp-acp" / "SKILL.md").exists(),
            "Namespaced plugin skill was copied into profile-local skills",
        )

    def test_enabled_companion_loads_namespaced_package_skill_and_keeps_provider(self):
        home = self._configure_home("profile-enabled")
        result = self._inspect_home(home)

        self._assert_provider_survives_general_discovery(result)
        self.assertTrue(Path(result["resource_path"]).is_file())
        view = result["view"]
        self.assertIs(view.get("success"), True, view)
        self.assertEqual(view.get("name"), "omp-acp-skills:omp-acp")
        self.assertTrue(view["content"].endswith(result["resource_content"]))
        if view.get("skill_dir"):
            self.assertEqual(Path(view["skill_dir"]).resolve(), Path(result["resource_path"]).parent)
        if not result["editable"]:
            self.assertTrue(result["recorded_resource"], "Installed wheel omitted the bundled skill resource")
        self._assert_no_local_skill_copy(home)

    def test_provider_alone_does_not_enable_companion_skill(self):
        home = self._configure_home("profile-provider-only", enable_skill=False)
        result = self._inspect_home(home)

        self._assert_provider_survives_general_discovery(result)
        self.assertIs(result["view"].get("success"), False, result["view"])
        self._assert_no_local_skill_copy(home)

    def test_disabling_companion_keeps_provider_but_hides_its_skill(self):
        home = self._configure_home("profile-disabled", disable_skill=True)
        result = self._inspect_home(home)

        self._assert_provider_survives_general_discovery(result)
        self.assertTrue(Path(result["resource_path"]).is_file())
        self.assertIs(result["view"].get("success"), False, result["view"])
        self._assert_no_local_skill_copy(home)

    def test_old_plugin_context_without_register_skill_warns_and_returns(self):
        with self.assertLogs("hermes_omp_acp.skills_plugin", level="WARNING") as captured:
            self.assertIsNone(register(SimpleNamespace()))

        warning = "\n".join(captured.output)
        self.assertIn("register_skill()", warning)
        self.assertIn("upgrade Hermes", warning)
        self.assertIn("provider remains available", warning)
