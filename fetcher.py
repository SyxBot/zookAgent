"""
fetcher.py — Pulls trending tokens from GMGN and wallet trade data via DataRouter.

Free-tier strategy (no paid APIs required):
  1. Fetch trending tokens from GMGN rank endpoint
  2. For each of the top N tokens, fetch real top_traders from GMGN
     (rate-limited, deduplicated across tokens)
  3. Check wallet cache — wallets enriched within WALLET_CACHE_TTL_HOURS
     are returned immediately at zero API cost
  4. Enrich remaining wallets via Helius (if key set) → Solana RPC → Apify (last resort)
  5. Cap total new enrichments at MAX_WALLETS_PER_RUN to protect free-tier credits

Sources (in priority order, managed by sources.DataRouter):
  1. Helius       — enhanced swap transactions (free tier ~100k credits/month)
  2. Solana RPC   — raw tx parsing, fully free, no key needed
  3. Apify        — last resort, costs credits
"""

import json
import os
from datetime import datetime

from sources import DataRouter
import config


def run(
    period: str = "6h",
    orderby: str = "smartmoney",
    limit: int = 20,
    use_apify: bool = True,
    force_source: str = "auto",
) -> str:
    """
    Main fetch pipeline. Returns path to the saved wallets JSON file.
    """
    print("=" * 55)
    print("  SOLANA SMART MONEY FETCHER")
    print("=" * 55)

    router = DataRouter()

    # ── Step 1: trending token discovery ─────────────────────────────────────
    print(f"\n[Step 1] Fetching trending tokens (period={period}, orderby={orderby}, limit={limit})...")
    tokens = router.get_trending_tokens(period=period, orderby=orderby, limit=limit)

    if not tokens:
        print("[!] GMGN returned no tokens. Aborting.")
        return _save_empty()

    mints = [t["address"] for t in tokens]
    print(f"[Step 1] Got {len(mints)} token(s): {mints[:3]}{'...' if len(mints) > 3 else ''}")

    # ── Step 2: collect real top trader wallets from GMGN ────────────────────
    print(f"\n[Step 2] Fetching top traders for up to {config.GMGN_TOP_TRADERS_TOKENS} token(s)...")
    trader_addresses = _collect_top_traders(router, mints)

    # If GMGN direct endpoint is Cloudflare-blocked (404/403), use Apify immediately
    # to scrape the same top traders data — Apify returns full wallet records directly.
    apify_seeded_records = []
    if not trader_addresses and use_apify and config.APIFY_TOKEN:
        print("[Step 2] GMGN top_traders blocked — using Apify to discover real traders...")
        apify_seeded_records = _run_apify(router, mints, use_apify)
        if apify_seeded_records:
            seen = set()
            for rec in apify_seeded_records:
                addr = rec.get("address") or rec.get("wallet_address") or ""
                if addr and addr not in seen:
                    seen.add(addr)
                    trader_addresses.append(addr)
            print(f"[Step 2] Apify returned {len(apify_seeded_records)} wallet records "
                  f"({len(trader_addresses)} unique addresses)")

    if not trader_addresses:
        print("[Step 2] No top traders from any source — falling back to creator addresses")
        trader_addresses = _extract_creator_wallets(tokens)

    print(f"[Step 2] {len(trader_addresses)} unique trader wallet(s) to process")

    # ── Step 3: cache check — skip wallets enriched recently ─────────────────
    print(f"\n[Step 3] Checking wallet cache (TTL={config.WALLET_CACHE_TTL_HOURS}h)...")
    cached_records, needs_fetch = router.get_cached_wallets(trader_addresses)
    print(f"[Step 3] Cache: {len(cached_records)} HIT, {len(needs_fetch)} MISS (need fetch)")

    if len(needs_fetch) > config.MAX_WALLETS_PER_RUN:
        print(f"[Step 3] Capping fetch list to {config.MAX_WALLETS_PER_RUN} wallets (free-tier guard)")
        needs_fetch = needs_fetch[:config.MAX_WALLETS_PER_RUN]

    # ── Step 4: enrich wallets that aren't cached ─────────────────────────────
    fresh_records = []

    # If Apify already returned full records in Step 2, use them directly —
    # no need to re-enrich via Helius for those addresses.
    if apify_seeded_records:
        fresh_records = apify_seeded_records
        for rec in fresh_records:
            addr = rec.get("address", "")
            if addr:
                router.mark_wallet_enriched(addr)
    elif needs_fetch:
        print(f"\n[Step 4] Enriching {len(needs_fetch)} wallet(s)...")

        if force_source == "apify":
            fresh_records = _run_apify(router, mints, use_apify)
        elif force_source in ("helius", "solana_rpc"):
            fresh_records = _enrich_wallets(router, needs_fetch, force_source)
        else:
            fresh_records = _enrich_wallets(router, needs_fetch, "auto")
            if not fresh_records and use_apify and config.APIFY_TOKEN:
                print("[Step 4] Free sources empty — falling back to Apify")
                fresh_records = _run_apify(router, mints, use_apify)
            elif not fresh_records:
                print("[Step 4] No data from free sources. Set APIFY_TOKEN for full coverage.")

        for rec in fresh_records:
            addr = rec.get("address", "")
            if addr:
                router.mark_wallet_enriched(addr)
    else:
        print("\n[Step 4] All wallets cached — no API calls needed \u2713")

    # ── Step 5: merge and save ────────────────────────────────────────────────
    _print_source_status(router)
    all_records = fresh_records + cached_records
    out_path = _save(all_records)
    return out_path


# ── helpers ───────────────────────────────────────────────────────────────────

def _collect_top_traders(router: DataRouter, mints: list) -> list:
    """Query GMGN top_traders for the first N mints, return deduplicated addresses."""
    seen = set()
    ordered = []
    for mint in mints[:config.GMGN_TOP_TRADERS_TOKENS]:
        for addr in router.get_top_traders(mint):
            if addr not in seen:
                seen.add(addr)
                ordered.append(addr)
    return ordered


def _extract_creator_wallets(tokens: list) -> list:
    """Fallback: creator addresses from rank token data."""
    wallets = []
    for t in tokens:
        creator = t.get("creator") or ""
        if creator and len(creator) > 10:
            wallets.append(creator)
    unique = list(dict.fromkeys(wallets))
    print(f"[fetcher] Extracted {len(unique)} creator wallet(s) from token rank data")
    return unique


def _enrich_wallets(router: DataRouter, addresses: list, source_hint: str) -> list:
    """
    Fetch trade history for a list of wallet addresses via DataRouter.

    For each address, first checks if GMGN top_traders data was already fetched
    (stored in router._top_trader_cache). If available, uses that directly and
    skips the Helius/RPC call — saving API credits and time.
    """
    results = []
    for i, addr in enumerate(addresses, 1):
        print(f"  [{i}/{len(addresses)}] {addr[:12]}... ", end="", flush=True)

        # Fast path: use GMGN top_traders PnL data already in memory
        if source_hint == "auto":
            gmgn_record = router.get_top_trader_wallet_record(addr)
            if gmgn_record:
                print(f"-> 1 record(s) [gmgn_top_traders]")
                results.append(gmgn_record)
                continue

        if source_hint == "auto":
            records = router.get_wallet_trades(addr)
        elif source_hint == "helius":
            records = router.helius.get_wallet_trades(addr) or []
        elif source_hint == "solana_rpc":
            records = router.solana_rpc.get_wallet_trades(addr) or []
        else:
            records = []

        if records:
            src = records[0].get("source", "?")
            print(f"-> {len(records)} record(s) [{src}]")
            results.extend(records)
        else:
            print("-> no data")

    return results


def _run_apify(router: DataRouter, mints: list, use_apify: bool) -> list:
    if not use_apify:
        print("[fetcher] Apify disabled via --no-apify")
        return []
    return router.run_apify_for_mints(mints)


def _save(results: list) -> str:
    os.makedirs("exports", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = os.path.join("exports", f"wallets_{ts}.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n[Done] Saved {len(results)} wallet record(s) to {out_path}")
    return out_path


def _save_empty() -> str:
    os.makedirs("exports", exist_ok=True)
    out_path = os.path.join("exports", f"wallets_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    with open(out_path, "w") as f:
        json.dump([], f)
    return out_path


def _print_source_status(router: DataRouter):
    status = router.source_status()
    parts = []
    for name, s in status.items():
        avail = "OK" if s["available"] else f"COOL({s['cooldown_remaining_s']}s)"
        parts.append(f"{name}={avail}")
    print(f"[sources] {' | '.join(parts)}")


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Fetch Solana smart money wallet data")
    parser.add_argument("--period", default="6h",
                        choices=["1m", "5m", "1h", "6h", "24h"])
    parser.add_argument("--orderby", default="smartmoney",
                        choices=["smartmoney", "volume", "holder_count", "marketcap", "swaps", "liquidity"])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--no-apify", action="store_true")
    parser.add_argument("--source", default="auto",
                        choices=["auto", "helius", "solana_rpc", "apify"])
    args = parser.parse_args()

    run(
        period=args.period,
        orderby=args.orderby,
        limit=args.limit,
        use_apify=not args.no_apify,
        force_source=args.source,
    )
