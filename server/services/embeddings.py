"""Text embeddings via OpenAI (text-embedding-3-small, 256 dims).

Used for semantic selection over the execution-agent roster. 256 dimensions
keeps cached vectors small (~2KB/agent as JSON) — at roster scale (hundreds),
brute-force cosine in pure Python is microseconds; a vector DB would be
overkill (slower than the network call to reach it).
"""

from __future__ import annotations

import math
from typing import List

import httpx

from ..config import get_settings

_EMBED_MODEL = "text-embedding-3-small"
_EMBED_DIMS = 256
_EMBED_URL = "https://api.openai.com/v1/embeddings"


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Embed a batch of texts; order of results matches input order."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY not configured — semantic agent selection unavailable.")

    response = httpx.post(
        _EMBED_URL,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={"model": _EMBED_MODEL, "input": texts, "dimensions": _EMBED_DIMS},
        timeout=15.0,
    )
    response.raise_for_status()
    data = response.json()["data"]
    return [item["embedding"] for item in sorted(data, key=lambda item: item["index"])]


def embed_text(text: str) -> List[float]:
    """Embed a single text."""
    return embed_texts([text])[0]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity = dot product over the product of norms."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
