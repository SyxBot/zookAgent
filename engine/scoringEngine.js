/**
 * LAYER 5 — SCORING ENGINE
 *
 * Only runs on qualified tokens (after layer 4).
 * Computes a 0–100 score from five weighted components.
 *
 * Output shape:
 *   { score, confidence, setup, risk, reasons }
 */
import { detectWalletClustering } from '../filters/smartFilter.js'

const HIGH_QUALITY = new Set(['SNIPER', 'SCALPER', 'SWING'])

// Scoring weights — must sum to 1.0
const W = { walletQuality: 0.35, clustering: 0.25, entryTiming: 0.20, liqStability: 0.10, volMomentum: 0.10 }

/**
 * @param {object}   tokenData    — from tokenState.getToken()
 * @param {object[]} recentBuyers — from walletState.getRecentBuyersForToken()
 * @returns {{ score, confidence, setup, risk, reasons }}
 */
export function computeScore(tokenData, recentBuyers) {
  const walletQuality = computeWalletQuality(recentBuyers)
  const clustering    = detectWalletClustering(recentBuyers)
  const entryTiming   = computeEntryTiming(tokenData)
  const liqStability  = computeLiquidityStability(tokenData)
  const volMomentum   = computeVolumeMomentum(tokenData)

  const raw   = walletQuality * W.walletQuality
              + clustering    * W.clustering
              + entryTiming   * W.entryTiming
              + liqStability  * W.liqStability
              + volMomentum   * W.volMomentum

  const score      = Math.round(Math.min(100, Math.max(0, raw * 100)))
  const confidence = score >= 70 ? 'HIGH' : score >= 50 ? 'MEDIUM' : 'LOW'
  const setup      = determineSetup(tokenData, recentBuyers)
  const risk       = computeRisk(tokenData, recentBuyers)
  const reasons    = buildReasons({ walletQuality, clustering, entryTiming, liqStability, volMomentum })

  return { score, confidence, setup, risk, reasons }
}

// ── Component scorers ─────────────────────────────────────────────────────────

function computeWalletQuality(buyers) {
  if (buyers.length === 0) return 0.3   // neutral baseline — no data yet
  const quality = buyers.filter(b => HIGH_QUALITY.has(b.classification)).length
  return quality / buyers.length
}

function computeEntryTiming(tokenData) {
  if (!tokenData.createdAt) return 0.5
  const ageSecs = (Date.now() - tokenData.createdAt) / 1_000
  // Peaks at 0 seconds, reaches 0 at 1 hour
  return Math.max(0, 1 - ageSecs / 3_600)
}

function computeLiquidityStability(tokenData) {
  const prev = tokenData.lastLiquidity || 0
  if (prev <= 0) return 0.5   // no baseline yet → neutral
  const change = (tokenData.liquidityDelta || 0) / prev
  // ±10% change maps to 0–1 range; growing liquidity is bullish
  return Math.min(1, Math.max(0, 0.5 + change * 5))
}

function computeVolumeMomentum(tokenData) {
  const vol = tokenData.volume1h  || 0
  const liq = tokenData.liquidity || 1
  const ratio = vol / liq
  // Healthy range: 1–5×. Below 0.5 = illiquid. Above 20 = wash-trade risk.
  if (ratio < 0.5)  return 0.2
  if (ratio > 20)   return 0.1
  return Math.min(1, ratio / 5)
}

// ── Setup + risk ──────────────────────────────────────────────────────────────

function determineSetup(tokenData, buyers) {
  const ageMs      = tokenData.createdAt ? Date.now() - tokenData.createdAt : Infinity
  const hasSnipers = buyers.some(b => b.classification === 'SNIPER')
  const volSpike   = (tokenData.volumeDelta || 0) > 0

  if (ageMs < 120_000 && hasSnipers) return 'EARLY_SNIPER'
  if (volSpike) return 'MOMENTUM'
  return 'LATE_EXIT'
}

function computeRisk(tokenData, buyers) {
  let r = 0
  if (!tokenData.lpBurned)                        r += 2
  if (!tokenData.renounced)                       r += 1
  if ((tokenData.top10HolderPct || 0) > 50)       r += 2
  r += buyers.filter(b => b.classification === 'RUGGER').length
  return r >= 4 ? 'HIGH' : r >= 2 ? 'MEDIUM' : 'LOW'
}

// ── Reason builder ────────────────────────────────────────────────────────────

function buildReasons({ walletQuality, clustering, entryTiming, liqStability, volMomentum }) {
  const reasons = []

  if (walletQuality >= 0.7) reasons.push('Strong wallet quality signals')
  else if (walletQuality <  0.4) reasons.push('Weak wallet quality')

  if (clustering >= 0.4) reasons.push(`Wallet clustering detected (${Math.round(clustering * 100)}%)`)

  if (entryTiming >= 0.8)      reasons.push('Early entry timing window')
  else if (entryTiming < 0.25) reasons.push('Late entry — reduced upside potential')

  if (volMomentum >= 0.7)  reasons.push('Strong volume momentum')
  if (liqStability < 0.3)  reasons.push('Liquidity declining — exercise caution')

  return reasons.slice(0, 3)
}
