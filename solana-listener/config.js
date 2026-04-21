import "dotenv/config";

const config = {
  helius: {
    apiKey: process.env.HELIUS_API_KEY,
    wsUrl: `wss://atlas-mainnet.helius-rpc.com/?api-key=${process.env.HELIUS_API_KEY}`,
  },
  birdeye: {
    // Optional. When set, tokenService uses Birdeye for richer metadata (decimals).
    apiKey: process.env.BIRDEYE_API_KEY ?? null,
  },
  reconnect: {
    initialDelayMs: 1000,
    maxDelayMs: 30000,
    backoffFactor: 2,
  },
  cache: {
    priceTtlMs: 2 * 60 * 1000,  // 2 min — price / liquidity data
    tokenTtlMs: 5 * 60 * 1000,  // 5 min — symbol / decimals metadata
  },
};

if (!config.helius.apiKey) {
  throw new Error("HELIUS_API_KEY is not set in environment variables");
}

export default config;
