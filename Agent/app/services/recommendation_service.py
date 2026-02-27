"""
Recommendation service — three-layer pipeline for personalised restaurant picks.

Layer 1: Algorithmic FitScorer     (pure Python, no LLM)
Layer 2: LLM fit explanation batch (one call for all candidates)
Layer 3: TTLCache (24 h, per user+day, refreshable)

The /expand endpoint uses get_expanded_detail() — always fresh, never cached.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from cachetools import TTLCache
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.schemas.recommendation import (
    AllergyDetail,
    AllergySummary,
    ExpandedDetail,
    ExpandedDetailResponse,
    FitTag,
    Highlight,
    RecommendationItem,
    RecommendationPayload,
)
from app.schemas.restaurant import RadarScores, RestaurantResult
from app.services.allergy_guard import AllergyGuard
from app.services.fit_scorer import FitScorer
from app.services.gemma import GemmaError, call_gemma_json
from app.utils.prompts import build_expand_detail_prompt, build_fit_explanation_prompt

logger = logging.getLogger(__name__)

# ── Singletons ────────────────────────────────────────────────────────────────

_allergy_guard = AllergyGuard()
_fit_scorer    = FitScorer()

# TTLCache: key = sha256(uid + date), value = RecommendationPayload JSON string
# 86400 s TTL — keys also naturally expire at midnight because the date rotates.
_recommendation_cache: TTLCache = TTLCache(maxsize=1_000, ttl=86_400)


# ── Cache helpers ─────────────────────────────────────────────────────────────


def _cache_key(uid: str) -> str:
    """Stable daily cache key: sha256(uid + YYYY-MM-DD)."""
    raw = uid + date.today().isoformat()
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(uid: str) -> RecommendationPayload | None:
    key = _cache_key(uid)
    raw = _recommendation_cache.get(key)
    if raw is None:
        return None
    try:
        return RecommendationPayload.model_validate_json(raw)
    except Exception:
        logger.warning("Corrupted recommendation cache entry for uid %s — discarding.", uid)
        _recommendation_cache.pop(key, None)
        return None


def _cache_set(uid: str, payload: RecommendationPayload) -> None:
    key = _cache_key(uid)
    _recommendation_cache[key] = payload.model_dump_json()


def _cache_delete(uid: str) -> None:
    key = _cache_key(uid)
    _recommendation_cache.pop(key, None)


# ── User profile fetch ────────────────────────────────────────────────────────


async def _fetch_user_profile(uid: str, db: AsyncSession) -> dict[str, Any] | None:
    """Return full user profile row as a dict, or None if user not found."""
    result = await db.execute(
        text(
            "SELECT preferences, allergies, allergy_flags, dietary_flags, "
            "vibe_tags, preferred_price_tiers "
            "FROM users WHERE uid = :uid"
        ),
        {"uid": uid},
    )
    row = result.fetchone()
    if not row:
        return None

    preferences: dict[str, Any] = row.preferences or {}
    return {
        "preferences": preferences,
        "allergies": row.allergies or {},
        "allergy_flags": row.allergy_flags or [],
        "dietary_flags": row.dietary_flags or [],
        "vibe_tags": row.vibe_tags or [],
        "preferred_price_tiers": row.preferred_price_tiers or [],
        "cuisine_affinity": preferences.get("cuisine_affinity", []),
        "cuisine_aversion": preferences.get("cuisine_aversion", []),
    }


# ── Candidate retrieval ───────────────────────────────────────────────────────


async def _fetch_candidates(
    allergy_flags: list[str],
    allergies: dict[str, Any],
    db: AsyncSession,
    limit: int = 50,
) -> list[RestaurantResult]:
    """
    Retrieve up to `limit` active restaurants ordered by rating DESC.

    Hard-filter: NOT (known_allergens && anaphylactic_allergens) — same
    anaphylactic safety rule as hybrid_search.
    """
    severity_map: dict[str, str] = allergies.get("severity", {})
    confirmed: list[str] = allergies.get("confirmed", [])
    anaphylactic: list[str] = [
        a for a in confirmed if severity_map.get(a) == "anaphylactic"
    ]

    conditions: list[str] = ["is_active = TRUE"]
    params: dict[str, Any] = {"limit": limit}

    if anaphylactic:
        conditions.append("NOT (known_allergens && :anaphylactic)")
        params["anaphylactic"] = anaphylactic

    where = " AND ".join(conditions)
    sql = text(f"""
        SELECT
            id, name, url, address, area, city,
            cuisine_types, price_tier, cost_for_two,
            rating, votes, lat, lng,
            known_allergens, allergen_confidence, meta
        FROM restaurants
        WHERE {where}
        ORDER BY rating DESC NULLS LAST
        LIMIT :limit
    """)

    result = await db.execute(sql, params)
    rows = result.fetchall()

    restaurants: list[RestaurantResult] = []
    for row in rows:
        restaurants.append(
            RestaurantResult(
                id=row.id,
                name=row.name,
                url=row.url,
                address=row.address,
                area=row.area,
                price_tier=row.price_tier,
                rating=float(row.rating) if row.rating else None,
                votes=row.votes or 0,
                cuisine_types=row.cuisine_types or [],
                lat=row.lat,
                lng=row.lng,
                known_allergens=row.known_allergens or [],
                allergen_confidence=row.allergen_confidence or "low",
                meta=row.meta or {},
            )
        )
    return restaurants


# ── Main recommendation pipeline ─────────────────────────────────────────────


async def get_recommendations(
    uid: UUID,
    db: AsyncSession,
    limit: int = 10,
    refresh: bool = False,
) -> RecommendationPayload:
    """
    Full recommendation pipeline — returns a synchronous JSON payload (no SSE).

    Steps:
      1  Fetch user profile
      2  Candidate retrieval (SQL, top 50, hard allergy filter)
      3  FitScorer on all candidates
      4  Sort by fit_score DESC, take top `limit`
      5  AllergyGuard.check() on selected candidates
      6  LLM batch: consolidated_review per restaurant
      7  Assemble and cache RecommendationPayload

    Raises ValueError if user not found.
    """
    uid_str = str(uid)

    # Cache check (bypass when refresh=True)
    if refresh:
        _cache_delete(uid_str)
    else:
        cached = _cache_get(uid_str)
        if cached is not None:
            return cached

    # ── Step 1: Fetch user profile ────────────────────────────────────────────
    user_profile = await _fetch_user_profile(uid_str, db)
    if user_profile is None:
        raise ValueError(f"User {uid_str} not found")

    # ── Step 2: Candidate retrieval ───────────────────────────────────────────
    candidates = await _fetch_candidates(
        allergy_flags=user_profile["allergy_flags"],
        allergies=user_profile["allergies"],
        db=db,
    )

    if not candidates:
        # Return empty payload — no crash
        payload = RecommendationPayload(
            uid=uid_str,
            generated_at=datetime.now(timezone.utc),
            recommendations=[],
        )
        _cache_set(uid_str, payload)
        return payload

    # ── Step 3: FitScorer on all candidates ──────────────────────────────────
    # AllergyGuard must annotate BEFORE FitScorer so allergy_safe is set
    guard_result = _allergy_guard.check(candidates, user_profile["allergies"])
    all_annotated = guard_result.safe_restaurants + guard_result.flagged_restaurants

    scored: list[tuple[RestaurantResult, int, list[FitTag]]] = []
    for restaurant in all_annotated:
        fit_result = _fit_scorer.score(restaurant, user_profile)
        scored.append((restaurant, fit_result.score, fit_result.fit_tags))

    # ── Step 4: Sort by fit_score DESC, take top `limit` ────────────────────
    limit = max(1, min(limit, 25))
    scored.sort(key=lambda x: x[1], reverse=True)
    selected = scored[:limit]

    # ── Step 5: AllergyGuard final pass on selected (already annotated) ───────
    # No re-run needed — annotation already done above. Build AllergySummary here.

    # ── Step 6: LLM batch — consolidated reviews ─────────────────────────────
    restaurants_for_prompt = [
        {
            "restaurant_id": r.id,
            "name": r.name,
            "area": r.area,
            "price_tier": r.price_tier,
            "rating": r.rating,
            "cuisine_types": r.cuisine_types,
            "meta": r.meta,
        }
        for r, _, _ in selected
    ]

    fit_prompt = build_fit_explanation_prompt(restaurants_for_prompt, user_profile)
    try:
        llm_results: list[dict[str, Any]] = await call_gemma_json(fit_prompt)
        if not isinstance(llm_results, list):
            llm_results = []
    except GemmaError as exc:
        logger.warning("Fit explanation LLM call failed — using empty reviews: %s", exc)
        llm_results = []

    # Map restaurant_id → consolidated_review
    review_map: dict[int, str] = {}
    for item in llm_results:
        if isinstance(item, dict) and "restaurant_id" in item:
            raw_review = str(item.get("consolidated_review", "")).strip()
            review_map[int(item["restaurant_id"])] = raw_review[:160]

    # ── Step 7: Assemble RecommendationPayload ───────────────────────────────
    recommendation_items: list[RecommendationItem] = []
    for rank, (restaurant, fit_score, fit_tags) in enumerate(selected, start=1):
        allergy_summary = AllergySummary(
            is_safe=restaurant.allergy_safe,
            warnings=restaurant.allergy_warnings,
        )

        consolidated_review = review_map.get(
            restaurant.id,
            f"{restaurant.name} — a popular dining spot in {restaurant.area or 'Bangalore'}.",
        )

        schema_fit_tags = [
            FitTag(label=t.label, type=t.type)  # type: ignore[arg-type]
            for t in fit_tags
        ]

        recommendation_items.append(
            RecommendationItem(
                rank=rank,
                restaurant=restaurant,
                fit_score=fit_score,
                fit_tags=schema_fit_tags,
                consolidated_review=consolidated_review,
                allergy_summary=allergy_summary,
                expanded_detail=None,
            )
        )

    payload = RecommendationPayload(
        uid=uid_str,
        generated_at=datetime.now(timezone.utc),
        recommendations=recommendation_items,
    )
    _cache_set(uid_str, payload)
    return payload


# ── Expand detail ─────────────────────────────────────────────────────────────


async def get_expanded_detail(
    uid: UUID,
    restaurant_id: int,
    db: AsyncSession,
) -> ExpandedDetailResponse:
    """
    Generate ExpandedDetail for a single restaurant — always freshly generated.

    Steps:
      1  Fetch restaurant row + reviews
      2  Fetch user profile
      3  LLM call → ExpandedDetail JSON
      4  AllergyGuard annotation
      5  Return ExpandedDetailResponse

    Raises ValueError if restaurant or user not found.
    """
    # ── Step 1: Fetch restaurant + reviews ────────────────────────────────────
    rest_result = await db.execute(
        text("""
            SELECT
                id, name, url, address, area, city,
                cuisine_types, price_tier, cost_for_two,
                rating, votes, lat, lng,
                known_allergens, allergen_confidence, meta
            FROM restaurants
            WHERE id = :rid AND is_active = TRUE
        """),
        {"rid": restaurant_id},
    )
    rest_row = rest_result.fetchone()
    if not rest_row:
        raise ValueError(f"Restaurant {restaurant_id} not found")

    reviews_result = await db.execute(
        text("""
            SELECT review_text
            FROM reviews
            WHERE restaurant_id = :rid
            ORDER BY id DESC
            LIMIT 10
        """),
        {"rid": restaurant_id},
    )
    review_texts: list[str] = [r.review_text for r in reviews_result.fetchall()]

    restaurant = RestaurantResult(
        id=rest_row.id,
        name=rest_row.name,
        url=rest_row.url,
        address=rest_row.address,
        area=rest_row.area,
        price_tier=rest_row.price_tier,
        rating=float(rest_row.rating) if rest_row.rating else None,
        votes=rest_row.votes or 0,
        cuisine_types=rest_row.cuisine_types or [],
        lat=rest_row.lat,
        lng=rest_row.lng,
        known_allergens=rest_row.known_allergens or [],
        allergen_confidence=rest_row.allergen_confidence or "low",
        meta=rest_row.meta or {},
    )

    # ── Step 2: Fetch user profile ─────────────────────────────────────────────
    user_profile = await _fetch_user_profile(str(uid), db)
    if user_profile is None:
        raise ValueError(f"User {uid} not found")

    # ── Step 3: LLM call → ExpandedDetail ────────────────────────────────────
    restaurant_dict = {
        "id": restaurant.id,
        "name": restaurant.name,
        "area": restaurant.area,
        "price_tier": restaurant.price_tier,
        "rating": restaurant.rating,
        "cuisine_types": restaurant.cuisine_types,
        "meta": restaurant.meta,
    }

    expand_prompt = build_expand_detail_prompt(
        restaurant=restaurant_dict,
        reviews=review_texts,
        user_profile=user_profile,
    )

    try:
        raw: dict[str, Any] = await call_gemma_json(expand_prompt)
    except GemmaError as exc:
        logger.error("Expand detail LLM call failed for restaurant %d: %s", restaurant_id, exc)
        raw = {}

    # ── Step 4: AllergyGuard annotation ──────────────────────────────────────
    guard_result = _allergy_guard.check([restaurant], user_profile["allergies"])
    annotated = (
        guard_result.safe_restaurants[0]
        if guard_result.safe_restaurants
        else (guard_result.flagged_restaurants[0] if guard_result.flagged_restaurants else restaurant)
    )

    worst_confidence = "high"
    if annotated.allergy_warnings:
        # Pick the lowest confidence level among warnings
        conf_rank = {"high": 0, "medium": 1, "low": 2}
        worst_confidence = min(
            (w.confidence for w in annotated.allergy_warnings),
            key=lambda c: conf_rank.get(c, 1),
        )

    allergy_detail = AllergyDetail(
        is_safe=annotated.allergy_safe,
        confidence=worst_confidence,
        warnings=annotated.allergy_warnings,
        safe_note=(
            raw.get("allergy_detail", {}).get("safe_note")
            if annotated.allergy_safe
            else None
        ),
    )

    # ── Assemble ExpandedDetail, falling back gracefully on LLM failures ──────
    def _safe_str(key: str, default: str = "") -> str:
        return str(raw.get(key, default))

    def _safe_list_str(key: str) -> list[str]:
        val = raw.get(key, [])
        if isinstance(val, list):
            return [str(i) for i in val]
        return []

    highlights_raw = raw.get("highlights", [])
    highlights: list[Highlight] = []
    if isinstance(highlights_raw, list):
        for h in highlights_raw[:5]:
            if isinstance(h, dict):
                highlights.append(
                    Highlight(
                        emoji=str(h.get("emoji", "✨")),
                        text=str(h.get("text", "")),
                    )
                )

    radar_raw = raw.get("radar_scores", {})
    if isinstance(radar_raw, dict):
        radar_scores = RadarScores(
            romance=float(radar_raw.get("romance", 5.0)),
            noise_level=float(radar_raw.get("noise_level", 5.0)),
            food_quality=float(radar_raw.get("food_quality", 5.0)),
            vegan_options=float(radar_raw.get("vegan_options", 5.0)),
            value_for_money=float(radar_raw.get("value_for_money", 5.0)),
        )
    else:
        radar_scores = RadarScores(
            romance=5.0, noise_level=5.0, food_quality=5.0,
            vegan_options=5.0, value_for_money=5.0,
        )

    expanded = ExpandedDetail(
        review_summary=_safe_str(
            "review_summary",
            f"Reviewers generally speak well of {restaurant.name}.",
        ),
        highlights=highlights or [Highlight(emoji="⭐", text="Well-reviewed by locals")],
        crowd_profile=_safe_str("crowd_profile", "A mixed crowd of locals and regulars."),
        best_for=_safe_list_str("best_for") or ["Casual dining"],
        avoid_if=_safe_list_str("avoid_if") or [],
        radar_scores=radar_scores,
        why_fit_paragraph=_safe_str(
            "why_fit_paragraph",
            f"{restaurant.name} aligns with several of your dining preferences.",
        ),
        allergy_detail=allergy_detail,
    )

    return ExpandedDetailResponse(
        restaurant_id=restaurant_id,
        expanded_detail=expanded,
    )


# ── Pre-warm task ─────────────────────────────────────────────────────────────


async def prewarm_recommendations(uid: UUID, _db: AsyncSession) -> None:  # noqa: ARG001
    """
    Fire-and-forget pre-warming: compute fresh recommendations for `uid` and
    write the result to cache so the next page visit is instant.

    Uses its own AsyncSessionLocal session — does not share a session with
    the caller.  Silently swallows ALL exceptions.
    """
    try:
        async with AsyncSessionLocal() as session:
            await get_recommendations(uid=uid, db=session, limit=10, refresh=True)
            logger.debug("Recommendations pre-warmed for user %s.", uid)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Prewarm silently swallowed error for user %s: %s", uid, exc)
