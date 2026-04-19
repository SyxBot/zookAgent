"""
smart_money_filter.py — Scores wallets and writes to SQLite registry
"""

import sqlite3
import json
import sys
import os
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "smart_money.db")

THRESHOLDS = {
    "min_pnl_diamond": 10_000,       # USD — adjust after seeing real data distribution
    "min_pnl_long_hold": 2_000,
    "min_hold_hrs": 12,              # hours — calibrate empirically
    "rug_max_mins": 30,              # minutes — flag if avg hold below this
    "win_rate_bonus_threshold": 0.55,
    "quick_flip_penalty_threshold": 5,
    # Bot detection thresholds (GMGN flags "Buy/Sell within 10 secs" as bot signal)
    "bot_sub10s_flips_threshold": 3,     # ≥3 sub-10-second round trips → likely bot
    "bot_high_frequency_threshold": 50,  # ≥50 trades in history → likely automated
}


def init_db(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            address         TEXT PRIMARY KEY,
            label           TEXT,
            score           INTEGER,
            total_pnl       REAL,
            win_rate        REAL,
            avg_hold_hrs    REAL,
            coin_count      INTEGER,
            quick_flips     INTEGER,
            last_seen       TEXT,
            first_seen      TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wallet_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet_address  TEXT,
            token_mint      TEXT,
            pnl             REAL,
            hold_minutes    REAL,
            seen_at         TEXT,
            FOREIGN KEY(wallet_address) REFERENCES wallets(address)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS apify_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at       TEXT,
            token_count  INTEGER,
            record_count INTEGER,
            run_id       TEXT
        )
    """)
    conn.commit()


def score_wallet(wallet: dict, history: list) -> tuple:
    """
    Score a wallet and return (score: int, label: str).

    Inputs:
      wallet   — dict with keys: total_pnl_usd, avg_hold_minutes, quick_flip_count,
                 and optionally pnl_available (False for solana_rpc wallets where PnL
                 is structurally 0 — skips the PnL and win_rate scoring branches)
      history  — list of dicts with keys: pnl, hold_minutes, token_mint
    """
    score = 0
    pnl = wallet.get("total_pnl_usd", 0) or 0
    pnl_available = wallet.get("pnl_available", True)  # False for solana_rpc
    hold_hrs = (wallet.get("avg_hold_minutes", 0) or 0) / 60
    wins = sum(1 for h in history if (h.get("pnl") or 0) > 0)
    win_rate = wins / len(history) if history else 0
    quick_flips = wallet.get("quick_flip_count", 0) or 0

    # Bot signals — checked before scoring so we can apply a hard floor
    sub10s_flips = sum(1 for h in history if 0 < (h.get("hold_minutes") or 0) < (10 / 60))
    is_likely_bot = (
        sub10s_flips >= THRESHOLDS["bot_sub10s_flips_threshold"]
        or len(history) >= THRESHOLDS["bot_high_frequency_threshold"]
    )

    # --- PnL score (skip entirely if source can't provide PnL) ---
    if pnl_available:
        if pnl > 50_000:
            score += 30
        elif pnl > THRESHOLDS["min_pnl_diamond"]:
            score += 15
        elif pnl > THRESHOLDS["min_pnl_long_hold"]:
            score += 7
        elif pnl < 0:
            score -= 10

    # --- Hold duration ---
    rug_hrs = THRESHOLDS["rug_max_mins"] / 60
    if hold_hrs > 48:
        score += 25
    elif hold_hrs > THRESHOLDS["min_hold_hrs"]:
        score += 12
    elif hold_hrs > 3:
        score += 5
    elif 0 < hold_hrs < rug_hrs:
        score -= 25  # rug chaser penalty

    # --- Win rate (only meaningful when we have real PnL data) ---
    if pnl_available:
        if win_rate >= THRESHOLDS["win_rate_bonus_threshold"]:
            score += int(win_rate * 30)
        elif win_rate < 0.25 and len(history) > 3:
            score -= 10

    # --- Multi-coin consistency bonus (capped at 15 pts) ---
    score += min(len(history) * 2, 15)

    # --- Bot / farm penalty ---
    if quick_flips > THRESHOLDS["quick_flip_penalty_threshold"]:
        score -= 20
    # Additional bot penalties based on GMGN's own bot detection signals:
    # sub-10-second buy/sell round trips and high-frequency trading patterns
    if sub10s_flips >= THRESHOLDS["bot_sub10s_flips_threshold"]:
        score -= 25  # Strong bot signal — GMGN flags this explicitly
    if is_likely_bot:
        score = min(score, 9)  # Cap at Rug_Chaser regardless of PnL/hold signals

    # --- Label ---
    if score >= 70:
        label = "Diamond_Profitable"
    elif score >= 45:
        label = "Long_Hold_Green"
    elif score >= 10:
        label = "Short_Flipper"
    else:
        label = "Rug_Chaser"

    return score, label


def normalize_wallet(raw: dict) -> tuple[dict, list]:
    """
    Normalize raw Apify / GMGN wallet data into (wallet_dict, history_list).
    Handles field name variations from different sources.
    """
    # Common field aliases
    address = (
        raw.get("address") or
        raw.get("wallet_address") or
        raw.get("walletAddress") or
        ""
    )

    total_pnl = (
        raw.get("total_pnl_usd") or
        raw.get("totalPnlUsd") or
        raw.get("pnl") or
        raw.get("realized_pnl") or
        0
    )

    avg_hold_minutes = (
        raw.get("avg_hold_minutes") or
        raw.get("avgHoldMinutes") or
        raw.get("avg_duration_minutes") or
        (raw.get("avg_hold_hours", 0) * 60) or
        0
    )

    quick_flip_count = (
        raw.get("quick_flip_count") or
        raw.get("quickFlipCount") or
        raw.get("fast_txn_count") or
        0
    )

    wallet = {
        "address": address,
        "total_pnl_usd": float(total_pnl),
        "avg_hold_minutes": float(avg_hold_minutes),
        "quick_flip_count": int(quick_flip_count),
        # Preserve pnl_available from source (False for solana_rpc — means PnL is
        # structurally 0, not a real signal, so scorer skips PnL/win-rate branches)
        "pnl_available": raw.get("pnl_available", True),
    }

    # Extract trade history from various formats
    history = raw.get("history") or raw.get("trades") or raw.get("token_history") or []

    # Flat-record fallback: only use this if history is genuinely absent AND there's
    # a token_mint field indicating a single-trade record. Don't overwrite a real history.
    if not history and raw.get("token_mint"):
        history = [{
            "token_mint": raw.get("token_mint"),
            "pnl": float(total_pnl),
            "hold_minutes": float(avg_hold_minutes),
        }]

    normalized_history = []
    for h in history:
        normalized_history.append({
            "token_mint": h.get("token_mint") or h.get("tokenMint") or h.get("mint") or "",
            "pnl": float(h.get("pnl") or h.get("realized_pnl") or 0),
            "hold_minutes": float(h.get("hold_minutes") or h.get("holdMinutes") or h.get("duration_minutes") or 0),
        })

    return wallet, normalized_history


def upsert_wallet(conn, wallet: dict, history: list) -> tuple[int, str]:
    score, label = score_wallet(wallet, history)
    address = wallet["address"]
    now = datetime.now().isoformat()

    existing = conn.execute(
        "SELECT first_seen FROM wallets WHERE address = ?", (address,)
    ).fetchone()
    first_seen = existing[0] if existing else now

    win_rate = (
        sum(1 for h in history if (h.get("pnl") or 0) > 0) / max(len(history), 1)
    )

    conn.execute("""
        INSERT OR REPLACE INTO wallets
        (address, label, score, total_pnl, win_rate, avg_hold_hrs, coin_count, quick_flips, last_seen, first_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        address,
        label,
        score,
        wallet.get("total_pnl_usd"),
        win_rate,
        wallet.get("avg_hold_minutes", 0) / 60,
        len(history),
        wallet.get("quick_flip_count", 0),
        now,
        first_seen,
    ))

    for h in history:
        conn.execute("""
            INSERT INTO wallet_history (wallet_address, token_mint, pnl, hold_minutes, seen_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            address,
            h.get("token_mint"),
            h.get("pnl"),
            h.get("hold_minutes"),
            now,
        ))

    conn.commit()
    return score, label


def process_file(path: str, db_path: str = None) -> dict:
    """Process a JSON wallet file and write results to SQLite. Returns label counts."""
    db = db_path or DB_PATH
    conn = sqlite3.connect(db)
    init_db(conn)

    with open(path) as f:
        raw_wallets = json.load(f)

    if not isinstance(raw_wallets, list):
        raw_wallets = [raw_wallets]

    counts = {
        "Diamond_Profitable": 0,
        "Long_Hold_Green": 0,
        "Short_Flipper": 0,
        "Rug_Chaser": 0,
    }

    print(f"\nProcessing {len(raw_wallets)} wallet records from {path}")
    print("-" * 60)

    for raw in raw_wallets:
        wallet, history = normalize_wallet(raw)
        if not wallet["address"]:
            continue
        score, label = upsert_wallet(conn, wallet, history)
        counts[label] = counts.get(label, 0) + 1
        print(f"  {wallet['address'][:12]}  ->  {label:<22}  score={score:>3}  pnl=${wallet['total_pnl_usd']:>10,.0f}")

    conn.close()

    print(f"\n{'='*60}")
    print(f"Done. {sum(counts.values())} wallets -> {db}")
    for label, count in counts.items():
        bar = "#" * count
        print(f"  {label:<25} {count:>4}  {bar}")

    return counts


def export_to_json(db_path: str = None, out_path: str = "exports/wallets_latest.json"):
    """Export all wallets from SQLite to JSON for the dashboard."""
    db = db_path or DB_PATH
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT * FROM wallets ORDER BY score DESC").fetchall()
    cols = [
        "address", "label", "score", "total_pnl", "win_rate",
        "avg_hold_hrs", "coin_count", "quick_flips", "last_seen", "first_seen"
    ]
    data = [dict(zip(cols, r)) for r in rows]
    conn.close()

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Exported {len(data)} wallets to {out_path}")
    return data


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python smart_money_filter.py wallets.json [--export]")
        sys.exit(1)

    counts = process_file(sys.argv[1])

    if "--export" in sys.argv:
        export_to_json()
