import WebSocket from "ws";
import config from "../config.js";
import eventBus from "../core/eventBus.js";

const { wsUrl } = config.helius;
const { initialDelayMs, maxDelayMs, backoffFactor } = config.reconnect;

let reconnectDelay = initialDelayMs;
let ws = null;

function normalizeEventType(raw) {
  const type = (raw?.type || raw?.transactionType || "").toLowerCase();

  if (type.includes("swap")) return "swap";
  if (type.includes("transfer")) return "transfer";
  if (type.includes("mint")) return "mint";
  if (type.includes("add_liquidity") || type.includes("liquidity")) return "liquidity_add";
  return "unknown";
}

function extractTokenMint(raw) {
  return (
    raw?.tokenTransfers?.[0]?.mint ??
    raw?.events?.token?.mint ??
    raw?.mint ??
    null
  );
}

function extractWallet(raw) {
  return (
    raw?.feePayer ??
    raw?.tokenTransfers?.[0]?.fromUserAccount ??
    raw?.accountData?.[0]?.account ??
    null
  );
}

function extractAmount(raw) {
  const transfers = raw?.tokenTransfers;
  if (Array.isArray(transfers) && transfers.length > 0) {
    const amt = transfers[0]?.tokenAmount;
    if (amt != null) return Number(amt);
  }

  const nativeAmt = raw?.nativeTransfers?.[0]?.amount;
  if (nativeAmt != null) return Number(nativeAmt) / 1e9; // lamports → SOL

  return null;
}

function normalizeEvent(raw) {
  return {
    timestamp: raw?.timestamp ? raw.timestamp * 1000 : Date.now(),
    signature: raw?.signature ?? null,
    event_type: normalizeEventType(raw),
    token_mint: extractTokenMint(raw),
    wallet: extractWallet(raw),
    amount: extractAmount(raw),
    raw,
  };
}

function subscribe() {
  // Helius enhanced-transactions subscription payload
  const payload = {
    jsonrpc: "2.0",
    id: 1,
    method: "transactionSubscribe",
    params: [
      { failed: false },
      {
        commitment: "confirmed",
        encoding: "jsonParsed",
        transactionDetails: "full",
        showRewards: false,
        maxSupportedTransactionVersion: 0,
      },
    ],
  };

  ws.send(JSON.stringify(payload));
}

function connect() {
  ws = new WebSocket(wsUrl);

  ws.on("open", () => {
    console.log("[Helius] WebSocket connected");
    reconnectDelay = initialDelayMs;
    subscribe();
  });

  ws.on("message", (data) => {
    let parsed;
    try {
      parsed = JSON.parse(data.toString());
    } catch {
      return;
    }

    // Subscription confirmation — not an event
    if (parsed?.result !== undefined && !parsed?.params) return;

    const rawEvent = parsed?.params?.result?.value?.transaction ?? parsed;

    try {
      const normalized = normalizeEvent(rawEvent);
      eventBus.emit("solana:event", normalized);
    } catch (err) {
      console.error("[Helius] Normalization error:", err.message);
    }
  });

  ws.on("error", (err) => {
    console.error("[Helius] WebSocket error:", err.message);
  });

  ws.on("close", (code, reason) => {
    console.warn(
      `[Helius] Connection closed (${code}). Reconnecting in ${reconnectDelay}ms...`
    );
    scheduleReconnect();
  });
}

function scheduleReconnect() {
  setTimeout(() => {
    connect();
    reconnectDelay = Math.min(reconnectDelay * backoffFactor, maxDelayMs);
  }, reconnectDelay);
}

export function startHeliusListener() {
  console.log("[Helius] Starting listener...");
  connect();
}
