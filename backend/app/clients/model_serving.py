"""Thin Model Serving (Foundation Model API) client.

Uses the Databricks SDK's ``serving_endpoints.query`` so auth is handled the
same way as every other Databricks call: the app service principal's OAuth
identity in a Databricks App, or the local CLI profile in dev. No tokens or
endpoint URLs are hand-rolled here.

Kept deliberately small - one synchronous ``chat`` helper. Callers off the
request path (the poller, the authoring service) already run in worker threads.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.config import get_workspace_client, settings

logger = logging.getLogger(__name__)


class ModelServingError(Exception):
    """Raised when a serving-endpoint chat call fails."""


@dataclass
class ToolCall:
    """One tool the model asked to call in a tool-calling turn (ADR-0019)."""

    name: str
    arguments: dict
    id: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class ModelReply:
    """A model reply that may be a final answer, a set of tool calls, or both."""

    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)


def default_endpoint() -> Optional[str]:
    return settings.MODEL_SERVING_ENDPOINT


def chat(
    messages: list[dict],
    endpoint: Optional[str] = None,
    max_tokens: int = 1500,
    temperature: float = 0.1,
) -> str:
    """Send chat messages to a Foundation Model endpoint, return the text reply.

    ``messages`` is a list of ``{"role": "system"|"user"|"assistant",
    "content": str}`` dicts. Raises :class:`ModelServingError` on any failure so
    callers can degrade gracefully.
    """
    name = endpoint or default_endpoint()
    if not name:
        raise ModelServingError(
            "No Model Serving endpoint configured (MODEL_SERVING_ENDPOINT)."
        )

    try:
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

        client = get_workspace_client()
        role_map = {
            "system": ChatMessageRole.SYSTEM,
            "user": ChatMessageRole.USER,
            "assistant": ChatMessageRole.ASSISTANT,
        }
        sdk_messages = [
            ChatMessage(role=role_map.get(m.get("role", "user"), ChatMessageRole.USER),
                        content=m.get("content", ""))
            for m in messages
        ]
        resp = client.serving_endpoints.query(
            name=name,
            messages=sdk_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choices = getattr(resp, "choices", None) or []
        if not choices:
            raise ModelServingError(f"Endpoint '{name}' returned no choices.")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message else None
        if not content:
            raise ModelServingError(f"Endpoint '{name}' returned an empty message.")
        return content
    except ModelServingError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Model serving chat failed on '%s': %s", name, exc)
        raise ModelServingError(str(exc)) from exc


def chat_with_tools(
    messages: list[dict],
    tools: Optional[list[dict]] = None,
    tool_choice: Any = None,
    endpoint: Optional[str] = None,
    max_tokens: int = 1500,
    temperature: float = 0.1,
) -> ModelReply:
    """Query the pinned endpoint with a tool schema, returning any ``tool_calls``.

    This is the tool-calling counterpart of :func:`chat` (ADR-0019). ``tools`` is
    a list of OpenAI-style function schemas and ``tool_choice`` (e.g. ``"auto"``)
    is forwarded as-is. The reply carries the assistant ``content`` and/or the
    ``tool_calls`` the model chose.

    Note: the Databricks SDK's typed ``ChatMessage`` drops ``tool_calls`` on
    deserialization, so we issue the same ``POST /serving-endpoints/{name}/
    invocations`` call ``serving_endpoints.query()`` makes and read the raw
    OpenAI-shaped response, which preserves ``choices[].message.tool_calls``.
    """
    name = endpoint or default_endpoint()
    if not name:
        raise ModelServingError(
            "No Model Serving endpoint configured (MODEL_SERVING_ENDPOINT)."
        )

    body: dict = {
        "messages": [{"role": m.get("role", "user"), "content": m.get("content", ""),
                      **({"tool_call_id": m["tool_call_id"]} if m.get("tool_call_id") else {}),
                      **({"name": m["name"]} if m.get("name") else {})}
                     for m in messages],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice

    try:
        client = get_workspace_client()
        raw = client.api_client.do(
            "POST", f"/serving-endpoints/{name}/invocations",
            body=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        return _parse_reply(raw, name)
    except ModelServingError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Model serving tool-call failed on '%s': %s", name, exc)
        raise ModelServingError(str(exc)) from exc


def _parse_reply(raw: Any, name: str) -> ModelReply:
    """Turn a raw OpenAI-shaped chat response into a :class:`ModelReply`."""
    choices = (raw or {}).get("choices") if isinstance(raw, dict) else None
    if not choices:
        raise ModelServingError(f"Endpoint '{name}' returned no choices.")
    message = choices[0].get("message") or {}
    tool_calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except (json.JSONDecodeError, ValueError):
                args = {}
        tool_calls.append(ToolCall(name=fn.get("name", ""), arguments=args or {},
                                   id=tc.get("id", "")))
    return ModelReply(content=message.get("content"), tool_calls=tool_calls)
