import eventBus from "./core/eventBus.js";
import { startHeliusListener } from "./listeners/helius.js";

eventBus.on("solana:event", (event) => {
  console.log("[Event]", JSON.stringify(event, null, 2));
});

startHeliusListener();
