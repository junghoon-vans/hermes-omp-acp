import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from hermes_omp_acp.skill_resources import skill_directory


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_INSPECTION = r'''
import json
import sys
from providers import get_provider_profile
from tools.skills_tool import skill_view, skills_list

result = {
    "listing": json.loads(skills_list()),
    "view": json.loads(skill_view("search", preprocess=False)),
}
if "provider" in sys.argv[1:]:
    profile = get_provider_profile("omp-acp")
    result["provider"] = profile.name if profile is not None else None
print(json.dumps(result))
'''


class SkillResourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hermes-omp-acp-skills-")
        self.addCleanup(temporary.cleanup)
        self.temp_root = Path(temporary.name)
        self.skill_root = skill_directory().resolve()
        self.search_dir = self.skill_root / "search"
        self.search_file = self.search_dir / "SKILL.md"
        self.assertTrue(self.search_file.is_file(), f"Missing bundled search skill: {self.search_file}")
        self.search_content = self.search_file.read_text(encoding="utf-8-sig", errors="replace")

    def _configure_home(self, name, *, external_dirs=None, local_search=None):
        home = self.temp_root / name
        home.mkdir(parents=True)

        if local_search is not None:
            local_skill = home / "skills" / "search"
            local_skill.mkdir(parents=True)
            (local_skill / "SKILL.md").write_text(local_search, encoding="utf-8")

        plugins_dir = home / "plugins" / "model-providers"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "omp-acp-provider").symlink_to(_REPOSITORY_ROOT, target_is_directory=True)

        config = {"plugins": {"enabled": ["omp-acp-provider"]}}
        if external_dirs is not None:
            config["skills"] = {"external_dirs": [str(path) for path in external_dirs]}
        (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
        return home

    def _inspect_home(self, home, *, discover_provider=True):
        environment = os.environ.copy()
        environment.update({
            "HERMES_HOME": str(home),
            "HOME": str(home),
            "PI_CODING_AGENT_DIR": str(home / "omp"),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        command = [sys.executable, "-c", _RUNTIME_INSPECTION]
        if discover_provider:
            command.append("provider")
        completed = subprocess.run(
            command,
            cwd=_REPOSITORY_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"Hermes runtime failed for {home}:\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            self.fail(
                f"Hermes runtime returned invalid JSON for {home}: {error}\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )

    def _assert_shared_search_loaded(self, result):
        listing = result["listing"]
        self.assertTrue(listing.get("success"), listing)
        self.assertIn("search", [skill.get("name") for skill in listing.get("skills", [])])

        view = result["view"]
        self.assertTrue(view.get("success"), view)
        self.assertEqual(Path(view["skill_dir"]).resolve(), self.search_dir.resolve())
        self.assertEqual(view["path"], "search/SKILL.md")
        self.assertEqual(view["content"], self.search_content)
        self.assertEqual(result.get("provider"), "omp-acp")

    def test_shared_skill_is_loaded_in_two_independent_profiles(self):
        for profile_name in ("profile-alpha", "profile-beta"):
            with self.subTest(profile=profile_name):
                home = self._configure_home(profile_name, external_dirs=[self.skill_root])
                result = self._inspect_home(home)
                self._assert_shared_search_loaded(result)

    def test_enabling_provider_does_not_connect_shared_skill_automatically(self):
        home = self._configure_home("profile-unconnected")
        result = self._inspect_home(home)

        self.assertEqual(result.get("provider"), "omp-acp")
        self.assertTrue(result["listing"].get("success"), result["listing"])
        view = result["view"]
        # A Hermes release may have its own same-named skill. It must not resolve to
        # the provider's external resource unless skills.external_dirs connects it.
        resolved_dir = Path(view["skill_dir"]).resolve() if view.get("skill_dir") else None
        self.assertNotEqual(resolved_dir, self.search_dir.resolve())

    def test_profile_local_search_takes_precedence_over_shared_resource(self):
        from agent import skill_utils
        if not hasattr(skill_utils, "TIER_EXTERNAL"):
            self.skipTest("Legacy Hermes refuses cross-root name collisions; tested separately")
        local_content = (
            "---\n"
            "name: search\n"
            "description: Profile-local search fixture.\n"
            "---\n\n"
            "This content comes from the profile-local search skill.\n"
        )
        home = self._configure_home(
            "profile-local-override",
            external_dirs=[self.skill_root],
            local_search=local_content,
        )
        result = self._inspect_home(home)

        self.assertEqual(result.get("provider"), "omp-acp")
        view = result["view"]
        self.assertTrue(view.get("success"), view)
        self.assertEqual(Path(view["skill_dir"]).resolve(), (home / "skills" / "search").resolve())
        self.assertEqual(view["content"], local_content)

    def test_legacy_hermes_refuses_local_external_name_collision(self):
        # Hermes before tiered precedence refuses cross-root collisions.
        from agent import skill_utils
        if hasattr(skill_utils, "TIER_EXTERNAL"):
            self.skipTest("Current Hermes uses tiered local/external precedence")
        local_content = "---\nname: search\ndescription: Local search.\n---\nLocal search.\n"
        home = self._configure_home(
            "legacy-local-collision", external_dirs=[self.skill_root], local_search=local_content,
        )
        result = self._inspect_home(home)
        self.assertFalse(result["view"].get("success"), result["view"])
        self.assertIn("Ambiguous skill name", result["view"]["error"])
