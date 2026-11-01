"""Check the Nix-installed artifact, without Hermes or model credentials."""
from importlib.metadata import distribution, version
from importlib.resources import files
from pathlib import Path

from packaging.requirements import Requirement

from hermes_omp_acp.skill_resources import skill_directory

provider = distribution("hermes-omp-acp")
assert version("agent-client-protocol") == "0.9.0"
requirements = [Requirement(value) for value in provider.requires or []]
assert any(
    req.name == "agent-client-protocol" and version(req.name) in req.specifier
    for req in requirements
)
entries = {
    (entry.group, entry.name): entry.value
    for entry in provider.entry_points
}
assert entries[("hermes_agent.plugins", "omp-acp-provider")] == "hermes_omp_acp.provider:register"
assert entries[("hermes_agent.plugins", "omp-acp-skills")] == "hermes_omp_acp.skills_plugin"
assert entries[("console_scripts", "hermes-omp-acp-skills")] == "hermes_omp_acp.skill_resources:main"
assert files("hermes_omp_acp").joinpath("skills/omp-acp/SKILL.md").is_file()
external = skill_directory()
assert (external / "omp-web-search" / "SKILL.md").is_file()
assert str(external).startswith("/nix/store/")
assert Path(provider.locate_file("hermes_omp_acp/provider.py")).is_file()
print(f"Installed {provider.metadata['Name']} {provider.version}; SDK 0.9.0; entry points and both skill trees verified")
