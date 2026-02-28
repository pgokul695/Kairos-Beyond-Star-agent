"""
Embedding service — Ollama primary (nomic-embed-text, 768-dim), Gemini fallback.

Public interface (unchanged — all callers use await):
  embed_texts(texts: list[str])  -> list[list[float] | None]
  embed_single(text: str)        -> list[float] | None

To switch back to Gemini: set USE_OLLAMA_EMBEDDINGS=false in .env — no code changes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ── Ollama (sync helpers, called via asyncio.to_thread) ──────────────────────

def _ollama_embed_single_sync(text: str) -> list[float]:
    """Call Ollama /api/embeddings synchronously. Raises on any failure."""
    url = f"{settings.ollama_base_url}/api/embeddings"
    payload = {"model": settings.ollama_embed_model, "prompt": text}
    resp = httpx.post(url, json=payload, timeout=30.0)
    resp.raise_for_status()
    embedding = resp.json()["embedding"]
    if len(embedding) != settings.embedding_dimensions:
        raise ValueError(
            f"Ollama returned {len(embedding)}-dim vector; "
            f"expected {settings.embedding_dimensions}. "
            f"Run: ollama pull nomic-embed-text"
        )
    return embedding


def _ollama_embed_texts_sync(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts via Ollama, one request per text (sync)."""
    return [_ollama_embed_single_sync(t) for t in texts]


# ── Gemini fallback (sync helpers, called via asyncio.to_thread) ─────────────

def _gemini_embed_single_sync(text: str) -> list[float]:
    import google.generativeai as genai
    genai.configure(api_key=settings.google_api_key)
    result = genai.embed_content(
        model=f"models/{settings.embedding_model}",
        content=text,
        task_type="retrieval_query",
        output_dimensionality=settings.embedding_dimensions,
    )
    return result["embedding"]


def _gemini_embed_texts_sync(texts: list[str]) -> list[list[float]]:
    import google.generativeai as genai
    genai.configure(api_key=settings.google_api_key)
    results = []
    for t in texts:
        resp = genai.embed_content(
            model=f"models/{settings.embedding_model}",
            content=t,
            task_type="retrieval_document",
            output_dimensionality=settings.embedding_dimensions,
        )
        results.append(resp["embedding"])
    return results


# ── Public async interface ────────────────────────────────────────────────────

async def embed_single(text: str) -> Optional[list[float]]:
    """Return a 768-dimensional embedding for a single text, or None on error."""
    if settings.use_ollama_embeddings:
        try:
            return await asyncio.to_thread(_ollama_embed_single_sync, text)
        except Exception as exc:
            logger.warning("Ollama embed_single failed (%s), falling back to Gemini", exc)
    try:
        return await asyncio.to_thread(_gemini_embed_single_sync, text)
    except Exception as exc:
        logger.error("Gemini embed_single also failed: %s", exc)
        return None


async def embed_texts(texts: list[str]) -> list[Optional[list[float]]]:
    """
    Return 768-dimensional embeddings for a list of texts.

    Returns None for any text whose embedding could not be obtained.
    Ollama is tried first when USE_OLLAMA_EMBEDDINGS=true; Gemini is the fallback.
    """
    if not texts:
        return []

    if settings.use_ollama_embeddings:
        try:
            results = await asyncio.to_thread(_ollama_embed_texts_sync, texts)
            return results  # type: ignore[return-value]
        except Exception as exc:
            logger.warning(
                "Ollama embed_texts failed (%s), falling back to Gemini", exc
            )

    # Gemini fallback — per-item so partial failures return None rather than crashing
    out: list[Optional[list[float]]] = [None] * len(texts)
    for i, text in enumerate(texts):
        try:
            out[i] = await asyncio.to_thread(_gemini_embed_single_sync, text)
        except Exception as exc:
            logger.error("Gemini embed_texts item %d failed: %s", i, exc)
    return out
