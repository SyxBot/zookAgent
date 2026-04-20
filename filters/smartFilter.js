/**
 * LAYER 3 — SMART FILTER (BEHAVIORAL ANALYSIS)
 *
 * Runs AFTER state is updated. Checks wallet quality and token behaviour patterns.
 * Returns false → DROP, true → CONTINUE TO QUALIFICATION GATE
 */

const HIGH_QUALITY   = new Set(['SNIPER', 'SCALPER', 'SWING'])
const LOW_QUALITY    = new Set(['FARMER', 'RUGGER'])

// Volume/liquidity ratio above this threshold strongly suggests wash trading
const WASH_TRADE_RATIO = 50

/**
 * Decide whether this token event should advance to the Qualification Gate.
 *
 * @param {string}   mint
 * @param {object}   tokenData   — from tokenState.getToken()
 * @param {object[]} recentBuyers — from walletState.getRecentBuyersForToken()
 */
export function smartFilter(mint, tokenData, recentBuyers) {
  // ── Wash-trading detection ──────────────────────────────────────────────
  const liq = tokenData.liquidity || 0
  const vol = tokenData.volume1h  || 0

  if (liq > 0 && vol / liq > WASH_TRADE_RATIO) return false

  // ── Wallet quality gate ─────────────────────────────────────────────────
  if (recentBuyers.length === 0) {
    // No buyer data yet — allow early launches through so the qualification
    // gate can use the `isEarlyLaunch` condition instead
    return true
  }

  const qualityCount = recentBuyers.filter(b => HIGH_QUALITY.has(b.classification)).length
  const badCount     = recentBuyers.filter(b => LOW_QUALITY.has(b.classification)).length

  // All wallets are low quality → reject outright
  if (badCount === recentBuyers.length) return false

  // 5+ wallets observed but zero are high quality → downgrade
  if (recentBuyers.length >= 5 && qualityCount === 0) return false

  return true
}

/**
 * Compute clustering strength: fraction of recent buyers classified as high quality.
 * Returns a 0–1 score. Used in the Scoring Engine.
 *
 * @param {object[]} recentBuyers
 */
export function detectWalletClustering(recentBuyers) {
  if (recentBuyers.length < 2) return 0

  const qualityCount = recentBuyers.filter(b => HIGH_QUALITY.has(b.classification)).length
  if (qualityCount < 2) return 0

  // Normalise: 5+ quality buyers in window = full clustering score
  return Math.min(1, qualityCount / 5)
}
