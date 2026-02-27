"""
Prompt template builders for all Gemma LLM calls.
All prompt strings live here — no hardcoded prompts elsewhere in the codebase.
"""

from __future__ import annotations

import json
from typing import Any


# ── Decomposition ────────────────────────────────────────────────────────────


def build_decomposition_prompt(
    message: str,
    user_context: str,
    history: list[dict[str, str]],
    allergy_context: str,
) -> str:
    """
    Build the prompt for Gemma call #1: query decomposition.

    The SAFETY section is mandatory and always placed before the user query.
    Anaphylactic allergens must always appear in sql_filters.exclude_allergens.
    """
    history_text = ""
    if history:
        history_lines = []
        for turn in history[-6:]:   # last 3 turns
            role = turn.get("role", "user").capitalize()
            history_lines.append(f"{role}: {turn.get('content', '')}")
        history_text = "\n".join(history_lines) + "\n"

    return f"""You are Kairos, a restaurant recommendation AI for Bangalore.
Your job is to decompose the user's query into a structured search plan.

## USER CONTEXT
{user_context}

## SAFETY — USER ALLERGIES (NEVER SKIP)
{allergy_context}
Always populate sql_filters.exclude_allergens with any allergen listed
as 'anaphylactic' severity. This is not optional.

## CONVERSATION HISTORY
{history_text}
## CURRENT USER MESSAGE
{message}

## OUTPUT FORMAT
Output only valid JSON matching the schema below.
No markdown fences. No preamble. No explanation.

{{
  "intent": "find_restaurant" | "get_info" | "compare" | "clarify",
  "sql_filters": {{
    "price_tiers": ["$$", "$$$"],
    "cuisine_types": ["south indian"],
    "area": "Koramangala",
    "min_rating": 4.0,
    "exclude_allergens": []
  }},
  "vector_query": "quiet romantic vegan anniversary dinner",
  "ui_preference": "radar_comparison" | "map_view" | "restaurant_list" | "text",
  "needs_clarification": false,
  "clarification_question": null
}}"""


# ── Evaluation ───────────────────────────────────────────────────────────────


def build_evaluation_prompt(
    message: str,
    user_context: str,
    restaurants_json: str,
    allergy_context: str,
) -> str:
    """
    Build the prompt for Gemma call #2: scoring and ranking restaurants.

    Includes allergy context so Gemma factors dietary compliance into scores.
    Returns a JSON array with scores for each restaurant.
    """
    return f"""You are Kairos, a restaurant recommendation AI for Bangalore.
Score the following restaurants for the user's query.

## USER CONTEXT
{user_context}

## SAFETY — USER ALLERGIES
{allergy_context}

## USER QUERY
{message}

## RESTAURANTS TO SCORE
{restaurants_json}

## SCORING DIMENSIONS (0–10 each)
- romance: how romantic / intimate is the atmosphere
- noise_level: how quiet / peaceful (10 = very quiet)
- food_quality: quality and variety of food
- vegan_options: availability of vegan / plant-based dishes
- value_for_money: price vs quality ratio

## OUTPUT FORMAT
Output only a valid JSON array. No markdown fences. No preamble. No explanation.
Each element must have: id, romance, noise_level, food_quality, vegan_options, value_for_money.

Example:
[
  {{"id": 1, "romance": 8.5, "noise_level": 7.0, "food_quality": 8.0, "vegan_options": 6.0, "value_for_money": 7.5}},
  {{"id": 2, "romance": 6.0, "noise_level": 9.0, "food_quality": 9.0, "vegan_options": 8.5, "value_for_money": 8.0}}
]"""


# ── Profiler ─────────────────────────────────────────────────────────────────


def build_profiler_prompt(message: str, response_summary: str) -> str:
    """
    Build the prompt to extract preference signals from a chat turn.

    NEVER returns allergy fields — allergies are updated only via the
    PATCH /users/{uid}/allergies endpoint from the Backend.
    """
    return f"""You are extracting user preference signals from a dining conversation.

## USER MESSAGE
{message}

## AGENT RESPONSE SUMMARY
{response_summary}

## YOUR TASK
Extract new preference signals ONLY from:
- dietary: dietary preferences (e.g. "vegan", "vegetarian", "halal")
- vibes: atmosphere preferences (e.g. "quiet", "romantic", "outdoor")
- cuisine_affinity: cuisines the user seems to like
- cuisine_aversion: cuisines the user seems to dislike
- price_comfort: price tiers the user is comfortable with (e.g. ["$$", "$$$"])

## RULES
- NEVER return allergy fields — allergies are never inferred from chat
- Only include fields where you found clear evidence in the conversation
- Output {{}} if nothing new was learned

## OUTPUT FORMAT
Output only valid JSON. No markdown fences. No preamble. No explanation.

Example:
{{"dietary": ["vegan"], "vibes": ["quiet", "romantic"], "cuisine_affinity": ["south indian"]}}"""


# ── Context builders ─────────────────────────────────────────────────────────


def build_user_context(preferences: dict[str, Any]) -> str:
    """Format user preferences as a human-readable context string for prompts."""
    parts = []

    dietary = preferences.get("dietary", [])
    if dietary:
        parts.append(f"Dietary preferences: {', '.join(dietary)}")

    vibes = preferences.get("vibes", [])
    if vibes:
        parts.append(f"Atmosphere preferences: {', '.join(vibes)}")

    cuisine_affinity = preferences.get("cuisine_affinity", [])
    if cuisine_affinity:
        parts.append(f"Cuisine preferences: {', '.join(cuisine_affinity)}")

    cuisine_aversion = preferences.get("cuisine_aversion", [])
    if cuisine_aversion:
        parts.append(f"Cuisines to avoid: {', '.join(cuisine_aversion)}")

    price_comfort = preferences.get("price_comfort", [])
    if price_comfort:
        parts.append(f"Price comfort: {', '.join(price_comfort)}")

    location_bias = preferences.get("location_bias", {})
    if location_bias and location_bias.get("area"):
        area = location_bias["area"]
        radius = location_bias.get("radius_km", 5)
        parts.append(f"Preferred location: {area} (within {radius} km)")

    custom_notes = preferences.get("custom_notes", "")
    if custom_notes:
        parts.append(f"Notes: {custom_notes}")

    return "\n".join(parts) if parts else "No preferences set."


def build_allergy_context(allergies: dict[str, Any]) -> str:
    """
    Format allergy data as a safety-critical context string.
    This string is inserted into every Gemma prompt.
    """
    confirmed = allergies.get("confirmed", [])
    intolerances = allergies.get("intolerances", [])
    severity = allergies.get("severity", {})

    if not confirmed and not intolerances:
        return "No known allergies on file."

    parts = ["SAFETY-CRITICAL ALLERGY INFORMATION:"]

    if confirmed:
        allergen_details = []
        for allergen in confirmed:
            sev = severity.get(allergen, "severe")
            allergen_details.append(f"{allergen} ({sev})")
        parts.append(f"  Confirmed allergens: {', '.join(allergen_details)}")

    if intolerances:
        parts.append(f"  Intolerances: {', '.join(intolerances)}")

    anaphylactic = [a for a in confirmed if severity.get(a) == "anaphylactic"]
    if anaphylactic:
        parts.append(
            f"  ⚠️  ANAPHYLACTIC ALLERGENS (MUST be in exclude_allergens): "
            f"{', '.join(anaphylactic)}"
        )

    return "\n".join(parts)


# ── Recommendation fit explanation ────────────────────────────────────────────


def build_fit_explanation_prompt(
    restaurants: list[dict[str, Any]],
    user_profile: dict[str, Any],
) -> str:
    """
    Build the batch prompt for generating consolidated_review strings.

    Sends all selected restaurants in a single LLM call.
    Returns a JSON array — one object per restaurant, keyed by restaurant_id.
    """
    restaurants_json = json.dumps(restaurants, ensure_ascii=False, indent=2)

    # Build a readable user context for the prompt
    dietary = user_profile.get("dietary_flags", [])
    vibes   = user_profile.get("vibe_tags", [])
    cuisine = user_profile.get("cuisine_affinity", [])
    price   = user_profile.get("preferred_price_tiers", [])

    user_ctx_parts: list[str] = []
    if dietary:
        user_ctx_parts.append(f"Dietary: {', '.join(dietary)}")
    if vibes:
        user_ctx_parts.append(f"Vibes: {', '.join(vibes)}")
    if cuisine:
        user_ctx_parts.append(f"Cuisine affinity: {', '.join(cuisine)}")
    if price:
        user_ctx_parts.append(f"Price comfort: {', '.join(price)}")
    user_ctx = "\n".join(user_ctx_parts) if user_ctx_parts else "No preferences set."

    return f"""You are Kairos, a restaurant intelligence AI for Bangalore.
Your task is to write a concise consolidated_review for each restaurant below,
personalised to the user's preferences.

## USER PROFILE
{user_ctx}

## RESTAURANTS
{restaurants_json}

## TASK
For each restaurant, produce:
  - "restaurant_id": the integer id of the restaurant
  - "consolidated_review": a single sentence ≤ 160 characters.
    Requirements:
      - Must include ONE specific concrete detail: a dish name, a defining
        characteristic, or a direct quote-style insight from reviewer sentiment.
      - Written in present tense.
      - NEVER generic ("great food and ambiance"). Always specific.
      - Do NOT mention any allergen that is in the user's dietary restrictions.
        AllergyGuard handles that separately; do not duplicate allergy language here.
  - "fit_tags_override": null  (always null — use the algorithmic tags)

## SAFETY
Never mention the user's allergens or intolerances in the consolidated_review.
AllergyGuard annotates allergen warnings separately. Your job is only the review copy.

## OUTPUT FORMAT
Output only a valid JSON array. No markdown fences. No preamble. No explanation.
One element per restaurant, in the same order as the input list.

Example:
[
  {{
    "restaurant_id": 42,
    "consolidated_review": "Famous for their ghee-roast dosa served on a banana leaf — the crispy edges alone are worth the trip.",
    "fit_tags_override": null
  }}
]"""


# ── Recommendation expand detail ──────────────────────────────────────────────


def build_expand_detail_prompt(
    restaurant: dict[str, Any],
    reviews: list[str],
    user_profile: dict[str, Any],
) -> str:
    """
    Build the prompt for generating a fully structured ExpandedDetail payload.

    Uses a single restaurant + its reviews + the user profile.
    Returns JSON matching the ExpandedDetail schema exactly.
    """
    restaurant_json = json.dumps(restaurant, ensure_ascii=False, indent=2)
    reviews_json    = json.dumps(reviews[:10], ensure_ascii=False, indent=2)

    dietary = user_profile.get("dietary_flags", [])
    vibes   = user_profile.get("vibe_tags", [])
    cuisine = user_profile.get("cuisine_affinity", [])
    price   = user_profile.get("preferred_price_tiers", [])

    user_ctx_parts: list[str] = []
    if dietary:
        user_ctx_parts.append(f"Dietary preferences: {', '.join(dietary)}")
    if vibes:
        user_ctx_parts.append(f"Vibe preferences: {', '.join(vibes)}")
    if cuisine:
        user_ctx_parts.append(f"Cuisine affinity: {', '.join(cuisine)}")
    if price:
        user_ctx_parts.append(f"Price comfort: {', '.join(price)}")
    user_ctx = "\n".join(user_ctx_parts) if user_ctx_parts else "No preferences set."

    return f"""You are Kairos, a restaurant intelligence AI for Bangalore.
Analyse the restaurant and its reviews below, then generate a richly structured
ExpandedDetail payload that explains why this restaurant fits the user.

## USER PROFILE
{user_ctx}

## RESTAURANT
{restaurant_json}

## REVIEWS (up to 10, most recent first)
{reviews_json}

## OUTPUT SCHEMA
Return ONLY a JSON object with EXACTLY these fields:

{{
  "review_summary": "A full paragraph summarising all reviews — tone, highlights, recurring praise, recurring complaints. 3–5 sentences.",
  "highlights": [
    {{"emoji": "<single relevant emoji>", "text": "<concise highlight text>"}},
    ...   // 3–5 items total
  ],
  "crowd_profile": "Describe the actual customer type inferred from review signals — e.g. regulars, demographics, occasion types. Not generic language.",
  "best_for": ["<occasion tag>", ...],   // 2–4 items grounded in review content
  "avoid_if": ["<situation>", ...],      // 1–3 items grounded in review content
  "radar_scores": {{
    "romance": <float 0–10>,
    "noise_level": <float 0–10>,
    "food_quality": <float 0–10>,
    "vegan_options": <float 0–10>,
    "value_for_money": <float 0–10>
  }},
  "why_fit_paragraph": "Reference the user's specific stored preferences by name e.g. 'your vegan diet', 'your preference for quiet vibes'. Explain concretely how this restaurant satisfies them. 2–3 sentences.",
  "allergy_detail": {{
    "is_safe": <bool>,
    "confidence": "high" | "medium" | "low",
    "warnings": [],
    "safe_note": "<optional short sentence — present only when is_safe is true>"
  }}
}}

## RULES
- highlights: each must start with a single relevant emoji (the emoji goes in the "emoji" field, not the "text" field)
- crowd_profile: infer from actual review language — never invent
- why_fit_paragraph: must explicitly reference the user's named preferences listed above
- best_for / avoid_if: must be grounded in actual review signals, not invented
- radar_scores: infer from review sentiment; use 5.0 as neutral when signal is insufficient
- allergy_detail.warnings: leave as empty array — AllergyGuard handles this separately
- Output only valid JSON. No markdown fences. No preamble. No trailing text."""
