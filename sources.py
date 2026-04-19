"""
sources.py — Multi-source wallet data fetcher with rotation, rate limiting, and fallback.

Priority order for wallet trade data:
  1. GmgnCli      — official GMGN Agent API via npx gmgn-cli (requires GMGN_API_KEY)
                    Provides top traders + wallet stats with real PnL, win rate, hold time.
                    Bypasses Cloudflare — replaces both the broken direct endpoint and Apify.
  2. Helius       — enhanced swap transactions (requires HELIUS_API_KEY, generous free tier)
  3. Solana RPC   — raw tx parsing, free, no key, hold times accurate, PnL=0
  4. Apify        — last resort, costs credits (kept as emergency fallback)

Normalized output — every source returns this exact shape for smart_money_filter.py:
{
    "address": str,
    "total_pnl_usd": float,
    "avg_hold_minutes": float,
    "quick_flip_count": int,
    "history": [{"token_mint": str, "pnl": float, "hold_minutes": float}],
    "source": str,   # "gmgn_cli" | "helius" | "solana_rpc" | "apify"
}
"""

import json
import os
import sqlite3
import subprocess
import time
import random
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime

import config


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

_sol_price_cache: dict = {}   # {"price": float, "fetched_at": float}


def get_sol_price_usd() -> float:
    """Fetch current SOL/USD price from CoinGecko. Cached for the process lifetime."""
    now = time.time()
    if _sol_price_cache.get("fetched_at", 0) > now - 300:   # 5-min cache
        return _sol_price_cache["price"]
    try:
        req = urllib.request.Request(
            config.COINGECKO_SOL_PRICE_URL,
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
            price = float(data["solana"]["usd"])
            _sol_price_cache.update({"price": price, "fetched_at": now})
            return price
    except Exception as e:
        print(f"[PriceOracle] CoinGecko unavailable ({e}), using fallback ${config.SOL_PRICE_FALLBACK_USD}")
        return config.SOL_PRICE_FALLBACK_USD


def _get(url: str, headers: dict = None, timeout: int = 12) -> bytes:
    """Raw GET returning bytes. Raises urllib.error.HTTPError on non-2xx."""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _rpc_call(method: str, params: list, timeout: int = 15) -> dict:
    """Single Solana JSON-RPC call."""
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        config.SOLANA_RPC_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE STATE & BASE CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class SourceState:
    def __init__(self, name: str, delay: float, cooldown_minutes: float, max_errors: int):
        self.name = name
        self.delay = delay
        self.cooldown_seconds = cooldown_minutes * 60
        self.max_errors = max_errors
        self.last_used: float = 0.0
        self.consecutive_errors: int = 0
        self.cooldown_until: float = 0.0

    def is_available(self) -> bool:
        return time.time() >= self.cooldown_until

    def seconds_until_available(self) -> float:
        return max(0.0, self.cooldown_until - time.time())

    def mark_error(self, hard: bool = False):
        """hard=True triggers immediate cooldown (e.g. 429). False increments counter."""
        self.consecutive_errors += 1
        if hard or self.consecutive_errors >= self.max_errors:
            self.cooldown_until = time.time() + self.cooldown_seconds
            print(
                f"[{self.name}] Cooling down for "
                f"{self.cooldown_seconds/60:.1f}min "
                f"(errors={self.consecutive_errors})"
            )

    def mark_success(self):
        self.consecutive_errors = 0

    def wait_for_rate_limit(self, jitter: float = 0.0):
        elapsed = time.time() - self.last_used
        wait = self.delay + random.uniform(0, jitter) - elapsed
        if wait > 0:
            time.sleep(wait)
        self.last_used = time.time()


class BaseSource:
    name: str
    state: SourceState

    def get_wallet_trades(self, address: str) -> list | None:
        """
        Returns list of normalized wallet records, or None on failure/unavailable.
        An empty list [] means the address had no swap activity (soft miss).
        None means the source failed or is unavailable.
        """
        raise NotImplementedError


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 1: HELIUS
# ═══════════════════════════════════════════════════════════════════════════════

class HeliusSource(BaseSource):
    """
    Fetch enhanced swap transactions for a wallet.

    Endpoint: GET https://api.helius.xyz/v0/addresses/{addr}/transactions
              ?api-key={key}&limit=100&type=SWAP

    Each transaction has:
      - timestamp  (unix epoch)
      - tokenTransfers[].{fromUserAccount, toUserAccount, mint, tokenAmount}
      - nativeTransfers[].{fromUserAccount, toUserAccount, amount}  (lamports)

    Logic:
      - tokenTransfer toUserAccount==wallet  → wallet received that token (buy)
      - tokenTransfer fromUserAccount==wallet → wallet sent that token (sell)
      - nativeTransfer fromUserAccount==wallet in same tx → SOL paid for buy
      - nativeTransfer toUserAccount==wallet in same tx  → SOL received from sell
      PnL = (sol_received_lamports - sol_spent_lamports) / 1e9 * sol_price_usd
    """

    name = "helius"

    def __init__(self):
        self.state = SourceState(
            "helius",
            config.HELIUS_DELAY_SECONDS,
            config.HELIUS_COOLDOWN_MINUTES,
            config.HELIUS_MAX_CONSECUTIVE_ERRORS,
        )

    def _enabled(self) -> bool:
        if not config.HELIUS_API_KEY:
            return False
        return True

    def get_wallet_trades(self, address: str) -> list | None:
        if not self._enabled():
            return None
        if not self.state.is_available():
            return None

        self.state.wait_for_rate_limit()

        # Primary domain is api-mainnet.helius-rpc.com (as of 2026); api.helius.xyz
        # still routes there but is the legacy path. No type filter — "SWAP" misses
        # many DEX swaps (e.g. Jupiter V6 routes classified as UNKNOWN by Helius).
        url = (
            f"https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions"
            f"?api-key={config.HELIUS_API_KEY}"
            f"&limit={config.HELIUS_MAX_TXS}"
        )
        try:
            raw = _get(url, headers={"Accept": "application/json"})
            txs = json.loads(raw)
            if not isinstance(txs, list):
                self.state.mark_error()
                return None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.state.mark_error(hard=True)
            elif e.code == 401:
                print(f"[helius] 401 Unauthorized — check HELIUS_API_KEY")
                self.state.mark_error(hard=True)
            else:
                self.state.mark_error()
            return None
        except Exception as e:
            print(f"[helius] Error fetching {address[:8]}...: {e}")
            self.state.mark_error()
            return None

        self.state.mark_success()
        record = self._parse_transactions(address, txs)
        return [record] if record else []

    def _parse_transactions(self, address: str, txs: list) -> dict | None:
        """
        Group swap transactions by token mint and compute per-position stats.
        Returns a single normalized wallet record.
        """
        if not txs:
            return None

        sol_price = get_sol_price_usd()
        SOL_MINT = "So11111111111111111111111111111111111111112"

        # Per-mint tracking: {mint: {"buys": [...], "sells": [...], "sol_spent": int, "sol_received": int}}
        positions: dict = defaultdict(lambda: {
            "buys": [],    # timestamps
            "sells": [],   # timestamps
            "sol_spent_lamports": 0,
            "sol_received_lamports": 0,
        })

        for tx in txs:
            ts = tx.get("timestamp", 0)
            transfers = tx.get("tokenTransfers", [])
            native = tx.get("nativeTransfers", [])

            # Tokens received by wallet (not SOL-wrapped, not NFTs).
            # NFTs have tokenAmount==1 with decimals==0 — skip them to avoid
            # treating airdrops and NFT purchases as meme coin buys.
            received_mints = list({
                t["mint"] for t in transfers
                if t.get("toUserAccount") == address
                and t.get("mint") != SOL_MINT
                and float(t.get("tokenAmount") or 0) != 1.0
            })
            # Tokens sent by wallet (same NFT filter)
            sent_mints = list({
                t["mint"] for t in transfers
                if t.get("fromUserAccount") == address
                and t.get("mint") != SOL_MINT
                and float(t.get("tokenAmount") or 0) != 1.0
            })

            # SOL net flow for this tx (positive = wallet gained SOL, negative = wallet spent SOL)
            sol_out = sum(
                n.get("amount", 0) for n in native
                if n.get("fromUserAccount") == address
            )
            sol_in = sum(
                n.get("amount", 0) for n in native
                if n.get("toUserAccount") == address
            )
            net_sol = sol_in - sol_out  # positive = sell proceeds, negative = buy cost

            # Classify the tx:
            # - Only received tokens (buy): wallet spent SOL → charge to received mint(s)
            # - Only sent tokens (sell): wallet received SOL → credit to sent mint(s)
            # - Both (token-to-token swap): no direct SOL flow to attribute to PnL
            # Divide cost/proceeds equally among mints if multiple (rare edge case)
            is_buy  = bool(received_mints) and not sent_mints
            is_sell = bool(sent_mints) and not received_mints

            if is_buy and received_mints:
                cost_per_mint = abs(min(net_sol, 0)) // len(received_mints)
                for mint in received_mints:
                    positions[mint]["buys"].append(ts)
                    positions[mint]["sol_spent_lamports"] += cost_per_mint

            elif is_sell and sent_mints:
                proceeds_per_mint = max(net_sol, 0) // len(sent_mints)
                for mint in sent_mints:
                    positions[mint]["sells"].append(ts)
                    positions[mint]["sol_received_lamports"] += proceeds_per_mint

            else:
                # Token-to-token swap or ambiguous — record timing only, no SOL flow
                for mint in received_mints:
                    positions[mint]["buys"].append(ts)
                for mint in sent_mints:
                    positions[mint]["sells"].append(ts)

        if not positions:
            return None

        history = []
        total_pnl_lamports = 0

        for mint, pos in positions.items():
            all_ts = sorted(pos["buys"] + pos["sells"])
            if not all_ts:
                continue

            first_buy = min(pos["buys"]) if pos["buys"] else (all_ts[0] if all_ts else 0)
            last_activity = max(all_ts)
            hold_minutes = (last_activity - first_buy) / 60.0

            net_lamports = pos["sol_received_lamports"] - pos["sol_spent_lamports"]
            pnl_usd = (net_lamports / 1e9) * sol_price
            total_pnl_lamports += net_lamports

            history.append({
                "token_mint": mint,
                "pnl": round(pnl_usd, 2),
                "hold_minutes": round(max(0.0, hold_minutes), 2),
            })

        if not history:
            return None

        avg_hold = sum(h["hold_minutes"] for h in history) / len(history)
        quick_flips = sum(1 for h in history if 0 < h["hold_minutes"] < config.QUICK_FLIP_MINUTES)
        total_pnl_usd = round((total_pnl_lamports / 1e9) * sol_price, 2)

        return {
            "address": address,
            "total_pnl_usd": total_pnl_usd,
            "avg_hold_minutes": round(avg_hold, 2),
            "quick_flip_count": quick_flips,
            "history": history,
            "source": "helius",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 2: SOLANA PUBLIC RPC
# ═══════════════════════════════════════════════════════════════════════════════

class SolanaRpcSource(BaseSource):
    """
    Free fallback using the Solana public JSON-RPC API.

    Steps:
      1. getSignaturesForAddress → recent tx signatures + blockTimes
      2. For each signature (up to SOLANA_RPC_MAX_TXS_TO_PARSE):
           getTransaction(sig, encoding=jsonParsed) → preTokenBalances / postTokenBalances
      3. For each mint where the wallet's token balance changed:
           - balance increased → buy (record timestamp)
           - balance decreased → sell (record timestamp)
      4. Compute hold_minutes from first buy to last sell per mint

    Limitations vs Helius:
      - total_pnl_usd = 0 (no price oracle per historical tx — we'd need getPrice for each block)
      - history[i].pnl = 0 for the same reason
      - hold_minutes and quick_flip_count are accurate
    """

    name = "solana_rpc"

    def __init__(self):
        self.state = SourceState(
            "solana_rpc",
            config.SOLANA_RPC_DELAY_SECONDS,
            config.SOLANA_RPC_COOLDOWN_MINUTES,
            config.SOLANA_RPC_MAX_CONSECUTIVE_ERRORS,
        )

    def get_wallet_trades(self, address: str) -> list | None:
        if not self.state.is_available():
            return None

        # Step 1: fetch recent signatures — this failure IS a source-level error
        self.state.wait_for_rate_limit()
        try:
            res = _rpc_call(
                "getSignaturesForAddress",
                [address, {"limit": config.SOLANA_RPC_MAX_SIGNATURES}],
            )
            if res.get("error"):
                print(f"[solana_rpc] RPC error: {res['error']}")
                self.state.mark_error()
                return None
            sigs = res.get("result", [])
        except Exception as e:
            print(f"[solana_rpc] getSignaturesForAddress failed: {e}")
            self.state.mark_error()
            return None

        if not sigs:
            self.state.mark_success()
            return []   # wallet exists but no recent activity

        # Step 2: fetch and parse transactions.
        # getTransaction 429s are *expected* from the public RPC — they don't indicate
        # the source itself is broken, just that we need to slow down. We back off locally
        # (sleep + retry once) rather than marking the source as failed.
        mint_events: dict = defaultdict(list)
        parse_limit = min(len(sigs), config.SOLANA_RPC_MAX_TXS_TO_PARSE)
        tx_429_streak = 0   # local 429 counter; only mark source error after too many

        for sig_obj in sigs[:parse_limit]:
            sig = sig_obj.get("signature")
            block_time = sig_obj.get("blockTime", 0)
            if sig_obj.get("err"):
                continue

            self.state.wait_for_rate_limit()
            tx = None
            for attempt in range(2):
                try:
                    res = _rpc_call(
                        "getTransaction",
                        [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                    )
                    tx = res.get("result")
                    tx_429_streak = 0
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        tx_429_streak += 1
                        backoff = 3.0 * (2 ** attempt)
                        time.sleep(backoff)
                    else:
                        break   # non-429 HTTP error — skip this tx
                except Exception:
                    break

            # If we're getting sustained 429s stop parsing this wallet's txs early
            if tx_429_streak >= 5:
                print(f"[solana_rpc] Sustained 429s on getTransaction — stopping early for {address[:8]}...")
                break

            if not tx:
                continue

            meta = tx.get("meta") or {}
            pre = {b["mint"]: b for b in meta.get("preTokenBalances", [])}
            post = {b["mint"]: b for b in meta.get("postTokenBalances", [])}

            all_mints = set(pre.keys()) | set(post.keys())
            for mint in all_mints:
                pre_b = pre.get(mint, {})
                post_b = post.get(mint, {})

                if (pre_b.get("owner") != address and
                        post_b.get("owner") != address):
                    continue

                pre_amt = float(
                    (pre_b.get("uiTokenAmount") or {}).get("uiAmount") or 0
                )
                post_amt = float(
                    (post_b.get("uiTokenAmount") or {}).get("uiAmount") or 0
                )
                delta = post_amt - pre_amt
                if abs(delta) < 1e-9:
                    continue

                mint_events[mint].append((block_time, delta))

        # Source-level error only if getTransaction was completely unusable (all 429s)
        if tx_429_streak >= 5:
            self.state.mark_error()
        else:
            self.state.mark_success()

        if not mint_events:
            return []

        record = self._build_record(address, mint_events)
        return [record] if record else []

    def _build_record(self, address: str, mint_events: dict) -> dict | None:
        history = []

        for mint, events in mint_events.items():
            buys = sorted([ts for ts, d in events if d > 0])
            sells = sorted([ts for ts, d in events if d < 0])

            if not buys and not sells:
                continue

            first_buy = buys[0] if buys else (sells[0] if sells else 0)
            last_sell = sells[-1] if sells else (buys[-1] if buys else 0)
            hold_minutes = max(0.0, (last_sell - first_buy) / 60.0)

            history.append({
                "token_mint": mint,
                "pnl": 0.0,          # no price oracle for historical blocks
                "hold_minutes": round(hold_minutes, 2),
            })

        if not history:
            return None

        avg_hold = sum(h["hold_minutes"] for h in history) / len(history)
        # Count both 30-min flips (rug signal) and sub-10-second flips (bot signal).
        # scorer reads quick_flip_count for the 30-min threshold; sub-10s is derived
        # in the scorer directly from history hold_minutes values.
        quick_flips = sum(
            1 for h in history
            if 0 < h["hold_minutes"] < config.QUICK_FLIP_MINUTES
        )

        return {
            "address": address,
            "total_pnl_usd": 0.0,   # RPC source limitation — see docstring
            "avg_hold_minutes": round(avg_hold, 2),
            "quick_flip_count": quick_flips,
            "history": history,
            "source": "solana_rpc",
            "pnl_available": False,  # scorer should skip PnL penalty for RPC wallets
        }
# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE 3: GMGN CLI (official Agent API)
# ═══════════════════════════════════════════════════════════════════════════════

class GmgnCliSource(BaseSource):
    """
    Official GMGN Agent API accessed via `npx gmgn-cli`.

    Two capabilities:
      1. get_top_traders(mint) — replaces the broken direct GMGN top_traders endpoint.
         Returns list of (address, wallet_record) with full realized PnL already included.

      2. get_wallet_trades(address) — replaces Helius/RPC for wallet enrichment.
         Calls `gmgn-cli portfolio stats` which returns win rate, realized PnL, avg hold time.

    Requires GMGN_API_KEY in ~/.config/gmgn/.env (written by setup logic in DataRouter).
    Rate limit: ~1 req/s to be safe (GMGN cooperation API is documented at 1 req/5s but
    the Agent API is more generous in practice).
    """

    name = "gmgn_cli"

    def __init__(self):
        self.state = SourceState(
            "gmgn_cli",
            delay=config.GMGN_CLI_DELAY_SECONDS,
            cooldown_minutes=config.GMGN_COOLDOWN_MINUTES,
            max_errors=config.GMGN_MAX_CONSECUTIVE_ERRORS,
        )

    def _enabled(self) -> bool:
        return bool(config.GMGN_API_KEY)

    def _run(self, args: list) -> dict | list | None:
        """Run a gmgn-cli command and return parsed JSON, or None on failure."""
        if not self._enabled():
            return None
        self.state.wait_for_rate_limit(jitter=0.5)
        # On Windows, npx is npx.cmd — use shell=True to let the OS resolve it.
        cmd = "npx --yes gmgn-cli " + " ".join(args) + " --raw"
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, shell=True,
                env={**os.environ, "GMGN_API_KEY": config.GMGN_API_KEY},
            )
            if result.returncode != 0:
                err = result.stderr.strip()[:200]
                print(f"[gmgn_cli] Non-zero exit: {err}")
                self.state.mark_error()
                return None
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            print("[gmgn_cli] Timeout")
            self.state.mark_error()
            return None
        except Exception as e:
            print(f"[gmgn_cli] Error: {e}")
            self.state.mark_error()
            return None

    def get_top_traders(self, mint: str, limit: int = None) -> list:
        """
        Fetch top traders for a token mint via `gmgn-cli token traders`.
        Returns list of normalized wallet records (already enriched with PnL).
        """
        if not self._enabled() or not self.state.is_available():
            return []

        n = limit or config.GMGN_TOP_TRADERS_PER_TOKEN
        data = self._run([
            "token", "traders",
            "--chain", "sol",
            "--address", mint,
            "--order-by", "profit",
            "--direction", "desc",
            "--limit", str(n),
        ])
        if not data or not isinstance(data.get("list"), list):
            self.state.mark_error()
            return []

        records = []
        for item in data["list"]:
            addr = item.get("address", "")
            if not addr or len(addr) < 10:
                continue

            realized_pnl = float(item.get("realized_profit") or 0)
            unrealized_pnl = float(item.get("unrealized_profit") or 0)
            total_pnl = realized_pnl + unrealized_pnl

            buy_count = int(item.get("buy_tx_count_cur") or 0)
            sell_count = int(item.get("sell_tx_count_cur") or 0)

            # Hold time: start_holding_at / end_holding_at are unix timestamps
            start_ts = item.get("start_holding_at") or 0
            end_ts = item.get("end_holding_at") or item.get("last_active_timestamp") or 0
            hold_minutes = max(0.0, (end_ts - start_ts) / 60.0) if start_ts and end_ts else 0.0

            quick_flips = int(item.get("sell_tx_count_cur") or 0) if hold_minutes < config.QUICK_FLIP_MINUTES else 0

            # Bot signals from GMGN tags
            tags = item.get("tags") or []
            maker_tags = item.get("maker_token_tags") or []
            is_suspicious = item.get("is_suspicious", False)
            has_bot_tag = any(t in tags for t in ("bot", "sniper", "sandwich")) or is_suspicious

            records.append({
                "address": addr,
                "total_pnl_usd": round(total_pnl, 2),
                "avg_hold_minutes": round(hold_minutes, 2),
                "quick_flip_count": quick_flips,
                "history": [{"token_mint": mint, "pnl": round(realized_pnl, 2), "hold_minutes": round(hold_minutes, 2)}],
                "source": "gmgn_cli",
                "pnl_available": True,
                "tags": tags,
                "maker_tags": maker_tags,
                "is_suspicious": is_suspicious,
                "has_bot_tag": has_bot_tag,
                "buy_count": buy_count,
                "sell_count": sell_count,
            })

        self.state.mark_success()
        print(f"[gmgn_cli] {mint[:8]}... -> {len(records)} trader(s)")
        return records

    def get_wallet_trades(self, address: str) -> list | None:
        """
        Enrich a wallet via `gmgn-cli portfolio stats`.
        Returns a normalized wallet record with 7-day PnL stats, win rate, avg hold time.
        """
        if not self._enabled():
            return None
        if not self.state.is_available():
            return None

        data = self._run([
            "portfolio", "stats",
            "--chain", "sol",
            "--wallet", address,
            "--period", "7d",
        ])
        if not data or not isinstance(data, dict):
            self.state.mark_error()
            return None
        if "realized_profit" not in data:
            self.state.mark_success()
            return []  # wallet found, no activity

        self.state.mark_success()

        realized_pnl = float(data.get("realized_profit") or 0)
        buy_count = int(data.get("buy") or 0)
        sell_count = int(data.get("sell") or 0)

        pnl_stat = data.get("pnl_stat") or {}
        win_rate = float(pnl_stat.get("winrate") or 0)
        avg_hold_secs = float(pnl_stat.get("avg_holding_period") or 0)
        avg_hold_minutes = avg_hold_secs / 60.0
        token_count = int(pnl_stat.get("token_num") or 0)

        # Rebuild history from pnl distribution buckets (we don't have per-token breakdown here)
        # Use token_count as coin_count proxy; pnl goes to a single summary entry
        history = []
        if token_count > 0:
            pnl_per_token = realized_pnl / token_count
            hold_per_token = avg_hold_minutes
            history = [
                {"token_mint": f"__summary_{i}", "pnl": round(pnl_per_token, 2), "hold_minutes": round(hold_per_token, 2)}
                for i in range(min(token_count, 20))  # cap at 20 to match scorer expectations
            ]

        quick_flips = sum(1 for h in history if 0 < h["hold_minutes"] < config.QUICK_FLIP_MINUTES)

        return [{
            "address": address,
            "total_pnl_usd": round(realized_pnl, 2),
            "avg_hold_minutes": round(avg_hold_minutes, 2),
            "quick_flip_count": quick_flips,
            "history": history,
            "source": "gmgn_cli",
            "pnl_available": True,
            "win_rate_override": win_rate,  # scorer will use this directly
            "buy_count": buy_count,
            "sell_count": sell_count,
        }]


# ═══════════════════════════════════════════════════════════════════════════════

class ApifySource(BaseSource):
    """
    Last-resort source. Uses the muhammetakkurtt/gmgn-token-top-traders-scraper actor.

    Unlike the other sources this works per-token (fetches all top traders for a list
    of mints), not per-wallet. DataRouter.get_wallet_trades_bulk() uses this path.

    Every run is logged to the apify_runs SQLite table.
    Silently disabled if APIFY_TOKEN is not set.
    """

    name = "apify"

    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DB_PATH
        self.state = SourceState(
            "apify",
            delay=0,                       # no rate limit, just credit cost
            cooldown_minutes=config.APIFY_COOLDOWN_MINUTES,
            max_errors=3,
        )

    def _enabled(self) -> bool:
        return bool(config.APIFY_TOKEN)

    def get_wallet_trades(self, address: str) -> list | None:
        # Apify fetches by token mint, not by wallet address.
        # Direct per-wallet lookup is not supported by this actor.
        # Use run_for_mints() instead.
        return None

    def run_for_mints(self, mints: list) -> list:
        """
        Trigger Apify actor for a list of token mints.
        Returns list of raw wallet records (already in the right shape for smart_money_filter).
        Logs the run to apify_runs table.
        """
        if not self._enabled():
            print("[apify] APIFY_TOKEN not set — skipping")
            return []
        if not self.state.is_available():
            print(f"[apify] Cooling down for {self.state.seconds_until_available()/60:.1f}min")
            return []

        print(f"[apify] Triggering actor for {len(mints)} mint(s)...")
        try:
            run_id = self._trigger_run(mints)
        except Exception as e:
            print(f"[apify] Failed to trigger: {e}")
            self.state.mark_error()
            return []

        try:
            results = self._fetch_results(run_id)
        except Exception as e:
            print(f"[apify] Failed to fetch results for run {run_id}: {e}")
            self.state.mark_error()
            return []

        self.state.mark_success()
        self._log_run(run_id, len(mints), len(results))
        print(f"[apify] Run {run_id} returned {len(results)} records")
        return results

    # ── internal ──────────────────────────────────────────────────────────────

    def _trigger_run(self, mints: list) -> str:
        # Apify actor IDs use "~" as separator in URLs, not "/".
        # "username/actor-name" must become "username~actor-name" in the path.
        actor_id = config.APIFY_ACTOR_ID.replace("/", "~")
        url = (
            f"https://api.apify.com/v2/acts/{actor_id}"
            f"/runs?token={config.APIFY_TOKEN}"
        )
        payload = json.dumps({"tokenAddresses": mints, "chain": "sol"}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["data"]["id"]

    def _fetch_results(self, run_id: str, poll_interval: int = 10, max_wait: int = 600) -> list:
        status_url = (
            f"https://api.apify.com/v2/actor-runs/{run_id}"
            f"?token={config.APIFY_TOKEN}"
        )
        waited = 0
        while waited < max_wait:
            resp = json.loads(_get(status_url))
            status = resp["data"]["status"]
            print(f"[apify] status={status} ({waited}s elapsed)")
            if status == "SUCCEEDED":
                break
            if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                raise RuntimeError(f"Apify run ended: {status}")
            time.sleep(poll_interval)
            waited += poll_interval
        else:
            raise TimeoutError(f"Apify run {run_id} timed out after {max_wait}s")

        items_url = (
            f"https://api.apify.com/v2/actor-runs/{run_id}"
            f"/dataset/items?token={config.APIFY_TOKEN}"
        )
        return json.loads(_get(items_url, timeout=30))

    def _log_run(self, run_id: str, token_count: int, record_count: int):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS apify_runs (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at       TEXT,
                    token_count  INTEGER,
                    record_count INTEGER,
                    run_id       TEXT
                )
            """)
            conn.execute(
                "INSERT INTO apify_runs (run_at, token_count, record_count, run_id) VALUES (?,?,?,?)",
                (datetime.now().isoformat(), token_count, record_count, run_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[apify] Failed to log run to DB: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# DATA ROUTER
# ═══════════════════════════════════════════════════════════════════════════════

class DataRouter:
    """
    Orchestrates all wallet data sources in priority order.

    Usage:
        router = DataRouter()

        # Get wallet trade data for a known address
        records = router.get_wallet_trades("SomeWalletAddress...")

        # Run Apify for a set of token mints (last resort)
        records = router.run_apify_for_mints(["mint1", "mint2", ...])

        # Get trending tokens (always uses GMGN rank endpoint)
        tokens = router.get_trending_tokens("6h", "smartmoney", 20)
    """

    def __init__(self, db_path: str = None):
        self.gmgn_cli = GmgnCliSource()
        self.helius = HeliusSource()
        self.solana_rpc = SolanaRpcSource()
        self.apify = ApifySource(db_path=db_path or config.DB_PATH)

        # Ordered sources for wallet trade lookups.
        # GmgnCliSource is first — it uses the official authenticated API,
        # bypasses Cloudflare, and returns real PnL. Helius/RPC are fallbacks.
        self._wallet_sources: list[BaseSource] = [
            self.gmgn_cli,
            self.helius,
            self.solana_rpc,
        ]

        # Ensure gmgn-cli can find the API key
        self._ensure_gmgn_cli_config()

        # Ensure DB tables exist before any cache lookups
        self._db_path = db_path or config.DB_PATH
        self._ensure_db()

    def _ensure_gmgn_cli_config(self):
        """Write GMGN_API_KEY to ~/.config/gmgn/.env so npx gmgn-cli can find it."""
        if not config.GMGN_API_KEY:
            return
        try:
            cfg_dir = os.path.join(os.path.expanduser("~"), ".config", "gmgn")
            os.makedirs(cfg_dir, exist_ok=True)
            cfg_path = os.path.join(cfg_dir, ".env")
            with open(cfg_path, "w") as f:
                f.write(f"GMGN_API_KEY={config.GMGN_API_KEY}\n")
        except Exception as e:
            print(f"[gmgn_cli] Warning: could not write config: {e}")

    def _ensure_db(self):
        """Create DB tables if they don't exist yet (idempotent)."""
        from smart_money_filter import init_db
        conn = sqlite3.connect(self._db_path)
        init_db(conn)
        conn.close()

    def get_wallet_trades(self, address: str) -> list:
        """
        Try each source in priority order for a single wallet address.
        Returns list of normalized records (usually 1 item).
        Falls back through all sources. If all are cooling down, waits and retries once.
        Never crashes.
        """
        for source in self._wallet_sources:
            if not source.state.is_available():
                wait = source.state.seconds_until_available()
                print(f"[router] {source.name} cooling down ({wait:.0f}s remaining) — skipping")
                continue

            result = source.get_wallet_trades(address)
            if result is None:
                # Hard failure or source not configured — try next
                continue
            if isinstance(result, list) and len(result) > 0:
                return result
            # Empty list = soft miss (wallet found, no swap history) — still continue
            # to next source for a second opinion, but note the soft miss
            print(f"[router] {source.name} returned 0 records for {address[:8]}...")

        # All free sources exhausted or empty — check if Apify should fire
        # Apify is triggered at the bulk level (run_apify_for_mints), not per-wallet.
        # Return empty list; the caller (fetcher.py) decides whether to run Apify.
        print(f"[router] All sources exhausted for {address[:8]}... — returning empty")
        return []

    def get_wallet_trades_bulk(self, addresses: list) -> list:
        """
        Fetch trade data for multiple wallet addresses.
        Tries free sources per-wallet first; falls back to Apify for any that remain empty.
        """
        results = []
        apify_needed_for: list[str] = []

        for addr in addresses:
            records = self.get_wallet_trades(addr)
            if records:
                results.extend(records)
            else:
                apify_needed_for.append(addr)

        if apify_needed_for:
            print(
                f"[router] {len(apify_needed_for)} wallet(s) had no data from free sources — "
                f"flagged for Apify (call run_apify_for_mints separately)"
            )

        return results, apify_needed_for

    def run_apify_for_mints(self, mints: list) -> list:
        """
        Trigger Apify for a list of token mints (last resort).
        Only fires if APIFY_TOKEN is set and Apify is not cooling down.
        """
        return self.apify.run_for_mints(mints)

    def get_top_traders(self, mint: str) -> list[str]:
        """
        Fetch top trader wallet addresses for a token mint.

        Priority:
          1. GmgnCliSource — official authenticated API, returns full wallet records
          2. Direct GMGN endpoint — unauthenticated, often 404'd by Cloudflare (fallback)

        Records from the CLI are stored in _cli_trader_records keyed by address so that
        fetcher.py can skip re-enrichment for those wallets.
        """
        if not hasattr(self, "_cli_trader_records"):
            self._cli_trader_records = {}  # address -> full wallet record

        # ── Path 1: GMGN CLI (preferred) ────────────────────────────────────
        if self.gmgn_cli._enabled() and self.gmgn_cli.state.is_available():
            records = self.gmgn_cli.get_top_traders(mint)
            if records:
                for rec in records:
                    self._cli_trader_records[rec["address"]] = rec
                return [r["address"] for r in records]

        # ── Path 2: direct unauthenticated endpoint (fallback) ───────────────
        if not hasattr(self, "_traders_state"):
            self._traders_state = SourceState(
                "gmgn_traders_direct",
                config.GMGN_TOP_TRADERS_DELAY,
                config.GMGN_COOLDOWN_MINUTES,
                config.GMGN_MAX_CONSECUTIVE_ERRORS,
            )
        state = self._traders_state
        if not state.is_available():
            return []

        state.wait_for_rate_limit(jitter=config.GMGN_TOP_TRADERS_JITTER)
        url = f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{mint}/top_traders"
        try:
            raw = _get(url, headers=config.GMGN_HEADERS)
            data = json.loads(raw)
            if data.get("code") != 0:
                state.mark_error()
                return []
            items = data.get("data", {}).get("items", []) or []
            wallets = [
                item["address"] for item in items[:config.GMGN_TOP_TRADERS_PER_TOKEN]
                if item.get("address") and len(item["address"]) > 10
            ]
            state.mark_success()
            print(f"[gmgn_traders_direct] {mint[:8]}... -> {len(wallets)} trader(s)")
            return wallets
        except urllib.error.HTTPError as e:
            if e.code == 429:
                state.mark_error(hard=True)
            else:
                state.mark_error()
            print(f"[gmgn_traders_direct] HTTP {e.code} for {mint[:8]}...")
            return []
        except Exception as e:
            print(f"[gmgn_traders_direct] Error: {e}")
            state.mark_error()
            return []

    def get_top_trader_wallet_record(self, address: str) -> dict | None:
        """
        Return the pre-built wallet record from the CLI top_traders fetch (if available).
        Returns None if the address wasn't seen in the most recent get_top_traders call.
        """
        if not hasattr(self, "_cli_trader_records"):
            return None
        return self._cli_trader_records.get(address)

    def get_cached_wallets(self, addresses: list[str], db_path: str = None) -> tuple[list, list[str]]:
        """
        Split addresses into (already_enriched_records, needs_fetch_addresses).
        Wallets enriched within WALLET_CACHE_TTL_HOURS are returned from DB
        without touching any API — the primary free-tier protection.
        """
        db = db_path or config.DB_PATH
        ttl_hours = config.WALLET_CACHE_TTL_HOURS
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")

        # Ensure enriched_at column exists (added in v2)
        try:
            conn.execute("ALTER TABLE wallets ADD COLUMN enriched_at TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

        cached_records = []
        needs_fetch = []

        for addr in addresses:
            row = conn.execute(
                """SELECT address, label, score, total_pnl, win_rate, avg_hold_hrs,
                          coin_count, quick_flips, last_seen, first_seen, enriched_at
                   FROM wallets WHERE address = ?""",
                (addr,),
            ).fetchone()

            if row:
                enriched_at_str = row[10]
                if enriched_at_str:
                    from datetime import timezone
                    enriched_at = datetime.fromisoformat(enriched_at_str)
                    age_hours = (datetime.now() - enriched_at).total_seconds() / 3600
                    if age_hours < ttl_hours:
                        cached_records.append({
                            "address": row[0], "label": row[1], "score": row[2],
                            "total_pnl_usd": row[3], "win_rate": row[4],
                            "avg_hold_hrs": row[5], "coin_count": row[6],
                            "quick_flips": row[7], "last_seen": row[8],
                            "first_seen": row[9], "source": "cache",
                        })
                        continue
            needs_fetch.append(addr)

        conn.close()
        return cached_records, needs_fetch

    def mark_wallet_enriched(self, address: str, db_path: str = None):
        """Stamp enriched_at on a wallet row so subsequent runs can skip it."""
        db = db_path or config.DB_PATH
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute(
                "UPDATE wallets SET enriched_at = ? WHERE address = ?",
                (datetime.now().isoformat(), address),
            )
            conn.commit()
        except Exception as e:
            print(f"[cache] Failed to stamp enriched_at for {address[:8]}...: {e}")
        finally:
            conn.close()

    def get_trending_tokens(self, period: str = "6h", orderby: str = "smartmoney",
                            limit: int = 20) -> list:
        """
        Fetch trending tokens from GMGN rank endpoint.
        GMGN is the only source that provides this data.
        Applies rate limiting and exponential backoff on error.
        """
        import urllib.parse

        gmgn_state = SourceState(
            "gmgn_rank",
            config.GMGN_DELAY_SECONDS,
            config.GMGN_COOLDOWN_MINUTES,
            config.GMGN_MAX_CONSECUTIVE_ERRORS,
        )
        # Share state across calls by storing on the router instance
        if not hasattr(self, "_gmgn_state"):
            self._gmgn_state = gmgn_state
        state = self._gmgn_state

        if not state.is_available():
            print(f"[gmgn] Cooling down ({state.seconds_until_available()/60:.1f}min remaining)")
            return []

        state.wait_for_rate_limit(jitter=config.GMGN_JITTER)

        params = urllib.parse.urlencode([
            ("orderby", orderby),
            ("direction", "desc"),
            ("filters[]", "not_honeypot"),
            ("filters[]", "renounced"),
            ("limit", limit),
        ])
        url = f"https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/{period}?{params}"

        try:
            raw = _get(url, headers=config.GMGN_HEADERS)
            data = json.loads(raw)
            if data.get("code") == 0:
                tokens = data["data"]["rank"][:limit]
                state.mark_success()
                return tokens
            print(f"[gmgn] Non-zero code: {data.get('code')} — {data.get('msg')}")
            state.mark_error()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                state.mark_error(hard=True)
            else:
                state.mark_error()
            print(f"[gmgn] HTTP {e.code} fetching rank")
        except Exception as e:
            print(f"[gmgn] Error: {e}")
            state.mark_error()

        return []

    def source_status(self) -> dict:
        """Returns a dict of source name -> availability for logging/debugging."""
        return {
            s.name: {
                "available": s.state.is_available(),
                "errors": s.state.consecutive_errors,
                "cooldown_remaining_s": round(s.state.seconds_until_available(), 1),
            }
            for s in self._wallet_sources + [self.apify]
        }
