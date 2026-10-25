import re
import subprocess
import tempfile
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

try:
    from agent.delegation_provider import DelegationProvider, DelegationRouteError
    import_module("agent.delegation_registry")
except ModuleNotFoundError as error:
    if error.name not in {"agent.delegation_provider", "agent.delegation_registry"}:
        raise
    raise unittest.SkipTest(
        "Hermes delegation APIs are unavailable; omp-worktree routing is unsupported."
    )

from hermes_omp_acp.delegation import OmpWorktreeDelegationProvider


class _ModelMapProvider(DelegationProvider):
    """A small route-decision fixture; repository validation uses real Git repositories."""

    def __init__(self, routes):
        self.routes = routes

    @property
    def name(self):
        return "model_map"

    def task_parameters(self, config):
        return {"option": {"type": "string", "enum": ["fast", "deep"]}}

    def description(self, config):
        return "Choose a fast or deep route."

    def route_task(self, task, config):
        return dict(self.routes[task.get("option", "deep")])


class OmpWorktreeDelegationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.allowed_root = self.root / "allowed"
        self.allowed_root.mkdir()
        self.repository = self._create_repository(self.allowed_root / "project")
        self.outside_repository = self._create_repository(self.root / "outside")
        self.provider = OmpWorktreeDelegationProvider()
        self.config = {
            "backends": {
                "omp-worktree": {"allowed_roots": [str(self.allowed_root)]},
                "model_map": {"options": []},
            },
        }

    def _create_repository(self, path):
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True)
        subprocess.run([
            "git", "-C", str(path), "-c", "user.name=Hermes Test",
            "-c", "user.email=hermes@example.invalid", "commit", "-q", "-m", "initial",
        ], check=True)
        return path

    def _routes(self, deep=None):
        return {
            "fast": {"provider": "openai", "model": "fast-model", "label": "fast"},
            "deep": deep or {"provider": "openai", "model": "deep-model", "label": "deep"},
        }

    def test_omp_acp_route_adds_isolated_task_metadata_without_mutating_route(self):
        deep = {
            "provider": "omp-acp",
            "model": "omp/model",
            "request_overrides": {
                "temperature": 0.2,
                "extra_body": {
                    "keep": {"enabled": True},
                    "hermes_omp_acp": {"stale": True},
                },
            },
        }
        routes = self._routes(deep)
        model_map = _ModelMapProvider(routes)
        task = {"option": "deep", "repository": str(self.repository)}
        with patch("agent.delegation_registry.backend_status", return_value=(model_map, "")):
            first = self.provider.route_task(task, self.config)
            second = self.provider.route_task(task, self.config)

        first_metadata = first["request_overrides"]["extra_body"]["hermes_omp_acp"]
        second_metadata = second["request_overrides"]["extra_body"]["hermes_omp_acp"]
        self.assertEqual(first_metadata["repository"], str(self.repository.resolve()))
        self.assertRegex(first_metadata["task_id"], re.compile(r"^[0-9a-f]{32}$"))
        self.assertNotEqual(first_metadata["task_id"], second_metadata["task_id"])
        self.assertEqual(first["request_overrides"]["temperature"], 0.2)
        self.assertEqual(first["request_overrides"]["extra_body"]["keep"], {"enabled": True})
        self.assertEqual(first["fallback_providers"], [])
        self.assertEqual(
            deep["request_overrides"]["extra_body"]["hermes_omp_acp"],
            {"stale": True},
        )

    def test_omp_acp_route_requires_repository_inside_allowed_git_root(self):
        model_map = _ModelMapProvider(self._routes({"provider": "omp-acp", "model": "omp/model"}))
        invalid_repositories = (
            {},
            {"repository": str(self.outside_repository)},
            {"repository": str(self.allowed_root / "not-a-repository")},
        )
        with patch("agent.delegation_registry.backend_status", return_value=(model_map, "")):
            for repository_fields in invalid_repositories:
                with self.subTest(repository_fields=repository_fields):
                    with self.assertRaises(DelegationRouteError):
                        self.provider.route_task({"option": "deep", **repository_fields}, self.config)

    def test_omp_acp_route_refuses_fallback_providers(self):
        route = {"provider": "omp-acp", "model": "omp/model", "fallback_providers": ["openai"]}
        model_map = _ModelMapProvider(self._routes(route))
        with patch("agent.delegation_registry.backend_status", return_value=(model_map, "")):
            with self.assertRaises(DelegationRouteError):
                self.provider.route_task(
                    {"option": "deep", "repository": str(self.repository)}, self.config,
                )

    def test_model_map_unavailability_is_reported_without_a_route(self):
        with patch(
            "agent.delegation_registry.backend_status",
            return_value=(None, "The model_map backend is unavailable."),
        ):
            self.assertFalse(self.provider.is_available())
            with self.assertRaises(DelegationRouteError):
                self.provider.route_task({"option": "fast"}, self.config)

    def test_omp_acp_route_rejects_invalid_allowed_roots_configuration(self):
        model_map = _ModelMapProvider(self._routes({"provider": "omp-acp", "model": "omp/model"}))
        invalid_config = {
            "backends": {
                "omp-worktree": {"allowed_roots": ["relative/path"]},
                "model_map": {"options": []},
            },
        }
        with patch("agent.delegation_registry.backend_status", return_value=(model_map, "")):
            with self.assertRaises(DelegationRouteError):
                self.provider.route_task(
                    {"option": "deep", "repository": str(self.repository)}, invalid_config,
                )


if __name__ == "__main__":
    unittest.main()
