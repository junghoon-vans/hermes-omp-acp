"""Hermes message conversion for the OMP ACP client."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from agent.acp_openai_bridge import (
    completion_to_stream_chunks as _hermes_completion_to_stream_chunks,
    extract_tool_calls_from_text,
    render_tool_bridge_sections,
)

from .errors import ACPCancelledError, ACPError

_ROLE_LABELS = {
    "system": "System",
    "user": "User",
    "assistant": "Assistant",
    "tool": "Tool",
    "context": "Context",
}
_PROMPT_PREAMBLE = (
    "You are being used as the active ACP agent backend for Hermes.",
    "Use ACP capabilities to complete tasks.",
    (
        "IMPORTANT: If you take an action with a tool, you MUST output tool calls using "
        "<tool_call>{...}</tool_call> blocks with JSON exactly in OpenAI function-call shape."
    ),
    "If no tool is needed, answer normally.",
)


def format_messages_as_prompt(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: Any = None,
) -> str:
    sections: list[str] = [*_PROMPT_PREAMBLE, *render_tool_bridge_sections(tools, tool_choice)]
    transcript: list[str] = []
    for message in (item for item in messages if isinstance(item, dict)):
        role = str(message.get("role") or "unknown").strip().lower()
        if rendered := _render_message_content(message.get("content")):
            transcript.append(f"{_ROLE_LABELS.get(role, 'Context')}:\n{rendered}")
    if transcript:
        sections.append("Conversation transcript:\n\n" + "\n\n".join(transcript))
    sections.append("Continue the conversation from the latest user request.")
    return "\n\n".join(section.strip() for section in sections if section and section.strip())


def _render_message_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "").strip()
        if isinstance(content.get("content"), str):
            return content["content"].strip()
        return json.dumps(content, ensure_ascii=True)
    if isinstance(content, list):
        parts = [
            item if isinstance(item, str) else item["text"].strip()
            for item in content
            if isinstance(item, str)
            or (
                isinstance(item, dict)
                and isinstance(item.get("text"), str)
                and item["text"].strip()
            )
        ]
        return "\n".join(parts).strip()
    return str(content).strip()


def build_completion(
    response_text: str, reasoning: str, model: str | None, *, stop_reason: str
) -> Any:
    if stop_reason == "cancelled":
        raise ACPCancelledError("OMP ACP prompt cancelled.")
    if stop_reason == "max_turn_requests":
        # Hermes automatically continues a `length` response in a new completion.
        # Exhausting the ACP turn budget must not restart already-executed work.
        raise ACPError(
            "OMP ACP stopped because max_turn_requests was reached.",
            phase="prompt",
            method="session/prompt",
            prompt_dispatched=True,
        )
    if stop_reason not in {"end_turn", "max_tokens", "refusal"}:
        raise ACPError(
            f"Unsupported OMP ACP stop reason: {stop_reason!r}.",
            phase="prompt",
            method="session/prompt",
            prompt_dispatched=True,
        )
    if stop_reason == "end_turn":
        tool_calls, cleaned_text = extract_tool_calls_from_text(response_text)
    else:
        tool_calls, cleaned_text = [], response_text
    refusal = None
    if stop_reason == "refusal":
        refusal = cleaned_text or "The ACP agent refused to continue."
        cleaned_text = refusal
    message = SimpleNamespace(
        content=cleaned_text,
        tool_calls=tool_calls,
        reasoning=reasoning or None,
        reasoning_content=reasoning or None,
        reasoning_details=None,
        refusal=refusal,
    )
    finish_reason = "length" if stop_reason == "max_tokens" else "tool_calls" if tool_calls else "stop"
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
        model=model or "omp-acp",
        acp_stop_reason=stop_reason,
    )


def completion_to_stream_chunks(completion: Any) -> Any:
    chunks = _hermes_completion_to_stream_chunks(completion)
    for chunk in chunks:
        for choice in chunk.choices:
            choice.delta.refusal = completion.choices[0].message.refusal
            choice.delta.acp_stop_reason = completion.acp_stop_reason
    return chunks
