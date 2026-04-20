/**
 * CORE PIPELINE ORCHESTRATOR
 *
 * Receives every decoded WebSocket event and routes it through the 6-layer pipeline:
 *
 *   [1] FAST REJECT  → [2] STATE UPDATE → [3] SMART FILTER
 *       → [4] QUALIFICATION GATE → [5] SCORING ENGINE → [6] OUTPUT
 *
 * All operations are synchronous and non-blocking.
 * Counters are the only persistent side-effect per event (no raw history stored).
 */
import { fastReject }             from '../filters/fastReject.js'
import { smartFilter }            from '../filters/smartFilter.js'
import { qualificationGate }      from '../engine/qualificationGate.js'
import { computeScore }           from '../engine/scoringEngine.js'
import { updateToken, getToken }  from '../state/tokenState.js'
import {
  updateWallet,
  recordTokenEntry,
  getRecentBuyersForToken,
} from '../state/walletState.js'
import { emit }                   from '../output/alertSystem.js'
import { CONFIG }                 from '../config/index.js'

// Lightweight pipeline counters — never stored to disk
const stats = { total: 0, rejected: 0, filtered: 0, gated: 0, scored: 0, emitted: 0 }

// ── Entry point ───────────────────────────────────────────────────────────────

export function processEvent(event) {
  stats.total++

  // ── LAYER 1: FAST REJECT ─────────────────────────────────────────────────
  if (fastReject(event)) {
    stats.rejected++
    return
  }

  const d      = event.data || event
  const channel = event.channel || ''
  const mint   = d.mint || d.address || d.base_mint

  // ── LAYER 2: STATE UPDATE ────────────────────────────────────────────────
  updateToken(mint, extractTokenDelta(d))

  // Wallet side: deployer, maker, or buyer address depending on channel
  const walletAddr = d.wallet || d.maker || d.deployer || d.trader
  if (walletAddr) {
    updateWallet(walletAddr, extractWalletDelta(d, channel))
    if (isBuySide(d, channel)) recordTokenEntry(walletAddr, mint)
  }

  // Wallet-trade events on previously seen tokens: state is updated, no further
  // processing needed unless the token itself is the primary object of the event.
  if (channel === 'wallet_trades' && !isNewToken(channel)) return

  // ── LAYERS 3–6 ───────────────────────────────────────────────────────────
  runTokenPipeline(mint, d)
}

// ── Pipeline for a single token ───────────────────────────────────────────────

function runTokenPipeline(mint, d) {
  const tokenData    = getToken(mint)
  const recentBuyers = getRecentBuyersForToken(mint)

  // ── LAYER 3: SMART FILTER ────────────────────────────────────────────────
  if (!smartFilter(mint, tokenData, recentBuyers)) {
    stats.filtered++
    return
  }

  // ── LAYER 4: QUALIFICATION GATE ──────────────────────────────────────────
  if (!qualificationGate(tokenData, recentBuyers)) {
    stats.gated++
    return
  }

  // ── LAYER 5: SCORING ENGINE ──────────────────────────────────────────────
  stats.scored++
  const result = computeScore(tokenData, recentBuyers)

  // ── LAYER 6: OUTPUT ──────────────────────────────────────────────────────
  if (result.score > CONFIG.SCORE_THRESHOLD && result.confidence !== 'LOW') {
    stats.emitted++
    emit({
      token:   mint,
      symbol:  d.symbol  || tokenData?.symbol  || '',
      name:    d.name    || tokenData?.name    || '',
      price:   d.price   || tokenData?.price   || 0,
      ...result,
    })
  }
}

// ── Delta extractors ──────────────────────────────────────────────────────────

function extractTokenDelta(d) {
  const delta = {}

  // Identity
  if (d.symbol)  delta.symbol  = d.symbol
  if (d.name)    delta.name    = d.name
  if (d.logo)    delta.logo    = d.logo

  // Market data — use nullish so 0 is preserved
  if (d.liquidity  != null) delta.liquidity  = parseFloat(d.liquidity)
  if (d.price      != null) delta.price      = parseFloat(d.price)

  // Volume — try multiple field name variations from GMGN
  const vol = d.volume1h ?? d.volume_1h ?? d.volume
  if (vol != null) delta.volume1h = parseFloat(vol)

  // Security flags — handle boolean and numeric (0/1) representations
  if (d.is_honeypot             != null) delta.isHoneypot      = Boolean(d.is_honeypot)
  if (d.is_renounced            != null) delta.renounced        = Boolean(d.is_renounced)
  if (d.lp_burned               != null) delta.lpBurned         = Boolean(d.lp_burned)
  if (d.top10_holder_percent    != null) delta.top10HolderPct   = parseFloat(d.top10_holder_percent)
  if (d.mint_authority_disabled != null) delta.mintAuthority    = d.mint_authority_disabled === false
  if (d.freeze_authority        != null) delta.freezeAuthority  = Boolean(d.freeze_authority)

  return delta
}

function extractWalletDelta(d, channel) {
  if (channel === 'wallet_trades') {
    const pnl  = d.pnl  != null ? parseFloat(d.pnl)  : null
    const side = (d.type || d.side || '').toLowerCase()
    return {
      tradeCount: 1,
      ...(pnl !== null && { totalPnl: pnl }),
      ...(pnl !== null && pnl > 0 && side === 'sell' && { winCount: 1 }),
    }
  }
  return {}
}

// ── Predicates ────────────────────────────────────────────────────────────────

function isBuySide(d, channel) {
  if (channel === 'wallet_trades') {
    const side = (d.type || d.side || '').toLowerCase()
    return side === 'buy' || side === ''
  }
  // For launch/pool events, treat all wallet activity as entry
  return true
}

function isNewToken(channel) {
  return channel === 'token_launches' || channel === 'new_pools'
}

// ── Public stats ──────────────────────────────────────────────────────────────

export function getStats() {
  const rejectRate = stats.total > 0
    ? ((stats.rejected / stats.total) * 100).toFixed(1)
    : '0.0'
  return { ...stats, rejectRatePct: parseFloat(rejectRate) }
}
