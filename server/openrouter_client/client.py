from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_settings

logger = logging.getLogger("openpoke.server")

OpenRouterBaseURL = "https://openrouter.ai/api/v1"


class OpenRouterError(RuntimeError):
    """Raised when the OpenRouter API returns an error response."""


def _headers(*, api_key: Optional[str] = None) -> Dict[str, str]:
    settings = get_settings()
    key = (api_key or settings.openrouter_api_key or "").strip()
    if not key:
        raise OpenRouterError("Missing OpenRouter API key")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    return headers


def _build_messages(messages: List[Dict[str, str]], system: Optional[str]) -> List[Dict[str, str]]:
    if system:
        return [{"role": "system", "content": system}, *messages]
    return messages


def _handle_response_error(exc: httpx.HTTPStatusError) -> None:
    response = exc.response
    detail: str
    try:
        payload = response.json()
        detail = payload.get("error") or payload.get("message") or json.dumps(payload)
    except Exception:
        detail = response.text
    raise OpenRouterError(f"OpenRouter request failed ({response.status_code}): {detail}") from exc


async def request_chat_completion(
    *,
    model: str,
    messages: List[Dict[str, str]],
    system: Optional[str] = None,
    api_key: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    base_url: str = OpenRouterBaseURL,
) -> Dict[str, Any]:
    """Request a chat completion and return the raw JSON payload."""

    payload: Dict[str, object] = {
        "model": model,
        "messages": _build_messages(messages, system),
        "stream": False,
    }
    if tools:
        payload["tools"] = tools

    url = f"{base_url.rstrip('/')}/chat/completions"

    # One retry on timeout / transient upstream errors (5xx, 429). A single
    # slow LLM request should degrade to a slower reply, not a failed turn.
    last_error: Optional[Exception] = None
    for attempt in range(2):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    headers=_headers(api_key=api_key),
                    json=payload,
                    timeout=60.0,
                )
                if response.status_code in (429, 500, 502, 503, 504) and attempt == 0:
                    logger.warning(
                        f"OpenRouter transient {response.status_code} — retrying (attempt 2/2)"
                    )
                    last_error = OpenRouterError(f"transient {response.status_code}")
                    await asyncio.sleep(1.0)
                    continue
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    _handle_response_error(exc)
                return response.json()
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt == 0:
                    logger.warning("OpenRouter request timed out after 60s — retrying (attempt 2/2)")
                    continue
                raise OpenRouterError(f"OpenRouter request timed out twice: {exc}") from exc
            except httpx.HTTPStatusError as exc:  # pragma: no cover - handled above
                _handle_response_error(exc)
            except httpx.HTTPError as exc:
                raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc

    raise OpenRouterError(f"OpenRouter request failed: {last_error}")


__all__ = ["OpenRouterError", "request_chat_completion", "OpenRouterBaseURL"]
