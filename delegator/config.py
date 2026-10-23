"""Validate settings supplied through the public Hermes PluginContext."""

import math
from pathlib import Path
import shutil


def load_config(ctx):
    """Resolve the installed tools without overriding OMP's configuration."""
    roots = ctx.get_config("allowed_roots", default=[])
    if not isinstance(roots, list) or not roots:
        raise ValueError("Configure a non-empty allowed_roots list in the omp-acp-delegator plugin settings.")
    allowed_roots = []
    for value in roots:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Every allowed_roots entry must be an absolute existing directory.")
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("Every allowed_roots entry must be an absolute existing directory.")
        try:
            path = path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError(f"Allowed repository root does not exist or cannot be resolved: {path}.") from error
        if not path.is_dir():
            raise ValueError(f"Allowed repository root is not a directory: {path}.")
        allowed_roots.append(str(path))

    timeout = ctx.get_config("timeout", default=1800)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("Worker timeout must be a positive finite number of seconds.")
    try:
        valid_timeout = math.isfinite(timeout) and timeout > 0
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        raise ValueError("Worker timeout must be a positive finite number of seconds.")

    command = ctx.get_config("omp_command", default="omp")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("omp_command must name the installed OMP executable.")
    omp = shutil.which(str(Path(command).expanduser()))
    if omp is None:
        raise RuntimeError("OMP is required: install it and make omp available on the gateway PATH, or set omp_command.")
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("Git is required and must be available on the gateway PATH.")

    from plugins.plugin_storage import plugin_data_dir

    return {
        "allowed_roots": allowed_roots,
        "command": [str(Path(omp).absolute()), "acp"],
        "git": str(Path(git).absolute()),
        "state_dir": str(plugin_data_dir(ctx.plugin_id) / "tasks"),
        "timeout": timeout,
    }
