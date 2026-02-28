# Kairos Agent — React Frontend Integration Guide

> **Audience:** Claude Sonnet 4.6 implementing a React frontend against the Kairos FastAPI Agent.
> Read every section before generating any code. The Agent has strict rules around auth headers,
> SSE parsing, and allergy safety UI that must be honoured exactly.

---

## 1. Base URL & CORS

| Environment | Base URL |
|---|---|
| Production | `https://kairos.gokulp.online` |
| Local dev | `http://localhost:8000` |

CORS is configured at the Agent. Allowed origins: `https://kairos.gokulp.online`, `http://localhost:3000`.
All methods and headers are allowed. Credentials (`cookies`) are also allowed.

---

## 2. Authentication Architecture

The Agent has **two separate auth contexts** — understand them before writing any API call.

### 2A. User-facing endpoints (`/chat`, `/recommendations/*`)

These use a **custom header `X-User-ID`** (not `Authorization`) carrying the user's UUID v4.  
The JWT access token is only used to decode the `uid` — the frontend must extract it first and
pass `X-User-ID` on every call to the Agent.

```
POST /chat
Headers:
  X-User-ID: <uuid-v4>        ← always required
  Content-Type: application/json
```

### 2B. Backend inter-service endpoints (`/users/*`)

These use `X-Service-Token` header.  
**The React frontend NEVER calls `/users/*` directly.** Only your backend server does.
Do not expose `SERVICE_TOKEN` in the browser under any circumstances.

### 2C. Auth endpoints (`/auth/*`)

Fully public or Bearer-token protected (see Section 4). The frontend calls these
directly to log users in, register, and refresh tokens.

### Token storage recommendation

```typescript
// Store in memory (not localStorage — XSS risk)
let accessToken: string | null = null;
let refreshToken: string | null = null;

// Decode uid from JWT (no signature verification needed on client)
function getUidFromToken(token: string): string {
  const payload = JSON.parse(atob(token.split('.')[1]));
  return payload.sub; // UUID v4 string
}
```

---

## 3. TypeScript Type Definitions

Paste this block verbatim into `src/types/kairos.ts` — every interface maps 1:1 to the
Agent's Pydantic schemas.

```typescript
// ── Auth ─────────────────────────────────────────────────────────────────────

export interface RegisterRequest {
  email: string;
  password: string;       // min 8, max 128 chars
  display_name: string;   // min 1, max 100 chars
}

export interface RegisterResponse {
  uid: string;            // UUID v4
  email: string;
  message: string;        // "Registration successful. Please verify your email."
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;     // seconds
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface ForgotPasswordRequest {
  email: string;
}

export interface ResetPasswordRequest {
  email: string;
  otp: string;            // exactly 6 chars
  new_password: string;   // min 8, max 128 chars
}

export interface MessageResponse {
  message: string;
}

// ── Chat ─────────────────────────────────────────────────────────────────────

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

export interface ChatRequest {
  message: string;               // max 2000 chars
  conversation_history: ChatMessage[];
}

// ── Restaurant ───────────────────────────────────────────────────────────────

export interface AllergyWarning {
  allergen: string;              // e.g. "peanuts"
  severity: "anaphylactic" | "severe" | "moderate" | "intolerance";
  level: "danger" | "warning" | "caution" | "info";
  emoji: string;                 // "🚨" | "⚠️" | "⚡" | "ℹ️"
  title: string;
  message: string;
  confidence: "high" | "medium" | "low";
  confidence_note?: string;      // present when confidence !== "high"
}

export interface RadarScores {
  romance: number;               // 0–10
  noise_level: number;
  food_quality: number;
  vegan_options: number;
  value_for_money: number;
}

export interface RestaurantResult {
  id: number;
  name: string;
  area?: string;
  address?: string;
  price_tier?: string;           // "$" | "$$" | "$$$" | "$$$$"
  rating?: number;               // 0.0–5.0
  votes: number;
  cuisine_types: string[];
  url?: string;
  lat?: number;
  lng?: number;
  known_allergens: string[];
  allergen_confidence: "high" | "medium" | "low";
  meta: Record<string, unknown>; // holds vibe_tags, dietary_options, etc.
  allergy_safe: boolean;         // always populated by AllergyGuard
  allergy_warnings: AllergyWarning[];
  scores?: RadarScores;          // present on radar_comparison ui_type
}

export type GenerativeUIType = "restaurant_list" | "radar_comparison" | "map_view" | "text";

export interface GenerativeUIPayload {
  ui_type: GenerativeUIType;
  message: string;
  restaurants: RestaurantResult[];
  flagged_restaurants: RestaurantResult[];
  has_allergy_warnings: boolean;
  follow_up_questions?: string[];   // text ui_type only
  map_center?: { lat: number; lng: number }; // map_view only
}

// ── SSE events ────────────────────────────────────────────────────────────────

export interface ThinkingEvent {
  event: "thinking";
  data: {
    step: "decomposing_query" | "searching" | "evaluating" | "checking_allergies";
    [key: string]: unknown;   // extra fields like count, filters
  };
}

export interface ResultEvent {
  event: "result";
  data: GenerativeUIPayload;
}

export type ChatSSEEvent = ThinkingEvent | ResultEvent;

// ── Recommendations ──────────────────────────────────────────────────────────

export interface FitTag {
  label: string;
  type: "cuisine" | "vibe" | "price" | "dietary" | "allergy_safe";
}

export interface AllergySummary {
  is_safe: boolean;
  warnings: AllergyWarning[];
}

export interface Highlight {
  emoji: string;
  text: string;
}

export interface AllergyDetail {
  is_safe: boolean;
  confidence: string;
  warnings: AllergyWarning[];
  safe_note?: string;
}

export interface ExpandedDetail {
  review_summary: string;
  highlights: Highlight[];      // 3–5 items
  crowd_profile: string;
  best_for: string[];           // 2–4 items
  avoid_if: string[];           // 1–3 items
  radar_scores: RadarScores;
  why_fit_paragraph: string;
  allergy_detail: AllergyDetail;
}

export interface RecommendationItem {
  rank: number;
  restaurant: RestaurantResult;
  fit_score: number;            // 0–100
  fit_tags: FitTag[];
  consolidated_review: string;  // ≤160 chars, LLM-written
  why_fit_paragraph: string;    // 2–3 sentence personalised rationale
  allergy_summary: AllergySummary;
  expanded_detail?: ExpandedDetail; // null until /expand called
}

export interface RecommendationPayload {
  uid: string;
  generated_at?: string;        // ISO-8601. null on empty result
  recommendations: RecommendationItem[];
}

export interface ExpandedDetailResponse {
  restaurant_id: number;
  expanded_detail: ExpandedDetail;
}
```

---

## 4. Auth Endpoints

Base prefix: `/auth`

### POST /auth/register

Create a new account. **Public — no auth header needed.**

```typescript
async function register(body: RegisterRequest): Promise<RegisterResponse> {
  const res = await fetch(`${BASE_URL}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}
```

**Response `201`:**
```json
{
  "uid": "24476d6e-823f-4936-a493-23c200fbec0a",
  "email": "user@example.com",
  "message": "Registration successful. Please verify your email."
}
```

**Errors:** `422` validation, `409` email already taken.

---

### POST /auth/login

```typescript
async function login(body: LoginRequest): Promise<TokenResponse> {
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}
```

**Response `200`:**
```json
{
  "access_token": "eyJhbGci...",
  "refresh_token": "eyJhbGci...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

After login: store tokens in memory, decode `uid` from `access_token.sub`.

---

### POST /auth/refresh

Exchange expiring access token before it expires (`expires_in` seconds from issue).

```typescript
async function refreshTokens(refreshToken: string): Promise<TokenResponse> {
  const res = await fetch(`${BASE_URL}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) throw await res.json(); // 401 if refresh token expired
  return res.json();
}
```

Implement an **axios/fetch interceptor** that catches `401` on any call, calls this once, retries the original request with the new token. If refresh also returns `401`, redirect to login.

---

### POST /auth/logout

Requires `Authorization: Bearer <access_token>` header (one of the ONLY endpoints that uses `Authorization`).

```typescript
async function logout(accessToken: string): Promise<void> {
  await fetch(`${BASE_URL}/auth/logout`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${accessToken}` },
  });
  accessToken = null;
  refreshToken = null;
}
```

---

### POST /auth/forgot-password

```typescript
async function forgotPassword(email: string): Promise<MessageResponse> {
  const res = await fetch(`${BASE_URL}/auth/forgot-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  return res.json(); // Always 200 to avoid email enumeration
}
```

---

### POST /auth/reset-password

```typescript
async function resetPassword(body: ResetPasswordRequest): Promise<MessageResponse> {
  const res = await fetch(`${BASE_URL}/auth/reset-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await res.json();
  return res.json();
}
```

---

## 5. Chat Endpoint — SSE

`POST /chat`  
Returns a `text/event-stream` stream. **Never call with `await fetch().then(r => r.json())`** — you must read it as an SSE stream.

### Request

```typescript
// Headers
{
  "Content-Type": "application/json",
  "X-User-ID": uid   // UUID v4 — REQUIRED, not Bearer token
}

// Body
{
  "message": "Find me a good south Indian place near Koramangala under ₹500",
  "conversation_history": [
    { "role": "user",      "content": "previous message" },
    { "role": "assistant", "content": "previous response text" }
  ]
}
```

`conversation_history` is optional (empty array on first turn). Keep the last N turns
(recommend 6–10 turns max for context). The `content` for assistant turns should be
the `message` string from the last `GenerativeUIPayload`.

### SSE Stream Format

Each line in the stream is a JSON object (NOT the standard `data:` SSE wire format).
Parse each `\n\n`-delimited chunk as raw JSON:

```
{"event": "thinking", "data": {"step": "decomposing_query"}}\n\n
{"event": "thinking", "data": {"step": "searching", "filters": {...}}}\n\n
{"event": "thinking", "data": {"step": "evaluating", "count": 12}}\n\n
{"event": "thinking", "data": {"step": "checking_allergies"}}\n\n
{"event": "result",   "data": { ...GenerativeUIPayload... }}\n\n
```

`thinking` events come in order: `decomposing_query` → `searching` → `evaluating` → `checking_allergies`.  
`result` is always the final event. The stream closes after it.

### React Implementation

```typescript
// hooks/useChat.ts
import { useState, useCallback, useRef } from 'react';
import type { ChatMessage, ChatSSEEvent, GenerativeUIPayload, ThinkingEvent } from '../types/kairos';

interface UseChatReturn {
  sendMessage: (text: string) => Promise<void>;
  result: GenerativeUIPayload | null;
  thinkingStep: string | null;
  isStreaming: boolean;
  error: string | null;
}

export function useChat(uid: string): UseChatReturn {
  const [result, setResult] = useState<GenerativeUIPayload | null>(null);
  const [thinkingStep, setThinkingStep] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const historyRef = useRef<ChatMessage[]>([]);

  const sendMessage = useCallback(async (text: string) => {
    setIsStreaming(true);
    setThinkingStep(null);
    setResult(null);
    setError(null);

    try {
      const res = await fetch(`${BASE_URL}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-User-ID': uid,
        },
        body: JSON.stringify({
          message: text,
          conversation_history: historyRef.current,
        }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail ?? 'Chat failed');
      }

      const reader = res.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split('\n\n');
        buffer = chunks.pop() ?? ''; // keep incomplete tail

        for (const chunk of chunks) {
          const trimmed = chunk.trim();
          if (!trimmed) continue;

          try {
            const parsed: ChatSSEEvent = JSON.parse(trimmed);

            if (parsed.event === 'thinking') {
              setThinkingStep((parsed as ThinkingEvent).data.step);
            } else if (parsed.event === 'result') {
              const payload = parsed.data as GenerativeUIPayload;
              setResult(payload);
              // Append to history
              historyRef.current = [
                ...historyRef.current,
                { role: 'user', content: text },
                { role: 'assistant', content: payload.message },
              ].slice(-10); // keep last 10 turns
            }
          } catch {
            // Ignore malformed chunk
          }
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setIsStreaming(false);
      setThinkingStep(null);
    }
  }, [uid]);

  return { sendMessage, result, thinkingStep, isStreaming, error };
}
```

### Rendering GenerativeUIPayload

The `ui_type` field tells the frontend which component to mount:

```tsx
// components/GenerativeUI.tsx
import type { GenerativeUIPayload } from '../types/kairos';
import { RestaurantList } from './RestaurantList';
import { RadarComparison } from './RadarComparison';
import { MapView } from './MapView';
import { TextResponse } from './TextResponse';

export function GenerativeUI({ payload }: { payload: GenerativeUIPayload }) {
  return (
    <div>
      <p className="agent-message">{payload.message}</p>

      {/* ALWAYS render allergy warning banner when flagged restaurants exist */}
      {payload.flagged_restaurants.length > 0 && (
        <AllergyDangerBanner restaurants={payload.flagged_restaurants} />
      )}

      {payload.ui_type === 'restaurant_list' && (
        <RestaurantList restaurants={payload.restaurants} />
      )}
      {payload.ui_type === 'radar_comparison' && (
        <RadarComparison restaurants={payload.restaurants} />
      )}
      {payload.ui_type === 'map_view' && (
        <MapView restaurants={payload.restaurants} center={payload.map_center} />
      )}
      {payload.ui_type === 'text' && (
        <TextResponse followUpQuestions={payload.follow_up_questions} />
      )}
    </div>
  );
}
```

### ⚠️ Allergy Safety UI Rules (Non-Negotiable)

These rules exist in AllergyGuard and must be reflected in the UI:

1. **`flagged_restaurants`** are restaurants with `severity: "anaphylactic"` AND `confidence: "high"`. Render them in a **visually distinct danger zone** below safe results — red border, 🚨 emoji, prominent warning. Never mix them with safe results.
2. **`allergy_warnings`** on safe restaurants must be rendered per-card (⚠️ / ⚡ / ℹ️ based on `AllergyWarning.emoji`).
3. **`confidence_note`** on a warning must be displayed if present — it explains when AI confidence is lower than `"high"`.
4. Never hide an allergy warning silently.

---

## 6. Recommendations Endpoints

Base prefix: `/recommendations`  
All require `X-User-ID` header matching the `uid` in the path.

### GET /recommendations/{uid}

Synchronous JSON — no SSE. Cached daily per user (instant on repeat calls).

```typescript
async function getRecommendations(
  uid: string,
  options: { limit?: number; refresh?: boolean } = {}
): Promise<RecommendationPayload> {
  const params = new URLSearchParams({
    limit: String(options.limit ?? 10),     // 1–25
    refresh: String(options.refresh ?? false),
  });

  const res = await fetch(`${BASE_URL}/recommendations/${uid}?${params}`, {
    headers: { 'X-User-ID': uid },
  });

  if (!res.ok) throw await res.json();
  return res.json();
}
```

**Response `200`:**
```json
{
  "uid": "24476d6e-823f-4936-a493-23c200fbec0a",
  "generated_at": "2026-02-28T04:10:52Z",
  "recommendations": [
    {
      "rank": 1,
      "restaurant": { ...RestaurantResult },
      "fit_score": 87,
      "fit_tags": [
        { "label": "South Indian specialist", "type": "cuisine" },
        { "label": "Budget-friendly",          "type": "price" }
      ],
      "consolidated_review": "Famous for their crispy set dosa — worth the queue on weekends.",
      "why_fit_paragraph": "Given your affinity for South Indian and budget tier...",
      "allergy_summary": { "is_safe": true, "warnings": [] },
      "expanded_detail": null
    }
  ]
}
```

**Errors:** `400` bad uid format, `403` uid mismatch, `500` pipeline failure.

Pass `?refresh=true` when the user explicitly requests a refresh (pull-to-refresh gesture).

---

### GET /recommendations/{uid}/{restaurant_id}/expand

Called lazily when a user expands a recommendation card. Always fresh (not cached).
May take 2–4 s (LLM call). Show a skeleton/spinner inside the card.

```typescript
async function expandRecommendation(
  uid: string,
  restaurantId: number
): Promise<ExpandedDetailResponse> {
  const res = await fetch(
    `${BASE_URL}/recommendations/${uid}/${restaurantId}/expand`,
    { headers: { 'X-User-ID': uid } }
  );
  if (!res.ok) throw await res.json();
  return res.json();
}
```

**Response `200`:**
```json
{
  "restaurant_id": 42,
  "expanded_detail": {
    "review_summary": "Consistently praised for dosas and filter coffee...",
    "highlights": [
      { "emoji": "☕", "text": "Filter coffee rated best in Koramangala" },
      { "emoji": "🌱", "text": "Full vegan menu available" }
    ],
    "crowd_profile": "Mix of office lunches and family dinners; regulars dominate morning slots.",
    "best_for": ["Solo lunch", "Weekend breakfast"],
    "avoid_if": ["Loud gatherings", "Late-night dining"],
    "radar_scores": {
      "romance": 3.5, "noise_level": 6.0, "food_quality": 9.0,
      "vegan_options": 8.5, "value_for_money": 8.0
    },
    "why_fit_paragraph": "Your preference for South Indian and vegan-friendly dining is...",
    "allergy_detail": {
      "is_safe": true, "confidence": "high", "warnings": [], "safe_note": "No nut-based dishes on the menu."
    }
  }
}
```

**Errors:** `404` restaurant not found, `403` uid mismatch, `500` LLM failure.

---

### React: RecommendationFeed Component Pattern

```tsx
// components/RecommendationFeed.tsx
import { useState, useEffect } from 'react';
import type { RecommendationItem, ExpandedDetailResponse } from '../types/kairos';

export function RecommendationFeed({ uid }: { uid: string }) {
  const [recs, setRecs] = useState<RecommendationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [expanding, setExpanding] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Record<number, ExpandedDetailResponse>>({});

  useEffect(() => {
    getRecommendations(uid).then(p => {
      setRecs(p.recommendations);
      setLoading(false);
    });
  }, [uid]);

  async function handleExpand(restaurantId: number) {
    if (expanded[restaurantId]) {
      setExpandedId(restaurantId);
      return;
    }
    setExpanding(restaurantId);
    try {
      const detail = await expandRecommendation(uid, restaurantId);
      setExpanded(prev => ({ ...prev, [restaurantId]: detail }));
      setExpandedId(restaurantId);
    } finally {
      setExpanding(null);
    }
  }

  if (loading) return <RecommendationSkeleton />;

  return (
    <div className="recommendation-feed">
      {recs.map(item => (
        <RecommendationCard
          key={item.restaurant.id}
          item={item}
          isExpanded={expandedId === item.restaurant.id}
          isExpanding={expanding === item.restaurant.id}
          expandedDetail={expanded[item.restaurant.id]?.expanded_detail}
          onExpand={() => handleExpand(item.restaurant.id)}
        />
      ))}
    </div>
  );
}
```

---

## 7. Health Endpoints

These are for monitoring only — the frontend should call `/ready` at app startup to
verify the Agent is up before enabling the chat UI.

### GET /health

Liveness probe. `200` if process is running.

```json
{ "status": "ok", "version": "1.0.0" }
```

### GET /ready

Readiness probe. `200` if DB + embedding API are healthy; `503` if either is down.

```json
{ "db": "ok", "embedding_api": "ok" }
```

```typescript
async function checkAgentReady(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE_URL}/ready`);
    return res.status === 200;
  } catch {
    return false;
  }
}
```

---

## 8. Error Handling Reference

All error responses have the shape: `{ "detail": string, "code"?: string }`.

| Status | Meaning | Frontend action |
|---|---|---|
| `400` | Bad request (e.g. invalid UUID format) | Show inline validation error |
| `401` | Auth token invalid or expired | Attempt token refresh; if fails, redirect to login |
| `403` | UID in path ≠ UID in `X-User-ID` header | Should never occur — indicates a frontend bug |
| `404` | User or restaurant not found | Show empty state |
| `422` | Schema validation failure | Show field errors from `detail` |
| `500` | Agent pipeline failed | Show retry toast |
| `503` | DB or embedding API down | Show maintenance banner |

Special header on auth errors: `X-Error-Code: MISSING_USER_ID` or `X-Error-Code: USER_NOT_FOUND`.

```typescript
// Centralised error handler
async function handleApiError(res: Response): Promise<never> {
  const body = await res.json().catch(() => ({ detail: 'Unknown error' }));
  const errorCode = res.headers.get('X-Error-Code');

  if (res.status === 401) {
    // Attempt refresh, then retry — handled by interceptor
  }
  if (res.status === 403) {
    console.error('UID mismatch — frontend bug:', body);
  }

  throw Object.assign(new Error(body.detail), { status: res.status, code: errorCode });
}
```

---

## 9. Complete API Client

A production-ready client encapsulating all endpoints:

```typescript
// lib/kairosClient.ts
const BASE_URL = process.env.NEXT_PUBLIC_AGENT_URL ?? 'http://localhost:8000';

let _accessToken: string | null = null;
let _refreshToken: string | null = null;
let _uid: string | null = null;

function getUid(token: string): string {
  return JSON.parse(atob(token.split('.')[1])).sub;
}

function userHeaders(): HeadersInit {
  if (!_uid) throw new Error('Not authenticated');
  return { 'X-User-ID': _uid, 'Content-Type': 'application/json' };
}

async function _refreshIfNeeded(): Promise<void> {
  if (!_refreshToken) throw new Error('No refresh token');
  const res = await fetch(`${BASE_URL}/auth/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: _refreshToken }),
  });
  if (!res.ok) { _accessToken = null; _refreshToken = null; _uid = null; throw new Error('Session expired'); }
  const tokens: TokenResponse = await res.json();
  _accessToken = tokens.access_token;
  _refreshToken = tokens.refresh_token;
  _uid = getUid(_accessToken);
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export const auth = {
  async register(body: RegisterRequest): Promise<RegisterResponse> {
    const res = await fetch(`${BASE_URL}/auth/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await res.json();
    return res.json();
  },

  async login(body: LoginRequest): Promise<{ uid: string }> {
    const res = await fetch(`${BASE_URL}/auth/login`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await res.json();
    const tokens: TokenResponse = await res.json();
    _accessToken = tokens.access_token;
    _refreshToken = tokens.refresh_token;
    _uid = getUid(_accessToken);
    return { uid: _uid };
  },

  async logout(): Promise<void> {
    if (!_accessToken) return;
    await fetch(`${BASE_URL}/auth/logout`, {
      method: 'POST', headers: { 'Authorization': `Bearer ${_accessToken}` },
    });
    _accessToken = null; _refreshToken = null; _uid = null;
  },

  async forgotPassword(email: string): Promise<MessageResponse> {
    const res = await fetch(`${BASE_URL}/auth/forgot-password`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    return res.json();
  },

  async resetPassword(body: ResetPasswordRequest): Promise<MessageResponse> {
    const res = await fetch(`${BASE_URL}/auth/reset-password`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await res.json();
    return res.json();
  },

  getUid: () => _uid,
  isAuthenticated: () => !!_uid,
};

// ── Chat ──────────────────────────────────────────────────────────────────────

export const chat = {
  /**
   * Returns an async generator yielding parsed ChatSSEEvent objects.
   * The final item will always have event === "result".
   */
  async *stream(
    message: string,
    history: ChatMessage[],
  ): AsyncGenerator<ChatSSEEvent> {
    const res = await fetch(`${BASE_URL}/chat`, {
      method: 'POST',
      headers: userHeaders(),
      body: JSON.stringify({ message, conversation_history: history }),
    });
    if (!res.ok) throw await res.json();

    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop() ?? '';
      for (const chunk of chunks) {
        const trimmed = chunk.trim();
        if (trimmed) {
          try { yield JSON.parse(trimmed) as ChatSSEEvent; } catch { /* skip */ }
        }
      }
    }
  },
};

// ── Recommendations ───────────────────────────────────────────────────────────

export const recommendations = {
  async getAll(
    uid: string,
    limit = 10,
    refresh = false,
  ): Promise<RecommendationPayload> {
    const params = new URLSearchParams({ limit: String(limit), refresh: String(refresh) });
    const res = await fetch(`${BASE_URL}/recommendations/${uid}?${params}`, {
      headers: userHeaders(),
    });
    if (!res.ok) throw await res.json();
    return res.json();
  },

  async expand(uid: string, restaurantId: number): Promise<ExpandedDetailResponse> {
    const res = await fetch(
      `${BASE_URL}/recommendations/${uid}/${restaurantId}/expand`,
      { headers: userHeaders() },
    );
    if (!res.ok) throw await res.json();
    return res.json();
  },
};

// ── Health ────────────────────────────────────────────────────────────────────

export const health = {
  async isReady(): Promise<boolean> {
    try {
      return (await fetch(`${BASE_URL}/ready`)).status === 200;
    } catch { return false; }
  },
};
```

---

## 10. FitTag Visual Mapping

Map `FitTag.type` to colour/icon in the recommendation card UI:

| `type` | Colour | Icon suggestion |
|---|---|---|
| `"cuisine"` | amber-600 | 🍽️ |
| `"vibe"` | violet-600 | ✨ |
| `"price"` | green-600 | 💰 |
| `"dietary"` | emerald-600 | 🌱 |
| `"allergy_safe"` | blue-600 | 🛡️ |

`fit_score` is 0–100. Suggested display: show as `{fit_score}% match`, colour-code:
- `≥ 80` → green badge
- `60–79` → amber badge
- `< 60` → grey badge

---

## 11. Conversation History Management

```typescript
// Keep in zustand/context — persist to sessionStorage for page refresh recovery
interface ConversationStore {
  history: ChatMessage[];
  append: (user: string, assistant: string) => void;
  clear: () => void;
}

// Append after every successful result event
function appendToHistory(userMsg: string, resultPayload: GenerativeUIPayload) {
  store.append(userMsg, resultPayload.message);
  // Keep max 10 turns (20 messages) to stay within context limits
  if (store.history.length > 20) {
    store.history = store.history.slice(-20);
  }
}
```

---

## 12. Thinking Step Progress UI

The `thinking.step` sequence is always the same. Map to user-facing labels:

```typescript
const STEP_LABELS: Record<string, string> = {
  decomposing_query:  'Understanding your request…',
  searching:          'Searching restaurants…',
  evaluating:         'Ranking results…',
  checking_allergies: 'Checking allergy safety…',
};
```

Show a progress bar or animated step indicator while `isStreaming` is `true`.
Reset on next `sendMessage` call.

---

## 13. Key Constraints and Gotchas

| # | Rule |
|---|---|
| 1 | **Never** use `Authorization: Bearer` for `/chat` or `/recommendations`. Use `X-User-ID` only. |
| 2 | **Never** call `/users/*` from the frontend — those need `SERVICE_TOKEN` which must never be in the browser. |
| 3 | `/chat` returns raw JSON lines delimited by `\n\n`, **not** standard `data: ...` SSE format. Parse each chunk as `JSON.parse(chunk)`. |
| 4 | `flagged_restaurants` must always be rendered separately and visually prominently with allergy danger tags. Silently dropping them is a safety violation. |
| 5 | `expanded_detail` on a `RecommendationItem` is always `null` from the list endpoint. It is only populated after calling `/expand`. |
| 6 | `X-User-ID` in the header must exactly equal the `uid` in the path for recommendation endpoints — a mismatch returns `403`. |
| 7 | `conversation_history` must use the raw `message` string from `GenerativeUIPayload`, not any UI-rendered text. |
| 8 | Recommendations are cached for 24h per user. Only send `?refresh=true` on explicit user action — not on every mount. |
| 9 | `generated_at` in `RecommendationPayload` can be `null` when the result set is empty — handle the null case. |
| 10 | `RestaurantResult.meta` is a freeform JSONB dict. It contains `vibe_tags: string[]` and `dietary_options: string[]` but these are not guaranteed. Always optional-chain. |
