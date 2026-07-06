"""Clinic scheduling service — mock implementation.

Backs the intake tools (patient lookup, availability, booking, escalation,
human transfer). Slot/booking logic sits behind module-level functions so a
real calendar backend (Composio Google Calendar) can replace the mock without
touching the tool layer.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..logging_config import logger

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_BOOKINGS_PATH = _DATA_DIR / "clinic_bookings.json"
_ESCALATIONS_PATH = _DATA_DIR / "clinic_escalations.json"

# Mock patient records keyed by lowercase full name.
_MOCK_PATIENTS: Dict[str, Dict[str, str]] = {
    "mehdi essoussi": {
        "patient_id": "P-1001",
        "full_name": "Mehdi Essoussi",
        "date_of_birth": "1999-03-14",
        "phone_on_file": "437-998-9777",
    },
    "alice nguyen": {
        "patient_id": "P-1002",
        "full_name": "Alice Nguyen",
        "date_of_birth": "1988-11-02",
        "phone_on_file": "416-555-0134",
    },
    "james okafor": {
        "patient_id": "P-1003",
        "full_name": "James Okafor",
        "date_of_birth": "1975-06-21",
        "phone_on_file": "647-555-0192",
    },
}

# Slots offered in the current conversation, so book_appointment can resolve ids.
_OFFERED_SLOTS: Dict[str, Dict[str, str]] = {}

_SLOT_TIMES = ["9:20 AM", "10:40 AM", "1:30 PM", "2:40 PM", "4:10 PM"]


def _append_json(path: Path, record: Dict[str, Any]) -> None:
    """Append a record to a JSON list file (audit trail)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []
    if path.exists():
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            records = []
    records.append(record)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def lookup_patient(full_name: str, date_of_birth: str) -> Dict[str, Any]:
    """Find a patient record by name; DOB is used as a soft verification."""
    record = _MOCK_PATIENTS.get(full_name.strip().lower())
    if record is None:
        return {
            "found": False,
            "message": "No existing record. Treat as a new patient and collect a callback number.",
        }
    return {"found": True, **record}


def check_availability(urgency_level: str, preference: str = "") -> Dict[str, Any]:
    """Return open slots for the requested urgency window (mocked)."""
    urgency = urgency_level.strip().lower()
    now = datetime.now()

    if urgency == "same_day":
        day_offsets = [0]
    elif urgency == "soon":
        day_offsets = [1, 2]
    else:  # routine
        day_offsets = [5, 7, 8]

    slots: List[Dict[str, str]] = []
    for offset in day_offsets:
        day = now + timedelta(days=offset)
        for time_str in random.sample(_SLOT_TIMES, k=2):
            slot_id = f"S{len(_OFFERED_SLOTS) + len(slots) + 1:03d}"
            slots.append(
                {
                    "slot_id": slot_id,
                    "date": day.strftime("%A, %B %d"),
                    "time": time_str,
                }
            )

    for slot in slots:
        _OFFERED_SLOTS[slot["slot_id"]] = slot

    logger.info(f"[scheduling] offered {len(slots)} slots for urgency={urgency}")
    return {"urgency_level": urgency, "preference": preference, "slots": slots}


def book_appointment(
    patient_name: str,
    slot_id: str,
    reason_for_visit: str,
    callback_number: str,
) -> Dict[str, Any]:
    """Book a previously offered slot and persist the booking."""
    slot = _OFFERED_SLOTS.get(slot_id.strip())
    if slot is None:
        return {
            "success": False,
            "error": f"Unknown slot id '{slot_id}'. Re-check availability and offer fresh options.",
        }

    confirmation = f"MPL-{random.randint(1000, 9999)}"
    booking = {
        "confirmation_number": confirmation,
        "patient_name": patient_name,
        "date": slot["date"],
        "time": slot["time"],
        "reason_for_visit": reason_for_visit,
        "callback_number": callback_number,
        "booked_at": datetime.now().isoformat(timespec="seconds"),
    }
    _append_json(_BOOKINGS_PATH, booking)
    logger.info(f"[scheduling] booked {confirmation} for {patient_name}")
    return {"success": True, **booking}


def escalate_emergency(summary: str) -> Dict[str, Any]:
    """Log an emergency escalation (the agent delivers the 911 script)."""
    record = {
        "type": "emergency",
        "summary": summary,
        "logged_at": datetime.now().isoformat(timespec="seconds"),
    }
    _append_json(_ESCALATIONS_PATH, record)
    logger.warning(f"[scheduling] EMERGENCY escalation logged: {summary}")
    return {
        "logged": True,
        "instruction": (
            "Tell the caller clearly to hang up and call 911 now. Do not continue "
            "booking. Offer to transfer to staff if they refuse."
        ),
    }


def transfer_to_human(reason: str, urgency: str = "normal") -> Dict[str, Any]:
    """Log a human handoff request (mocked transfer)."""
    record = {
        "type": "human_transfer",
        "reason": reason,
        "urgency": urgency,
        "logged_at": datetime.now().isoformat(timespec="seconds"),
    }
    _append_json(_ESCALATIONS_PATH, record)
    logger.info(f"[scheduling] human transfer requested: {reason} ({urgency})")
    return {
        "transferred": True,
        "estimated_wait_minutes": 1 if urgency == "immediate" else 3,
        "message": "Transfer initiated. Tell the caller you're connecting them to staff now.",
    }
