/**
 * RecommendationFeed — personalised restaurant picks outside the chat interface.
 *
 * Component tree:
 *   RecommendationFeed
 *     └─ RecommendationCard (collapsed)
 *          └─ ExpandedPanel (lazy-loaded on first expand)
 *               └─ RadarChart (recharts)
 *
 * Data flow:
 *   Mount → GET /recommendations/{uid}
 *   Tap card → GET /recommendations/{uid}/{restaurant_id}/expand
 *
 * Styling: Tailwind CSS utility classes only — no custom CSS files.
 * Charts:  recharts RadarChart.
 */

import React, { useState, useEffect, useCallback } from 'react'
import {
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  ResponsiveContainer,
} from 'recharts'

// ── Constants ─────────────────────────────────────────────────────────────────

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

const FIT_SCORE_COLOUR = (score) => {
  if (score >= 80) return '#22c55e'
  if (score >= 60) return '#f59e0b'
  return '#6b7280'
}

const FIT_TAG_ICON = {
  cuisine: '🍴',
  vibe: '✨',
  price: '💰',
  dietary: '🌿',
  allergy_safe: '✅',
}

const ALLERGY_LEVEL_STYLES = {
  danger:  { bg: 'bg-red-50 dark:bg-red-950',    border: 'border-red-300 dark:border-red-700', text: 'text-red-800 dark:text-red-200', icon: '🚨' },
  warning: { bg: 'bg-amber-50 dark:bg-amber-950', border: 'border-amber-300 dark:border-amber-700', text: 'text-amber-800 dark:text-amber-200', icon: '⚠️' },
  caution: { bg: 'bg-yellow-50 dark:bg-yellow-950', border: 'border-yellow-300 dark:border-yellow-700', text: 'text-yellow-800 dark:text-yellow-200', icon: '⚡' },
  info:    { bg: 'bg-blue-50 dark:bg-blue-950',   border: 'border-blue-300 dark:border-blue-700', text: 'text-blue-800 dark:text-blue-200', icon: 'ℹ️' },
}

// ── Time formatting ───────────────────────────────────────────────────────────

function formatGeneratedAt(isoString) {
  if (!isoString) return ''
  const then = new Date(isoString)
  const nowMs = Date.now()
  const diffMs = nowMs - then.getTime()
  const diffMins = Math.floor(diffMs / 60_000)
  if (diffMins < 2)   return 'Updated just now'
  if (diffMins < 60)  return `Updated ${diffMins}m ago`
  const diffHrs = Math.floor(diffMins / 60)
  if (diffHrs < 24)   return `Updated ${diffHrs}h ago`
  const diffDays = Math.floor(diffHrs / 24)
  return `Updated ${diffDays}d ago`
}

// ── Skeleton loader ───────────────────────────────────────────────────────────

function SkeletonCard() {
  return (
    <div className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-2xl p-5 animate-pulse">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          <div className="w-6 h-6 rounded-full bg-zinc-200 dark:bg-zinc-700" />
          <div className="h-5 w-40 rounded bg-zinc-200 dark:bg-zinc-700" />
        </div>
        <div className="w-12 h-12 rounded-full bg-zinc-200 dark:bg-zinc-700" />
      </div>
      <div className="h-3 w-48 rounded bg-zinc-200 dark:bg-zinc-700 mb-3" />
      <div className="flex gap-2 mb-3">
        <div className="h-5 w-24 rounded-full bg-zinc-200 dark:bg-zinc-700" />
        <div className="h-5 w-20 rounded-full bg-zinc-200 dark:bg-zinc-700" />
        <div className="h-5 w-28 rounded-full bg-zinc-200 dark:bg-zinc-700" />
      </div>
      <div className="h-4 w-full rounded bg-zinc-200 dark:bg-zinc-700 mb-1" />
      <div className="h-4 w-3/4 rounded bg-zinc-200 dark:bg-zinc-700" />
    </div>
  )
}

function ExpandedSkeleton() {
  return (
    <div className="mt-4 pt-4 border-t border-zinc-100 dark:border-zinc-800 animate-pulse space-y-4">
      {/* Why fit */}
      <div>
        <div className="h-3 w-24 rounded bg-zinc-200 dark:bg-zinc-700 mb-2" />
        <div className="h-4 w-full rounded bg-zinc-200 dark:bg-zinc-700 mb-1" />
        <div className="h-4 w-5/6 rounded bg-zinc-200 dark:bg-zinc-700" />
      </div>
      {/* Highlights */}
      <div>
        <div className="h-3 w-20 rounded bg-zinc-200 dark:bg-zinc-700 mb-2" />
        <div className="space-y-1">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-4 w-4/5 rounded bg-zinc-200 dark:bg-zinc-700" />
          ))}
        </div>
      </div>
      {/* Radar placeholder */}
      <div className="h-48 w-full rounded-xl bg-zinc-100 dark:bg-zinc-800" />
      {/* Review summary */}
      <div>
        <div className="h-3 w-28 rounded bg-zinc-200 dark:bg-zinc-700 mb-2" />
        <div className="h-4 w-full rounded bg-zinc-200 dark:bg-zinc-700 mb-1" />
        <div className="h-4 w-full rounded bg-zinc-200 dark:bg-zinc-700 mb-1" />
        <div className="h-4 w-3/4 rounded bg-zinc-200 dark:bg-zinc-700" />
      </div>
    </div>
  )
}

// ── Radar chart ───────────────────────────────────────────────────────────────

function RestaurantRadar({ radarScores, colour }) {
  if (!radarScores) return null

  const data = [
    { axis: 'Romance',  value: radarScores.romance       ?? 0 },
    { axis: 'Quiet',    value: radarScores.noise_level   ?? 0 },
    { axis: 'Food',     value: radarScores.food_quality  ?? 0 },
    { axis: 'Vegan',    value: radarScores.vegan_options ?? 0 },
    { axis: 'Value',    value: radarScores.value_for_money ?? 0 },
  ]

  return (
    <ResponsiveContainer width="100%" height={200}>
      <RadarChart cx="50%" cy="50%" outerRadius="70%" data={data}>
        <PolarGrid stroke="#e4e4e7" />
        <PolarAngleAxis
          dataKey="axis"
          tick={{ fontSize: 11, fill: '#71717a' }}
        />
        <Radar
          dataKey="value"
          stroke={colour}
          fill={colour}
          fillOpacity={0.25}
          strokeWidth={2}
          dot={{ r: 3, fill: colour }}
        />
      </RadarChart>
    </ResponsiveContainer>
  )
}

// ── Allergy warning banner ────────────────────────────────────────────────────

function AllergyBanner({ warnings, compact = false }) {
  if (!warnings || warnings.length === 0) return null

  const worst = warnings.reduce((prev, curr) => {
    const rank = { danger: 3, warning: 2, caution: 1, info: 0 }
    return (rank[curr.level] ?? 0) > (rank[prev.level] ?? 0) ? curr : prev
  }, warnings[0])

  const style = ALLERGY_LEVEL_STYLES[worst.level] || ALLERGY_LEVEL_STYLES.info

  if (compact) {
    return (
      <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium mt-2 ${style.bg} ${style.border} ${style.text}`}>
        <span>{style.icon}</span>
        <span>{worst.title}</span>
      </div>
    )
  }

  return (
    <div className={`rounded-xl border px-4 py-3 mt-3 ${style.bg} ${style.border}`}>
      <p className={`font-semibold text-sm mb-1 ${style.text}`}>
        {style.icon} {worst.title}
      </p>
      {warnings.map((w, i) => (
        <p key={i} className={`text-xs ${style.text}`}>
          {w.message}
          {w.confidence_note && (
            <span className="opacity-70"> ({w.confidence_note})</span>
          )}
        </p>
      ))}
    </div>
  )
}

// ── Expanded panel ────────────────────────────────────────────────────────────

function SectionHeader({ children }) {
  return (
    <p className="text-xs font-semibold tracking-widest uppercase text-zinc-400 mb-2">
      {children}
    </p>
  )
}

function Divider() {
  return <hr className="border-zinc-100 dark:border-zinc-800 my-4" />
}

function ExpandedPanel({ detail, fitColour, restaurantUrl }) {
  const allergyStyle = detail.allergy_detail.is_safe
    ? ALLERGY_LEVEL_STYLES.info
    : ALLERGY_LEVEL_STYLES.danger

  return (
    <div className="mt-4 pt-4 border-t border-zinc-100 dark:border-zinc-800">

      {/* WHY IT'S YOUR FIT */}
      <SectionHeader>Why it&apos;s your fit</SectionHeader>
      <p className="text-sm italic text-zinc-600 dark:text-zinc-400 border-l-2 border-zinc-300 dark:border-zinc-600 pl-3">
        {detail.why_fit_paragraph}
      </p>

      <Divider />

      {/* HIGHLIGHTS */}
      <SectionHeader>Highlights</SectionHeader>
      <ul className="space-y-1">
        {detail.highlights.map((h, i) => (
          <li key={i} className="text-sm text-zinc-700 dark:text-zinc-300 flex items-start gap-2">
            <span className="shrink-0">{h.emoji}</span>
            <span>{h.text}</span>
          </li>
        ))}
      </ul>

      <Divider />

      {/* RADAR CHART */}
      <SectionHeader>Dimension scores</SectionHeader>
      <RestaurantRadar radarScores={detail.radar_scores} colour={fitColour} />

      <Divider />

      {/* REVIEW SUMMARY */}
      <SectionHeader>Review summary</SectionHeader>
      <p className="text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed">
        {detail.review_summary}
      </p>

      <Divider />

      {/* BEST FOR / AVOID IF */}
      <div className="flex gap-6">
        <div className="flex-1">
          <SectionHeader>Best for</SectionHeader>
          <div className="flex flex-wrap gap-1.5">
            {detail.best_for.map((tag, i) => (
              <span
                key={i}
                className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
              >
                {tag}
              </span>
            ))}
          </div>
        </div>
        {detail.avoid_if.length > 0 && (
          <div className="flex-1">
            <SectionHeader>Avoid if</SectionHeader>
            <div className="flex flex-wrap gap-1.5">
              {detail.avoid_if.map((tag, i) => (
                <span
                  key={i}
                  className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300"
                >
                  {tag}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <Divider />

      {/* WHO GOES HERE */}
      <SectionHeader>Who goes here</SectionHeader>
      <p className="text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed">
        {detail.crowd_profile}
      </p>

      <Divider />

      {/* ALLERGY DETAIL */}
      <SectionHeader>Allergy detail</SectionHeader>
      <div className={`rounded-xl border px-4 py-3 ${allergyStyle.bg} ${allergyStyle.border}`}>
        {detail.allergy_detail.is_safe ? (
          <p className={`text-sm font-medium ${allergyStyle.text}`}>
            ✅ {detail.allergy_detail.safe_note || 'No allergens detected matching your profile.'}
          </p>
        ) : (
          <>
            <p className={`text-sm font-semibold mb-1 ${allergyStyle.text}`}>
              🚨 Allergy warnings
            </p>
            {detail.allergy_detail.warnings.map((w, i) => (
              <p key={i} className={`text-xs ${allergyStyle.text}`}>
                {w.emoji} <strong>{w.allergen}</strong> — {w.message}
              </p>
            ))}
          </>
        )}
        <p className="text-xs text-zinc-400 mt-1">
          Confidence: {detail.allergy_detail.confidence}
        </p>
      </div>

      {/* Footer actions */}
      <div className="flex items-center justify-between mt-5">
        {/* Collapse hint is handled by parent — nothing here */}
        <span />
        {restaurantUrl && (
          <a
            href={restaurantUrl}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="inline-flex items-center gap-1 text-xs font-medium text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-white transition-colors"
          >
            Open on Zomato ↗
          </a>
        )}
      </div>
    </div>
  )
}

// ── Recommendation card ───────────────────────────────────────────────────────

function RecommendationCard({ item, uid, apiHeaders }) {
  const [expanded, setExpanded]         = useState(false)
  const [expandedDetail, setExpandedDetail] = useState(null)
  const [expandLoading, setExpandLoading]   = useState(false)
  const [expandError, setExpandError]       = useState(null)

  const { restaurant, fit_score, fit_tags, consolidated_review, allergy_summary, rank } = item
  const fitColour = FIT_SCORE_COLOUR(fit_score)

  // Fire expand fetch when first opened
  useEffect(() => {
    if (!expanded || expandedDetail !== null) return

    let cancelled = false
    setExpandLoading(true)
    setExpandError(null)

    fetch(`${API_BASE}/recommendations/${uid}/${restaurant.id}/expand`, {
      headers: apiHeaders,
    })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then((data) => {
        if (!cancelled) {
          setExpandedDetail(data.expanded_detail)
          setExpandLoading(false)
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setExpandError('Failed to load details — tap to retry.')
          setExpandLoading(false)
        }
      })

    return () => { cancelled = true }
  }, [expanded, expandedDetail, uid, restaurant.id, apiHeaders])

  const handleToggle = () => setExpanded((v) => !v)

  const cuisineLabel = (restaurant.cuisine_types || []).slice(0, 2).join(' · ')
  const metaLine = [cuisineLabel, restaurant.area, restaurant.price_tier]
    .filter(Boolean)
    .join('  ·  ')
  const ratingLabel = restaurant.rating ? `⭐ ${restaurant.rating.toFixed(1)}` : null

  return (
    <div
      onClick={handleToggle}
      className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-2xl p-5 cursor-pointer hover:shadow-lg transition-shadow duration-200 select-none"
    >
      {/* Top row: rank + name + fit score */}
      <div className="flex items-start justify-between gap-3 mb-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="w-6 h-6 shrink-0 rounded-full bg-zinc-900 dark:bg-white text-white dark:text-zinc-900 text-xs font-bold flex items-center justify-center">
            {rank}
          </span>
          <h3 className="font-semibold text-zinc-900 dark:text-white text-base leading-tight truncate">
            {restaurant.name}
          </h3>
        </div>
        <div
          className="w-12 h-12 shrink-0 rounded-full flex items-center justify-center font-bold text-white text-sm"
          style={{ backgroundColor: fitColour }}
          title={`Fit score: ${fit_score}/100`}
        >
          {fit_score}
        </div>
      </div>

      {/* Meta line */}
      <p className="text-xs text-zinc-400 dark:text-zinc-500 mb-3 flex items-center gap-2 flex-wrap">
        {metaLine}
        {ratingLabel && <span className="ml-auto">{ratingLabel}</span>}
      </p>

      {/* Fit tag pills */}
      {fit_tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {fit_tags.map((tag, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300"
            >
              {FIT_TAG_ICON[tag.type] || '•'}
              {tag.label}
            </span>
          ))}
        </div>
      )}

      {/* Consolidated review */}
      <p className="text-sm text-zinc-600 dark:text-zinc-400 leading-relaxed mb-2 italic">
        &ldquo;{consolidated_review}&rdquo;
      </p>

      {/* Allergy banner (compact) */}
      {allergy_summary.warnings.length > 0 && (
        <AllergyBanner warnings={allergy_summary.warnings} compact />
      )}

      {/* Expand/collapse caret */}
      <p className="text-xs text-zinc-400 dark:text-zinc-600 mt-3 text-right">
        {expanded ? '↑ Collapse' : '↓ See full details'}
      </p>

      {/* Expanded content */}
      <div
        className="overflow-hidden transition-all duration-400 ease-in-out"
        style={{ maxHeight: expanded ? '2000px' : '0px' }}
      >
        {expanded && (
          expandLoading ? (
            <ExpandedSkeleton />
          ) : expandError ? (
            <p className="mt-4 text-sm text-red-500 dark:text-red-400">{expandError}</p>
          ) : expandedDetail ? (
            <ExpandedPanel
              detail={expandedDetail}
              fitColour={fitColour}
              restaurantUrl={restaurant.url}
            />
          ) : null
        )}
      </div>
    </div>
  )
}

// ── Recommendation feed ───────────────────────────────────────────────────────

/**
 * RecommendationFeed
 *
 * Props:
 *   uid        — authenticated user UUID string
 *   authToken  — the value to send as X-User-ID header
 */
export default function RecommendationFeed({ uid, authToken }) {
  const [recommendations, setRecommendations] = useState([])
  const [generatedAt, setGeneratedAt]         = useState(null)
  const [loading, setLoading]                 = useState(true)
  const [refreshing, setRefreshing]           = useState(false)
  const [error, setError]                     = useState(null)

  const apiHeaders = {
    'X-User-ID': authToken || uid,
    'Content-Type': 'application/json',
  }

  const fetchRecommendations = useCallback(
    async (forceRefresh = false) => {
      const url = `${API_BASE}/recommendations/${uid}${forceRefresh ? '?refresh=true' : ''}`
      try {
        const res = await fetch(url, { headers: apiHeaders })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        setRecommendations(data.recommendations || [])
        setGeneratedAt(data.generated_at || null)
        setError(null)
      } catch (err) {
        setError('Could not load recommendations. Please try again.')
      }
    },
    [uid, apiHeaders],
  )

  // Initial load
  useEffect(() => {
    setLoading(true)
    fetchRecommendations(false).finally(() => setLoading(false))
  }, [uid])

  const handleRefresh = async () => {
    setRefreshing(true)
    await fetchRecommendations(true)
    setRefreshing(false)
  }

  return (
    <div className="max-w-xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-lg font-semibold text-zinc-900 dark:text-white">
            Picked for you
          </h2>
          {generatedAt && (
            <p className="text-xs text-zinc-400 dark:text-zinc-500 mt-0.5">
              {formatGeneratedAt(generatedAt)}
            </p>
          )}
        </div>
        <button
          onClick={handleRefresh}
          disabled={loading || refreshing}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium
                     bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300
                     hover:bg-zinc-200 dark:hover:bg-zinc-700
                     disabled:opacity-50 disabled:cursor-not-allowed
                     transition-colors duration-150"
        >
          <span className={refreshing ? 'animate-spin' : ''}>🔄</span>
          Refresh
        </button>
      </div>

      {/* Error state */}
      {error && !loading && (
        <div className="bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800 rounded-xl px-4 py-3 text-sm text-red-700 dark:text-red-300 mb-4">
          {error}
        </div>
      )}

      {/* Loading skeleton */}
      {loading ? (
        <div className="space-y-4">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : recommendations.length === 0 && !error ? (
        <div className="text-center py-12 text-zinc-400 dark:text-zinc-500">
          <p className="text-3xl mb-3">🍽️</p>
          <p className="text-sm">No recommendations yet — chat with Kairos to build your profile!</p>
        </div>
      ) : (
        <div className="space-y-4">
          {recommendations.map((item) => (
            <RecommendationCard
              key={item.restaurant.id}
              item={item}
              uid={uid}
              apiHeaders={apiHeaders}
            />
          ))}
        </div>
      )}
    </div>
  )
}
