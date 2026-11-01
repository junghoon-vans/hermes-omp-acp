"""Expose the bundled OMP ACP guidance through Hermes's plugin skill registry."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def register(ctx: object) -> None:
    """Register the packaged skill when the host supports plugin skills."""
    register_skill = getattr(ctx, "register_skill", None)
    if not callable(register_skill):
        logger.warning(
            "Cannot register the bundled omp-acp skill: this Hermes PluginContext does not "
            "provide register_skill(); upgrade Hermes to a release with plugin skill support. "
            "The OMP ACP provider remains available."
        )
        return

    skill_path = Path(__file__).resolve().parent / "skills" / "omp-acp" / "SKILL.md"
    register_skill(
        "omp-acp",
        skill_path,
        "OMP ACP usage. Load with skill_view(name='omp-acp-skills:omp-acp').",
    )
