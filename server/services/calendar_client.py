"""Google Calendar via Composio — connect flow + tool execution.

Mirrors the Gmail integration and reuses its Composio client singleton.
The connected user id is persisted to disk (unlike Gmail's in-memory id)
so --reload restarts don't drop the calendar connection.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import get_settings
from ..logging_config import logger
from .gmail.client import _get_composio_client, _normalize_tool_response

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CAL_STATE_PATH = _DATA_DIR / "calendar_connection.json"


def _load_state() -> Dict[str, str]:
    if _CAL_STATE_PATH.exists():
        try:
            return json.loads(_CAL_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: Dict[str, str]) -> None:
    _CAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CAL_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


# Start the Google Calendar OAuth flow; returns a redirect URL for the user
def initiate_calendar_connect() -> Dict[str, Any]:
    settings = get_settings()
    auth_config_id = settings.composio_google_calendar_auth_config_id or ""
    if not auth_config_id:
        return {"ok": False, "error": "Set COMPOSIO_GOOGLE_CALENDAR_AUTH_CONFIG_ID in .env"}

    user_id = f"calendar-{os.getpid()}"
    client = _get_composio_client()
    req = client.connected_accounts.link(user_id=user_id, auth_config_id=auth_config_id)

    _save_state(
        {
            "user_id": user_id,
            "connection_request_id": getattr(req, "id", None) or "",
        }
    )
    logger.info(f"[calendar] connect initiated for user_id={user_id}")
    return {
        "ok": True,
        "redirect_url": getattr(req, "redirect_url", None) or getattr(req, "redirectUrl", None),
        "user_id": user_id,
    }


# Check whether the calendar connection is active
def calendar_status() -> Dict[str, Any]:
    state = _load_state()
    connection_id = state.get("connection_request_id")
    if not connection_id:
        return {"ok": True, "connected": False, "detail": "Not connected yet."}

    try:
        client = _get_composio_client()
        account = client.connected_accounts.get(connection_id)
        status = str(getattr(account, "status", "")).upper()
        connected = "ACTIVE" in status
        return {"ok": True, "connected": connected, "status": status}
    except Exception as exc:
        logger.warning(f"[calendar] status check failed: {exc}")
        return {"ok": True, "connected": False, "detail": str(exc)}


# True when a calendar connection exists (cheap check used by scheduling)
def is_calendar_connected() -> bool:
    return bool(_load_state().get("connection_request_id"))


# Execute a Composio Google Calendar tool as the connected user
def execute_calendar_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state = _load_state()
    user_id = state.get("user_id")
    if not user_id:
        raise RuntimeError("Google Calendar is not connected.")

    prepared: Dict[str, Any] = {k: v for k, v in (arguments or {}).items() if v is not None}

    client = _get_composio_client()
    result = client.client.tools.execute(tool_name, user_id=user_id, arguments=prepared)
    return _normalize_tool_response(result)
