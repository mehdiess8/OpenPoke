"""Voice call endpoint — same assistant, voice channel.

Unlike /chat/send (fire-and-forget + history polling), a caller is waiting on
the line, so /voice/send awaits the agent and returns the reply text for the
frontend to speak via TTS. Turns are recorded in the SAME conversation log as
text chat — one assistant, shared memory across channels.

A deterministic red-flag guard scans each utterance for emergency language and
injects an <emergency_screen_alert> into the agent's prompt. Code flags,
the model converses — conservative and explainable by design.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ..agents.interaction_agent.runtime import InteractionAgentRuntime
from ..config import get_settings
from ..logging_config import logger
from ..openrouter_client import request_chat_completion
from ..services.conversation import get_conversation_log
from ..services.voice_call import get_call_session

router = APIRouter(prefix="/voice", tags=["voice"])

_SUMMARY_SYSTEM_PROMPT = (
    "You summarize a clinic phone call into a short recap the assistant sends the user "
    "by text right after hanging up. Two or three sentences, plain text, warm but brief. "
    "Include the appointment day, time, and confirmation number if something was booked. "
    "If the call involved an emergency or a transfer to staff, say that plainly. "
    "Write in second person ('you called about...', 'you're booked for...')."
)

# Conservative emergency red flags. False positives are acceptable — the agent
# asks ONE clarifying question on a flag; it does not immediately jump to 911.
_RED_FLAG_PATTERNS = [
    r"chest (pain|pressure|tightness)",
    r"(can'?t|cannot|trouble|difficulty|hard to) breath",
    r"short(ness)? of breath",
    r"stroke",
    r"face (droop|numb)",
    r"slurr(ed|ing) speech",
    r"(unconscious|passed out|won'?t wake)",
    r"(severe|heavy|won'?t stop) bleed",
    r"bleeding (a lot|badly|heavily)",
    r"overdose",
    r"suicid|kill (myself|himself|herself)|end my life|(don'?t|do not) want to (live|be alive)",
    r"allergic reaction|anaphyla|throat (closing|swelling)",
    r"seizure|convuls",
    r"choking",
    r"heart attack",
    r"severe (pain|burn)",
]
_RED_FLAG_RE = re.compile("|".join(f"({p})" for p in _RED_FLAG_PATTERNS), re.IGNORECASE)


class VoiceRequest(BaseModel):
    message: str


class InterruptionRequest(BaseModel):
    heard: str = ""


# Scan an utterance for emergency language; return an alert string when flagged
def _detect_red_flags(text: str) -> Optional[str]:
    match = _RED_FLAG_RE.search(text)
    if match is None:
        return None
    matched = match.group(0)
    logger.warning(f"[voice] emergency red flag detected: '{matched}'")
    return (
        f"Possible emergency language detected in the caller's words: '{matched}'. "
        "Address this first per the safety rules."
    )


@router.post("/send", response_class=JSONResponse, summary="Handle one voice turn and return the reply to speak")
# Process a transcribed caller utterance and return the agent's spoken reply
async def voice_send(payload: VoiceRequest) -> JSONResponse:
    message = payload.message.strip()
    if not message:
        return JSONResponse({"ok": False, "error": "Empty message"}, status_code=400)

    alert = _detect_red_flags(message)

    try:
        runtime = InteractionAgentRuntime()
    except ValueError as exc:  # missing API key
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    logger.info(f"[voice] turn received ({len(message)} chars)")
    result = await runtime.execute(user_message=message, channel="voice", emergency_alert=alert)

    if not result.success:
        return JSONResponse(
            {
                "ok": False,
                "reply": "I'm sorry, I'm having a technical issue. Let me transfer you to our staff.",
                "error": result.error,
            },
            status_code=500,
        )

    return JSONResponse(
        {
            "ok": True,
            "reply": result.response,
            "emergency_flagged": alert is not None,
        }
    )


class TTSRequest(BaseModel):
    text: str


@router.post("/tts", summary="Synthesize a reply to speech via OpenRouter")
# Convert reply text to natural speech; the frontend falls back to browser TTS on failure
async def voice_tts(payload: TTSRequest) -> Response:
    text = payload.text.strip()
    if not text:
        return JSONResponse({"ok": False, "error": "Empty text"}, status_code=400)

    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            upstream = await client.post(
                "https://openrouter.ai/api/v1/audio/speech",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                json={
                    "model": settings.tts_model,
                    "input": text,
                    "voice": settings.tts_voice,
                    "response_format": "mp3",
                },
            )
        upstream.raise_for_status()
        logger.info(f"[voice] tts synthesized {len(text)} chars ({len(upstream.content)} bytes)")
        return Response(content=upstream.content, media_type="audio/mpeg")
    except Exception as exc:
        logger.warning(f"[voice] tts failed, frontend will fall back to browser voice: {exc}")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)


@router.post("/interrupted", response_class=JSONResponse, summary="Record that the caller interrupted the last reply")
# Annotate the call log with how much of the last reply was actually heard
async def voice_interrupted(payload: InterruptionRequest) -> JSONResponse:
    session = get_call_session()
    if not session.load_transcript():
        return JSONResponse({"ok": True, "detail": "No active call."})

    session.record_interruption(payload.heard)
    logger.info(f"[voice] barge-in recorded (heard {len(payload.heard.strip())} chars)")
    return JSONResponse({"ok": True})


class FollowupRequest(BaseModel):
    items: List[Dict[str, Any]] = []


@router.post("/followup", response_class=JSONResponse, summary="Hand unresolved call tasks to the text agent")
# Failed/timed-out call tasks are delivered to the interaction agent as a
# normal agent message — its standard pipeline (routing, delegation or its
# own tools, reply to the user) takes it from there. No special scaffolding.
async def voice_followup(payload: FollowupRequest) -> JSONResponse:
    if not payload.items:
        return JSONResponse({"ok": True, "detail": "Nothing outstanding."})

    lines = []
    for item in payload.items:
        if item.get("task"):
            lines.append(f"- caller request: {item['task']}")
        else:
            lines.append(
                f"- {item.get('tool', 'unknown')}({json.dumps(item.get('arguments', {}))[:200]}) — {item.get('problem', 'unknown problem')}"
            )
    message = (
        "Voice call assistant: the call just ended with unfinished business. The following "
        "could not be completed during the call, and the caller was told we would follow "
        "up by text:\n" + "\n".join(lines) + "\n\n"
        "These are requests from a phone caller whose identity was verified only by "
        "name and date of birth. Apply your normal judgment, safety rules, and draft-"
        "confirmation requirements. Decline anything inappropriate, suspicious, or "
        "outside clinic business, and say why."
    )

    try:
        runtime = InteractionAgentRuntime()
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    logger.info(f"[voice] {len(payload.items)} outstanding item(s) handed to the text agent")
    asyncio.create_task(runtime.handle_agent_message(message))
    return JSONResponse({"ok": True, "items": len(payload.items)})


@router.post("/end", response_class=JSONResponse, summary="End the active call and post a recap to the chat")
# Summarize the finished call into the main conversation log, then clear the session
async def voice_end() -> JSONResponse:
    session = get_call_session()
    transcript = session.load_transcript()

    if not transcript:
        return JSONResponse({"ok": True, "summary": None, "detail": "No active call to end."})

    settings = get_settings()
    try:
        response = await request_chat_completion(
            model=settings.summarizer_model,
            messages=[{"role": "user", "content": f"Call transcript:\n\n{transcript}"}],
            system=_SUMMARY_SYSTEM_PROMPT,
            api_key=settings.openrouter_api_key,
        )
        summary = (response.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as exc:
        logger.error(f"[voice] call summary failed: {exc}")
        summary = ""

    if not summary:
        # Never lose the record — fall back to a minimal note.
        summary = "We spoke on a call just now. If anything didn't get resolved, just text me here."

    get_conversation_log().record_reply(f"Call recap: {summary}")
    session.clear()

    logger.info("[voice] call ended, recap posted to chat")
    return JSONResponse({"ok": True, "summary": summary})


__all__ = ["router"]
