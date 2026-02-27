"""
Recommendations router — personalised restaurant picks outside the chat interface.

GET  /recommendations/{uid}
GET  /recommendations/{uid}/{restaurant_id}/expand

Both endpoints authenticate via X-User-ID header (same pattern as /chat).
Neither endpoint uses SSE — they return synchronous JSON responses.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.recommendation import ExpandedDetailResponse, RecommendationPayload
from app.services.recommendation_service import get_expanded_detail, get_recommendations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _validate_uid(x_user_id: str) -> uuid.UUID:
    """Parse and validate the X-User-ID header as UUID v4."""
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format — must be UUID v4",
            headers={"X-Error-Code": "MISSING_USER_ID"},
        )


@router.get(
    "/{uid}",
    response_model=RecommendationPayload,
    summary="Get personalised restaurant recommendations for a user",
)
async def recommendations(
    uid: str,
    limit: int = Query(default=10, ge=1, le=25, description="Max number of results (1–25)"),
    refresh: bool = Query(default=False, description="Bypass cache and recompute"),
    x_user_id: str = Header(..., alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
) -> RecommendationPayload:
    """
    Return a ranked, personalised list of restaurant recommendations for `uid`.

    The full pipeline runs synchronously (no SSE):
      1. Fetch user preferences + allergy profile
      2. SQL candidate retrieval (top 50, hard-filter anaphylactic allergens)
      3. Algorithmic FitScorer on all candidates
      4. Top-N selection
      5. AllergyGuard annotation
      6. LLM batch consolidated-review generation
      7. Cache + return

    Results are cached per user per calendar day and served instantly on repeat
    visits. Pass `?refresh=true` to force a fresh computation.
    """
    auth_uid = _validate_uid(x_user_id)

    # Ensure the requesting user matches the path uid
    try:
        path_uid = uuid.UUID(uid)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid uid in path — must be UUID v4",
        )

    if auth_uid != path_uid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-User-ID does not match uid in path",
        )

    try:
        return await get_recommendations(uid=auth_uid, db=db, limit=limit, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.exception("Recommendation pipeline failed for uid %s: %s", uid, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate recommendations — please try again",
        )


@router.get(
    "/{uid}/{restaurant_id}/expand",
    response_model=ExpandedDetailResponse,
    summary="Get rich expanded detail for a single recommendation card",
)
async def expand_recommendation(
    uid: str,
    restaurant_id: int,
    x_user_id: str = Header(..., alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
) -> ExpandedDetailResponse:
    """
    Generate a richly structured ExpandedDetail payload for one restaurant.

    Called lazily when the user opens a recommendation card.
    Always freshly computed — not cached.

    The LLM produces: review_summary, highlights, crowd_profile, best_for,
    avoid_if, radar_scores, why_fit_paragraph.  AllergyGuard annotates the
    allergy_detail.
    """
    auth_uid = _validate_uid(x_user_id)

    try:
        path_uid = uuid.UUID(uid)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid uid in path — must be UUID v4",
        )

    if auth_uid != path_uid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-User-ID does not match uid in path",
        )

    try:
        return await get_expanded_detail(
            uid=auth_uid,
            restaurant_id=restaurant_id,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        logger.exception(
            "Expand detail failed for uid %s restaurant %d: %s",
            uid, restaurant_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate expanded detail — please try again",
        )
