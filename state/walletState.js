import { CONFIG } from '../config/index.js'

// address → WalletEntry
const wallets = new Map()

// mint → Map<walletAddress, entryTimestampMs>  (for clustering analysis)
const tokenBuyers = new Map()

// ── Classification ───────────────────────────────────────────────────────────

function classifyWallet(w) {
  const holdMin  = w.tradeCount > 0 ? (w.totalHoldMs / w.tradeCount) / 60_000 : 0
  const winRate  = w.tradeCount > 0 ? w.winCount / w.tradeCount : 0
  const entryMs  = w.avgEntryDelayMs

  // Must have enough history to classify — otherwise stay UNKNOWN
  if (w.tradeCount < 3) return 'UNKNOWN'

  // SNIPER: enters < 30 s after launch, exits < 5 min
  if (entryMs !== undefined && entryMs < 30_000 && holdMin < 5) return 'SNIPER'

  // SCALPER: high frequency, short holds
  if (w.tradeCount >= 20 && holdMin < 30) return 'SCALPER'

  // SWING: longer holds with good win rate
  if (holdMin >= 60 && winRate >= 0.6) return 'SWING'

  // FARMER: low conviction, low win rate
  if (winRate < 0.35 && w.tradeCount < 15) return 'FARMER'

  // RUGGER: very fast exits, many tokens, suspicious pattern
  if (holdMin > 0 && holdMin < 1 && w.tradeCount >= 10) return 'RUGGER'

  return 'UNKNOWN'
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Accumulate incremental wallet delta. Counters (tradeCount, winCount, etc.)
 * are ADDED to existing values, not replaced.
 */
export function updateWallet(address, delta) {
  const now = Date.now()
  const existing = wallets.get(address)

  if (existing) {
    const updated = { ...existing, lastActivity: now }

    // Accumulate counters
    if (delta.tradeCount)  updated.tradeCount  = existing.tradeCount  + delta.tradeCount
    if (delta.winCount)    updated.winCount    = existing.winCount    + delta.winCount
    if (delta.totalPnl)    updated.totalPnl    = existing.totalPnl    + delta.totalPnl
    if (delta.totalHoldMs) {
      updated.totalHoldMs = existing.totalHoldMs + delta.totalHoldMs
      updated.avgHoldMs   = updated.totalHoldMs / Math.max(updated.tradeCount, 1)
    }

    // Direct overrides (latest wins)
    if (delta.avgEntryDelayMs !== undefined) updated.avgEntryDelayMs = delta.avgEntryDelayMs

    updated.classification = classifyWallet(updated)
    wallets.set(address, updated)
  } else {
    const wallet = {
      classification:  'UNKNOWN',
      tradeCount:      delta.tradeCount  || 0,
      winCount:        delta.winCount    || 0,
      totalPnl:        delta.totalPnl    || 0,
      totalHoldMs:     delta.totalHoldMs || 0,
      avgHoldMs:       0,
      avgEntryDelayMs: delta.avgEntryDelayMs,
      lastActivity:    now,
      firstSeenAt:     now,
    }
    wallet.classification = classifyWallet(wallet)
    wallets.set(address, wallet)

    if (wallets.size > CONFIG.MAX_WALLETS) {
      wallets.delete(wallets.keys().next().value)
    }
  }
}

/**
 * Record that a wallet bought into a token (for clustering analysis).
 * Call this after updateWallet() for buy-side events.
 */
export function recordTokenEntry(walletAddress, mint) {
  if (!tokenBuyers.has(mint)) {
    tokenBuyers.set(mint, new Map())
  }
  tokenBuyers.get(mint).set(walletAddress, Date.now())

  // Keep the buyers index bounded
  if (tokenBuyers.size > CONFIG.MAX_TOKENS) {
    tokenBuyers.delete(tokenBuyers.keys().next().value)
  }
}

/**
 * Returns wallet entries for all wallets that bought `mint` within CLUSTER_WINDOW_MS.
 * Result includes only wallets that have a known state entry.
 */
export function getRecentBuyersForToken(mint) {
  const buyers = tokenBuyers.get(mint)
  if (!buyers) return []

  const cutoff = Date.now() - CONFIG.CLUSTER_WINDOW_MS
  const result = []

  for (const [addr, ts] of buyers) {
    if (ts < cutoff) continue
    const wallet = wallets.get(addr)
    if (wallet) result.push({ address: addr, ...wallet })
  }

  return result
}

export function getWallet(address) {
  return wallets.get(address) ?? null
}

export function getWalletCount() {
  return wallets.size
}
