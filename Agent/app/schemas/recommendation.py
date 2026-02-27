"""Pydantic schemas for the recommendation endpoints.

All new models live here. Existing schema files are untouched.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas.restaurant import AllergyWarning, RadarScores, RestaurantResult


# ── Fit tags & scoring ────────────────────────────────────────────────────────


class FitTag(BaseModel):
    """A human-readable tag explaining one dimension of the restaurant–user fit."""

    label: str
    type: Literal["cuisine", "vibe", "price", "dietary", "allergy_safe"]


# ── Allergy summaries ─────────────────────────────────────────────────────────


class AllergySummary(BaseModel):
    """High-level allergy safety summary for a recommendation card."""

    is_safe: bool
    warnings: list[AllergyWarning]


class AllergyDetail(BaseModel):
    """Detailed allergy annotation returned in the expanded panel."""

    is_safe: bool
    confidence: str
    warnings: list[AllergyWarning]
    safe_note: Optional[str] = None


# ── Expanded panel ────────────────────────────────────────────────────────────


class Highlight(BaseModel):
    """A single emoji + text highlight item."""

    emoji: str
    text: str


class ExpandedDetail(BaseModel):
    """
    Rich data payload generated on-demand when the user expands a card.
    Produced by a dedicated LLM call with full review context.
    """

    review_summary: str
    highlights: list[Highlight]          # 3–5 items
    crowd_profile: str
    best_for: list[str]                  # 2–4 occasion tags
    avoid_if: list[str]                  # 1–3 items
    radar_scores: RadarScores
    why_fit_paragraph: str
    allergy_detail: AllergyDetail


class ExpandedDetailResponse(BaseModel):
    """Response envelope for GET /recommendations/{uid}/{restaurant_id}/expand."""

    restaurant_id: int
    expanded_detail: ExpandedDetail


# ── Recommendation list ───────────────────────────────────────────────────────


class RecommendationItem(BaseModel):
    """A single ranked recommendation card."""

    rank: int
    restaurant: RestaurantResult
    fit_score: int                            # 0–100 algorithmic score
    fit_tags: list[FitTag]                    # up to 4 dimensional tags
    consolidated_review: str                  # ≤160 chars, LLM-generated
    allergy_summary: AllergySummary
    expanded_detail: Optional[ExpandedDetail] = None   # null until /expand called


class RecommendationPayload(BaseModel):
    """Top-level response envelope for GET /recommendations/{uid}."""

    uid: str
    generated_at: datetime
    recommendations: list[RecommendationItem]
