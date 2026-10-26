"""Structured ACP failures shared by the runtime and provider policy."""

from __future__ import annotations

from typing import Literal

FailureKind = Literal["deterministic", "cancelled", "pre_dispatch_transport", "outcome_unknown"]


class _ACPFailureContext:
    def _set_context(
        self,
        *,
        phase: str,
        method: str | None,
        rpc_code: int | str | None,
        exit_code: int | None,
        prompt_dispatched: bool | None,
        failure_kind: FailureKind,
        cause: BaseException | None,
    ) -> None:
        self.phase = phase
        self.method = method
        self.rpc_code = rpc_code
        self.exit_code = exit_code
        self.prompt_dispatched = prompt_dispatched
        self.failure_kind = failure_kind
        self.cause = cause
        if cause is not None:
            self.__cause__ = cause


class ACPError(_ACPFailureContext, RuntimeError):
    """A classified ACP failure with bounded, non-secret request context."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        method: str | None = None,
        rpc_code: int | str | None = None,
        exit_code: int | None = None,
        prompt_dispatched: bool | None = None,
        failure_kind: FailureKind = "deterministic",
        cause: BaseException | None = None,
    ):
        super().__init__(message)
        self._set_context(
            phase=phase,
            method=method,
            rpc_code=rpc_code,
            exit_code=exit_code,
            prompt_dispatched=prompt_dispatched,
            failure_kind=failure_kind,
            cause=cause,
        )


class ACPTimeoutError(_ACPFailureContext, TimeoutError):
    """A completion deadline error with the same structured ACP context."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        method: str | None = None,
        rpc_code: int | str | None = None,
        exit_code: int | None = None,
        prompt_dispatched: bool | None = None,
        failure_kind: FailureKind,
        cause: BaseException | None = None,
    ):
        super().__init__(message)
        self._set_context(
            phase=phase,
            method=method,
            rpc_code=rpc_code,
            exit_code=exit_code,
            prompt_dispatched=prompt_dispatched,
            failure_kind=failure_kind,
            cause=cause,
        )


class ACPCancelledError(_ACPFailureContext, InterruptedError):
    """A caller-initiated ACP cancellation recognized by Hermes interruption handling."""

    def __init__(
        self,
        message: str = "OMP ACP request cancelled.",
        *,
        phase: str = "cancel",
        method: str | None = None,
        rpc_code: int | str | None = None,
        exit_code: int | None = None,
        prompt_dispatched: bool | None = None,
        cause: BaseException | None = None,
    ):
        super().__init__(message)
        self._set_context(
            phase=phase,
            method=method,
            rpc_code=rpc_code,
            exit_code=exit_code,
            prompt_dispatched=prompt_dispatched,
            failure_kind="cancelled",
            cause=cause,
        )
