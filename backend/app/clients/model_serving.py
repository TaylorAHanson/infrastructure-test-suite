"""Thin Model Serving (Foundation Model API) client.

Uses the Databricks SDK's ``serving_endpoints.query`` so auth is handled the
same way as every other Databricks call: the app service principal's OAuth
identity in a Databricks App, or the local CLI profile in dev. No tokens or
endpoint URLs are hand-rolled here.

Kept deliberately small - one synchronous ``chat`` helper. Callers off the
request path (the poller, the authoring service) already run in worker threads.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import get_workspace_client, settings

logger = logging.getLogger(__name__)


class ModelServingError(Exception):
    """Raised when a serving-endpoint chat call fails."""


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
