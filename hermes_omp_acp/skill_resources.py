"""Locate provider-bundled skills for Hermes's ``skills.external_dirs`` setting."""

from pathlib import Path


def skill_directory() -> Path:
    """Return the shared skill root without reading or changing a Hermes profile."""
    package = Path(__file__).resolve().parent
    bundled = package / "bundled_skills"
    if (bundled / "search" / "SKILL.md").is_file():
        return bundled
    # Source and editable installations retain the repository's skill tree.
    source = package.parent / "skills"
    if (source / "search" / "SKILL.md").is_file():
        return source
    raise FileNotFoundError("The hermes-omp-acp distribution is missing its search skill")


def main() -> None:
    """Print the external skill directory; installation/activation remains explicit."""
    print(skill_directory())


if __name__ == "__main__":
    main()
