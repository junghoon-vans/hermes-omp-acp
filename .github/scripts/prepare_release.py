"""Validate a model-provider release before running compatibility jobs."""

import os
from pathlib import Path
import re
import subprocess
import tomllib


def prepare(tag: str, root: Path) -> None:
    match = re.fullmatch(
        r"model-provider/v([0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?)",
        tag,
    )
    if match is None:
        raise ValueError("Expected model-provider/vX.Y.Z (optional aN/bN/rcN)")
    version = match.group(1)
    directory = root / "model-provider"
    with (directory / "pyproject.toml").open("rb") as metadata:
        project = tomllib.load(metadata)["project"]
    if project["name"] != "hermes-omp-acp-provider" or project["version"] != version:
        raise ValueError(f"Tag {tag!r} does not match model-provider/pyproject.toml")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    prepare(os.environ["RELEASE_TAG"], root)
    environment = os.environ["PUBLISH_ENVIRONMENT"]
    if environment not in {"pypi", "testpypi"}:
        raise ValueError("Unsupported publishing environment")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"sha={sha}\nenvironment={environment}\n")
    print(f"Validated {os.environ['RELEASE_TAG']} at {sha} for {environment}")
