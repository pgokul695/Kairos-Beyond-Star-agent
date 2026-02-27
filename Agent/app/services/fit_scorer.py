"""
Algorithmic pre-filter for the v2 recommendation pipeline.

Stage 1 of a two-stage scoring system:
  AlgorithmicPreFilter  — pure Python, 50 → 15, zero LLM calls.
  AgenticScorer         — one batched LLM call on the 15 survivors (in recommendation_service).

Scoring dimensions (max 100 pts):
  cuisine_affinity   30 pts
  vibe_match         25 pts
  price_comfort      20 pts
  dietary_fit        15 pts
  rating_baseline    10 pts   (replaces allergy_safety from v1)

Hard-drop rule:
  If any item in the restaurant's cuisine_type matches the user's
  cuisine_aversions the restaurant is excluded entirely (score = -1,
  never appears in output).

Usage::
    snapshot = UserProfileSnapshot.from_dict(user_profile_dict)
    pre_filter = AlgorithmicPreFilter()
    scored = pre_filter.score_all(candidates, snapshot, top_k=15)
    # scored: list[tuple[RestaurantResult, int]] sorted desc by score
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.schemas.restaurant import RestaurantResult

logger = logging.getLogger(__name__)

# ── Normalisation helpers ──────────────────────────────────────────────────────

_PRICE_TIER_ORDER: dict[str, int] = {
    "budget":      0,
    "affordable":  1,
    "mid-range":   2,
    "upscale":     3,
    "fine-dining": 4,
}


def _norm(text: str) -> str:
    """Lowercase + strip for fuzzy-matching tags."""
    return text.lower().strip()


def _norm_set(items: list[str]) -> set[str]:
    return {_norm(i) for i in items if i}


def _r_cuisine(r: RestaurantResult) -> str:
    """Return a single normalised cuisine string for matching."""
    # cuisine_types is the canonical list; join for substring matching
    return _norm(" ".join(r.cuisine_types or []))


def _r_vibes(r: RestaurantResult) -> list[str]:
    """Vibe tags live in the meta JSONB column."""
    return (r.meta or {}).get("vibe_tags", []) or []


def _r_dietary(r: RestaurantResult) -> list[str]:
    """Dietary options live in the meta JSONB column."""
    return (r.meta or {}).get("dietary_options", []) or []


# ── UserProfileSnapshot ────────────────────────────────────────────────────────


@dataclass(slots=True)
class UserProfileSnapshot:
    """
    Immutable view of the fields needed for algorithmic scoring.

    Created from the raw ``user_profile`` dict stored in the DB so the scorer
    never touches the DB directly.
    """

    cuisine_affinity:      frozenset[str] = field(default_factory=frozenset)
    cuisine_aversions:     frozenset[str] = field(default_factory=frozenset)
    vibe_tags:             frozenset[str] = field(default_factory=frozenset)
    dietary_flags:         frozenset[str] = field(default_factory=frozenset)
    preferred_price_tiers: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_dict(cls, profile: dict) -> "UserProfileSnapshot":
        """Build a snapshot from the dict stored in users.preferences / columns."""
        return cls(
            cuisine_affinity=frozenset(
                _norm(c) for c in profile.get("cuisine_affinity", []) if c
            ),
            cuisine_aversions=frozenset(
                _norm(c) for c in profile.get("cuisine_aversions", []) if c
            ),
            vibe_tags=frozenset(
                _norm(v) for v in profile.get("vibe_tags", []) if v
            ),
            dietary_flags=frozenset(
                _norm(d) for d in profile.get("dietary_flags", []) if d
            ),
            preferred_price_tiers=frozenset(
                _norm(p) for p in profile.get("preferred_price_tiers", []) if p
            ),
        )

    @property
    def is_empty(self) -> bool:
        """True when user has no stored preference signals at all."""
        return not any([
            self.cuisine_affinity,
            self.vibe_tags,
            self.dietary_flags,
            self.preferred_price_tiers,
        ])


# ── AlgorithmicPreFilter ───────────────────────────────────────────────────────


class AlgorithmicPreFilter:
    """
    Pure-Python, zero-latency pre-filter.

    Scores each candidate on five dimensions and returns the top *top_k*
    by descending score.  Restaurants that hard-fail the aversion check are
    excluded before scoring — they never appear in the output.

    An instance is stateless; create one singleton per process.
    """

    # ── Dimension weights ──────────────────────────────────────────────────────

    CUISINE_W  = 30
    VIBE_W     = 25
    PRICE_W    = 20
    DIETARY_W  = 15
    RATING_W   = 10

    # ── Cuisine affinity scoring ───────────────────────────────────────────────

    def _cuisine_score(
        self,
        restaurant: RestaurantResult,
        snapshot: UserProfileSnapshot,
    ) -> int:
        """
        +30 exact match, +15 partial token overlap, 15 neutral (no preference).
        Hard-drop tested upstream; aversion restaurants never reach here.
        """
        if not snapshot.cuisine_affinity:
            return self.CUISINE_W // 2  # neutral: 15

        r_cuisine = _r_cuisine(restaurant)
        r_tokens  = set(r_cuisine.split())

        for affinity in snapshot.cuisine_affinity:
            if affinity == r_cuisine or affinity in r_cuisine:
                return self.CUISINE_W

        affinity_tokens: set[str] = set()
        for a in snapshot.cuisine_affinity:
            affinity_tokens.update(a.split())

        if r_tokens & affinity_tokens:
            return self.CUISINE_W // 2  # 15

        return 0

    # ── Vibe match ────────────────────────────────────────────────────────────

    def _vibe_score(
        self,
        restaurant: RestaurantResult,
        snapshot: UserProfileSnapshot,
    ) -> int:
        if not snapshot.vibe_tags:
            return self.VIBE_W // 2  # neutral: 12

        r_vibes = _norm_set(_r_vibes(restaurant))
        overlap = len(r_vibes & snapshot.vibe_tags)

        if overlap == 0:
            return 0
        # 1 overlap → 12, 2 → 20, 3+ → 25
        return min(self.VIBE_W, 12 + (overlap - 1) * 8)

    # ── Price comfort ─────────────────────────────────────────────────────────

    def _price_score(
        self,
        restaurant: RestaurantResult,
        snapshot: UserProfileSnapshot,
    ) -> int:
        if not snapshot.preferred_price_tiers:
            return self.PRICE_W // 2  # neutral: 10

        r_tier = _norm(restaurant.price_tier or "")
        if r_tier in snapshot.preferred_price_tiers:
            return self.PRICE_W

        # Tolerate one tier away
        r_order = _PRICE_TIER_ORDER.get(r_tier, -1)
        for pref in snapshot.preferred_price_tiers:
            p_order = _PRICE_TIER_ORDER.get(pref, -1)
            if r_order != -1 and p_order != -1 and abs(r_order - p_order) == 1:
                return self.PRICE_W // 2  # 10

        return 0

    # ── Dietary fit ───────────────────────────────────────────────────────────

    def _dietary_score(
        self,
        restaurant: RestaurantResult,
        snapshot: UserProfileSnapshot,
    ) -> int:
        if not snapshot.dietary_flags:
            return self.DIETARY_W  # no dietary needs → full marks

        r_dietary = _norm_set(_r_dietary(restaurant))
        matched   = len(r_dietary & snapshot.dietary_flags)
        needed    = len(snapshot.dietary_flags)

        if needed == 0:
            return self.DIETARY_W

        return round(self.DIETARY_W * matched / needed)

    # ── Rating baseline ───────────────────────────────────────────────────────

    def _rating_score(self, restaurant: RestaurantResult) -> int:
        """
        +10 rating ≥ 4.5
        +7  rating ≥ 4.0
        +4  rating ≥ 3.5
         0  below 3.5 or unknown
        """
        r = restaurant.rating  # RestaurantResult uses .rating not .average_rating
        if r is None:
            return 0
        if r >= 4.5:
            return 10
        if r >= 4.0:
            return 7
        if r >= 3.5:
            return 4
        return 0

    # ── Hard-drop check ───────────────────────────────────────────────────────

    def _is_aversion_hit(
        self,
        restaurant: RestaurantResult,
        snapshot: UserProfileSnapshot,
    ) -> bool:
        """Return True if the restaurant's cuisine matches any stored aversion."""
        if not snapshot.cuisine_aversions:
            return False
        r_cuisine = _r_cuisine(restaurant)
        r_tokens  = set(r_cuisine.split())
        for aversion in snapshot.cuisine_aversions:
            if aversion == r_cuisine or aversion in r_cuisine:
                return True
            if r_tokens & set(aversion.split()):
                return True
        return False

    # ── score_all (public) ────────────────────────────────────────────────────

    def score_all(
        self,
        candidates: list[RestaurantResult],
        snapshot: UserProfileSnapshot,
        top_k: int = 15,
    ) -> list[tuple[RestaurantResult, int]]:
        """
        Score all candidates and return the top *top_k* by descending score.

        Restaurants that hit the hard-drop aversion rule are excluded entirely.

        Args:
            candidates: Up to ~50 restaurants from hybrid_search.
            snapshot:   Derived from the user's stored profile.
            top_k:      How many survivors to return (default 15).

        Returns:
            List of (RestaurantResult, score) tuples, highest score first.
        """
        scored: list[tuple[RestaurantResult, int]] = []

        for r in candidates:
            if self._is_aversion_hit(r, snapshot):
                logger.debug(
                    "Hard-drop restaurant %s (%s) — cuisine aversion hit",
                    r.id, r.name,
                )
                continue

            score = (
                self._cuisine_score(r, snapshot)
                + self._vibe_score(r, snapshot)
                + self._price_score(r, snapshot)
                + self._dietary_score(r, snapshot)
                + self._rating_score(r)
            )
            scored.append((r, score))

        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:top_k]
