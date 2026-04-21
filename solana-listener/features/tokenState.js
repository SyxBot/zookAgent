import { SlidingWindow } from "./windowManager.js";
import { safeDiv, clamp, normalize } from "./mathUtils.js";

const WIN_1M = 60_000;
const WIN_2M = 120_000; // used to derive previous-1m volume without a separate snapshot
const WIN_5M = 300_000;

/**
 * Classify an enriched event_type into buy/sell/swap flags.
 *
 * Layer 2 does not carry swap directionality (which token is input/output),
 * so we use event_type as the best available proxy:
 *   swap / large_swap → buy-side activity on the token
 *   transfer          → sell-side proxy (moving tokens off DEX)
 *   all others        → neutral (excluded from buy/sell ratio computation)
 */
function classify(eventType) {
  switch (eventType) {
    case "swap":
    case "large_swap":
      return { isBuy: true, isSell: false, isSwap: true };
    case "transfer":
      return { isBuy: false, isSell: true, isSwap: false };
    default:
      return { isBuy: false, isSell: false, isSwap: false };
  }
}

export class TokenState {
  constructor(mint) {
    this.mint = mint;

    // Rolling windows — 2m exists solely to give us "previous 1m" without snapshots.
    // prev_1m_volume = win2m.volumeUsd - win1m.volumeUsd  (always current, zero lag)
    this.win1m = new SlidingWindow(WIN_1M);
    this.win2m = new SlidingWindow(WIN_2M);
    this.win5m = new SlidingWindow(WIN_5M);

    // Liquidity is a point-in-time value (not cumulative), so it has its own
    // time-series buffer rather than being summed inside a SlidingWindow.
    this._liqBuf = []; // [{ timestamp, value }]
    this._liqHead = 0;

    this.ageSec = null;
    this.isNewToken = false;
    this.lastActivityAt = Date.now(); // used by featureEngine for stale-state eviction
  }

  update(event) {
    const { timestamp, event_type, wallet, metrics, metadata } = event;
    const now = timestamp ?? Date.now();

    this.lastActivityAt = now;
    if (metadata?.token_age_sec != null) this.ageSec = metadata.token_age_sec;
    if (metadata?.is_new_token != null) this.isNewToken = metadata.is_new_token;

    // Liquidity point-in-time reading
    if (metrics?.liquidity_usd != null) {
      this._liqBuf.push({ timestamp: now, value: metrics.liquidity_usd });
      this._evictLiquidity(now);
    }

    const { isBuy, isSell, isSwap } = classify(event_type);
    const entry = {
      timestamp: now,
      amountUsd: metrics?.amount_usd ?? 0,
      wallet: wallet ?? null,
      isBuy,
      isSell,
      isSwap,
    };

    this.win1m.push(entry);
    this.win2m.push(entry);
    this.win5m.push(entry);
  }

  _evictLiquidity(now) {
    const cutoff = now - WIN_5M;
    while (
      this._liqHead < this._liqBuf.length &&
      this._liqBuf[this._liqHead].timestamp < cutoff
    ) {
      this._liqHead++;
    }
    // Compact once the dead prefix grows large
    if (this._liqHead > 200) {
      this._liqBuf = this._liqBuf.slice(this._liqHead);
      this._liqHead = 0;
    }
  }

  // O(1) — tail is always the most recent push, guaranteed within window after eviction
  _latestLiquidity() {
    if (this._liqBuf.length <= this._liqHead) return null;
    return this._liqBuf[this._liqBuf.length - 1].value;
  }

  // O(1) — compare oldest live entry against newest
  _liquidityChange5m() {
    const liveCount = this._liqBuf.length - this._liqHead;
    if (liveCount < 2) return 0;
    const oldest = this._liqBuf[this._liqHead].value;
    const latest = this._liqBuf[this._liqBuf.length - 1].value;
    return safeDiv(latest - oldest, Math.max(oldest, 1), 0);
  }

  /**
   * Compute the full feature vector from current window state.
   * All values are derived from already-maintained incremental aggregates,
   * so this runs in O(W) where W = unique wallets in window (for concentration).
   */
  computeFeatures() {
    const vol1m = this.win1m.volumeUsd;
    const vol5m = this.win5m.volumeUsd;

    // Previous 1m volume = everything in [now-120s, now-60s].
    // Derived from win2m − win1m: no snapshot or separate window needed.
    const prevVol1m = Math.max(0, this.win2m.volumeUsd - vol1m);
    const volAcceleration = safeDiv(vol1m - prevVol1m, Math.max(prevVol1m, 1), 0);

    // Buy pressure: ratio of swap events to transfer events over 5m window.
    // Formula: buys / (sells + 1)  — the +1 prevents division-by-zero and
    // gives a meaningful ratio even when there are zero sells.
    const buys = this.win5m.buys;
    const sells = this.win5m.sells;
    const buyPressure = safeDiv(buys, sells + 1, buys > 0 ? buys : 0);

    // swap_intensity: average swap events per minute over the 5m window
    const swapIntensity = this.win5m.swaps / 5;

    const walletConc5m = this.win5m.walletConcentration();
    const walletDiversity = 1 - walletConc5m; // higher = more distributed activity

    // early_activity_score ∈ [0, 1]:
    // Peaks when a new token (<5 min old) shows rapid event rate and rising volume.
    // Decays to 0 once the token leaves its first 5-minute window.
    let earlyActivityScore = 0;
    if (this.isNewToken && this.ageSec != null) {
      const ageDecay = clamp(1 - this.ageSec / 300, 0, 1);     // 1→0 over first 5 min
      const eventRate = normalize(this.win1m.eventCount, 20);   // saturates at 20 ev/min
      const volSignal = normalize(vol1m, 10_000);               // saturates at $10k/min
      earlyActivityScore = ageDecay * 0.5 + eventRate * 0.3 + volSignal * 0.2;
    }

    // momentum_score ∈ [0, 1] — internal composite, NOT a trading signal.
    // Components are individually normalized before weighting so that a token
    // with extreme volume acceleration doesn't drown out buy/wallet signals.
    // Layer 4 (Signal Engine) is expected to consume this as one feature among others.
    const normVolAccel = normalize(clamp(volAcceleration, 0, 5), 5);
    const normBuyPressure = normalize(clamp(buyPressure, 0, 20), 20);
    const momentumScore =
      normVolAccel * 0.4 + normBuyPressure * 0.3 + walletDiversity * 0.3;

    return {
      // ── Time ──────────────────────────────────────────────────────────────
      age_sec: this.ageSec,
      events_1m: this.win1m.eventCount,
      events_5m: this.win5m.eventCount,

      // ── Volume ────────────────────────────────────────────────────────────
      volume_1m_usd: vol1m,
      volume_5m_usd: vol5m,
      volume_acceleration: volAcceleration,

      // ── Liquidity ─────────────────────────────────────────────────────────
      liquidity_usd: this._latestLiquidity(),
      liquidity_change_5m: this._liquidityChange5m(),

      // ── Wallet ────────────────────────────────────────────────────────────
      unique_wallets_1m: this.win1m.uniqueWallets,
      unique_wallets_5m: this.win5m.uniqueWallets,
      wallet_concentration: walletConc5m,

      // ── Momentum ──────────────────────────────────────────────────────────
      buy_pressure: buyPressure,
      swap_intensity: swapIntensity,

      // ── Early Signal ──────────────────────────────────────────────────────
      is_new_token: this.isNewToken,
      early_activity_score: earlyActivityScore,

      // ── Internal composite (for Layer 4 consumption only) ─────────────────
      momentum_score: momentumScore,
    };
  }
}
