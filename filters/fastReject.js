/**
 * LAYER 1 — FAST REJECT FILTER
 *
 * Rules:
 *  - O(1) only: Set lookups + property accesses
 *  - No loops, no async, no DB calls
 *  - Called on EVERY incoming WebSocket event
 *  - Returns true  → DROP EVENT
 *  - Returns false → PASS TO LAYER 2
 */
import { CONFIG } from '../config/index.js'

const VALID_CHANNELS = new Set([
  'token_launches',
  'new_pools',
  'pair_updates',
  'token_updates',
  'wallet_trades',
])

// Populated at startup from RUG_MINTS env var and at runtime via addRugMint()
const RUG_MINTS = new Set(
  (process.env.RUG_MINTS || '').split(',').filter(Boolean)
)

// Solana system program address — represents a disabled authority
const DISABLED_AUTHORITY = '11111111111111111111111111111111'

export function fastReject(event) {
  // Must be a recognised channel
  if (!VALID_CHANNELS.has(event.channel)) return true

  const d = event.data
  if (!d) return true

  // Must have an identifiable token mint
  const mint = d.mint || d.address || d.base_mint
  if (!mint) return true

  // Known rug — O(1) Set lookup
  if (RUG_MINTS.has(mint)) return true

  // Liquidity gating — only reject when the field is explicitly present and below threshold
  // (absent field = unknown, let it pass to state layer for enrichment)
  if (d.liquidity !== undefined) {
    const liq = parseFloat(d.liquidity)
    if (!isNaN(liq) && liq < CONFIG.MIN_LIQUIDITY) return true
  }

  // Mint authority enabled → high rug risk
  if (isMintAuthorityEnabled(d)) return true

  // Freeze authority enabled → holder funds can be frozen
  if (isFreezeAuthorityEnabled(d)) return true

  return false // PASS
}

/** Add a mint to the runtime rug list (O(1) write). */
export function addRugMint(mint) {
  RUG_MINTS.add(mint)
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function isMintAuthorityEnabled(d) {
  // GMGN may express authority as boolean true OR as an address string
  if (d.mint_authority === true) return true
  if (d.mint_authority_disabled === false) return true
  if (typeof d.mint_authority === 'string' && d.mint_authority !== DISABLED_AUTHORITY) return true
  return false
}

function isFreezeAuthorityEnabled(d) {
  if (d.freeze_authority === true) return true
  if (d.freeze_authority_disabled === false) return true
  if (typeof d.freeze_authority === 'string' && d.freeze_authority !== DISABLED_AUTHORITY) return true
  return false
}
