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

import re
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..agents.interaction_agent.runtime import InteractionAgentRuntime
from ..logging_config import logger

router = APIRouter(prefix="/voice", tags=["voice"])

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


__all__ = ["router"]
