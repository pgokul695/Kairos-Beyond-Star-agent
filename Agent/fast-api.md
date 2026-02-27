# Kairos Agent — Complete FastAPI Architecture Reference
### For GitHub Copilot (Claude Sonnet 4.6)

> This document is the single source of truth for every design decision, pattern,
> constraint, and extension point in the Kairos Agent FastAPI service.
> Read it top-to-bottom before touching any file. Every section contains runnable
> examples and explicit instructions for Copilot.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Repository Layout](#2-repository-layout)
3. [Configuration & Environment](#3-configuration--environment)
4. [Database Layer](#4-database-layer)
5. [ORM Models](#5-orm-models)
6. [Schemas (Pydantic)](#6-schemas-pydantic)
7. [Routers](#7-routers)
8. [Services](#8-services)
9. [Utils](#9-utils)
10. [Authentication Model](#10-authentication-model)
11. [Error Handling Contract](#11-error-handling-contract)
12. [Lifespan & Startup](#12-lifespan--startup)
13. [SSE Streaming Contract](#13-sse-streaming-contract)
14. [Background Task Pattern](#14-background-task-pattern)
15. [Recommendation Pipeline](#15-recommendation-pipeline)
16. [FitScorer Specification](#16-fitscorer-specification)
17. [Caching Layer](#17-caching-layer)
18. [AllergyGuard Safety Rules](#18-allergyguard-safety-rules)
19. [Prompt Engineering Guidelines](#19-prompt-engineering-guidelines)
20. [Adding a New Endpoint — Checklist](#20-adding-a-new-endpoint--checklist)
21. [Adding a New Service — Checklist](#21-adding-a-new-service--checklist)
22. [Invariants — Never Break These](#22-invariants--never-break-these)
23. [Docker & Deployment](#23-docker--deployment)
24. [Data Ingestion Scripts](#24-data-ingestion-scripts)

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        KAIROS PLATFORM                              │
│                                                                     │
│   ┌──────────┐   JWT Auth    ┌─────────────┐   X-Service-Token     │
│   │ Frontend │──────────────▶│   Backend   │──────────────────────▶│
│   │ (Next.js)│               │  (NestJS)   │                       │
│   └────┬─────┘               └──────┬──────┘                       │
│        │                            │                               │
│        │  X-User-ID (UUID v4)       │  X-User-ID / X-Service-Token │
│        │                            │                               │
│        ▼                            ▼                               │
│   ┌─────────────────────────────────────────────────────────┐      │
│   │                    KAIROS AGENT (FastAPI)                │      │
│   │                                                         │      │
│   │  POST /chat              → SSE stream (GenerativeUI)    │      │
│   │  GET  /recommendations   → JSON (RecommendationPayload) │      │
│   │  GET  /recommendations/expand → JSON (ExpandedDetail)   │      │
│   │  GET/POST/PATCH /users/* → JSON (user management)       │      │
│   │  GET  /health  /ready    → JSON (probes)                │      │
│   │                                                         │      │
│   │  ┌──────────┐  ┌───────────┐  ┌──────────────────────┐ │      │
│   │  │ Postgres │  │ pgvector  │  │  Google Generative AI │ │      │
│   │  │ (pg 16)  │  │(768-dim)  │  │  gemini-2.5-flash    │ │      │
│   │  └──────────┘  └───────────┘  │  + gemma-3-12b-it    │ │      │
│   │                               │  + gemini-embed-001   │ │      │
│   │                               └──────────────────────┘ │      │
│   └─────────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────────┘
```

**Kairos Agent is a single-responsibility FastAPI service.** Its jobs are:

| Responsibility | Details |
|---|---|
| Conversational AI | 5-step reasoning loop over chat turns, streamed as SSE |
| Personalised recommendations | Algorithmic scoring + LLM enrichment, synchronous JSON |
| Allergy safety | Hard SQL filter + AllergyGuard annotation on every result |
| User profile management | CRUD for preferences, allergies, interaction history |
| Embedding & vector search | Hybrid SQL + pgvector cosine similarity |
| Background profiling | Extract preference signals from every chat turn |

**The Agent never:**
- Authenticates users (Backend owns JWT)
- Infers allergies from chat (allergies = explicit user action only)
- Returns unguarded restaurant results (AllergyGuard is always the last step)

---

## 2. Repository Layout

```
Agent/
├── app/
│   ├── main.py                  ← FastAPI app, lifespan, CORS, router registration
│   ├── config.py                ← Pydantic Settings (env vars, lru_cache)
│   ├── database.py              ← Async engine, session factory, Base
│   ├── models/
│   │   ├── __init__.py          ← Re-exports all ORM models (needed by Base.metadata)
│   │   ├── user.py              ← User ORM
│   │   ├── restaurant.py        ← Restaurant ORM
│   │   ├── review.py            ← Review ORM (+ pgvector embedding column)
│   │   └── interaction.py       ← Interaction ORM (chat history log)
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── chat.py              ← POST /chat → SSE
│   │   ├── health.py            ← GET /health, GET /ready
│   │   ├── users.py             ← CRUD /users/* (service-token protected)
│   │   └── recommendations.py   ← GET /recommendations/* (user-id protected)
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── chat.py              ← ChatRequest, ChatMessage
│   │   ├── restaurant.py        ← RestaurantResult, GenerativeUIPayload, RadarScores, AllergyWarning
│   │   ├── user.py              ← UserCreate, UserRead, AllergiesPatch, etc.
│   │   └── recommendation.py    ← RecommendationPayload, ExpandedDetail, FitTag, etc.
│   ├── services/
│   │   ├── __init__.py
│   │   ├── gemma.py             ← Google Generative AI wrapper (primary + fallback)
│   │   ├── embedding.py         ← Google embedding API (768-dim, batched)
│   │   ├── hybrid_search.py     ← SQL filter + pgvector cosine search
│   │   ├── allergy_guard.py     ← Safety layer — always runs on every result
│   │   ├── orchestrator.py      ← 5-step chat reasoning loop (SSE generator)
│   │   ├── profiler.py          ← Background preference extractor
│   │   ├── fit_scorer.py        ← Pure Python scoring (no LLM, no DB)
│   │   └── recommendation_service.py ← Full recommendation pipeline + cache
│   └── utils/
│       ├── __init__.py
│       ├── prompts.py           ← ALL prompt builders live here (no inline prompts)
│       └── allergy_data.py      ← Canonical allergen definitions (single source of truth)
├── scripts/
│   ├── ingest.py                ← CSV → restaurant + review rows + embeddings
│   └── create_tables.py         ← Standalone table creation
├── data/
│   └── zomato.csv               ← Source dataset
├── components/
│   └── RecommendationFeed.jsx   ← React component tree (frontend)
├── docker-compose.yml           ← pgvector/pgvector:pg16
├── requirements.txt
├── run.sh                       ← uvicorn entrypoint
└── run_ingest.sh                ← ingest script entrypoint
```

**Copilot instruction:** new files always go in the correct directory above. Never create a service inside `routers/` or a schema inside `services/`.

---

## 3. Configuration & Environment

**File:** `app/config.py`

```python
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache

class Settings(BaseSettings):
    database_url: str = Field(..., env="DATABASE_URL")
    google_api_key: str = Field(..., env="GOOGLE_API_KEY")
    gemma_model: str = Field("gemini-2.5-flash", env="GEMMA_MODEL")
    gemma_fallback_model: str = Field("gemma-3-12b-it", env="GEMMA_FALLBACK_MODEL")
    embedding_model: str = Field("gemini-embedding-001", env="EMBEDDING_MODEL")
    embedding_dimensions: int = Field(768, env="EMBEDDING_DIMENSIONS")
    service_token: str = Field(..., env="SERVICE_TOKEN")
    allowed_origins: str = Field("https://kairos.gokulp.online,http://localhost:3000")
    app_env: str = Field("development", env="APP_ENV")
    log_level: str = Field("INFO", env="LOG_LEVEL")

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
```

**.env file (required keys):**
```dotenv
DATABASE_URL=postgresql+asyncpg://kairos:secret@localhost:5432/kairos
GOOGLE_API_KEY=AIza...
SERVICE_TOKEN=my-inter-service-secret
POSTGRES_DB=kairos
POSTGRES_USER=kairos
POSTGRES_PASSWORD=secret
```

**Rules for Copilot:**
- Import `settings` from `app.config` everywhere — never use `os.environ` directly.
- Never hardcode URLs, credentials, or model names.
- `Settings` fields marked `...` (no default) will raise `ValidationError` on startup if absent — this is intentional.

---

## 4. Database Layer

**File:** `app/database.py`

```python
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.config import settings

class Base(DeclarativeBase):
    pass

engine = create_async_engine(
    settings.database_url,
    echo=(settings.app_env == "development"),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)

async def get_db() -> AsyncSession:
    """FastAPI dependency — yields a session, rolls back on exception."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
```

**Session patterns used in the codebase:**

| Pattern | Where used | Why |
|---------|-----------|-----|
| `Depends(get_db)` | All routers | Standard per-request session |
| `async with AsyncSessionLocal() as db:` | Background tasks (`_save_interaction`, `_run_profiler`, `prewarm_recommendations`) | Background tasks run after the request session closes |

**Copilot instruction:** Background tasks MUST create their own `AsyncSessionLocal()` session. Never pass a request-scoped `db` session into `asyncio.create_task()`.

---

## 5. ORM Models

### User (`app/models/user.py`)

```python
class User(Base):
    __tablename__ = "users"

    uid                = Column(UUID(as_uuid=True), primary_key=True)
    preferences        = Column(JSONB, nullable=False, server_default="{}")
    allergies          = Column(JSONB, nullable=False, server_default="{}")  # SAFETY-CRITICAL
    allergy_flags      = Column(ARRAY(String), ...)   # denormalised — GIN index
    dietary_flags      = Column(ARRAY(String), ...)   # denormalised — GIN index
    vibe_tags          = Column(ARRAY(String), ...)   # denormalised — GIN index
    preferred_price_tiers = Column(ARRAY(String), ...)
    interaction_count  = Column(Integer, ...)
    last_active_at     = Column(TIMESTAMP(timezone=True), ...)
    created_at         = Column(TIMESTAMP(timezone=True), ...)
    updated_at         = Column(TIMESTAMP(timezone=True), ...)
```

**Key design decisions:**
- `allergies` (JSONB) stores `{"confirmed": [...], "intolerances": [...], "severity": {...}}` — never modified by the profiler
- `allergy_flags`, `dietary_flags`, `vibe_tags` are denormalised flat arrays for fast `&&` GIN array overlap queries
- `uid` = UUID provided by Backend — the Agent never generates UIDs

### Restaurant (`app/models/restaurant.py`)

```python
class Restaurant(Base):
    __tablename__ = "restaurants"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    name                = Column(Text, nullable=False)
    cuisine_types       = Column(ARRAY(Text), ...)
    price_tier          = Column(String(10), ...)    # '$' | '$$' | '$$$' | '$$$$'
    rating              = Column(Numeric(3, 1), ...)
    known_allergens     = Column(ARRAY(Text), ...)   # populated by ingest.py
    allergen_confidence = Column(String(10), ...)    # 'high' | 'medium' | 'low'
    meta                = Column(JSONB, ...)         # vibes, dietary_flags, etc.
    is_active           = Column(Boolean, ...)
```

**`meta` JSONB schema (used by FitScorer + queries):**
```json
{
  "vibes": ["quiet", "romantic", "outdoor"],
  "dietary_flags": ["vegan", "halal", "jain"],
  "tags": ["rooftop", "live music"]
}
```

### Review (`app/models/review.py`)

```python
class Review(Base):
    __tablename__ = "reviews"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    restaurant_id   = Column(Integer, ForeignKey("restaurants.id"))
    review_text     = Column(Text, nullable=False)
    embedding       = Column(Vector(768), nullable=True)   # pgvector column
    allergen_mentions = Column(ARRAY(Text), ...)
    review_date     = Column(Date, ...)
    review_rating   = Column(Numeric(3, 1), ...)
```

`embedding` is a `pgvector.sqlalchemy.Vector(768)` column. Populated by `ingest.py` using the `embed_texts()` batching service. Reviews with no embedding fall back to SQL-only ranking.

### Interaction (`app/models/interaction.py`)

Persisted by `_save_interaction()` after every chat turn. Fields include `uid`, `user_query`, `agent_response` (JSONB), `ui_type`, `restaurant_ids`, `allergy_warnings_shown`, `allergens_flagged`, `prompt_tokens`, `completion_tokens`.

---

## 6. Schemas (Pydantic)

### `app/schemas/restaurant.py` — core shared schemas

```python
class AllergyWarning(BaseModel):
    allergen: str          # canonical allergen name e.g. "peanuts"
    severity: str          # "anaphylactic"|"severe"|"moderate"|"intolerance"
    level: str             # "danger"|"warning"|"caution"|"info"
    emoji: str             # "🚨"|"⚠️"|"⚡"|"ℹ️"
    title: str
    message: str
    confidence: str        # "high"|"medium"|"low"
    confidence_note: Optional[str]   # present when confidence != "high"

class RadarScores(BaseModel):
    romance: float = 0.0
    noise_level: float = 0.0
    food_quality: float = 0.0
    vegan_options: float = 0.0
    value_for_money: float = 0.0

class RestaurantResult(BaseModel):
    id: int
    name: str
    area: Optional[str]
    address: Optional[str]
    price_tier: Optional[str]
    rating: Optional[float]
    votes: int = 0
    cuisine_types: list[str] = []
    url: Optional[str]
    lat: Optional[float]
    lng: Optional[float]
    known_allergens: list[str] = []
    allergen_confidence: str = "low"
    meta: dict[str, Any] = {}
    allergy_safe: bool = True                      # set by AllergyGuard
    allergy_warnings: list[AllergyWarning] = []    # set by AllergyGuard
    scores: Optional[RadarScores] = None           # radar_comparison only

class GenerativeUIPayload(BaseModel):
    ui_type: Literal["restaurant_list", "radar_comparison", "map_view", "text"]
    message: str
    restaurants: list[RestaurantResult] = []
    flagged_restaurants: list[RestaurantResult] = []
    has_allergy_warnings: bool = False
    follow_up_questions: Optional[list[str]] = None
```

**Copilot instruction:** `AllergyWarning`, `RadarScores`, `RestaurantResult`, `GenerativeUIPayload` are immutable shared contracts. Never modify these. New schemas go in a new schema file.

### `app/schemas/chat.py`

```python
class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_history: list[ChatMessage] = []
```

### `app/schemas/user.py`

Key models:
- `UserCreate` — POST /users/{uid} body
- `UserRead` — GET /users/{uid} response
- `AllergiesPatch` — PATCH /users/{uid}/allergies — FULL REPLACE, not merge
- `UserPreferencesPatch` — PATCH /users/{uid} — deep-merges preferences only
- `InteractionListResponse` — paginated GET /users/{uid}/interactions

### `app/schemas/recommendation.py`

```python
class FitTag(BaseModel):
    label: str
    type: Literal["cuisine", "vibe", "price", "dietary", "allergy_safe"]

class AllergySummary(BaseModel):
    is_safe: bool
    warnings: list[AllergyWarning]

class RecommendationItem(BaseModel):
    rank: int
    restaurant: RestaurantResult
    fit_score: int                  # 0–100
    fit_tags: list[FitTag]          # max 4
    consolidated_review: str        # ≤160 chars, LLM-generated
    allergy_summary: AllergySummary
    expanded_detail: Optional[ExpandedDetail] = None

class RecommendationPayload(BaseModel):
    uid: str
    generated_at: datetime
    recommendations: list[RecommendationItem]

class ExpandedDetail(BaseModel):
    review_summary: str
    highlights: list[Highlight]         # 3–5 items
    crowd_profile: str
    best_for: list[str]                 # 2–4 items
    avoid_if: list[str]                 # 1–3 items
    radar_scores: RadarScores
    why_fit_paragraph: str
    allergy_detail: AllergyDetail

class ExpandedDetailResponse(BaseModel):
    restaurant_id: int
    expanded_detail: ExpandedDetail
```

---

## 7. Routers

### Registration in `app/main.py`

```python
from app.routers import chat, health, users, recommendations

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(users.router)
app.include_router(recommendations.router)
```

**Copilot instruction:** Every new router must be imported and registered here. Router files go in `app/routers/`. Always use `prefix` and `tags` in the `APIRouter()` constructor.

### `GET /health` — liveness probe

Returns `{"status": "ok", "version": "1.0.0"}`. Always 200. No DB check.

### `GET /ready` — readiness probe

Checks `check_db_connectivity()` AND a trivial `embed_single("ping")` call. Returns 200 when both pass, 503 with `{"db": "ok"|"error", "embedding_api": "ok"|"error"}` when anything fails.

### `POST /chat`

Auth: `X-User-ID` (UUID v4). Returns `StreamingResponse` with `media_type="text/event-stream"`. Delegates to `orchestrate()` async generator. See [Section 13](#13-sse-streaming-contract).

### `GET /recommendations/{uid}` and `GET /recommendations/{uid}/{restaurant_id}/expand`

Auth: `X-User-ID` must match `{uid}` in path — 403 if mismatch. Returns synchronous JSON. See [Section 15](#15-recommendation-pipeline).

### `/users/*` endpoints

All protected by `X-Service-Token` (inter-service auth). Called by the Backend, never directly by the Frontend.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/users/{uid}` | Create user row (upsert) |
| GET | `/users/{uid}` | Read full user profile |
| PATCH | `/users/{uid}` | Deep-merge preferences |
| PATCH | `/users/{uid}/allergies` | Full-replace allergies |
| DELETE | `/users/{uid}` | Soft-delete (sets is_active=false) or hard delete |
| GET | `/users/{uid}/allergy-flags` | Current canonical allergen flag list |
| GET | `/users/{uid}/interactions` | Paginated interaction history |

---

## 8. Services

### `app/services/gemma.py` — LLM gateway

**Two-model fallback strategy:**

```
call_gemma(prompt) →
  Try PRIMARY (gemini-2.5-flash, 30s timeout)
    On any error → log warning
  Try FALLBACK (gemma-3-12b-it, 60s timeout)
    On any error → raise GemmaError

call_gemma_json(prompt) →
  call_gemma(prompt) → strip markdown fences → json.loads() → raise GemmaError on bad JSON
```

**Usage pattern (always):**

```python
from app.services.gemma import GemmaError, call_gemma_json

try:
    result: dict | list = await call_gemma_json(my_prompt)
except GemmaError as exc:
    # Degrade gracefully — never crash the endpoint
    logger.warning("LLM call failed: %s", exc)
    result = {}
```

**Copilot instruction:** Never call `genai.GenerativeModel().generate_content()` directly. All LLM calls go through `call_gemma()` or `call_gemma_json()`. Temperature is always 0.3. Max output tokens: 2048.

### `app/services/embedding.py` — Google embedding API

```python
# Single text
embedding: Optional[list[float]] = await embed_single("quiet vegan restaurant")

# Batch (100 per API call, 0.5s sleep between batches)
embeddings: list[Optional[list[float]]] = await embed_texts(["text1", "text2", ...])
```

- Model: `gemini-embedding-001` (768 dimensions), configurable via `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS`
- `embed_single` uses `task_type="retrieval_query"` — for search queries
- `embed_texts` uses `task_type="retrieval_document"` — for indexing documents
- Returns `None` per text on failure (never raises)

### `app/services/hybrid_search.py` — Search core

Combines SQL filtering with pgvector cosine similarity. Called by the chat orchestrator.

```python
candidates: list[RestaurantResult] = await hybrid_search(
    db=db,
    sql_filters={
        "price_tiers": ["$$", "$$$"],
        "cuisine_types": ["south indian"],
        "area": "Koramangala",
        "min_rating": 4.0,
        "exclude_allergens": ["peanuts"],    # anaphylactic only — hard filter
    },
    vector_query="quiet romantic vegan anniversary dinner",
    limit=15,
)
```

**SQL structure:**

```sql
SELECT DISTINCT ON (r.id)
    r.id, r.name, r.url, r.address, r.area, ...
FROM restaurants r
LEFT JOIN (
    SELECT restaurant_id, embedding
    FROM reviews WHERE embedding IS NOT NULL ORDER BY id DESC
) rv ON rv.restaurant_id = r.id
WHERE
    r.is_active = TRUE
    AND r.price_tier = ANY(:price_tiers)
    AND r.cuisine_types && :cuisine_types
    AND r.area ILIKE :area
    AND r.rating >= :min_rating
    AND NOT (r.known_allergens && :exclude_allergens)   -- hard anaphylactic filter
ORDER BY r.id, rv.embedding <=> :embedding ASC, r.rating DESC NULLS LAST
LIMIT :limit
```

When no embedding is available (API failure), falls back to `ORDER BY r.rating DESC`.

### `app/services/allergy_guard.py` — Safety layer

**Singleton instantiated at module level:**

```python
_allergy_guard = AllergyGuard()      # in orchestrator.py
_allergy_guard = AllergyGuard()      # in recommendation_service.py
```

**API:**

```python
result: AllergyCheckResult = _allergy_guard.check(
    restaurants=[...],           # list[RestaurantResult]
    user_allergies={             # from users.allergies JSONB column
        "confirmed": ["peanuts", "shellfish"],
        "intolerances": ["dairy"],
        "severity": {"peanuts": "anaphylactic", "shellfish": "severe"}
    }
)

result.safe_restaurants      # annotated, sorted safest-first
result.flagged_restaurants   # anaphylactic + high confidence — separate danger banner
result.has_any_warnings      # bool
```

**Five rules enforced unconditionally (see [Section 18](#18-allergyguard-safety-rules))::**
1. Never silently hide a restaurant — always show with warnings
2. `anaphylactic` + `confidence="high"` → flagged_restaurants (danger banner)
3. Sort: `allergy_safe=True` first, then by worst severity (`intolerance < moderate < severe < anaphylactic`)
4. Warning language matches severity — never alarm for intolerance, never soft-pedal anaphylaxis
5. Add `confidence_note` when `allergen_confidence != "high"` (data was inferred from cuisine heuristics)

### `app/services/orchestrator.py` — Chat reasoning loop

**5-step pipeline (async generator yielding SSE strings):**

```
Step 1 │ Fetch user profile (preferences, allergies, vibe_tags, etc.) from DB
       │
Step 2 │ LLM call #1: build_decomposition_prompt()
       │   → {intent, sql_filters, vector_query, ui_preference,
       │      needs_clarification, clarification_question}
       │   Inject anaphylactic allergens into sql_filters.exclude_allergens
       │
Step 3 │ hybrid_search(sql_filters, vector_query, limit=15)
       │
Step 4 │ LLM call #2: build_evaluation_prompt()
       │   → [{id, romance, noise_level, food_quality, vegan_options, value_for_money}]
       │   Merge RadarScores back onto top 10 candidates
       │   Sort by composite score = mean(romance, food, value, vegan), take top 5
       │
Step 5 │ AllergyGuard.check() on top 5
       │   → yield SSE result event
       │   → asyncio.create_task(_save_interaction())
       │   → asyncio.create_task(_run_profiler())
```

**SSE format:**
```
{"event": "thinking", "data": {"step": "decomposing_query"}}\n\n
{"event": "thinking", "data": {"step": "searching", "filters": {...}}}\n\n
{"event": "thinking", "data": {"step": "evaluating", "count": 12}}\n\n
{"event": "thinking", "data": {"step": "checking_allergies"}}\n\n
{"event": "result", "data": <GenerativeUIPayload JSON>}\n\n
```

### `app/services/profiler.py` — Background preference extractor

```python
await update_user_profile(uid=uid, message=message, agent_response=payload_dict, db=db)
```

- Runs as `asyncio.create_task()` after every chat turn — never blocks the response
- Uses `build_profiler_prompt()` → `call_gemma_json()` to extract `{dietary, vibes, cuisine_affinity, cuisine_aversion, price_comfort}`
- **NEVER extracts allergy fields** — allergies only via PATCH /users/{uid}/allergies
- Deep-merges into `preferences` JSONB; unions lists, replaces scalars
- On success: creates a `prewarm_recommendations()` task (fire-and-forget)
- Entirely wrapped in try/except — never raises, never affects chat response

**Allowed preference keys (enforced by allowlist):**
```python
_ALLOWED_PREFERENCE_KEYS = {
    "dietary", "vibes", "cuisine_affinity", "cuisine_aversion", "price_comfort"
}
```

### `app/services/fit_scorer.py` — Pure algorithmic scorer

```python
scorer = FitScorer()
result: FitResult = scorer.score(restaurant, user_profile)
# result.score     → int 0–100
# result.fit_tags  → list[FitTag], max 4 items
```

Zero LLM calls. Zero DB calls. See [Section 16](#16-fitscorer-specification) for full scoring breakdown.

### `app/services/recommendation_service.py` — Recommendation pipeline

Three public functions:

```python
# Get personalised recommendations (cached daily)
payload = await get_recommendations(uid=uid, db=db, limit=10, refresh=False)

# Get expanded detail for one card (always fresh)
response = await get_expanded_detail(uid=uid, restaurant_id=42, db=db)

# Fire-and-forget pre-warm (called by profiler after successful update)
asyncio.create_task(prewarm_recommendations(uid=uid, _db=db))
```

---

## 9. Utils

### `app/utils/prompts.py` — All prompt builders

**Rule: Never hardcode a prompt string anywhere outside this file.**

| Function | LLM call # | Returns |
|---------|-----------|---------|
| `build_decomposition_prompt(message, user_context, history, allergy_context)` | Gemma #1 | Decomposition JSON |
| `build_evaluation_prompt(message, user_context, restaurants_json, allergy_context)` | Gemma #2 | Scores array |
| `build_profiler_prompt(message, response_summary)` | Profiler | Preference signals |
| `build_user_context(preferences)` | — Context builder | Human-readable string |
| `build_allergy_context(allergies)` | — Context builder | Safety warning string |
| `build_fit_explanation_prompt(restaurants, user_profile)` | Rec Gemma #1 | Reviews array |
| `build_expand_detail_prompt(restaurant, reviews, user_profile)` | Rec Gemma #2 | ExpandedDetail JSON |

**Template for a new prompt builder:**

```python
def build_my_feature_prompt(param1: str, param2: dict[str, Any]) -> str:
    """
    Build the prompt for [describe what this call does].
    Returns JSON matching [schema name] exactly.
    """
    return f"""You are Kairos, a restaurant intelligence AI for Bangalore.

## USER CONTEXT
{param1}

## TASK
[Clear, specific instruction]

## OUTPUT FORMAT
Output only valid JSON. No markdown fences. No preamble. No explanation.

[Example output]"""
```

### `app/utils/allergy_data.py` — Canonical allergen definitions

Single source of truth imported by both `ingest.py` (build time) and `AllergyGuard` (runtime).

- `CANONICAL_ALLERGENS` — 14 EU allergens + extras
- `ALLERGEN_SYNONYMS` — maps "ghee" → "dairy", "maida" → "gluten", etc.
- `CUISINE_ALLERGEN_MAP` — heuristic cuisine → allergen list (medium confidence)
- `ALLERGY_WARNINGS` — warning template dict keyed by severity
- `SEVERITY_LEVELS` — ordered list for comparison

---

## 10. Authentication Model

The Agent uses **two independent auth mechanisms** on separate endpoint groups:

### User-facing endpoints: `X-User-ID` header

Used by: `POST /chat`, `GET /recommendations/*`

```python
@router.post("/chat")
async def chat(
    body: ChatRequest,
    x_user_id: str = Header(..., alias="X-User-ID"),
    db: AsyncSession = Depends(get_db),
):
    try:
        uid = uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid user ID format — must be UUID v4",
            headers={"X-Error-Code": "MISSING_USER_ID"},
        )
```

The header value must be a valid UUID v4. The Backend sends this after JWT validation. The Agent trusts it unconditionally.

### Service-to-service endpoints: `X-Service-Token` header

Used by: `POST/GET/PATCH/DELETE /users/*`

```python
async def verify_service_token(
    x_service_token: str = Header(..., alias="X-Service-Token"),
) -> None:
    if x_service_token.strip() != settings.service_token.strip():
        raise HTTPException(status_code=401, detail="Invalid service token")
```

The token value must exactly match `settings.service_token` (from `SERVICE_TOKEN` env var). All `/users/*` routes depend on this via `Depends(verify_service_token)`.

**Copilot instruction:** New user-facing endpoints use `X-User-ID`. New inter-service endpoints use `X-Service-Token`. Never mix them. Never skip auth on any endpoint.

---

## 11. Error Handling Contract

### Endpoint-level

```python
@router.get("/recommendations/{uid}")
async def recommendations(...):
    try:
        return await get_recommendations(uid=auth_uid, db=db, ...)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Pipeline failed for uid %s: %s", uid, exc)
        raise HTTPException(status_code=500, detail="Failed to generate — please try again")
```

### Background tasks — always swallow errors

```python
async def prewarm_recommendations(uid: UUID, _db: AsyncSession) -> None:
    try:
        async with AsyncSessionLocal() as session:
            await get_recommendations(uid=uid, db=session, ...)
    except Exception as exc:
        logger.debug("Prewarm silently swallowed error for user %s: %s", uid, exc)
        # Never re-raise — background tasks must not affect request flow
```

### AllergyGuard — never silently passes

```python
try:
    allergy_result = _allergy_guard.check(restaurants, allergies)
except Exception as exc:
    logger.error("AllergyGuard raised: %s", exc)
    # Return a safe error payload — never return unguarded results
    yield safe_error_sse
    return
```

### Global exception handler

```python
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception on %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "code": "AGENT_UNAVAILABLE"},
    )
```

---

## 12. Lifespan & Startup

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 1. CREATE EXTENSION IF NOT EXISTS vector
    async with AsyncSessionLocal() as session:
        await init_pgvector(session)

    # 2. Create all ORM tables (idempotent)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 3. Connectivity check
    ok = await check_db_connectivity()
    if not ok:
        logger.error("DB check FAILED at startup.")

    yield  # ← app runs here

    await engine.dispose()
```

**Copilot instruction:** Startup order is fixed — pgvector extension first (required by `Vector` column), then tables, then connectivity. Never reorder. New startup steps go between `check_db_connectivity()` and `yield`.

---

## 13. SSE Streaming Contract

The chat endpoint streams Server-Sent Events. Each event is a single JSON object on one line, terminated by `\n\n`.

**Event types:**

```
data: {"event": "thinking", "data": {"step": "decomposing_query"}}

data: {"event": "thinking", "data": {"step": "searching", "filters": {"price_tiers": ["$$"]}}}

data: {"event": "thinking", "data": {"step": "evaluating", "count": 12}}

data: {"event": "thinking", "data": {"step": "checking_allergies"}}

data: {"event": "result", "data": {<GenerativeUIPayload JSON>}}
```

**Generator signature:**

```python
async def orchestrate(
    uid: UUID,
    message: str,
    history: list[ChatMessage],
    db: AsyncSession,
) -> AsyncIterator[str]:
    yield f'{{"event": "thinking", "data": {json.dumps({"step": "decomposing_query"})}}}\n\n'
    # ...
    yield f'{{"event": "result", "data": {payload.model_dump_json()}}}\n\n'
```

**StreamingResponse setup:**

```python
return StreamingResponse(
    orchestrate(uid=uid, message=body.message, history=body.conversation_history, db=db),
    media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
)
```

**Copilot instruction:** `result` is always the LAST event. Never yield after `result`. The Frontend reconnects on its own — the Agent does not manage reconnection.

---

## 14. Background Task Pattern

All background tasks follow the same pattern — fire-and-forget, own session, never raise:

```python
# 1. Create the task after streaming the result
asyncio.create_task(_save_interaction(
    uid=uid,
    message=message,
    payload=payload,
    restaurant_ids=restaurant_ids,
    allergens_flagged=allergens_flagged,
    has_warnings=has_warnings,
))
asyncio.create_task(_run_profiler(
    uid=uid,
    message=message,
    payload_dict=payload.model_dump(),
))

# 2. Implementation always uses AsyncSessionLocal()
async def _save_interaction(...) -> None:
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("INSERT INTO interactions ..."), {...})
            await db.commit()
    except Exception as exc:
        logger.error("Failed to save interaction: %s", exc)
        # Never re-raise
```

**Task execution order in `orchestrator.py`:**
1. `_save_interaction` — persists the full interaction record
2. `_run_profiler` → inside profiler: `prewarm_recommendations` — pre-warms recommendation cache

---

## 15. Recommendation Pipeline

### `GET /recommendations/{uid}` — 7-step synchronous pipeline

```
User Request
    │
    ▼
[Cache check] ──hit──▶ Return cached RecommendationPayload
    │ (miss or refresh=true)
    ▼
Step 1 │ Fetch user profile
       │   preferences, allergies, allergy_flags, dietary_flags,
       │   vibe_tags, preferred_price_tiers, cuisine_affinity, cuisine_aversion
    ▼
Step 2 │ SQL candidate retrieval
       │   SELECT top 50 FROM restaurants WHERE is_active=TRUE
       │   AND NOT (known_allergens && anaphylactic_allergens)   ← hard filter
       │   ORDER BY rating DESC NULLS LAST
    ▼
Step 3 │ AllergyGuard.check() on all 50 candidates
       │   (annotates allergy_safe + allergy_warnings on each)
    ▼
Step 4 │ FitScorer.score() on all 50 annotated candidates
       │   Returns (score 0–100, list[FitTag]) per candidate
    ▼
Step 5 │ Sort by fit_score DESC, slice top N (max 25)
    ▼
Step 6 │ LLM batch call: build_fit_explanation_prompt()
       │   → single call for ALL selected restaurants
       │   → returns [{restaurant_id, consolidated_review, fit_tags_override}]
       │   consolidated_review truncated to [:160]
    ▼
Step 7 │ Assemble RecommendationPayload
       │ Write to cache: TTLCache[sha256(uid + date.today())]
       ▼
    Return JSON
```

### `GET /recommendations/{uid}/{restaurant_id}/expand` — 5-step fresh pipeline

```
Step 1 │ Fetch restaurant row + up to 10 reviews (ORDER BY id DESC)
Step 2 │ Fetch user profile
Step 3 │ LLM call: build_expand_detail_prompt(restaurant, reviews, user_profile)
       │   → ExpandedDetail JSON
Step 4 │ AllergyGuard.check() on this single restaurant
Step 5 │ Assemble ExpandedDetailResponse and return (never cached)
```

---

## 16. FitScorer Specification

`FitScorer.score(restaurant, user_profile)` → `FitResult(score, fit_tags)`

**Scoring table (must total 100 max):**

| Dimension | Max | Trigger |
|-----------|-----|---------|
| Cuisine affinity | 30 | `+30` full overlap (all user cuisines match); `+15` partial (≥1); `-10` aversion hit |
| Vibe match | 25 | `+5` per overlapping vibe tag (capped); restaurant vibes from `meta.vibes` |
| Price comfort | 20 | `+20` exact tier match; `+10` one tier adjacent; `0` two+ tiers away |
| Dietary compat | 15 | `+5` per matching dietary flag (capped); restaurant flags from `meta.dietary_flags` |
| Allergy safety | 10 | `+10` no warnings; `+5` intolerance/moderate only; `0` severe/anaphylactic |

**FitTag label templates (no LLM — string templates only):**

```python
cuisine hit:    f"Matches your {cuisine.title()} preference"
vibe hit:       f"Known for {vibe.title()} — your top vibe tag"
price hit:      f"Within your {price_tier} comfort zone"
dietary hit:    f"{flag.title()}-friendly"
allergy safe:   "Safe for your allergy profile"
```

**Output rules:**
- Collect (points, tag) per dimension
- Sort descending by points
- Return top 4 tags only

**Price tier adjacency:**

```python
_PRICE_TIER_ORDER = ["$", "$$", "$$$", "$$$$"]
# |"$" - "$$"| = 1 → adjacent (+10)
# |"$" - "$$$"| = 2 → not adjacent (0)
```

---

## 17. Caching Layer

### Recommendation cache

```python
from cachetools import TTLCache

_recommendation_cache: TTLCache = TTLCache(maxsize=1_000, ttl=86_400)

# Key derivation — daily rotation is baked into the key
def _cache_key(uid: str) -> str:
    raw = uid + date.today().isoformat()   # "abc123...2026-02-28"
    return hashlib.sha256(raw.encode()).hexdigest()
```

**Behaviour:**

| Operation | Implementation |
|-----------|---------------|
| Cache hit | `_recommendation_cache.get(key)` → deserialise JSON → `RecommendationPayload` |
| Cache miss | Run 7-step pipeline → serialise → `_recommendation_cache[key] = payload.model_dump_json()` |
| `refresh=True` | `_recommendation_cache.pop(key, None)` → then run pipeline as normal |
| Daily expiry | Key includes `date.today()` — old key becomes unreachable at midnight |
| TTL safety net | 86400 seconds — protects against midnight edge cases |
| `/expand` | Never cached — always runs fresh LLM call |

**Copilot instruction:** Cache stores the full `RecommendationPayload` as a JSON string (not the model object). On deserialisation, validate with `RecommendationPayload.model_validate_json()` and discard on `ValidationError`.

---

## 18. AllergyGuard Safety Rules

These rules are **absolute constraints** — they cannot be relaxed by any feature.

### Rule 1 — No silent hiding
Every restaurant with allergy warnings must still appear in the result. Only `anaphylactic + high confidence` moves to `flagged_restaurants`. Never filter out a restaurant.

### Rule 2 — Flagged list for anaphylactic
```python
is_flagged = any(
    w.severity == "anaphylactic" and w.confidence == "high"
    for w in annotated.allergy_warnings
)
```
When true: restaurant goes to `flagged_restaurants`, rendered in a red danger banner.

### Rule 3 — Sort order
```python
safe_list.sort(key=self._sort_key)
# key: (0, 0) for allergy_safe=True; (1, worst_severity_rank) for unsafe
```

### Rule 4 — Warning language by severity
```python
ALLERGY_WARNINGS = {
    "anaphylactic": {"level": "danger",  "emoji": "🚨", "title": "Anaphylaxis Risk",   ...},
    "severe":       {"level": "warning", "emoji": "⚠️", "title": "Allergy Warning",    ...},
    "moderate":     {"level": "caution", "emoji": "⚡", "title": "Heads Up",           ...},
    "intolerance":  {"level": "info",    "emoji": "ℹ️", "title": "Dietary Note",       ...},
}
```

### Rule 5 — Confidence notes
When `allergen_confidence != "high"` (data was inferred from cuisine heuristics):
```python
confidence_note = "Allergen data inferred from cuisine type — call ahead to confirm."
```
Rendered visibly in the UI.

### The anaphylactic hard SQL filter
Applied in **both** `hybrid_search.py` and `recommendation_service._fetch_candidates()`:
```sql
AND NOT (r.known_allergens && :anaphylactic_allergens)
```
This prevents anaphylactic-risk restaurants from even entering the result set. AllergyGuard then handles the remaining warnings.

---

## 19. Prompt Engineering Guidelines

All prompts follow this structure:

```
1. Role assignment         "You are Kairos, a restaurant intelligence AI for Bangalore."
2. User context section    ## USER CONTEXT / ## USER PROFILE
3. SAFETY section          ## SAFETY — USER ALLERGIES (mandatory in all user-facing prompts)
4. Data section            ## RESTAURANTS / ## REVIEWS
5. Task description        ## TASK / ## YOUR TASK
6. Scoring/schema          ## OUTPUT SCHEMA / ## DIMENSIONS
7. Output constraint       "Output only valid JSON. No markdown fences. No preamble."
8. Example                 [example JSON block]
```

**Safety section (mandatory in every prompt that shows restaurants):**

```python
allergy_context = build_allergy_context(allergies)
# → "SAFETY-CRITICAL ALLERGY INFORMATION:
#      Confirmed allergens: peanuts (anaphylactic), shellfish (severe)
#      ⚠️  ANAPHYLACTIC ALLERGENS (MUST be in exclude_allergens): peanuts"
```

**Output format constraint (exact wording, always at the end):**

```
Output only valid JSON. No markdown fences. No preamble. No explanation.
```

**Never ask the LLM to infer allergens.** The prompt for `build_fit_explanation_prompt` explicitly says: "Do NOT mention any allergen that is in the user's dietary restrictions — AllergyGuard handles that separately."

**Consolidated review constraint:**
```
consolidated_review must:
  - be ≤ 160 characters
  - include ONE specific concrete detail (dish name, characteristic, reviewer quote)
  - be in present tense
  - never be generic ("great food and ambiance")
```
After LLM response: always apply `.strip()[:160]` before building the schema.

---

## 20. Adding a New Endpoint — Checklist

```
□ 1. Create / update schema file in app/schemas/
     — new schemas in a new file (e.g. app/schemas/my_feature.py)
     — never modify restaurant.py, chat.py

□ 2. Create router file app/routers/my_feature.py
     router = APIRouter(prefix="/my-feature", tags=["my-feature"])

□ 3. Add auth pattern
     — user-facing: x_user_id = Header(..., alias="X-User-ID") → uuid.UUID(x_user_id)
     — inter-service: Depends(verify_service_token) from app.routers.users

□ 4. Register in app/main.py
     from app.routers import ..., my_feature
     app.include_router(my_feature.router)

□ 5. Create service in app/services/my_service.py
     — pure business logic
     — zero FastAPI imports inside service files

□ 6. Add prompt builder(s) in app/utils/prompts.py
     — new function appended at bottom
     — no existing functions modified

□ 7. Wrap all LLM calls in try/except GemmaError → degrade gracefully

□ 8. Run AllergyGuard on any restaurant result before returning it

□ 9. Background tasks: use asyncio.create_task() + own AsyncSessionLocal() session

□ 10. Verify with: python -m py_compile app/routers/my_feature.py
```

---

## 21. Adding a New Service — Checklist

```
□ 1. File: app/services/my_service.py
     — module-level docstring explaining what the service does
     — from __future__ import annotations at top

□ 2. No FastAPI imports — services are framework-agnostic

□ 3. Singleton pattern (if stateful):
     _my_service = MyService()   # module level
     # imported and reused everywhere

□ 4. LLM calls: only via call_gemma() / call_gemma_json()
     Prompts: only via function in app/utils/prompts.py

□ 5. DB calls: only via AsyncSession passed as parameter
     Never create engine/session inside a service

□ 6. Background-safe services: accept AsyncSession but use AsyncSessionLocal()
     for fire-and-forget sub-tasks

□ 7. Error policy:
     — Request-path services: raise ValueError/RuntimeError → router catches + returns 4xx/5xx
     — Background services: wrap everything in try/except and log, never raise

□ 8. Pure Python services (like FitScorer): zero LLM, zero DB, zero I/O
     — document this constraint in the class docstring
     — accept all data as parameters, return dataclasses
```

---

## 22. Invariants — Never Break These

These constraints are enforced across the entire codebase. Copilot must never generate code that violates them.

| # | Invariant | Where enforced |
|---|-----------|---------------|
| 1 | AllergyGuard runs on **every** restaurant result before it reaches the user | `orchestrator.py`, `recommendation_service.py` |
| 2 | Anaphylactic allergens are **hard-filtered at SQL level** | `hybrid_search.py`, `recommendation_service._fetch_candidates()` |
| 3 | Profiler **never** touches `allergies`, `allergy_flags` columns | `profiler.py` `_ALLOWED_PREFERENCE_KEYS` |
| 4 | Allergies updated **only** via `PATCH /users/{uid}/allergies` | `users.py` |
| 5 | `GenerativeUIPayload`, `RestaurantResult`, `RadarScores`, `AllergyWarning` schemas are **immutable** | `app/schemas/restaurant.py` |
| 6 | All LLM calls go through `call_gemma()` / `call_gemma_json()` — never direct API calls | everywhere |
| 7 | All prompts live in `app/utils/prompts.py` — no inline prompts | everywhere |
| 8 | Background tasks use `AsyncSessionLocal()` — never the request session | `orchestrator.py`, `profiler.py`, `recommendation_service.py` |
| 9 | `/chat` always returns SSE — never a plain JSON response | `chat.py` |
| 10 | `/recommendations` always returns plain JSON — never SSE | `recommendations.py` |
| 11 | `uid` is always provided by the caller — Agent never generates UIDs | everywhere |
| 12 | Settings are only from `app.config.settings` — never `os.environ` | everywhere |
| 13 | `consolidated_review` is truncated to 160 chars after LLM response | `recommendation_service.py` |
| 14 | `/expand` endpoint is never cached | `recommendations.py` |

---

## 23. Docker & Deployment

### `docker-compose.yml`

```yaml
version: '3.9'
services:
  db:
    image: pgvector/pgvector:pg16
    container_name: kairos_agent_db
    environment:
      POSTGRES_DB: ${POSTGRES_DB}
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 5
volumes:
  pgdata:
```

### Run commands

```bash
# Start DB
docker compose up -d db

# Install dependencies (venv recommended)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Launch Agent
bash run.sh
# Internally: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Ingest data
bash run_ingest.sh
# Internally: python scripts/ingest.py
```

### `requirements.txt` (key deps)

```
fastapi==0.111.0
uvicorn[standard]==0.30.1
sqlalchemy[asyncio]==2.0.30
asyncpg==0.29.0
pgvector==0.3.2
pydantic==2.7.1
pydantic-settings==2.2.1
google-generativeai==0.7.2
cachetools>=5.3
```

---

## 24. Data Ingestion Scripts

### `scripts/ingest.py`

Full pipeline: reads `data/zomato.csv` → inserts restaurants → generates 768-dim embeddings for review texts → inserts reviews → upgrades `allergen_confidence` to `"high"` on restaurants with allergen-keyword mentions in reviews.

**Key steps:**
1. Normalise price tiers: cost_for_two to `$` / `$$` / `$$$` / `$$$$`
2. Parse cuisine_types array from CSV string
3. Map cuisines → allergens via `CUISINE_ALLERGEN_MAP` (medium confidence)
4. Embed review texts in batches of 100 via `embed_texts()`
5. Scan review text for `ALLERGEN_SYNONYMS` → upgrade restaurant to `allergen_confidence="high"`
6. Populate `meta` JSONB with `{"vibes": [...], "dietary_flags": [...]}` from review text heuristics

**Re-runnable:** uses `INSERT ... ON CONFLICT DO NOTHING` — safe to re-run.

### `scripts/create_tables.py`

Standalone table creation (for CI / fresh installs without running the full app):

```python
asyncio.run(create_all_tables())
# Runs: CREATE EXTENSION IF NOT EXISTS vector; Base.metadata.create_all
```

---

## Complete Request Flow Examples

### Example 1: Chat turn — "Find me a quiet vegan restaurant in Indiranagar"

```
Frontend sends:
  POST /chat
  X-User-ID: 550e8400-e29b-41d4-a716-446655440000
  Body: {"message": "Find me a quiet vegan restaurant in Indiranagar", "conversation_history": []}

SSE stream received by Frontend:

  data: {"event":"thinking","data":{"step":"decomposing_query"}}

  data: {"event":"thinking","data":{"step":"searching","filters":{"cuisine_types":[],"area":"Indiranagar","dietary_flags":["vegan"],"exclude_allergens":["peanuts"]}}}

  data: {"event":"thinking","data":{"step":"evaluating","count":9}}

  data: {"event":"thinking","data":{"step":"checking_allergies"}}

  data: {"event":"result","data":{
    "ui_type": "restaurant_list",
    "message": "I found 5 restaurants for you!",
    "restaurants": [
      {
        "id": 42,
        "name": "The Green Table",
        "area": "Indiranagar",
        "price_tier": "$$",
        "rating": 4.3,
        "cuisine_types": ["continental", "vegan"],
        "allergy_safe": true,
        "allergy_warnings": [],
        "scores": {"romance": 7.5, "noise_level": 8.5, "food_quality": 8.0, "vegan_options": 9.5, "value_for_money": 7.0}
      }
    ],
    "flagged_restaurants": [],
    "has_allergy_warnings": false
  }}

Background (fire-and-forget, after SSE):
  _save_interaction() → INSERT INTO interactions ...
  _run_profiler()     → LLM extracts {"vibes": ["quiet"], "dietary": ["vegan"]}
                      → UPDATE users SET preferences = ..., vibe_tags = '{"quiet"}'
                      → asyncio.create_task(prewarm_recommendations(uid, db))
```

### Example 2: Recommendations — first load

```
Frontend sends:
  GET /recommendations/550e8400-e29b-41d4-a716-446655440000
  X-User-ID: 550e8400-e29b-41d4-a716-446655440000

Agent pipeline:
  _cache_get(uid) → None (miss)
  _fetch_user_profile() →
    {preferences: {cuisine_affinity: ["south indian"], vibes: ["quiet"]},
     allergies: {confirmed: ["peanuts"], severity: {peanuts: "anaphylactic"}},
     dietary_flags: ["vegan"], vibe_tags: ["quiet"],
     preferred_price_tiers: ["$$"]}

  _fetch_candidates(anaphylactic=["peanuts"]) →
    SELECT ... WHERE NOT (known_allergens && '{"peanuts"}') ORDER BY rating DESC LIMIT 50

  AllergyGuard.check(50 candidates) → 50 annotated restaurants

  FitScorer.score() × 50:
    Restaurant "Saatvik" → score=82, tags=[
      FitTag("Matches your South Indian preference", "cuisine"),
      FitTag("Known for Quiet — your top vibe tag", "vibe"),
      FitTag("Within your $$ comfort zone", "price"),
      FitTag("Vegan-friendly", "dietary"),
    ]

  Sort by score DESC → top 10

  build_fit_explanation_prompt([10 restaurants], user_profile) →
    LLM returns: [{restaurant_id: 12, consolidated_review: "Famous for ghee-free dosas served on banana leaf — the crispy texture is unmatched.", fit_tags_override: null}, ...]

  consolodated_review.strip()[:160] applied

  RecommendationPayload assembled → _cache_set(uid) → return JSON

Response:
  {
    "uid": "550e8400-...",
    "generated_at": "2026-02-28T10:00:00Z",
    "recommendations": [
      {
        "rank": 1,
        "restaurant": {"id": 12, "name": "Saatvik", "area": "Jayanagar", "price_tier": "$$", ...},
        "fit_score": 82,
        "fit_tags": [
          {"label": "Matches your South Indian preference", "type": "cuisine"},
          {"label": "Known for Quiet — your top vibe tag", "type": "vibe"},
          {"label": "Within your $$ comfort zone", "type": "price"},
          {"label": "Vegan-friendly", "type": "dietary"}
        ],
        "consolidated_review": "Famous for ghee-free dosas served on banana leaf — the crispy texture is unmatched.",
        "allergy_summary": {"is_safe": true, "warnings": []},
        "expanded_detail": null
      }
    ]
  }
```

### Example 3: Expand a card

```
Frontend sends:
  GET /recommendations/550e8400-.../12/expand
  X-User-ID: 550e8400-...

Agent (no cache — always fresh):
  Fetch restaurant row 12 + last 10 reviews
  Fetch user profile
  build_expand_detail_prompt(restaurant_dict, reviews, user_profile) →
    LLM returns:
    {
      "review_summary": "Saatvik is widely praised for its pure vegetarian menu...",
      "highlights": [
        {"emoji": "🌿", "text": "100% vegan menu with rotating seasonal specials"},
        {"emoji": "🔇", "text": "Calm, library-quiet atmosphere — perfect for conversation"},
        {"emoji": "💸", "text": "Generous portions at ₹350 average per head"}
      ],
      "crowd_profile": "A mix of health-conscious young professionals and older vegetarian regulars...",
      "best_for": ["Mindful solo lunch", "First date", "Work meeting"],
      "avoid_if": ["You need fast service", "You want non-veg options"],
      "radar_scores": {"romance": 7.0, "noise_level": 9.0, "food_quality": 8.5, "vegan_options": 10.0, "value_for_money": 8.0},
      "why_fit_paragraph": "Given your vegan diet and preference for quiet vibes, Saatvik scores on every dimension you care about...",
      "allergy_detail": {"is_safe": true, "confidence": "high", "warnings": [], "safe_note": "No peanuts detected."}
    }
  AllergyGuard annotates single restaurant → allergy_detail assembled

Response: {"restaurant_id": 12, "expanded_detail": {...}}
```

---

*End of Kairos Agent Architecture Reference.*
*Version: 1.0.0 — February 2026*
