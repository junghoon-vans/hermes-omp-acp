"""Reject a sealed host that cannot satisfy the installed provider's requirements."""
from importlib.metadata import PackageNotFoundError, distribution, version

from packaging.requirements import Requirement

provider = distribution("hermes-omp-acp")
for declared in provider.requires or []:
    requirement = Requirement(declared)
    if requirement.marker and not requirement.marker.evaluate():
        continue
    try:
        installed = version(requirement.name)
    except PackageNotFoundError:
        installed = None
    if requirement.url or installed is None or installed not in requirement.specifier:
        raise RuntimeError(
            f"Sealed Hermes runtime cannot satisfy {declared}: "
            f"installed {installed or 'missing'}. Use a compatible Hermes dependency group; "
            "do not inject a second SDK or disable its package collision guard."
        )

# Metadata alone must not conceal a broken SDK installation.
import acp  # noqa: E402,F401

print(f"Sealed runtime satisfies {provider.metadata['Name']} {provider.version} requirements")
