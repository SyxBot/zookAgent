import { safeDiv } from "./mathUtils.js";

/**
 * Sliding time window with O(1) amortized push and eviction.
 *
 * Design: uses a head pointer instead of Array.shift() to avoid O(n) shifts
 * on every eviction. The backing buffer is compacted lazily once the dead
 * prefix grows beyond a threshold, keeping memory bounded without thrashing.
 *
 * Aggregates (volumeUsd, buys, sells, swaps, walletVolume) are updated
 * incrementally on push AND on eviction — no full recomputation ever.
 */
export class SlidingWindow {
  constructor(durationMs) {
    this.durationMs = durationMs;
    this._buf = [];
    this._head = 0; // index of the oldest live entry

    this.volumeUsd = 0;
    this.buys = 0;    // swap / large_swap events (buy-side proxy)
    this.sells = 0;   // transfer events (sell-side proxy)
    this.swaps = 0;   // swap + large_swap (for swap_intensity metric)

    // wallet → cumulative USD volume contributed within this window
    this.walletVolume = new Map();
  }

  /**
   * Push a new entry and evict anything that has aged out.
   *
   * entry shape:
   *   { timestamp: number, amountUsd: number, wallet: string|null,
   *     isBuy: boolean, isSell: boolean, isSwap: boolean }
   */
  push(entry) {
    const vol = entry.amountUsd ?? 0;

    this._buf.push(entry);
    this.volumeUsd += vol;
    if (entry.isBuy) this.buys++;
    if (entry.isSell) this.sells++;
    if (entry.isSwap) this.swaps++;
    if (entry.wallet) {
      this.walletVolume.set(
        entry.wallet,
        (this.walletVolume.get(entry.wallet) ?? 0) + vol
      );
    }

    this._evict(entry.timestamp);
  }

  _evict(now) {
    const cutoff = now - this.durationMs;

    while (
      this._head < this._buf.length &&
      this._buf[this._head].timestamp < cutoff
    ) {
      const old = this._buf[this._head++];
      const vol = old.amountUsd ?? 0;

      this.volumeUsd = Math.max(0, this.volumeUsd - vol);
      if (old.isBuy) this.buys = Math.max(0, this.buys - 1);
      if (old.isSell) this.sells = Math.max(0, this.sells - 1);
      if (old.isSwap) this.swaps = Math.max(0, this.swaps - 1);

      if (old.wallet && vol > 0) {
        const prev = this.walletVolume.get(old.wallet) ?? 0;
        const next = Math.max(0, prev - vol);
        if (next < Number.EPSILON) {
          this.walletVolume.delete(old.wallet);
        } else {
          this.walletVolume.set(old.wallet, next);
        }
      }
    }

    // Compact: once the dead prefix exceeds 500 entries, slice it away.
    // This bounds memory without paying for it on every push.
    if (this._head > 500) {
      this._buf = this._buf.slice(this._head);
      this._head = 0;
    }
  }

  /** Number of live entries currently inside the window. */
  get eventCount() {
    return this._buf.length - this._head;
  }

  /** Number of distinct wallets with volume in this window. */
  get uniqueWallets() {
    return this.walletVolume.size;
  }

  /**
   * Fraction of total window volume owned by the single largest wallet.
   * Returns 0 when volume is zero (no concentration to measure).
   */
  walletConcentration() {
    if (this.volumeUsd <= 0) return 0;
    let maxVol = 0;
    for (const vol of this.walletVolume.values()) {
      if (vol > maxVol) maxVol = vol;
    }
    return safeDiv(maxVol, this.volumeUsd, 0);
  }
}
