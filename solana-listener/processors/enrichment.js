import eventBus from "../core/eventBus.js";
import { getDexPairData, getSolPrice, SOL_MINT } from "./priceService.js";
import { getTokenMetadata } from "./tokenService.js";

/**
 * Refine event_type using USD value (swaps) and pair age (liquidity events).
 * This is purely a classification enhancement — no filtering or scoring.
 */
function refineEventType(eventType, amountUsd, pairData) {
  if (eventType === "swap") {
    if (amountUsd != null && amountUsd >= 10_000) return "large_swap";
    return "swap";
  }

  if (eventType === "liquidity_add" && pairData?.pairCreatedAt) {
    const pairAgeSec = (Date.now() - pairData.pairCreatedAt) / 1000;
    // Pair created within the last 5 minutes → this event is LP creation, not just an add.
    if (pairAgeSec < 300) return "lp_creation";
  }

  return eventType;
}

async function enrichEvent(rawEvent) {
  const { timestamp, signature, event_type, token_mint, wallet, amount, raw } =
    rawEvent;

  // Fire all external lookups concurrently.
  // getDexPairData is internally deduplicated so getTokenMetadata + getDexPairData
  // for the same mint share a single HTTP request when the cache is cold.
  const [tokenMeta, pairData, solPrice] = await Promise.all([
    getTokenMetadata(token_mint),
    getDexPairData(token_mint),
    getSolPrice(),
  ]);

  // USD conversion — handle SOL-native amounts separately.
  const isSolAmount = token_mint === SOL_MINT || token_mint == null;
  const priceUsd = isSolAmount ? solPrice : (pairData?.priceUsd ?? null);
  const amountUsd =
    amount != null && priceUsd != null ? amount * priceUsd : null;

  // Token age from the earliest known liquidity event (Dexscreener pairCreatedAt).
  let tokenAgeSec = null;
  let isNewToken = false;
  if (pairData?.pairCreatedAt) {
    tokenAgeSec = Math.floor((Date.now() - pairData.pairCreatedAt) / 1000);
    isNewToken = tokenAgeSec < 3600; // first hour of trading
  }

  return {
    timestamp,
    signature,
    event_type: refineEventType(event_type, amountUsd, pairData),

    token: {
      mint: token_mint ?? null,
      symbol: tokenMeta?.symbol ?? null,
      decimals: tokenMeta?.decimals ?? null,
    },

    wallet: wallet ?? null,

    metrics: {
      amount: amount ?? null,
      amount_usd: amountUsd,
      liquidity_usd: pairData?.liquidityUsd ?? null,
      price_usd: priceUsd,
    },

    metadata: {
      token_age_sec: tokenAgeSec,
      is_new_token: isNewToken,
      source: "helius",
    },

    raw,
  };
}

/**
 * Start the enrichment processor.
 * Subscribes to Layer 1 "solana:event" and emits enriched data as "solana:enriched".
 * Errors are isolated per event — one bad payload cannot stall the pipeline.
 */
export function startEnrichmentProcessor() {
  eventBus.on("solana:event", async (rawEvent) => {
    try {
      const enriched = await enrichEvent(rawEvent);
      eventBus.emit("solana:enriched", enriched);
    } catch (err) {
      console.error("[Enrichment] Failed to enrich event:", err.message);
    }
  });

  console.log("[Enrichment] Processor started — listening on solana:event");
}
