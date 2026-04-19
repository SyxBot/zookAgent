import { CONFIG } from '../config/index.js'

// mint → TokenEntry  (Map preserves insertion order for O(1) LRU eviction)
const tokens = new Map()

/**
 * Upsert a token with incremental delta. Only stores the minimum required fields.
 * Computes liquidityDelta and volumeDelta automatically on updates.
 */
export function updateToken(mint, delta) {
  const now = Date.now()
  const existing = tokens.get(mint)

  if (existing) {
    const newLiq = delta.liquidity ?? existing.liquidity
    const newVol = delta.volume1h ?? existing.volume1h

    tokens.set(mint, {
      ...existing,
      ...delta,
      lastLiquidity:  existing.liquidity,
      liquidityDelta: newLiq - existing.liquidity,
      volumeDelta:    newVol - existing.volume1h,
      lastUpdate:     now,
    })
  } else {
    tokens.set(mint, {
      // Baseline defaults
      liquidity:       0,
      volume1h:        0,
      volumeDelta:     0,
      lastLiquidity:   0,
      liquidityDelta:  0,
      isHoneypot:      false,
      mintAuthority:   false,
      freezeAuthority: false,
      renounced:       false,
      lpBurned:        false,
      top10HolderPct:  0,
      price:           0,
      createdAt:       now,
      lastUpdate:      now,
      ...delta,
    })

    // O(1) eviction: delete the oldest-inserted entry (Map insertion order)
    if (tokens.size > CONFIG.MAX_TOKENS) {
      tokens.delete(tokens.keys().next().value)
    }
  }
}

export function getToken(mint) {
  return tokens.get(mint) ?? null
}

export function getTokenCount() {
  return tokens.size
}

/** Returns the N most recently updated token entries as plain objects. */
export function getRecentTokens(limit = 100) {
  const result = []
  for (const [mint, data] of tokens) {
    result.push({ mint, ...data })
    if (result.length >= limit) break
  }
  return result
}
