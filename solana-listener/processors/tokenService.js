import axios from "axios";
import config from "../config.js";
import { get, set } from "./cache.js";
import { getDexPairData } from "./priceService.js";

const BIRDEYE_BASE = "https://public-api.birdeye.so";

// Birdeye provides decimals + richer metadata; DexScreener is the no-key fallback.
async function fetchBirdeyeMetadata(mint) {
  try {
    const { data } = await axios.get(
      `${BIRDEYE_BASE}/defi/token_overview?address=${mint}`,
      {
        headers: { "X-API-KEY": config.birdeye.apiKey },
        timeout: 5000,
      }
    );
    const token = data?.data;
    if (!token) return null;
    return {
      symbol: token.symbol ?? null,
      decimals: token.decimals ?? null,
    };
  } catch (err) {
    console.error(`[TokenService] Birdeye error for ${mint}:`, err.message);
    return null;
  }
}

/**
 * Returns { symbol, decimals } for the given mint.
 * Prefers Birdeye (has decimals) when BIRDEYE_API_KEY is configured.
 * Falls back to DexScreener symbol — decimals will be null in that case.
 * Results are cached for config.cache.tokenTtlMs (5 min default).
 */
export async function getTokenMetadata(mint) {
  if (!mint) return { symbol: null, decimals: null };

  const cacheKey = `token:meta:${mint}`;
  const cached = get(cacheKey);
  if (cached !== undefined) return cached;

  let metadata = null;

  if (config.birdeye.apiKey) {
    metadata = await fetchBirdeyeMetadata(mint);
  }

  if (!metadata) {
    // DexScreener pair data is already cached by priceService; no extra HTTP call.
    const pairData = await getDexPairData(mint);
    metadata = {
      symbol: pairData?.symbol ?? null,
      decimals: null,
    };
  }

  set(cacheKey, metadata, config.cache.tokenTtlMs);
  return metadata;
}
