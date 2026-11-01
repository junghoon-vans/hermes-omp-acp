#!/usr/bin/env python3
"""Check an installed Hermes runtime discovers the ACP provider and loads its skills."""

import json
from importlib import import_module, metadata
from importlib.resources import files
import os
from pathlib import Path
import tempfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def _requirement_for(requirements, name, *, extra=None):
    wanted = canonicalize_name(name)
    for raw in requirements or ():
        requirement = Requirement(raw)
        if canonicalize_name(requirement.name) != wanted:
            continue
        if requirement.marker is not None:
            environment = {"extra": extra or ""}
            if not requirement.marker.evaluate(environment):
                continue
        return requirement
    raise AssertionError(f"Missing declared requirement for {name!r} (extra={extra!r})")


def main():
    with tempfile.TemporaryDirectory(prefix="hermes-omp-acp-installed-") as temporary:
        root = Path(temporary).resolve()
        home = root / "home"
        hermes_home = home / ".hermes"
        home.mkdir()
        hermes_home.mkdir()
        os.environ.update({
            "HOME": str(home),
            "HERMES_HOME": str(hermes_home),
            "HERMES_REAL_HOME": str(home),
            "PI_CODING_AGENT_DIR": str(home / "omp"),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        os.chdir(root)

        provider = metadata.distribution("hermes-omp-acp")
        provider_requirements = [Requirement(raw) for raw in provider.requires or ()]
        sdk_requirement = _requirement_for(provider.requires, "agent-client-protocol")
        sdk_version = metadata.version("agent-client-protocol")
        assert sdk_requirement.specifier.contains(sdk_version, prereleases=True), (
            f"agent-client-protocol {sdk_version} does not satisfy provider requirement "
            f"{sdk_requirement}"
        )

        hermes = metadata.distribution("hermes-agent")
        hermes_acp_requirement = _requirement_for(
            hermes.requires, "agent-client-protocol", extra="acp"
        )
        assert hermes_acp_requirement.specifier.contains(sdk_version, prereleases=True), (
            f"agent-client-protocol {sdk_version} does not satisfy Hermes acp declaration "
            f"{hermes_acp_requirement}"
        )
        for requirement in provider_requirements:
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            if requirement.url is not None:
                raise AssertionError(
                    f"Cannot verify direct-URL provider requirement {requirement} from installed versions"
                )
            try:
                installed = metadata.version(requirement.name)
            except metadata.PackageNotFoundError:
                installed = None
            assert installed is not None and requirement.specifier.contains(
                installed, prereleases=True
            ), f"Installed {requirement.name} {installed or 'missing'} does not satisfy {requirement}"

        from hermes_omp_acp.skill_resources import skill_directory

        external = skill_directory().resolve()
        expected_external = (
            Path(provider.locate_file("")).resolve().parents[2] / "share/hermes-omp-acp/skills"
        )
        assert external == expected_external, (
            f"External skills do not come from the installed distribution: {external} != {expected_external}"
        )
        external_file = external / "omp-web-search" / "SKILL.md"
        assert external_file.is_file(), f"Missing installed external skill: {external_file}"
        external_content = external_file.read_text(encoding="utf-8-sig", errors="replace")
        hermes_home.joinpath("config.yaml").write_text(
            json.dumps({
                "plugins": {"enabled": ["omp-acp-provider", "omp-acp-skills"]},
                "skills": {"external_dirs": [str(external)]},
            }),
            encoding="utf-8",
        )

        from providers import get_provider_profile

        profile = get_provider_profile("omp-acp")
        assert profile is not None, "Hermes provider discovery did not register omp-acp"
        assert profile.auth_type == "external_process", profile
        assert profile.api_mode == "chat_completions", profile
        assert profile.base_url == "acp://omp", profile

        expected_provider = Path(provider.locate_file("hermes_omp_acp/provider.py")).resolve()
        provider_module = import_module("hermes_omp_acp.provider")
        assert Path(provider_module.__file__).resolve() == expected_provider, (
            "Provider import is shadowed instead of coming from the installed distribution: "
            f"{provider_module.__file__} != {expected_provider}"
        )

        expected_hermes = Path(hermes.locate_file("hermes_cli/plugins.py")).resolve()
        hermes_plugins = import_module("hermes_cli.plugins")
        assert Path(hermes_plugins.__file__).resolve() == expected_hermes, (
            "Hermes import is shadowed instead of coming from the installed distribution: "
            f"{hermes_plugins.__file__} != {expected_hermes}"
        )
        from hermes_cli.version_info import get_version_info

        hermes_version = get_version_info().base_version


        from hermes_cli.plugins import get_plugin_manager

        manager = get_plugin_manager()
        try:
            manager.discover_and_load()
            plugin_rows = {row["name"]: row for row in manager.list_plugins()}
            skills_plugin = plugin_rows.get("omp-acp-skills")
            assert skills_plugin is not None, f"Hermes did not discover omp-acp-skills: {plugin_rows}"
            assert skills_plugin["enabled"] and skills_plugin["error"] is None, skills_plugin

            from tools.skills_tool import skill_view, skills_list

            bundled_file = files("hermes_omp_acp").joinpath("skills/omp-acp/SKILL.md")
            bundled_content = bundled_file.read_text(encoding="utf-8-sig", errors="replace")
            bundled_view = json.loads(skill_view("omp-acp-skills:omp-acp", preprocess=False))
            assert bundled_view.get("success"), bundled_view
            assert bundled_view["content"].endswith(bundled_content), "Hermes did not load the bundled skill body"

            listing = json.loads(skills_list())
            assert listing.get("success"), listing
            listed_names = {skill["name"] for skill in listing.get("skills", [])}
            assert "omp-web-search" in listed_names, f"External skill missing from Hermes listing: {listing}"
            external_view = json.loads(skill_view("omp-web-search", preprocess=False))
            assert external_view.get("success"), external_view
            assert Path(external_view["skill_dir"]).resolve() == external_file.parent.resolve(), external_view
            assert external_view["content"] == external_content, "Hermes did not load the external skill body"

            print(
                f"Installed {provider.metadata['Name']} {provider.version}; Hermes {hermes_version}; "
                f"agent-client-protocol {sdk_version} satisfies provider and Hermes acp declarations; "
                "Hermes registered omp-acp and loaded bundled and external skill bodies"
            )
        finally:
            manager.unload()


if __name__ == "__main__":
    main()
