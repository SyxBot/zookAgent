// Centralized config — all values come from environment variables
import 'dotenv/config'

export const CONFIG = {
  GMGN_ACCESS_TOKEN: process.env.GMGN_ACCESS_TOKEN || '',

  // Layer 1: fast reject
  MIN_LIQUIDITY: parseFloat(process.env.MIN_LIQUIDITY || '5000'),

  // Layer 4: qualification gate
  QUAL_MIN_LIQUIDITY:       parseFloat(process.env.QUAL_MIN_LIQUIDITY       || '10000'),
  QUAL_VOLUME_SPIKE_RATIO:  parseFloat(process.env.QUAL_VOLUME_SPIKE_RATIO  || '2.0'),
  QUAL_EARLY_LAUNCH_SECS:   parseInt(  process.env.QUAL_EARLY_LAUNCH_SECS   || '300'),

  // Layer 5/6: scoring and output
  SCORE_THRESHOLD: parseInt(process.env.SCORE_THRESHOLD || '60'),

  // In-memory state caps
  MAX_TOKENS:  parseInt(process.env.MAX_TOKENS  || '5000'),
  MAX_WALLETS: parseInt(process.env.MAX_WALLETS || '10000'),

  // Wallet clustering detection window (ms)
  CLUSTER_WINDOW_MS: parseInt(process.env.CLUSTER_WINDOW_MS || '300000'),

  PORT: parseInt(process.env.PORT || '3001'),
}
