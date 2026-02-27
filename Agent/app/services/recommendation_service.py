"""
Recommendation service — v2 two-stage hybrid pipeline.

Pipeline (get_recommendations):
  1. Load user row from DB → UserProfileSnapshot
  2. _rec_cache hit?  → return immediately
  3. Build _search_cache key (allergy flags) → check _search_cache
  4. Cache miss → hybrid_search(limit=50) → store in _search_cache
  5. Build _coarse_cache key (cuisines+vibes+price) → check _coarse_cache
  6. Cache miss → AlgorithmicPreFilter(50→15) → store in _coarse_cache
  7. USE_LOCAL_RERANKER? → local_ml.rerank(15) to reorder top-15
  8. Fetch review snippets (3 per restaurant, most helpful first)
  9. AgenticScorer → one batched call_gemma_json(build_agentic_scorer_prompt)
     → graceful degradation on LLM failure (keep algorithmic order, blank reviews)
 10. AllergyGuard.check on all 15
 11. Build RecommendationItem list → store in _rec_cache → return
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from datetime import date
from typing import Any
from uuid import UUID

from cachetools import TTLCache
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.schemas.recommendation import (
    AllergySummary,
    ExpandedDetail,
    ExpandedDetailResponse,
    FitTag,
    RecommendationItem,
    RecommendationPayload,
    AllergyDetail,
    Highlight,
)
from app.schemas.restaurant import RadarScores, RestaurantResult
from app.services.allergy_guard import AllergyGuard
from app.services.fit_scorer import AlgorithmicPreFilter, UserProfileSnapshot
from app.services.gemma import call_gemma_json
from app.services.hybrid_search import hybrid_search
from app.utils.prompts import build_agentic_scorer_prompt, build_expand_detail_prompt

logger = logging.getLogger(__name__)

# ── Singletons ─────────────────────────────────────────────────────────────────

_pre_filter   = AlgorithmicPreFilter()
_allergy_guard = AllergyGuard()

# ── Three-tier TTL caches ──────────────────────────────────────────────────────

# Full recommendation payload — invalidated daily per user
_rec_cache: TTLCache = TTLCache(maxsize=1_000, ttl=86_400)

# Algorithmic pre-filter scores — varies by preference fingerprint (1 h)
_coarse_cache: TTLCache = TTLCache(maxsize=5_000, ttl=3_600)

# Raw hybrid_search results — keyed by allergy-safe query fingerprint (30 min)
_search_cache: TTLCache = TTLCache(maxsize=5_000, ttl=1_800)


# ── Cache key helpers ──────────────────────────────────────────────────────────

def _rec_key(uid: UUID) -> str:
    """Daily key per user: uid + calendar date so cache auto-expires at midnight."""
    raw = f"{uid}:{date.today().isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _search_key(allergy_confirmed: list[str], allergy_intol: list[str]) -> str:
    """Key based on allergy profile (anaphylactic flags drive SQL WHERE)."""
    raw = json.dumps(sorted(allergy_confirmed) + sorted(allergy_intol), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _coarse_key(snapshot: UserProfileSnapshot) -> str:
    """Key based on preference fingerprint (cuisine + vibes + price)."""
    raw = json.dumps({
        "c": sorted(snapshot.cuisine_affinity),
        "v": sorted(snapshot.vibe_tags),
        "p": sorted(snapshot.preferred_price_tiers),
    }, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── User loader ────────────────────────────────────────────────────────────────

async def _load_user(uid: UUID, db: AsyncSession) -> dict[str, Any] | None:
    """Return the user row as a dict, or None if not found."""
    row = await db.execute(
        text("""
            SELECT uid, preferences, dietary_flags, vibe_tags,
                   allergies, allergy_flags, preferred_price_tiers
            FROM users
            WHERE uid = :uid
        """),
        {"uid": str(uid)},
    )
    r = row.mappings().first()
    return dict(r) if r else None


def _build_merged_profile(user_row: dict[str, Any]) -> dict[str, Any]:
    """Merge columnar user fields into a single profile dict for downstream use."""
    prefs = user_row.get("preferences") or {}
    # preferred_price_tiers: use the denormalised column first, fall back to preferences JSONB
    price_col = user_row.get("preferred_price_tiers") or []
    return {
        "cuisine_affinity":      prefs.get("cuisine_affinity", []),
        "cuisine_aversions":     prefs.get("cuisine_aversions", []),
        "vibe_tags":             user_row.get("vibe_tags") or [],
        "dietary_flags":         user_row.get("dietary_flags") or [],
        "preferred_price_tiers": price_col or prefs.get("preferred_price_tiers", []),
    }


def _build_allergy_dict(user_row: dict[str, Any]) -> dict[str, Any]:
    """Extract allergy profile for AllergyGuard.check()."""
    ap = user_row.get("allergies") or {}
    # allergy_flags column is a flat list of confirmed allergen strings
    confirmed_col = user_row.get("allergy_flags") or []
    return {
        "confirmed":    ap.get("confirmed", confirmed_col),
        "intolerances": ap.get("intolerances", []),
    }


# ── Review snippet fetcher ────────────────────────────────────────────────────

async def _fetch_review_snippets(
    db: AsyncSession,
    restaurant_ids: list[int],
    snippets_per_restaurant: int = 3,
) -> dict[int, list[str]]:
    """
    Fetch the *snippets_per_restaurant* most-helpful review texts for each ID.

    Returns dict mapping restaurant_id → list[str].
    """
    if not restaurant_ids:
        return {}

    rows = await db.execute(
        text("""
            SELECT restaurant_id, review_text
            FROM (
                SELECT
                    restaurant_id,
                    review_text,
                    ROW_NUMBER() OVER (
                        PARTITION BY restaurant_id
                        ORDER BY helpful_count DESC NULLS LAST, created_at DESC
                    ) AS rn
                FROM reviews
                WHERE restaurant_id = ANY(:ids)
                  AND review_text IS NOT NULL
                  AND char_length(review_text) > 20
            ) sub
            WHERE rn <= :k
        """),
        {"ids": restaurant_ids, "k": snippets_per_restaurant},
    )
    snippets: dict[int, list[str]] = {rid: [] for rid in restaurant_ids}
    for row in rows.mappings():
        snippets[row["restaurant_id"]].append(row["review_text"])
    return snippets


# ── Local-embedding candidate re-sort ─────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity (both vectors assumed unit-norm)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    # Vectors already L2-normalised by sentence-transformers
    return dot


def _local_embed_resort(
    candidates: list[RestaurantResult],
    snapshot: UserProfileSnapshot,
) -> list[RestaurantResult]:
    """
    Re-sort candidates by cosine similarity to a local embedding of the
    user preference query.  Falls through to original order on any failure.
    """
    try:
        from app.services.local_ml import embed_batch_local, embed_single_local

        query_parts: list[str] = []
        if snapshot.cuisine_affinity:
            query_parts.append("cuisine: " + ", ".join(sorted(snapshot.cuisine_affinity)))
        if snapshot.vibe_tags:
            query_parts.append("vibe: " + ", ".join(sorted(snapshot.vibe_tags)))
        if snapshot.preferred_price_tiers:
            query_parts.append("price: " + ", ".join(sorted(snapshot.preferred_price_tiers)))
        if snapshot.dietary_flags:
            query_parts.append("dietary: " + ", ".join(sorted(snapshot.dietary_flags)))
        query_text = " | ".join(query_parts) or "restaurant bangalore"

        query_vec = embed_single_local(query_text)
        if not query_vec:
            return candidates

        candidate_texts = [
            f"{r.name} {' '.join(r.cuisine_types or [])} {' '.join((r.meta or {}).get('vibe_tags', []))}"
            for r in candidates
        ]
        cand_vecs = embed_batch_local(candidate_texts)
        if not any(cand_vecs):
            return candidates

        scored = [
            (r, _cosine(query_vec, vec))
            for r, vec in zip(candidates, cand_vecs)
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        return [r for r, _ in scored]

    except Exception as exc:
        logger.warning("Local embed re-sort failed, keeping original order: %s", exc)
        return candidates


# ── AgenticScorer ──────────────────────────────────────────────────────────────

_DEFAULT_FIT_TAGS: list[dict[str, str]] = []


async def _agentic_score(
    top15: list[tuple[RestaurantResult, int]],
    user_profile: dict[str, Any],
    review_snippets: dict[int, list[str]],
) -> dict[int, dict[str, Any]]:
    """
    Call the LLM for all top-15 restaurants in one batch.

    Returns dict: restaurant_id → {fit_score, fit_tags, consolidated_review,
                                    why_fit_paragraph}

    On any failure returns a graceful fallback dict using the algorithmic score.
    """
    restaurants_for_prompt = [r for r, _ in top15]
    candidates_as_dicts = [
        {
            "id":              r.id,
            "name":            r.name,
            "cuisine_type":    ", ".join(r.cuisine_types or []),
            "vibe_tags":       (r.meta or {}).get("vibe_tags", []),
            "price_tier":      r.price_tier,
            "average_rating":  r.rating,
            "area":            r.area,
            "dietary_options": (r.meta or {}).get("dietary_options", []),
        }
        for r in restaurants_for_prompt
    ]
    try:
        prompt = build_agentic_scorer_prompt(
            candidates=candidates_as_dicts,
            user_profile=user_profile,
            review_snippets=review_snippets,
        )
        raw = await call_gemma_json(prompt)
        if not isinstance(raw, list):
            raise ValueError(f"AgenticScorer returned non-list: {type(raw)}")

        results: dict[int, dict[str, Any]] = {}
        for item in raw:
            rid = item.get("restaurant_id")
            if rid is not None:
                results[int(rid)] = item
        return results

    except Exception as exc:
        logger.warning("AgenticScorer LLM call failed, using graceful fallback: %s", exc)
        # Graceful fallback: use algorithmic score, no tags, empty review copy
        fallback: dict[int, dict[str, Any]] = {}
        for r, algo_score in top15:
            fallback[r.id] = {
                "restaurant_id":     r.id,
                "fit_score":         algo_score,
                "fit_tags":          [],
                "consolidated_review": "",
                "why_fit_paragraph": "",
            }
        return fallback


# ── Result builder ────────────────────────────────────────────────────────────

def _parse_fit_tags(raw_tags: list[dict[str, str]]) -> list[FitTag]:
    """Parse fit_tags from LLM output, discarding malformed items."""
    tags: list[FitTag] = []
    valid_types = {"cuisine", "vibe", "price", "dietary", "allergy_safe"}
    for t in raw_tags or []:
        try:
            tag_type = t.get("type", "")
            label    = t.get("label", "")
            if tag_type in valid_types and label:
                tags.append(FitTag(label=label, type=tag_type))  # type: ignore[arg-type]
        except Exception:
            pass
    return tags


def _build_rec_item(
    restaurant: RestaurantResult,
    agentic: dict[str, Any],
    allergy_is_safe: bool,
    allergy_warnings: list,
    rank: int = 1,
) -> RecommendationItem:
    fit_score        = int(agentic.get("fit_score") or 0)
    fit_tags         = _parse_fit_tags(agentic.get("fit_tags", []))
    consolidated_rev = agentic.get("consolidated_review", "") or ""
    why_fit          = agentic.get("why_fit_paragraph", "") or ""

    return RecommendationItem(
        rank=rank,
        restaurant=restaurant,
        fit_score=fit_score,
        fit_tags=fit_tags,
        consolidated_review=consolidated_rev,
        why_fit_paragraph=why_fit,
        allergy_summary=AllergySummary(
            is_safe=allergy_is_safe,
            warnings=allergy_warnings,
        ),
    )


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def get_recommendations(
    uid: UUID,
    db: AsyncSession,
    limit: int = 10,
    refresh: bool = False,
) -> RecommendationPayload:
    """
    Return personalised restaurant recommendations for *uid*.

    Caching layers make repeated calls within 24 h essentially free.
    Set *refresh=True* to bypass all caches and force a fresh pipeline run.
    """
    # ── Step 1: Load user ──────────────────────────────────────────────────────
    user_row = await _load_user(uid, db)
    if not user_row:
        logger.warning("get_recommendations: user %s not found", uid)
        return RecommendationPayload(uid=uid, recommendations=[], generated_at=None)

    user_profile = _build_merged_profile(user_row)
    allergy_dict = _build_allergy_dict(user_row)
    snapshot     = UserProfileSnapshot.from_dict(user_profile)

    # ── Step 2: _rec_cache check ───────────────────────────────────────────────
    rec_key = _rec_key(uid)
    if not refresh and rec_key in _rec_cache:
        logger.debug("_rec_cache HIT for user %s", uid)
        payload: RecommendationPayload = _rec_cache[rec_key]
        # Trim to requested limit without re-running pipeline
        return RecommendationPayload(
            uid=payload.uid,
            recommendations=payload.recommendations[:limit],
            generated_at=payload.generated_at,
        )

    # ── Step 3 & 4: _search_cache check / hybrid_search ───────────────────────
    s_key = _search_key(
        allergy_dict.get("confirmed", []),
        allergy_dict.get("intolerances", []),
    )
    if not refresh and s_key in _search_cache:
        raw_candidates: list[RestaurantResult] = _search_cache[s_key]
        logger.debug("_search_cache HIT key=%s", s_key)
    else:
        sql_filters: dict[str, Any] = {}
        if snapshot.preferred_price_tiers:
            sql_filters["price_tiers"] = list(snapshot.preferred_price_tiers)
        # Hard exclude anaphylactic allergens at SQL level
        if allergy_dict.get("confirmed"):
            sql_filters["exclude_allergens"] = allergy_dict["confirmed"]

        vector_query_parts = []
        if snapshot.cuisine_affinity:
            vector_query_parts.append(", ".join(sorted(snapshot.cuisine_affinity)))
        if snapshot.vibe_tags:
            vector_query_parts.append(", ".join(sorted(snapshot.vibe_tags)))
        vector_query_parts.append("restaurant bangalore")
        vector_query = " ".join(vector_query_parts)

        raw_candidates = await hybrid_search(
            db=db,
            sql_filters=sql_filters,
            vector_query=vector_query,
            limit=50,
        )
        _search_cache[s_key] = raw_candidates
        logger.debug("hybrid_search returned %d candidates", len(raw_candidates))

    # Optional: local embedding re-sort before pre-filter
    if settings.use_local_embeddings and raw_candidates:
        raw_candidates = _local_embed_resort(raw_candidates, snapshot)

    # ── Step 5 & 6: _coarse_cache check / AlgorithmicPreFilter ────────────────
    c_key = _coarse_key(snapshot)
    if not refresh and c_key in _coarse_cache:
        top15_scored: list[tuple[RestaurantResult, int]] = _coarse_cache[c_key]
        logger.debug("_coarse_cache HIT key=%s", c_key)
    else:
        top15_scored = _pre_filter.score_all(raw_candidates, snapshot, top_k=15)
        _coarse_cache[c_key] = top15_scored
        logger.debug("AlgorithmicPreFilter → %d survivors", len(top15_scored))

    if not top15_scored:
        logger.warning("No candidates survived pre-filter for user %s", uid)
        return RecommendationPayload(uid=uid, recommendations=[], generated_at=None)

    # ── Step 7: Optional cross-encoder rerank ─────────────────────────────────
    top15_restaurants = [r for r, _ in top15_scored]
    if settings.use_local_reranker:
        try:
            from app.services.local_ml import rerank as local_rerank
            query_text = " ".join(
                list(snapshot.cuisine_affinity)
                + list(snapshot.vibe_tags)
                + list(snapshot.dietary_flags)
            ) or "restaurant bangalore"
            reranked = local_rerank(
                query=query_text,
                candidates=top15_restaurants,
                top_k=len(top15_restaurants),
            )
            # Re-pair with original algorithmic scores (preserved for AgenticScorer context)
            score_map = {r.id: s for r, s in top15_scored}
            top15_scored = [(r, score_map.get(r.id, 0)) for r in reranked]
            logger.debug("Cross-encoder rerank applied on %d candidates", len(top15_scored))
        except Exception as exc:
            logger.warning("Cross-encoder rerank failed, keeping pre-filter order: %s", exc)

    # ── Step 8: Fetch review snippets ─────────────────────────────────────────
    top15_ids = [r.id for r, _ in top15_scored]
    review_snippets = await _fetch_review_snippets(db, top15_ids, snippets_per_restaurant=3)

    # ── Step 9: AgenticScorer ─────────────────────────────────────────────────
    agentic_results = await _agentic_score(top15_scored, user_profile, review_snippets)

    # ── Step 10: AllergyGuard ─────────────────────────────────────────────────
    top15_restaurants_only = [r for r, _ in top15_scored]
    allergy_result = _allergy_guard.check(top15_restaurants_only, allergy_dict)

    # Build lookup: restaurant_id → is_safe, warnings
    safe_ids   = {r.id for r in allergy_result.safe_restaurants}
    # Build warnings map from the annotated results (safe + flagged combined carry warnings)
    all_ann: dict[int, RestaurantResult] = {}
    for r in allergy_result.safe_restaurants + allergy_result.flagged_restaurants:
        all_ann[r.id] = r

    # ── Step 11: Build output ──────────────────────────────────────────────────
    items: list[RecommendationItem] = []
    for rank, (restaurant, _) in enumerate(top15_scored, start=1):
        ann          = all_ann.get(restaurant.id, restaurant)
        is_safe      = restaurant.id in safe_ids
        warnings     = ann.allergy_warnings or []
        agentic_data = agentic_results.get(restaurant.id, {
            "fit_score": 0, "fit_tags": [], "consolidated_review": "", "why_fit_paragraph": "",
        })
        items.append(_build_rec_item(ann, agentic_data, is_safe, warnings, rank=rank))

    from datetime import datetime, timezone
    payload = RecommendationPayload(
        uid=uid,
        recommendations=items,
        generated_at=datetime.now(timezone.utc),
    )
    _rec_cache[rec_key] = payload

    return RecommendationPayload(
        uid=payload.uid,
        recommendations=payload.recommendations[:limit],
        generated_at=payload.generated_at,
    )


# ── Expanded detail ────────────────────────────────────────────────────────────

async def get_expanded_detail(
    uid: UUID,
    restaurant_id: int,
    db: AsyncSession,
) -> ExpandedDetailResponse | None:
    """
    Generate a richly structured ExpandedDetail for one restaurant.

    Always makes a fresh LLM call — results are NOT cached since this is an
    on-demand user action (expand button tap) that occurs infrequently.
    """
    # Load user profile for personalisation
    user_row = await _load_user(uid, db)
    user_profile = _build_merged_profile(user_row) if user_row else {}

    # Pull restaurant metadata
    row = await db.execute(
        text("""
            SELECT
                r.id, r.name, r.cuisine_types, r.area,
                r.price_tier, r.rating,
                r.is_active,
                r.known_allergens, r.allergen_confidence,
                r.lat, r.lng,
                r.meta
            FROM restaurants r
            WHERE r.id = :rid AND r.is_active = TRUE
        """),
        {"rid": restaurant_id},
    )
    r_row = row.mappings().first()
    if not r_row:
        return None

    # Fetch reviews (up to 10, most helpful first)
    rev_rows = await db.execute(
        text("""
            SELECT review_text FROM reviews
            WHERE restaurant_id = :rid
              AND review_text IS NOT NULL
            ORDER BY helpful_count DESC NULLS LAST, created_at DESC
            LIMIT 10
        """),
        {"rid": restaurant_id},
    )
    reviews = [row_["review_text"] for row_ in rev_rows.mappings() if row_["review_text"]]

    r_row_dict = dict(r_row)
    meta = r_row_dict.get("meta") or {}
    # Flatten meta fields for the prompt so it gets vibe_tags, dietary_options, radar scores
    restaurant_dict = {
        **r_row_dict,
        "cuisine_type":    ", ".join(r_row_dict.get("cuisine_types") or []),
        "vibe_tags":       meta.get("vibe_tags", []),
        "dietary_options": meta.get("dietary_options", []),
        "average_rating":  r_row_dict.get("rating"),
        "latitude":        r_row_dict.get("lat"),
        "longitude":       r_row_dict.get("lng"),
        "radar_romance":        meta.get("radar_romance"),
        "radar_noise_level":    meta.get("radar_noise_level"),
        "radar_food_quality":   meta.get("radar_food_quality"),
        "radar_vegan_options":  meta.get("radar_vegan_options"),
        "radar_value_for_money":meta.get("radar_value_for_money"),
    }
    prompt  = build_expand_detail_prompt(restaurant_dict, reviews, user_profile)
    raw     = await call_gemma_json(prompt)

    if not isinstance(raw, dict):
        logger.error("get_expanded_detail: LLM returned non-dict for restaurant %s", restaurant_id)
        return None

    try:
        highlights = [
            Highlight(emoji=h.get("emoji", ""), text=h.get("text", ""))
            for h in raw.get("highlights", [])
        ]
        ad_raw = raw.get("allergy_detail", {})
        allergy_detail = AllergyDetail(
            is_safe=bool(ad_raw.get("is_safe", True)),
            confidence=str(ad_raw.get("confidence", "low")),
            warnings=ad_raw.get("warnings", []),
            safe_note=ad_raw.get("safe_note"),
        )
        radar_raw = raw.get("radar_scores", {})
        radar = RadarScores(
            romance=float(radar_raw.get("romance", 5.0)),
            noise_level=float(radar_raw.get("noise_level", 5.0)),
            food_quality=float(radar_raw.get("food_quality", 5.0)),
            vegan_options=float(radar_raw.get("vegan_options", 5.0)),
            value_for_money=float(radar_raw.get("value_for_money", 5.0)),
        )
        expanded = ExpandedDetail(
            review_summary=raw.get("review_summary", ""),
            highlights=highlights,
            crowd_profile=raw.get("crowd_profile", ""),
            best_for=raw.get("best_for", []),
            avoid_if=raw.get("avoid_if", []),
            radar_scores=radar,
            why_fit_paragraph=raw.get("why_fit_paragraph", ""),
            allergy_detail=allergy_detail,
        )
        return ExpandedDetailResponse(restaurant_id=restaurant_id, expanded_detail=expanded)
    except Exception as exc:
        logger.error("get_expanded_detail: failed to parse LLM output: %s", exc)
        return None


# ── Prewarm ────────────────────────────────────────────────────────────────────

async def prewarm_recommendations(uid: UUID) -> None:
    """
    Fire-and-forget background task — pre-populate _rec_cache after profile update.

    Opens its own database session so it can run without the request's session.
    Errors are silently swallowed to never affect the calling request.
    """
    try:
        async with AsyncSessionLocal() as db:
            await get_recommendations(uid=uid, db=db, limit=10, refresh=False)
            logger.debug("Prewarm complete for user %s", uid)
    except Exception as exc:
        logger.debug("Prewarm failed silently for user %s: %s", uid, exc)
