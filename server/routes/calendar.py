"""Google Calendar connection endpoints (Composio OAuth)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..logging_config import logger
from ..services.calendar_client import calendar_status, initiate_calendar_connect

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.post("/connect", response_class=JSONResponse, summary="Start Google Calendar OAuth, returns redirect URL")
async def calendar_connect() -> JSONResponse:
    try:
        return JSONResponse(initiate_calendar_connect())
    except Exception as exc:
        logger.exception("calendar connect failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/status", response_class=JSONResponse, summary="Check Google Calendar connection status")
async def calendar_connection_status() -> JSONResponse:
    return JSONResponse(calendar_status())


__all__ = ["router"]
