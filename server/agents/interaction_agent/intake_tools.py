"""Clinic intake & scheduling tools for the interaction agent.

Kept separate from tools.py so the core assistant tool set stays untouched;
tools.py registers these additively. Handlers return plain dicts — tools.py
wraps them into ToolResult (avoids a circular import).
"""

from typing import Any, Dict, Optional

from ...services import scheduling

INTAKE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_patient",
            "description": "Look up a clinic patient record by full name and date of birth. Call as soon as the caller has provided both.",
            "parameters": {
                "type": "object",
                "properties": {
                    "full_name": {"type": "string", "description": "Caller's full name."},
                    "date_of_birth": {"type": "string", "description": "Caller's date of birth, any clear format."},
                },
                "required": ["full_name", "date_of_birth"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Get open appointment slots for a given urgency window. Offer the caller 2-3 options, never the full list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "urgency_level": {
                        "type": "string",
                        "enum": ["same_day", "soon", "routine"],
                        "description": "same_day = today; soon = within 2-3 days; routine = next week or later.",
                    },
                    "preference": {"type": "string", "description": "Optional caller preference, e.g. 'mornings' or 'after 3pm'."},
                },
                "required": ["urgency_level"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book a slot that was offered by check_availability. Only call AFTER the caller explicitly confirms the day and time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "patient_name": {"type": "string", "description": "Patient full name."},
                    "slot_id": {"type": "string", "description": "The slot_id of the confirmed slot."},
                    "reason_for_visit": {"type": "string", "description": "Brief reason for visit for the doctor's note."},
                    "callback_number": {"type": "string", "description": "Confirmed callback phone number."},
                },
                "required": ["patient_name", "slot_id", "reason_for_visit", "callback_number"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_emergency",
            "description": "Log an emergency escalation when the caller may be in danger. Use together with telling the caller to hang up and call 911. Do not continue booking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "One-line summary of the possible emergency."},
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_to_human",
            "description": "Transfer the caller to clinic staff: caller asks for a person, situation is sensitive, you are uncertain, or a tool failed twice.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why the call is being handed off."},
                    "urgency": {"type": "string", "enum": ["immediate", "normal"], "description": "immediate for safety/sensitive issues."},
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]

_HANDLERS = {
    "lookup_patient": scheduling.lookup_patient,
    "check_availability": scheduling.check_availability,
    "book_appointment": scheduling.book_appointment,
    "escalate_emergency": scheduling.escalate_emergency,
    "transfer_to_human": scheduling.transfer_to_human,
}


# Dispatch an intake tool call; returns None when the tool is not an intake tool
def handle_intake_tool(name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return None
    return handler(**args)
