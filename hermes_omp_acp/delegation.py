"""Route selected model-map tasks through OMP ACP with repository metadata."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any
import uuid

from agent.delegation_provider import DelegationProvider, DelegationRouteError

from .workspaces import validate_repository


_REPOSITORY_PARAMETER = {
    "type": "string",
    "description": (
        "Absolute path to a Git repository root under the configured allowed roots. "
        "Required only when the selected model_map option routes to omp-acp."
    ),
}
_ROUTE_GUIDANCE = (
    "For tasks whose selected model_map option routes to omp-acp, provide `repository` as the "
    "absolute path to the Git repository root. It may be omitted for other routes."
)


class OmpWorktreeDelegationProvider(DelegationProvider):
    """Compose Hermes model_map routing and require repository context for OMP ACP."""

    @property
    def name(self) -> str:
        return "omp-worktree"

    @property
    def display_name(self) -> str:
        return "OMP Worktree"

    @property
    def summary(self) -> str:
        return "Route model_map tasks to OMP ACP with per-task Git worktrees."

    @staticmethod
    def _model_map_provider() -> DelegationProvider:
        try:
            from agent.delegation_registry import backend_status

            provider, reason = backend_status("model_map")
        except Exception as error:
            raise DelegationRouteError(
                f"Could not resolve the Hermes model_map delegation backend: {error}"
            ) from error

        if provider is None or reason:
            detail = reason or "No model_map provider was returned."
            raise DelegationRouteError(
                f"The omp-worktree backend requires an available model_map provider: {detail}"
            )
        return provider

    def is_available(self) -> bool:
        try:
            self._model_map_provider()
        except DelegationRouteError:
            return False
        return True

    def task_parameters(self, config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        provider = self._model_map_provider()
        parameters = provider.task_parameters(config)
        if not isinstance(parameters, Mapping):
            raise DelegationRouteError("The model_map backend returned an invalid task-parameter schema.")
        composed = dict(parameters)
        composed["repository"] = dict(_REPOSITORY_PARAMETER)
        return composed

    def description(self, config: Mapping[str, Any]) -> str:
        provider = self._model_map_provider()
        description = provider.description(config)
        if not isinstance(description, str):
            raise DelegationRouteError("The model_map backend returned an invalid task description.")
        return f"{description}\n{_ROUTE_GUIDANCE}" if description else _ROUTE_GUIDANCE

    @staticmethod
    def _allowed_roots(config: Mapping[str, Any]) -> list[str]:
        settings = config.get("backends") if isinstance(config, Mapping) else None
        own_settings = settings.get("omp-worktree") if isinstance(settings, Mapping) else None
        roots = own_settings.get("allowed_roots") if isinstance(own_settings, Mapping) else None
        if not isinstance(roots, list) or not roots:
            raise DelegationRouteError(
                "Configure delegation.backends.omp-worktree.allowed_roots as a non-empty list "
                "of absolute directory paths."
            )
        for root in roots:
            if not isinstance(root, str) or not root:
                raise DelegationRouteError(
                    "Every delegation.backends.omp-worktree.allowed_roots entry must be a "
                    "non-empty absolute path."
                )
            if not Path(root).is_absolute():
                raise DelegationRouteError(
                    "Every delegation.backends.omp-worktree.allowed_roots entry must be absolute."
                )
        return roots

    def route_task(self, task: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
        provider = self._model_map_provider()
        try:
            route = provider.route_task(task, config)
        except DelegationRouteError:
            raise
        except Exception as error:
            raise DelegationRouteError(f"Could not route the task through model_map: {error}") from error
        if not isinstance(route, Mapping):
            raise DelegationRouteError("The model_map backend returned an invalid task route.")

        result = dict(route)
        resolved_provider = result.get("provider")
        if not isinstance(resolved_provider, str) or resolved_provider.strip().lower() != "omp-acp":
            return result

        if "repository" not in task:
            raise DelegationRouteError(
                "Provide `repository` as an absolute Git repository root for tasks routed to omp-acp."
            )
        allowed_roots = self._allowed_roots(config)
        try:
            repository = validate_repository(task.get("repository"), allowed_roots)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise DelegationRouteError(f"Invalid repository for omp-acp: {error}") from error

        if result.get("fallback_providers"):
            raise DelegationRouteError(
                "The omp-acp route cannot use fallback_providers because its task metadata is local-only. "
                "Remove fallback providers from this model_map option."
            )

        request_overrides = result.get("request_overrides")
        if request_overrides is None:
            request_overrides = {}
        if not isinstance(request_overrides, Mapping):
            raise DelegationRouteError("The omp-acp route request_overrides must be an object.")
        try:
            overrides = deepcopy(dict(request_overrides))
        except Exception as error:
            raise DelegationRouteError(f"Could not copy omp-acp request_overrides: {error}") from error

        extra_body = overrides.get("extra_body")
        if extra_body is None:
            extra_body = {}
        if not isinstance(extra_body, Mapping):
            raise DelegationRouteError("The omp-acp route request_overrides.extra_body must be an object.")
        extra_body = dict(extra_body)
        extra_body["hermes_omp_acp"] = {
            "repository": str(repository),
            "task_id": uuid.uuid4().hex,
        }
        overrides["extra_body"] = extra_body
        result["request_overrides"] = overrides
        result["fallback_providers"] = []
        return result
