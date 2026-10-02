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
    """A model reply that may be a final answer, a set of tool calls, or both.

    ``reasoning`` is the model's thinking summary when a reasoning level is on
    and the model returns one; ``notes`` explain any adjustment made to the call.
    """

    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: Optional[str] = None
    notes: list[str] = field(default_factory=list)


def default_endpoint() -> Optional[str]:
    """The endpoint chosen in Settings → Assistant, else MODEL_SERVING_ENDPOINT."""
    try:
        from app.services.assistant_config import effective

        return effective().model
    except Exception:  # noqa: BLE001 - settings unreadable: fall back to the deployment's
        return settings.MODEL_SERVING_ENDPOINT

# Claude takes a thinking budget (newer Claude: adaptive thinking with an effort);
# GPT, Gemini and the open models take reasoning_effort.
_THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": 12000}
_REASONING_KEYS = ("reasoning_effort", "thinking", "budget_tokens", "output_config", "effort")

# What each endpoint has refused, learned from its 400s so later calls don't repeat them:
# "adaptive-thinking", "no-reasoning", "no-temperature".
_learned: dict[str, set[str]] = {}


def _reasoning_params(endpoint: str, level: str, max_tokens: int, learned: set[str]) -> dict:
    if level not in _THINKING_BUDGET or "no-reasoning" in learned:
        return {}
    if "claude" in endpoint.lower():
        budget = _THINKING_BUDGET[level]
        # Thinking counts against max_tokens and requires the default temperature.
        thinking = ({"thinking": {"type": "adaptive"}, "output_config": {"effort": level}}
                    if "adaptive-thinking" in learned
                    else {"thinking": {"type": "enabled", "budget_tokens": budget}})
        return {**thinking, "max_tokens": max_tokens + budget, "temperature": None}
    return {"reasoning_effort": level}


def _lesson(exc: Exception, sent: dict) -> Optional[str]:
    """What to change after an endpoint refused ``sent``, or None if it isn't a parameter refusal."""
    text = str(exc).lower()
    if "thinking.type.adaptive" in text and (sent.get("thinking") or {}).get("type") == "enabled":
        return "adaptive-thinking"
    if "temperature" in text and sent.get("temperature") is not None:
        return "no-temperature"
    if any(k in text for k in _REASONING_KEYS) and (sent.get("thinking") or sent.get("reasoning_effort")):
        return "no-reasoning"
    return None


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
    reasoning: str = "off",
) -> ModelReply:
    """Query the pinned endpoint with a tool schema, returning any ``tool_calls``.

    This is the tool-calling counterpart of :func:`chat` (ADR-0019). ``tools`` is
    a list of OpenAI-style function schemas and ``tool_choice`` (e.g. ``"auto"``)
    is forwarded as-is. The reply carries the assistant ``content`` and/or the
    ``tool_calls`` the model chose. ``reasoning`` (low/medium/high) asks the
    model to think first. When an endpoint refuses a parameter (a thinking
    style, the reasoning level, temperature) the call is retried without it,
    and the endpoint's refusals are remembered for later calls.

    Note: the Databricks SDK's typed ``ChatMessage`` drops ``tool_calls`` on
    deserialization, so we issue the same ``POST /serving-endpoints/{name}/
    invocations`` call ``serving_endpoints.query()`` makes and read the raw
    OpenAI-shaped response, which preserves ``choices[].message.tool_calls``.
    """
    name = endpoint or default_endpoint()
    if not name:
        raise ModelServingError(
            "No Model Serving endpoint configured: pick a model in Settings → Assistant "
            "or set MODEL_SERVING_ENDPOINT."
        )

    body: dict = {
        "messages": [_wire_message(m) for m in messages],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice

    def payload(learned: set[str]) -> dict:
        out = {**body, **_reasoning_params(name, reasoning, max_tokens, learned)}
        if "no-temperature" in learned:
            out["temperature"] = None
        return {k: v for k, v in out.items() if v is not None}

    learned = set(_learned.get(name, ()))
    try:
        for attempt in range(4):
            sent = payload(learned)
            try:
                raw = get_workspace_client().api_client.do(
                    "POST", f"/serving-endpoints/{name}/invocations", body=sent,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                break
            except Exception as exc:  # noqa: BLE001
                lesson = _lesson(exc, sent)
                if lesson is None or lesson in learned or attempt == 3:
                    raise
                logger.info("'%s' refused a parameter (%s); retrying: %s", name, lesson, exc)
                learned.add(lesson)
                _learned.setdefault(name, set()).add(lesson)
        reply = _parse_reply(raw, name)
        if reasoning in _THINKING_BUDGET and "no-reasoning" in learned:
            reply.notes = [f"{name} doesn't accept a reasoning level with tools, so this ran without one."]
        return reply
    except ModelServingError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Model serving tool-call failed on '%s': %s", name, exc)
        raise ModelServingError(str(exc)) from exc


def _wire_message(m: dict) -> dict:
    """One chat message in OpenAI wire shape, carrying tool-reply fields if set."""
    wire = {"role": m.get("role", "user"), "content": m.get("content", "")}
    if m.get("tool_calls"):
        wire["tool_calls"] = m["tool_calls"]
    if m.get("tool_call_id"):
        wire["tool_call_id"] = m["tool_call_id"]
    if m.get("name"):
        wire["name"] = m["name"]
    return wire


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
    content, reasoning = _split_content(message.get("content"))
    return ModelReply(content=content, tool_calls=tool_calls, reasoning=reasoning)


def _split_content(content: Any) -> tuple[Optional[str], Optional[str]]:
    """Text and reasoning from a message's content.

    Reasoning models return a list of blocks — ``{"type": "reasoning", "summary":
    [{"type": "summary_text", "text": ...}]}`` and ``{"type": "text", "text": ...}``
    — instead of a string."""
    if content is None or isinstance(content, str):
        return content, None
    if not isinstance(content, list):
        return str(content), None
    text, thoughts = [], []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text.append(str(block.get("text") or ""))
        elif block.get("type") == "reasoning":
            for part in block.get("summary") or []:
                if isinstance(part, dict) and part.get("text"):
                    thoughts.append(str(part["text"]))
    return ("".join(text) or None), ("\n\n".join(thoughts) or None)
