"""Relevance-based selection over the execution-agent roster.

THE AGENT OVERLOAD FIX. Previously the interaction agent received the entire
roster in every prompt — token cost grows linearly with agents ever created,
and the model has to pick the right worker out of hundreds of similar names.

Layered selection instead:
  ① semantic top-k — embed the user's message, cosine against cached agent
     embeddings (relevance)
  ② recently-active agents always included (recency covers what semantics
     misses, e.g. "actually, cancel that")
  ③ hard cap on the injected set
  ④ escape hatch — the `search_agents` tool lets the interaction agent query
     the FULL roster when the selection missed; every use is a logged miss
     signal (free retrieval eval)

Embeddings are cached on the roster records (computed once per agent, lazily
backfilled). Failure mode is graceful: any error falls back to injecting the
full roster — correctness over efficiency.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

from ...logging_config import logger
from ..embeddings import cosine_similarity, embed_text, embed_texts
from .roster import AgentRoster

# Below this roster size selection is pointless — inject everything.
SELECTION_THRESHOLD = 10
TOP_K = 6
RECENT_HOURS = 24
MAX_INJECTED = 10


def _embedding_text(record: Dict[str, Any]) -> str:
    """What we embed for an agent: its name plus its description."""
    description = (record.get("description") or "").strip()
    return f"{record['name']}: {description}" if description else record["name"]


# Lazily embed any roster records that don't have a cached embedding yet
def _backfill_embeddings(records: List[Dict[str, Any]], roster: AgentRoster) -> List[Dict[str, Any]]:
    missing = [r for r in records if not r.get("embedding")]
    if not missing:
        return records
    vectors = embed_texts([_embedding_text(r) for r in missing])
    for record, vector in zip(missing, vectors):
        roster.set_embedding(record["name"], vector)
    logger.info(f"[selection] backfilled embeddings for {len(missing)} agents")
    return roster.get_records()


def _recently_active(record: Dict[str, Any], hours: int) -> bool:
    stamp = record.get("last_active")
    if not stamp:
        return False
    try:
        return datetime.fromisoformat(stamp) >= datetime.now() - timedelta(hours=hours)
    except ValueError:
        return False


def score_agents(records: List[Dict[str, Any]], query: str, roster: AgentRoster) -> List[Dict[str, Any]]:
    """All roster records scored by semantic similarity to the query, best first."""
    records = _backfill_embeddings(records, roster)
    query_vector = embed_text(query)
    scored = [
        {**record, "score": cosine_similarity(query_vector, record["embedding"] or [])}
        for record in records
    ]
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored


# Cheap selector for latency-critical channels (voice): recency only, no
# embedding call. Execution agents are rarely delegated to mid-call, so the
# ~0.7s query-embedding round-trip isn't worth the dead air.
def select_recent_agents(records: List[Dict[str, Any]], cap: int = MAX_INJECTED) -> List[Dict[str, Any]]:
    def _stamp(record: Dict[str, Any]) -> str:
        return record.get("last_active") or record.get("created_at") or ""

    ordered = sorted(records, key=_stamp, reverse=True)
    return ordered[:cap]


def select_relevant_agents(
    records: List[Dict[str, Any]],
    query: str,
    roster: AgentRoster,
) -> List[Dict[str, Any]]:
    """The layered selection: semantic top-k ∪ recently-active, capped."""
    scored = score_agents(records, query, roster)

    selected: Dict[str, Dict[str, Any]] = {}
    for record in scored[:TOP_K]:
        selected[record["name"]] = record
    for record in scored:  # recency pass — covers semantic misses
        if record["name"] not in selected and _recently_active(record, RECENT_HOURS):
            selected[record["name"]] = record

    chosen = list(selected.values())[:MAX_INJECTED]
    logger.info(
        f"[selection] roster={len(records)} → injected={len(chosen)} "
        f"(top score={scored[0]['score']:.3f} '{scored[0]['name']}')"
    )
    return chosen
