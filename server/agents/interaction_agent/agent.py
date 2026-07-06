"""Interaction agent helpers for prompt construction."""

from html import escape
from pathlib import Path
from typing import Dict, List, Optional

from ...services.execution import get_agent_roster

_prompt_path = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT = _prompt_path.read_text(encoding="utf-8").strip()

_scheduling_path = Path(__file__).parent / "scheduling_addendum.md"
SCHEDULING_ADDENDUM = _scheduling_path.read_text(encoding="utf-8").strip()

_voice_path = Path(__file__).parent / "voice_addendum.md"
VOICE_ADDENDUM = _voice_path.read_text(encoding="utf-8").strip()


# Load and return the system prompt, extended per channel
def build_system_prompt(channel: str = "text") -> str:
    """Return the system prompt: base persona + scheduling; voice style when on a call."""
    prompt = f"{SYSTEM_PROMPT}\n\n{SCHEDULING_ADDENDUM}"
    if channel == "voice":
        prompt = f"{prompt}\n\n{VOICE_ADDENDUM}"
    return prompt


# Build structured message with conversation history, active agents, and current turn
def prepare_message_with_history(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
    channel: str = "text",
    emergency_alert: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Compose a message that bundles history, roster, and the latest turn."""
    sections: List[str] = []

    sections.append(_render_conversation_history(transcript))
    sections.append(f"<active_agents>\n{_render_active_agents(latest_text, channel)}\n</active_agents>")
    if emergency_alert:
        sections.append(f"<emergency_screen_alert>\n{emergency_alert}\n</emergency_screen_alert>")
    sections.append(_render_current_turn(latest_text, message_type, channel))

    content = "\n\n".join(sections)
    return [{"role": "user", "content": content}]


# Format conversation transcript into XML tags for LLM context
def _render_conversation_history(transcript: str) -> str:
    history = transcript.strip()
    if not history:
        history = "None"
    return f"<conversation_history>\n{history}\n</conversation_history>"


# Render the execution agents relevant to this turn (the agent-overload fix).
# Small rosters are injected whole; past the threshold, semantic top-k +
# recently-active agents are selected. Any failure falls back to the full
# roster — correctness over efficiency.
def _render_active_agents(latest_text: str = "", channel: str = "text") -> str:
    from ...logging_config import logger
    from ...services.execution.selection import (
        SELECTION_THRESHOLD,
        select_recent_agents,
        select_relevant_agents,
    )

    roster = get_agent_roster()
    roster.load()
    records = roster.get_records()

    if not records:
        return "None"

    selected = records
    note = ""
    if len(records) > SELECTION_THRESHOLD and latest_text.strip():
        try:
            if channel == "voice":
                # Voice pays for latency: recency-only selection, no embedding call.
                selected = select_recent_agents(records)
            else:
                selected = select_relevant_agents(records, latest_text, roster)
            note = (
                f"\n<!-- showing {len(selected)} of {len(records)} agents most relevant to this "
                "turn; use search_agents to find others -->"
            )
        except Exception as exc:
            logger.warning(f"agent selection failed; injecting full roster: {exc}")
            selected = records

    rendered: List[str] = []
    for record in selected:
        name = escape(record["name"] or "agent", quote=True)
        rendered.append(f'<agent name="{name}" />')

    return "\n".join(rendered) + note


# Wrap the current message in appropriate XML tags based on sender type
def _render_current_turn(latest_text: str, message_type: str, channel: str = "text") -> str:
    tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    body = latest_text.strip()
    attrs = ' channel="voice"' if (channel == "voice" and tag == "new_user_message") else ""
    return f"<{tag}{attrs}>\n{body}\n</{tag}>"
