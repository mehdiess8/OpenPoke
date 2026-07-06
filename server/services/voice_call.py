"""Call-scoped transcript for voice turns.

Voice conversations are isolated here instead of the main conversation log so
the chat UI stays clean. On call end (/voice/end) the transcript is summarized
into one recap message for the main chat, then cleared.

Single active call session (demo scope). Production would key sessions by
caller/callee identifiers to support concurrent calls.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..logging_config import logger

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CALL_LOG_PATH = _DATA_DIR / "active_call.log"


class CallSessionLog:
    """File-backed transcript of the currently active call."""

    def __init__(self, path: Path = _CALL_LOG_PATH) -> None:
        self._path = path

    def _append(self, tag: str, text: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%H:%M:%S")
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(f'<{tag} time="{stamp}">\n{text.strip()}\n</{tag}>\n')

    # Record what the caller said (transcribed speech)
    def record_caller(self, text: str) -> None:
        self._append("caller_message", text)

    # Record what the assistant replied (spoken via TTS)
    def record_reply(self, text: str) -> None:
        self._append("clinic_reply", text)

    # Record that the caller interrupted the last reply mid-speech.
    # The full intended reply stays in the log (honest record); this marker
    # tells the agent how much the caller actually HEARD before cutting in.
    def record_interruption(self, heard_text: str) -> None:
        heard = heard_text.strip() or "(nothing — cut off immediately)"
        self._append(
            "interruption",
            f'Caller interrupted the previous reply. They only heard: "{heard}"',
        )

    # Load the full transcript of the active call ("" when no call)
    def load_transcript(self) -> str:
        if not self._path.exists():
            return ""
        try:
            return self._path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning(f"[voice] failed to read call log: {exc}")
            return ""

    # End the call: clear the transcript
    def clear(self) -> None:
        try:
            if self._path.exists():
                self._path.unlink()
            logger.info("[voice] call session cleared")
        except Exception as exc:
            logger.warning(f"[voice] failed to clear call log: {exc}")


_call_session = CallSessionLog()


def get_call_session() -> CallSessionLog:
    """Get the singleton call session."""
    return _call_session
