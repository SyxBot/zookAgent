/**
 * LAYER 4 — QUALIFICATION GATE (HARD-STOP FILTER)
 *
 * ALL conditions must be true for deep analysis to proceed.
 * Returns false → STOP immediately, returns true → ADVANCE TO SCORING
 */
import { CONFIG } from '../config/index.js'

const HIGH_QUALITY_TYPES = new Set(['SNIPER', 'SCALPER', 'SWING'])

/**
 * @param {object}   tokenData    — from tokenState.getToken()
 * @param {object[]} recentBuyers — from walletState.getRecentBuyersForToken()
 */
export function qualificationGate(tokenData, recentBuyers) {
  // 1. Liquidity must be above the qualification floor
  if ((tokenData.liquidity || 0) < CONFIG.QUAL_MIN_LIQUIDITY) return false

  // 2. No critical safety flags
  if (tokenData.isHoneypot)     return false
  if (tokenData.mintAuthority)  return false
  if (tokenData.freezeAuthority) return false

  // 3. At least 1 high-quality wallet OR token is in early launch window
  const hasQualityWallet = recentBuyers.some(b => HIGH_QUALITY_TYPES.has(b.classification))
  const earlyLaunch      = isEarlyLaunch(tokenData)

  if (!hasQualityWallet && !earlyLaunch) return false

  // 4. Volume spike OR early launch phase (token must have momentum or recency)
  if (!hasVolumeSpike(tokenData) && !earlyLaunch) return false

  return true
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function isEarlyLaunch(tokenData) {
  if (!tokenData.createdAt) return false
  const ageSecs = (Date.now() - tokenData.createdAt) / 1_000
  return ageSecs <= CONFIG.QUAL_EARLY_LAUNCH_SECS
}

function hasVolumeSpike(tokenData) {
  const delta  = tokenData.volumeDelta || 0
  const prevVol = (tokenData.volume1h || 0) - delta

  if (prevVol <= 0) return delta > 0  // any volume on first read is a spike
  return (delta / prevVol) >= CONFIG.QUAL_VOLUME_SPIKE_RATIO
}
