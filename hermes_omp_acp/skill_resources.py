"""Locate provider-bundled skills for Hermes's ``skills.external_dirs`` setting."""

from pathlib import Path
import sys


def skill_directory() -> Path:
    """Return the shared skill root without reading or changing a Hermes profile."""
    package = Path(__file__).resolve().parent
    # Hermes excludes site-packages ancestors from skill discovery.
    bundled = Path(sys.prefix) / "share" / "hermes-omp-acp" / "skills"
    if (bundled / "omp-web-search" / "SKILL.md").is_file():
        return bundled
    # Source and editable installations retain the repository's skill tree.
    source = package.parent / "skills"
    if (source / "omp-web-search" / "SKILL.md").is_file():
        return source
    raise FileNotFoundError("The hermes-omp-acp distribution is missing its omp-web-search skill")


def main() -> None:
    """Print the external skill directory; installation/activation remains explicit."""
    print(skill_directory())


if __name__ == "__main__":
    main()
