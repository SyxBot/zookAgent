import axios from "axios";
import config from "../config.js";
import { get, set } from "./cache.js";

const DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex/tokens";
export const SOL_MINT = "So11111111111111111111111111111111111111112";

// Deduplicate concurrent in-flight requests for the same mint.
// Without this, a burst of events for the same token would fire N identical HTTP requests
// before any of them populate the cache.
const inFlight = new Map();

async function fetchDexPairData(mint) {
  const cacheKey = `dex:${mint}`;
  const cached = get(cacheKey);
  if (cached !== undefined) return cached;

  if (inFlight.has(mint)) return inFlight.get(mint);

  const promise = (async () => {
    try {
      const { data } = await axios.get(`${DEXSCREENER_BASE}/${mint}`, {
        timeout: 5000,
      });

      const pairs = data?.pairs;
      if (!pairs || pairs.length === 0) {
        set(cacheKey, null, config.cache.priceTtlMs);
        return null;
      }

      // Pick the highest-liquidity USD-priced pair for the most reliable quote.
      const best =
        pairs
          .filter((p) => p.priceUsd && p.liquidity?.usd)
          .sort((a, b) => b.liquidity.usd - a.liquidity.usd)[0] ?? pairs[0];

      const isBase =
        best.baseToken.address.toLowerCase() === mint.toLowerCase();
      const tokenInfo = isBase ? best.baseToken : best.quoteToken;

      const result = {
        priceUsd: best.priceUsd ? parseFloat(best.priceUsd) : null,
        liquidityUsd: best.liquidity?.usd ?? null,
        // pairCreatedAt is Unix ms from Dexscreener
        pairCreatedAt: best.pairCreatedAt ?? null,
        dexId: best.dexId ?? null,
        pairAddress: best.pairAddress ?? null,
        symbol: tokenInfo.symbol ?? null,
        name: tokenInfo.name ?? null,
      };

      set(cacheKey, result, config.cache.priceTtlMs);
      return result;
    } catch (err) {
      console.error(`[PriceService] DexScreener error for ${mint}:`, err.message);
      set(cacheKey, null, config.cache.priceTtlMs);
      return null;
    } finally {
      inFlight.delete(mint);
    }
  })();

  inFlight.set(mint, promise);
  return promise;
}

/** Full pair data: price, liquidity, pairCreatedAt, symbol, name. */
export async function getDexPairData(mint) {
  if (!mint) return null;
  return fetchDexPairData(mint);
}

/** Current SOL/USD price. */
export async function getSolPrice() {
  const data = await fetchDexPairData(SOL_MINT);
  return data?.priceUsd ?? null;
}

/** Current USD price for any SPL token (or SOL). */
export async function getTokenPrice(mint) {
  if (!mint) return null;
  if (mint === SOL_MINT) return getSolPrice();
  const data = await fetchDexPairData(mint);
  return data?.priceUsd ?? null;
}
