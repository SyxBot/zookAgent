import "dotenv/config";

const config = {
  helius: {
    apiKey: process.env.HELIUS_API_KEY,
    wsUrl: `wss://atlas-mainnet.helius-rpc.com/?api-key=${process.env.HELIUS_API_KEY}`,
  },
  reconnect: {
    initialDelayMs: 1000,
    maxDelayMs: 30000,
    backoffFactor: 2,
  },
};

if (!config.helius.apiKey) {
  throw new Error("HELIUS_API_KEY is not set in environment variables");
}

export default config;
