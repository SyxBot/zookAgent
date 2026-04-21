import eventBus from "./core/eventBus.js";
import { startHeliusListener } from "./listeners/helius.js";
import { startEnrichmentProcessor } from "./processors/enrichment.js";

// Layer 1 — raw events (compact log)
eventBus.on("solana:event", (event) => {
  console.log(
    `[L1:Raw]    type=${event.event_type} sig=${event.signature?.slice(0, 12)}...`
  );
});

// Layer 2 — enriched events ready for downstream feature extraction
eventBus.on("solana:enriched", (event) => {
  console.log("[L2:Enriched]", JSON.stringify(event, null, 2));
});

startEnrichmentProcessor();
startHeliusListener();
