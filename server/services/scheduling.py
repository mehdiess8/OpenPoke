"""Clinic scheduling service.

Backs the intake tools (patient lookup, availability, booking, escalation,
human transfer). Availability and booking use Google Calendar via Composio
when connected; otherwise they fall back to the mock so the demo never
blocks on OAuth. The tool layer above never knows the difference.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from ..logging_config import logger
from .timezone_store import get_timezone_store

_APPOINTMENT_MINUTES = 30
_CLINIC_OPEN_HOUR = 9
_CLINIC_LAST_START = (16, 30)  # last bookable start time

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
# Calendar-backed slots carry a "start_iso" key; mock slots don't.
_OFFERED_SLOTS: Dict[str, Dict[str, str]] = {}

_SLOT_TIMES = ["9:20 AM", "10:40 AM", "1:30 PM", "2:40 PM", "4:10 PM"]


def _clinic_tz() -> ZoneInfo:
    name = get_timezone_store().get_timezone(default="America/Toronto")
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("America/Toronto")


def _day_offsets(urgency: str) -> List[int]:
    if urgency == "same_day":
        return [0]
    if urgency == "soon":
        return [1, 2]
    return [5, 7, 8]  # routine


# Generate every bookable 30-min start within clinic hours for the target days
def _candidate_slots(urgency: str) -> List[Dict[str, Any]]:
    tz = _clinic_tz()
    now = datetime.now(tz)
    candidates: List[Dict[str, Any]] = []

    for offset in _day_offsets(urgency):
        day = (now + timedelta(days=offset)).date()
        cursor = datetime(day.year, day.month, day.day, _CLINIC_OPEN_HOUR, 0, tzinfo=tz)
        last = datetime(day.year, day.month, day.day, *_CLINIC_LAST_START, tzinfo=tz)
        while cursor <= last:
            if cursor > now + timedelta(hours=1):  # never offer slots in the immediate past
                candidates.append(
                    {
                        "start": cursor,
                        "date": cursor.strftime("%A, %B %d"),
                        "time": cursor.strftime("%-I:%M %p"),
                    }
                )
            cursor += timedelta(minutes=_APPOINTMENT_MINUTES)
    return candidates


# Pull busy [(start, end)] ranges out of the free/busy response, defensively
def _extract_busy_ranges(response: Dict[str, Any], tz: ZoneInfo) -> List[Any]:
    busy_ranges = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            busy = node.get("busy")
            if isinstance(busy, list):
                for entry in busy:
                    if isinstance(entry, dict) and entry.get("start") and entry.get("end"):
                        try:
                            start = datetime.fromisoformat(str(entry["start"]).replace("Z", "+00:00"))
                            end = datetime.fromisoformat(str(entry["end"]).replace("Z", "+00:00"))
                            busy_ranges.append((start.astimezone(tz), end.astimezone(tz)))
                        except ValueError:
                            continue
            for value in node.values():
                _walk(value)

    _walk(response)
    return busy_ranges


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
    """Return open slots: real Google Calendar free/busy when connected, mock otherwise."""
    urgency = urgency_level.strip().lower()

    from .calendar_client import execute_calendar_tool, is_calendar_connected

    if is_calendar_connected():
        try:
            tz = _clinic_tz()
            candidates = _candidate_slots(urgency)
            if not candidates:
                return {"urgency_level": urgency, "slots": [], "note": "No slots left in this window."}

            response = execute_calendar_tool(
                "GOOGLECALENDAR_FIND_FREE_SLOTS",
                {
                    "items": ["primary"],
                    "time_min": candidates[0]["start"].isoformat(),
                    "time_max": (candidates[-1]["start"] + timedelta(minutes=_APPOINTMENT_MINUTES)).isoformat(),
                    "timezone": str(tz),
                },
            )
            busy = _extract_busy_ranges(response, tz)

            free = [
                c for c in candidates
                if not any(
                    b_start < c["start"] + timedelta(minutes=_APPOINTMENT_MINUTES) and c["start"] < b_end
                    for b_start, b_end in busy
                )
            ]

            # Spread the offering across the window instead of clustering at 9am.
            step = max(1, len(free) // 4)
            chosen = free[::step][:4]

            slots: List[Dict[str, str]] = []
            for c in chosen:
                slot_id = f"S{len(_OFFERED_SLOTS) + len(slots) + 1:03d}"
                slot = {
                    "slot_id": slot_id,
                    "date": c["date"],
                    "time": c["time"],
                    "start_iso": c["start"].strftime("%Y-%m-%dT%H:%M:%S"),
                }
                slots.append(slot)
                _OFFERED_SLOTS[slot_id] = slot

            logger.info(
                f"[scheduling] calendar: {len(busy)} busy ranges, {len(free)} free candidates, offering {len(slots)} (urgency={urgency})"
            )
            return {"urgency_level": urgency, "preference": preference, "slots": slots, "source": "google_calendar"}
        except Exception as exc:
            logger.warning(f"[scheduling] calendar availability failed, falling back to mock: {exc}")

    # ── Mock fallback (calendar not connected or query failed) ──
    now = datetime.now()
    slots = []
    for offset in _day_offsets(urgency):
        day = now + timedelta(days=offset)
        for time_str in random.sample(_SLOT_TIMES, k=2):
            slot_id = f"S{len(_OFFERED_SLOTS) + len(slots) + 1:03d}"
            slots.append({"slot_id": slot_id, "date": day.strftime("%A, %B %d"), "time": time_str})

    for slot in slots:
        _OFFERED_SLOTS[slot["slot_id"]] = slot

    logger.info(f"[scheduling] mock: offered {len(slots)} slots for urgency={urgency}")
    return {"urgency_level": urgency, "preference": preference, "slots": slots, "source": "mock"}


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

    # Create the real calendar event when this slot came from Google Calendar.
    calendar_synced = False
    if slot.get("start_iso"):
        from .calendar_client import execute_calendar_tool, is_calendar_connected

        if is_calendar_connected():
            try:
                event = execute_calendar_tool(
                    "GOOGLECALENDAR_CREATE_EVENT",
                    {
                        "calendar_id": "primary",
                        "start_datetime": slot["start_iso"],
                        "event_duration_minutes": _APPOINTMENT_MINUTES,
                        "timezone": str(_clinic_tz()),
                        "summary": f"Appointment: {patient_name} — {reason_for_visit}",
                        "description": (
                            f"Confirmation: {confirmation}\n"
                            f"Callback: {callback_number}\n"
                            f"Booked by Maple voice assistant"
                        ),
                        "create_meeting_room": False,
                    },
                )
                calendar_synced = True
                logger.info(f"[scheduling] calendar event created for {confirmation}")
            except Exception as exc:
                logger.warning(f"[scheduling] calendar event creation failed: {exc}")

    booking["calendar_synced"] = calendar_synced
    _append_json(_BOOKINGS_PATH, booking)
    logger.info(f"[scheduling] booked {confirmation} for {patient_name} (calendar_synced={calendar_synced})")
    result = {"success": True, **booking}
    if slot.get("start_iso") and not calendar_synced:
        result["note"] = "Booking saved locally but the calendar sync failed — mention that staff will confirm the exact time."
    return result


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
