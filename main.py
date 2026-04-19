"""
main.py — Orchestrator: fetch → filter → export
Run this to execute the full pipeline.
"""

import os
import sys
import argparse
from dotenv import load_dotenv

load_dotenv()


def run_pipeline(period="6h", orderby="smartmoney", limit=20, use_apify=True, skip_fetch=False, wallet_file=None):
    print("\n" + "=" * 60)
    print("  SOLANA SMART MONEY TRACKER — PIPELINE")
    print("=" * 60)

    # Step 1: Fetch
    if skip_fetch and wallet_file:
        print(f"\n[Step 1] Skipping fetch — using existing file: {wallet_file}")
        out_path = wallet_file
    else:
        from fetcher import run as fetch
        print("\n[Step 1] Fetching wallet data...")
        out_path = fetch(period=period, orderby=orderby, limit=limit, use_apify=use_apify)

    # Step 2: Score + store
    from smart_money_filter import process_file, export_to_json
    print(f"\n[Step 2] Scoring wallets from {out_path}...")
    counts = process_file(out_path)

    # Step 3: Export for dashboard
    print("\n[Step 3] Exporting to JSON for dashboard...")
    export_to_json()

    print("\n[Pipeline complete]")
    print("  Open dashboard/index.html in your browser to view results.")
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Solana Smart Money Tracker — full pipeline")
    parser.add_argument("--period", default="6h", choices=["1m", "5m", "1h", "6h", "24h"],
                        help="Token trending period (default: 6h)")
    parser.add_argument("--orderby", default="smartmoney",
                        choices=["smartmoney", "volume", "holder_count", "marketcap", "swaps", "liquidity"],
                        help="Sort order for trending tokens (default: smartmoney)")
    parser.add_argument("--limit", type=int, default=20,
                        help="Number of trending tokens to fetch (default: 20)")
    parser.add_argument("--no-apify", action="store_true",
                        help="Fetch directly from GMGN instead of using Apify (may hit rate limits)")
    parser.add_argument("--file", type=str, default=None,
                        help="Skip fetch and process an existing wallet JSON file")
    args = parser.parse_args()

    run_pipeline(
        period=args.period,
        orderby=args.orderby,
        limit=args.limit,
        use_apify=not args.no_apify,
        skip_fetch=bool(args.file),
        wallet_file=args.file,
    )
