"""
Local GPU ML helpers for embedding and cross-encoder reranking.

Designed for GTX 1650 4 GB VRAM.  All models are lazy-loaded on first call so
startup time is unaffected when USE_LOCAL_EMBEDDINGS / USE_LOCAL_RERANKER are
disabled.

Public API (call from recommendation_service, never modify here):
  embed_single_local(text: str) -> list[float]
  rerank(query: str, candidates: list, top_k: int) -> list
"""

from __future__ import annotations

import logging
from threading import Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── lazy-loaded model handles ──────────────────────────────────────────────────

_embed_model: Any = None
_embed_lock: Lock = Lock()

_rerank_model: Any = None
_rerank_lock: Lock = Lock()

# Model identifiers — chosen to fit inside 4 GB VRAM when run exclusively.
# Override via SENTENCE_TRANSFORMERS_HOME env-var to point to cached weights.
_EMBED_MODEL_ID  = "sentence-transformers/all-MiniLM-L6-v2"   # 90 MB, 384-dim
_RERANK_MODEL_ID = "cross-encoder/ms-marco-MiniLM-L-6-v2"     # 90 MB


def _get_embed_model() -> Any:
    """Return a lazy-loaded SentenceTransformer embedding model (thread-safe)."""
    global _embed_model  # noqa: PLW0603
    if _embed_model is not None:
        return _embed_model
    with _embed_lock:
        if _embed_model is not None:
            return _embed_model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
            logger.info("Loading local embedding model: %s", _EMBED_MODEL_ID)
            _embed_model = SentenceTransformer(_EMBED_MODEL_ID)
            logger.info("Local embedding model loaded.")
        except Exception as exc:
            logger.warning("Failed to load local embedding model: %s", exc)
            _embed_model = None
    return _embed_model


def _get_rerank_model() -> Any:
    """Return a lazy-loaded CrossEncoder reranking model (thread-safe)."""
    global _rerank_model  # noqa: PLW0603
    if _rerank_model is not None:
        return _rerank_model
    with _rerank_lock:
        if _rerank_model is not None:
            return _rerank_model
        try:
            from sentence_transformers.cross_encoder import CrossEncoder  # type: ignore[import]
            logger.info("Loading local cross-encoder model: %s", _RERANK_MODEL_ID)
            _rerank_model = CrossEncoder(_RERANK_MODEL_ID, max_length=512)
            logger.info("Local cross-encoder model loaded.")
        except Exception as exc:
            logger.warning("Failed to load local cross-encoder model: %s", exc)
            _rerank_model = None
    return _rerank_model


# ── Public API ─────────────────────────────────────────────────────────────────


def embed_single_local(text: str) -> list[float]:
    """
    Embed *text* using the local GPU embedding model.

    Returns a list[float] of 384 dimensions (all-MiniLM-L6-v2 default).
    Falls back to an empty list on model-load failure so callers can detect
    and skip the local-embedding path.

    Usage::
        vec = embed_single_local("north indian, quiet rooftop, ₹₹")
        if vec:
            # use as query vector for ANN candidate ordering
    """
    model = _get_embed_model()
    if model is None:
        logger.warning("embed_single_local: model unavailable, returning []")
        return []
    try:
        import numpy as np  # type: ignore[import]
        vec = model.encode(text, normalize_embeddings=True)
        if hasattr(vec, "tolist"):
            return vec.tolist()
        return list(vec)
    except Exception as exc:
        logger.error("embed_single_local failed: %s", exc)
        return []


def embed_batch_local(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts in one GPU batch.

    Returns a parallel list of float vectors (empty list for items that fail).
    Uses the same model as :func:`embed_single_local`.

    Usage::
        vecs = embed_batch_local(["north indian rooftop", "south indian budget"])
        # vecs[i] corresponds to texts[i]
    """
    if not texts:
        return []
    model = _get_embed_model()
    if model is None:
        logger.warning("embed_batch_local: model unavailable, returning empty vectors")
        return [[] for _ in texts]
    try:
        embeddings = model.encode(texts, normalize_embeddings=True, batch_size=64)
        return [e.tolist() if hasattr(e, "tolist") else list(e) for e in embeddings]
    except Exception as exc:
        logger.error("embed_batch_local failed: %s", exc)
        return [[] for _ in texts]


def rerank(
    query: str,
    candidates: list[Any],
    top_k: int,
) -> list[Any]:
    """
    Reorder *candidates* using a local cross-encoder model.

    Each candidate must expose a `description` attribute or dict key that
    describes the restaurant (name + cuisine + vibe tags stringified).  If the
    model is unavailable the original order is preserved and the first *top_k*
    items are returned.

    Args:
        query:      The natural-language query (built from the user profile).
        candidates: List of ``RestaurantResult``-like objects (attrs accessed as
                    ``r.name``, ``r.cuisine_type``, ``r.vibe_tags``).
        top_k:      How many items to return.

    Returns:
        A reranked slice of *candidates* of length ≤ top_k.
    """
    model = _get_rerank_model()
    if model is None or not candidates:
        logger.warning("rerank: model unavailable or empty candidates, returning original order")
        return candidates[:top_k]

    try:
        pairs: list[tuple[str, str]] = []
        for r in candidates:
            # Build a compact textual representation for scoring
            name = getattr(r, "name", "") or (r.get("name", "") if hasattr(r, "get") else "")
            # cuisine_types is a list; join for a single string
            cuisine_list = getattr(r, "cuisine_types", None)
            if cuisine_list is None and hasattr(r, "get"):
                cuisine_list = r.get("cuisine_types") or r.get("cuisine_type") or []
            cuisine = ", ".join(cuisine_list) if isinstance(cuisine_list, list) else str(cuisine_list or "")
            # vibe_tags live in meta JSONB
            meta = getattr(r, "meta", None) or (r.get("meta") if hasattr(r, "get") else None) or {}
            vibes = meta.get("vibe_tags", []) if isinstance(meta, dict) else []
            doc = f"{name} — {cuisine} — {' '.join(vibes) if vibes else ''}"
            pairs.append((query, doc))

        scores = model.predict(pairs)

        import numpy as np  # type: ignore[import]
        ranked_indices = list(np.argsort(scores)[::-1])
        reranked = [candidates[i] for i in ranked_indices[:top_k]]
        logger.debug("rerank: reordered %d candidates → top %d", len(candidates), top_k)
        return reranked

    except Exception as exc:
        logger.error("rerank failed, preserving original order: %s", exc)
        return candidates[:top_k]
