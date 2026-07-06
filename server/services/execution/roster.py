"""Agent roster with metadata for relevance-based selection.

Originally a bare list of name strings — which is the root of the agent
overload problem: with no features (description, recency, embedding) there is
nothing to rank agents by, so the entire roster gets injected into every
interaction-agent prompt.

Records now carry: name, description (seeded from the first delegation's
instructions), created_at, last_active, and a cached embedding. Loading a
legacy roster (list of strings) upgrades entries in place.
"""

import json
import fcntl
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...logging_config import logger


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class AgentRoster:
    """Roster of execution agents persisted to roster.json."""

    def __init__(self, roster_path: Path):
        self._roster_path = roster_path
        self._records: List[Dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        """Load roster records; upgrade legacy bare-string entries in place."""
        if self._roster_path.exists():
            try:
                with open(self._roster_path, "r") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._records = [self._coerce(entry) for entry in data]
            except Exception as exc:
                logger.warning(f"Failed to load roster.json: {exc}")
                self._records = []
        else:
            self._records = []
            self.save()

    @staticmethod
    def _coerce(entry: Any) -> Dict[str, Any]:
        """Accept both legacy string entries and full record dicts."""
        if isinstance(entry, dict) and entry.get("name"):
            return entry
        return {
            "name": str(entry),
            "description": "",
            "created_at": None,
            "last_active": None,
            "embedding": None,
        }

    def save(self) -> None:
        """Save roster with file locking (single-host concurrency guard)."""
        max_retries = 5
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                self._roster_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._roster_path, "w") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        json.dump(self._records, f, indent=2)
                        return
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except BlockingIOError:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.warning("Failed to acquire lock on roster.json after retries")
            except Exception as exc:
                logger.warning(f"Failed to save roster.json: {exc}")
                break

    def _find(self, agent_name: str) -> Optional[Dict[str, Any]]:
        return next((r for r in self._records if r["name"] == agent_name), None)

    def add_agent(self, agent_name: str, description: str = "") -> None:
        """Add an agent if not present; refresh last_active either way."""
        record = self._find(agent_name)
        if record is None:
            self._records.append(
                {
                    "name": agent_name,
                    "description": description[:300],
                    "created_at": _now_iso(),
                    "last_active": _now_iso(),
                    "embedding": None,
                }
            )
        else:
            record["last_active"] = _now_iso()
            if description and not record.get("description"):
                record["description"] = description[:300]
        self.save()

    def touch(self, agent_name: str) -> None:
        """Mark an agent as just-used (recency signal for selection)."""
        record = self._find(agent_name)
        if record is not None:
            record["last_active"] = _now_iso()
            self.save()

    def set_embedding(self, agent_name: str, embedding: List[float]) -> None:
        """Cache an embedding for an agent (computed once, reused every turn)."""
        record = self._find(agent_name)
        if record is not None:
            record["embedding"] = embedding
            self.save()

    def get_agents(self) -> list[str]:
        """Get list of all agent names (legacy API, used across the codebase)."""
        return [r["name"] for r in self._records]

    def get_records(self) -> List[Dict[str, Any]]:
        """Get full roster records for selection/scoring."""
        return [dict(r) for r in self._records]

    def clear(self) -> None:
        """Clear the agent roster."""
        self._records = []
        try:
            if self._roster_path.exists():
                self._roster_path.unlink()
            logger.info("Cleared agent roster")
        except Exception as exc:
            logger.warning(f"Failed to clear roster.json: {exc}")


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_ROSTER_PATH = _DATA_DIR / "execution_agents" / "roster.json"

_agent_roster = AgentRoster(_ROSTER_PATH)


def get_agent_roster() -> AgentRoster:
    """Get the singleton roster instance."""
    return _agent_roster
