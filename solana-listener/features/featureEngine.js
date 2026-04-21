import eventBus from "../core/eventBus.js";
import { TokenState } from "./tokenState.js";

// Global token state registry. One TokenState per unique mint address.
const registry = new Map(); // mint → TokenState

// Tokens with no activity for 30 minutes are evicted to cap memory usage.
// A busy Solana session can accumulate thousands of token states; without
// pruning, the registry would grow without bound across long-running sessions.
const STALE_TTL_MS = 30 * 60 * 1000;

function getOrCreate(mint) {
  if (!registry.has(mint)) {
    registry.set(mint, new TokenState(mint));
  }
  return registry.get(mint);
}

function evictStale() {
  const cutoff = Date.now() - STALE_TTL_MS;
  let evicted = 0;
  for (const [mint, state] of registry) {
    if (state.lastActivityAt < cutoff) {
      registry.delete(mint);
      evicted++;
    }
  }
  if (evicted > 0) {
    console.log(
      `[FeatureEngine] Evicted ${evicted} stale state(s). Registry: ${registry.size} tokens`
    );
  }
}

let _cleanupTimer = null;

/**
 * Start the feature extraction engine.
 *
 * Subscribes to Layer 2 "solana:enriched" and emits feature vectors as
 * "solana:features" for consumption by Layer 4 (Signal Engine).
 *
 * Each event is processed synchronously (TokenState.update + computeFeatures
 * are pure CPU — no I/O), keeping per-event latency well under 1ms for
 * normal registry sizes.
 */
export function startFeatureEngine() {
  eventBus.on("solana:enriched", (event) => {
    const mint = event?.token?.mint;
    if (!mint) return;

    try {
      const state = getOrCreate(mint);
      state.update(event);

      eventBus.emit("solana:features", {
        token_mint: mint,
        features: state.computeFeatures(),
        updated_at: Date.now(),
      });
    } catch (err) {
      console.error(`[FeatureEngine] Error on ${mint}:`, err.message);
    }
  });

  // Periodic stale-state cleanup runs every 10 minutes.
  // unref() ensures this timer does not prevent Node.js from exiting cleanly.
  _cleanupTimer = setInterval(evictStale, 10 * 60 * 1000);
  _cleanupTimer.unref?.();

  console.log("[FeatureEngine] Started — listening on solana:enriched");
}

export function stopFeatureEngine() {
  if (_cleanupTimer) {
    clearInterval(_cleanupTimer);
    _cleanupTimer = null;
  }
}

/** Direct registry access for Layer 4 queries (e.g., "give me current state for mint X"). */
export function getTokenState(mint) {
  return registry.get(mint) ?? null;
}

/** Current number of tracked tokens — useful for monitoring dashboards. */
export function registrySize() {
  return registry.size;
}
