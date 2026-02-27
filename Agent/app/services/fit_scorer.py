"""
FitScorer — pure algorithmic restaurant–user fit scoring.

Zero LLM calls. Zero DB calls. Takes pre-fetched data; returns a score and
tag list. Designed to run on up to 50 candidates per request without I/O.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from app.schemas.restaurant import RestaurantResult

logger = logging.getLogger(__name__)

# Price tier ordering for adjacency calculation
_PRICE_TIER_ORDER: list[str] = ["$", "$$", "$$$", "$$$$"]


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class FitTag:
    """A single human-readable fit dimension tag."""

    label: str
    type: Literal["cuisine", "vibe", "price", "dietary", "allergy_safe"]


@dataclass
class FitResult:
    """Result of FitScorer.score() for one restaurant."""

    score: int               # 0–100
    fit_tags: list[FitTag] = field(default_factory=list)


# ── Scorer ────────────────────────────────────────────────────────────────────


class FitScorer:
    """
    Scores a restaurant against a user profile on five dimensions.

    Dimension breakdown (total 100 pts):
      Cuisine affinity   30 pts
      Vibe match         25 pts
      Price comfort      20 pts
      Dietary compat     15 pts
      Allergy safety     10 pts
    """

    def score(
        self,
        restaurant: RestaurantResult,
        user_profile: dict[str, Any],
    ) -> FitResult:
        """
        Compute fit score and generate up to 4 FitTag labels.

        Parameters
        ----------
        restaurant:
            A fully populated RestaurantResult (including allergy_safe /
            allergy_warnings from a prior AllergyGuard pass).
        user_profile:
            Dict with keys: cuisine_affinity, cuisine_aversion, vibe_tags,
            preferred_price_tiers, dietary_flags, allergy_safe (bool),
            allergy_warnings (list).
        """
        tagged: list[tuple[int, FitTag]] = []

        cuisine_pts, cuisine_tag = self._score_cuisine(restaurant, user_profile)
        vibe_pts, vibe_tag       = self._score_vibe(restaurant, user_profile)
        price_pts, price_tag     = self._score_price(restaurant, user_profile)
        dietary_pts, dietary_tags = self._score_dietary(restaurant, user_profile)
        allergy_pts, allergy_tag  = self._score_allergy(restaurant)

        total = cuisine_pts + vibe_pts + price_pts + dietary_pts + allergy_pts
        total = max(0, min(100, total))   # clamp

        # Collect (points, tag) for ranking — we pick highest-scoring dims first
        if cuisine_tag:
            tagged.append((cuisine_pts, cuisine_tag))
        if vibe_tag:
            tagged.append((vibe_pts, vibe_tag))
        if price_tag:
            tagged.append((price_pts, price_tag))
        for dtag in dietary_tags:
            tagged.append((dietary_pts, dtag))
        if allergy_tag:
            tagged.append((allergy_pts, allergy_tag))

        # Sort descending by points, keep top 4
        tagged.sort(key=lambda x: x[0], reverse=True)
        top_tags = [t for _, t in tagged[:4]]

        return FitResult(score=total, fit_tags=top_tags)

    # ── Dimension scorers ─────────────────────────────────────────────────────

    def _score_cuisine(
        self,
        restaurant: RestaurantResult,
        user_profile: dict[str, Any],
    ) -> tuple[int, FitTag | None]:
        """
        +30 full overlap, +15 partial overlap (≥1 of N), -10 aversion hit.
        Returns (points_awarded, FitTag | None).
        """
        affinity: list[str] = [
            c.lower() for c in user_profile.get("cuisine_affinity", [])
        ]
        aversion: list[str] = [
            c.lower() for c in user_profile.get("cuisine_aversion", [])
        ]
        restaurant_cuisines: list[str] = [
            c.lower() for c in (restaurant.cuisine_types or [])
        ]

        if not affinity:
            return 0, None

        overlap = set(affinity) & set(restaurant_cuisines)
        aversion_hit = set(aversion) & set(restaurant_cuisines)

        if overlap:
            pts = 30 if len(overlap) >= len(affinity) else 15
            label = f"Matches your {next(iter(overlap)).title()} preference"
            tag = FitTag(label=label, type="cuisine")
            return pts, tag

        if aversion_hit:
            return -10, None

        return 0, None

    def _score_vibe(
        self,
        restaurant: RestaurantResult,
        user_profile: dict[str, Any],
    ) -> tuple[int, FitTag | None]:
        """
        +5 per overlapping vibe tag, capped at 25.
        Returns (points_awarded, FitTag | None).
        """
        user_vibes: list[str] = [
            v.lower() for v in user_profile.get("vibe_tags", [])
        ]
        restaurant_vibes: list[str] = [
            v.lower() for v in (restaurant.meta.get("vibes", []) if restaurant.meta else [])
        ]

        if not user_vibes or not restaurant_vibes:
            return 0, None

        overlap = [v for v in user_vibes if v in restaurant_vibes]
        if not overlap:
            return 0, None

        pts = min(len(overlap) * 5, 25)
        first_vibe = overlap[0].title()
        tag = FitTag(
            label=f"Known for {first_vibe} — your top vibe tag",
            type="vibe",
        )
        return pts, tag

    def _score_price(
        self,
        restaurant: RestaurantResult,
        user_profile: dict[str, Any],
    ) -> tuple[int, FitTag | None]:
        """
        +20 exact match, +10 one tier adjacent, 0 two or more tiers away.
        Returns (points_awarded, FitTag | None).
        """
        preferred: list[str] = user_profile.get("preferred_price_tiers", [])
        rest_tier: str | None = restaurant.price_tier

        if not preferred or not rest_tier:
            return 0, None

        if rest_tier in preferred:
            tag = FitTag(
                label=f"Within your {rest_tier} comfort zone",
                type="price",
            )
            return 20, tag

        # Check adjacency using tier order
        try:
            rest_idx = _PRICE_TIER_ORDER.index(rest_tier)
        except ValueError:
            return 0, None

        for pref_tier in preferred:
            try:
                pref_idx = _PRICE_TIER_ORDER.index(pref_tier)
                if abs(rest_idx - pref_idx) == 1:
                    tag = FitTag(
                        label=f"Within your {pref_tier} comfort zone",
                        type="price",
                    )
                    return 10, tag
            except ValueError:
                continue

        return 0, None

    def _score_dietary(
        self,
        restaurant: RestaurantResult,
        user_profile: dict[str, Any],
    ) -> tuple[int, list[FitTag]]:
        """
        +5 per matching dietary flag, capped at 15.
        Returns (total_points, list[FitTag]).
        """
        user_dietary: list[str] = [
            d.lower() for d in user_profile.get("dietary_flags", [])
        ]
        rest_dietary: list[str] = [
            d.lower()
            for d in (restaurant.meta.get("dietary_flags", []) if restaurant.meta else [])
        ]

        if not user_dietary or not rest_dietary:
            return 0, []

        matched = [d for d in user_dietary if d in rest_dietary]
        if not matched:
            return 0, []

        pts = min(len(matched) * 5, 15)
        tags = [
            FitTag(label=f"{flag.title()}-friendly", type="dietary")
            for flag in matched
        ]
        return pts, tags

    def _score_allergy(
        self,
        restaurant: RestaurantResult,
    ) -> tuple[int, FitTag | None]:
        """
        +10 if allergy_safe=True, +5 if only intolerance-level warnings, 0 if severe.
        Expects restaurant already annotated by AllergyGuard.
        Returns (points_awarded, FitTag | None).
        """
        if restaurant.allergy_safe and not restaurant.allergy_warnings:
            tag = FitTag(label="Safe for your allergy profile", type="allergy_safe")
            return 10, tag

        if not restaurant.allergy_warnings:
            return 10, FitTag(label="Safe for your allergy profile", type="allergy_safe")

        severities = {w.severity for w in restaurant.allergy_warnings}
        danger_levels = {"anaphylactic", "severe"}
        if severities & danger_levels:
            return 0, None

        # Only intolerance / moderate warnings remain
        return 5, None
