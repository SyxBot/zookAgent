import eventBus from "./core/eventBus.js";
import { startHeliusListener } from "./listeners/helius.js";
import { startEnrichmentProcessor } from "./processors/enrichment.js";
import { startFeatureEngine } from "./features/featureEngine.js";

// Layer 1 — raw ingest (one line per event)
eventBus.on("solana:event", (event) => {
  console.log(
    `[L1:Raw]      type=${event.event_type} sig=${event.signature?.slice(0, 12)}...`
  );
});

// Layer 2 — enriched (compact summary)
eventBus.on("solana:enriched", (event) => {
  const m = event.metrics;
  console.log(
    `[L2:Enriched] mint=${event.token?.mint?.slice(0, 8)}... ` +
    `sym=${event.token?.symbol ?? "?"} ` +
    `price=$${m?.price_usd?.toFixed(4) ?? "?"} ` +
    `liq=$${m?.liquidity_usd?.toFixed(0) ?? "?"}`
  );
});

// Layer 3 — feature vectors (full JSON for inspection)
eventBus.on("solana:features", (fv) => {
  console.log("[L3:Features]", JSON.stringify(fv, null, 2));
});

// Boot order: feature engine before enrichment before listener
// so every event is guaranteed to have a subscriber ready before it arrives.
startFeatureEngine();
startEnrichmentProcessor();
startHeliusListener();
