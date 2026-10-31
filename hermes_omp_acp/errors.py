"""Structured ACP failures shared by the runtime and provider policy."""

from __future__ import annotations

import asyncio
from typing import Literal

from acp.exceptions import RequestError
from agent.redact import redact_sensitive_text

_DETAIL_LIMIT_BYTES = 8192
_PUBLIC_MESSAGE_LIMIT_BYTES = 16 * 1024
_DETAIL_SUPPRESSED = "[ACP diagnostic suppressed: unavailable or over limit]"


def _safe_detail(text: str, *, limit_bytes: int = _DETAIL_LIMIT_BYTES) -> str:
    """Return a forced-redacted complete detail, or suppress it as a whole."""
    if (
        type(limit_bytes) is not int
        or not 0 <= limit_bytes <= _PUBLIC_MESSAGE_LIMIT_BYTES
        or type(text) is not str
        or len(text) > limit_bytes
    ):
        return _DETAIL_SUPPRESSED
    try:
        raw_bytes = text.encode("utf-8")
    except UnicodeEncodeError:
        return _DETAIL_SUPPRESSED
    if len(raw_bytes) > limit_bytes:
        return _DETAIL_SUPPRESSED

    try:
        redacted = redact_sensitive_text(text, force=True)
    except Exception:
        return _DETAIL_SUPPRESSED
    if type(redacted) is not str or len(redacted) > limit_bytes:
        return _DETAIL_SUPPRESSED
    try:
        redacted_bytes = redacted.encode("utf-8")
    except UnicodeEncodeError:
        return _DETAIL_SUPPRESSED
    if len(redacted_bytes) > limit_bytes:
        return _DETAIL_SUPPRESSED
    return redacted


def _safe_type_name(cause: BaseException) -> str:
    try:
        name = type(cause).__name__
    except Exception:
        return "unknown exception"
    if type(name) is str and len(name) <= 64 and name.isascii() and name.isidentifier():
        return name
    return "unknown exception"


def _safe_cause(cause: BaseException | None) -> BaseException | None:
    """Copy only a recognized failure category and never retain its original chain."""
    if cause is None:
        return None
    if isinstance(cause, asyncio.CancelledError):
        return asyncio.CancelledError(_safe_detail("ACP request was cancelled."))
    if isinstance(cause, FileNotFoundError):
        return FileNotFoundError(_safe_detail("ACP executable could not be found."))
    if isinstance(cause, RequestError):
        try:
            code = vars(cause).get("code")
        except Exception:
            code = None
        if type(code) is not int:
            return RuntimeError(
                _safe_detail(f"Unclassified {_safe_type_name(cause)} during ACP request.")
            )
        return RequestError(code, _safe_detail("ACP RPC request was rejected."))
    if isinstance(cause, ConnectionError):
        return ConnectionError(_safe_detail("ACP transport connection failed."))
    if isinstance(cause, TimeoutError):
        return TimeoutError(_safe_detail("ACP operation timed out."))
    if isinstance(cause, OSError):
        return OSError(_safe_detail("ACP operating-system operation failed."))
    return RuntimeError(
        _safe_detail(f"Unclassified {_safe_type_name(cause)} during ACP request.")
    )


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
        safe_cause = _safe_cause(cause)
        self.cause = safe_cause
        if safe_cause is not None:
            self.__cause__ = safe_cause


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
        super().__init__(_safe_detail(message, limit_bytes=_PUBLIC_MESSAGE_LIMIT_BYTES))
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
        super().__init__(_safe_detail(message, limit_bytes=_PUBLIC_MESSAGE_LIMIT_BYTES))
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
        super().__init__(_safe_detail(message, limit_bytes=_PUBLIC_MESSAGE_LIMIT_BYTES))
        self._set_context(
            phase=phase,
            method=method,
            rpc_code=rpc_code,
            exit_code=exit_code,
            prompt_dispatched=prompt_dispatched,
            failure_kind="cancelled",
            cause=cause,
        )
