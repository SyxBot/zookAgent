"""
server.py — Local dev server for the Solana Smart Money dashboard.

Serves static files AND proxies GMGN API calls to bypass CORS/Cloudflare.

Usage:
    python server.py          # default port 8080
    python server.py 3000     # custom port

Then open: http://localhost:8080
"""

import http.server
import urllib.request
import urllib.parse
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
import sqlite3

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "smart_money.db")

GMGN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://gmgn.ai/",
    "Origin": "https://gmgn.ai",
}

# Simple in-memory cache to avoid hammering GMGN
_cache = {}
CACHE_TTL = 60  # seconds


def cached_fetch(url, cache_key):
    now = time.time()
    if cache_key in _cache:
        data, ts = _cache[cache_key]
        if now - ts < CACHE_TTL:
            return data, True  # (data, from_cache)

    req = urllib.request.Request(url, headers=GMGN_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            _cache[cache_key] = (raw, now)
            return raw, False
    except Exception as e:
        # Return cached stale data if available rather than erroring
        if cache_key in _cache:
            data, _ = _cache[cache_key]
            print(f"[Proxy] Live fetch failed ({e}), serving stale cache for {cache_key}")
            return data, True
        raise


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, format, *args):
        # Suppress noisy 200s for static assets, show errors and proxy calls
        code = args[1] if len(args) > 1 else "?"
        if str(code) not in ("200", "304") or "/proxy/" in args[0]:
            print(f"[{args[1]}] {args[0]}")

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # ── /proxy/gmgn/rank  →  GMGN trending tokens ──────────────────────
        if path.startswith("/proxy/gmgn/rank"):
            self._proxy_gmgn_rank(parsed)
            return

        # ── /proxy/gmgn/wallet  →  wallet stats ────────────────────────────
        if path.startswith("/proxy/gmgn/wallet"):
            self._proxy_gmgn_wallet(parsed)
            return

        # ── /proxy/gmgn/traders  →  top traders for a token ────────────────
        if path.startswith("/proxy/gmgn/traders"):
            self._proxy_gmgn_traders(parsed)
            return

        # ── /api/activity  →  recent wallet_history from SQLite ─────────────
        if path == "/api/activity":
            self._api_activity()
            return

        # ── /api/sm-token-counts  →  tracked SM wallet count per token ──────
        if path == "/api/sm-token-counts":
            self._api_sm_token_counts()
            return

        # ── /api/wallet-stats  →  wallet stats via gmgn-cli ─────────────────
        if path == "/api/wallet-stats":
            self._api_wallet_stats(parsed)
            return

        # ── /api/top-wallets  →  top wallets summary from SQLite ─────────────
        if path == "/api/top-wallets":
            self._api_top_wallets()
            return

        # ── / → redirect to dashboard ───────────────────────────────────────
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/dashboard/index.html")
            self.end_headers()
            return

        # ── Everything else: serve static files ─────────────────────────────
        super().do_GET()

    def _proxy_gmgn_rank(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        period = qs.get("period", ["6h"])[0]
        orderby = qs.get("orderby", ["smartmoney"])[0]
        limit = qs.get("limit", ["20"])[0]

        url = (
            f"https://gmgn.ai/defi/quotation/v1/rank/sol/swaps/{period}"
            f"?orderby={orderby}&direction=desc"
            f"&filters[]=not_honeypot&filters[]=renounced"
            f"&limit={limit}"
        )
        cache_key = f"rank:{period}:{orderby}"
        self._proxy(url, cache_key)

    def _proxy_gmgn_wallet(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        address = qs.get("address", [""])[0]
        window = qs.get("window", ["30"])[0]
        if not address:
            self._json_error(400, "address param required")
            return
        url = f"https://gmgn.ai/api/v1/wallet_stat/sol/{address}/{window}"
        cache_key = f"wallet:{address}:{window}"
        self._proxy(url, cache_key)

    def _proxy_gmgn_traders(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        mint = qs.get("mint", [""])[0]
        if not mint:
            self._json_error(400, "mint param required")
            return
        url = f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{mint}/top_traders"
        cache_key = f"traders:{mint}"
        self._proxy(url, cache_key)

    def _api_activity(self):
        """Return last 50 wallet_history rows joined with wallet label/score.
        Filters out __summary_ placeholder token mints (from portfolio stats source)."""
        try:
            if not os.path.exists(DB_PATH):
                self._json_response({"items": [], "source": "no_db"})
                return
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT h.wallet_address, h.token_mint, h.pnl, h.hold_minutes, h.seen_at,
                       w.label, w.score
                FROM wallet_history h
                LEFT JOIN wallets w ON w.address = h.wallet_address
                WHERE h.token_mint NOT LIKE '__summary_%'
                  AND h.token_mint IS NOT NULL
                  AND h.token_mint != ''
                ORDER BY h.seen_at DESC
                LIMIT 100
            """).fetchall()
            conn.close()
            items = []
            for r in rows:
                pnl = r["pnl"] or 0
                hold = r["hold_minutes"] or 0
                items.append({
                    "wallet":       r["wallet_address"],
                    "token":        r["token_mint"] or "",
                    "pnl":          round(pnl, 2),
                    "type":         "sell" if hold > 0 and pnl > 0 else "buy" if pnl >= 0 else "rug",
                    "hold_minutes": round(hold, 1),
                    "seen_at":      r["seen_at"] or "",
                    "label":        r["label"] or "Unknown",
                    "score":        r["score"] or 0,
                })
            self._json_response({"items": items, "source": "db"})
        except Exception as e:
            print(f"[API] activity error: {e}")
            self._json_response({"items": [], "source": "error", "error": str(e)})

    def _api_wallet_stats(self, parsed):
        """Call gmgn-cli portfolio stats for a wallet and return the result."""
        qs = urllib.parse.parse_qs(parsed.query)
        address = qs.get("address", [""])[0]
        period  = qs.get("period",  ["7d"])[0]
        if not address:
            self._json_error(400, "address param required")
            return

        # Read GMGN_API_KEY from .env if present
        env_path = os.path.join(BASE_DIR, ".env")
        gmgn_key = os.getenv("GMGN_API_KEY", "")
        if not gmgn_key and os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("GMGN_API_KEY="):
                        gmgn_key = line.strip().split("=", 1)[1]
                        break

        if not gmgn_key:
            self._json_error(503, "GMGN_API_KEY not configured")
            return

        cmd = f"npx gmgn-cli portfolio stats --chain sol --wallet {address} --period {period} --raw"
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=20, shell=True,
                env={**os.environ, "GMGN_API_KEY": gmgn_key},
            )
            if result.returncode != 0 or not result.stdout.strip():
                self._json_error(502, result.stderr.strip()[:200] or "empty response")
                return
            data = json.loads(result.stdout)
            self._json_response(data)
        except subprocess.TimeoutExpired:
            self._json_error(504, "gmgn-cli timeout")
        except Exception as e:
            self._json_error(500, str(e))

    def _api_top_wallets(self):
        """Return top 10 wallets by PnL for the activity tab leaderboard."""
        try:
            if not os.path.exists(DB_PATH):
                self._json_response({"wallets": []})
                return
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT address, label, score, total_pnl, win_rate, avg_hold_hrs,
                       coin_count, last_seen, first_seen
                FROM wallets
                ORDER BY total_pnl DESC
                LIMIT 10
            """).fetchall()
            conn.close()
            self._json_response({"wallets": [dict(r) for r in rows]})
        except Exception as e:
            self._json_error(500, str(e))

    def _api_sm_token_counts(self):
        """Return {mint: count} of distinct tracked wallets per token_mint."""
        try:
            if not os.path.exists(DB_PATH):
                self._json_response({"counts": {}, "source": "no_db"})
                return
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute("""
                SELECT h.token_mint, COUNT(DISTINCT h.wallet_address) as cnt
                FROM wallet_history h
                INNER JOIN wallets w ON w.address = h.wallet_address
                WHERE h.token_mint IS NOT NULL
                  AND h.token_mint != ''
                  AND h.token_mint NOT LIKE '__summary_%'
                GROUP BY h.token_mint
                ORDER BY cnt DESC
            """).fetchall()
            conn.close()
            counts = {r[0]: r[1] for r in rows}
            self._json_response({"counts": counts, "source": "db"})
        except Exception as e:
            print(f"[API] sm-token-counts error: {e}")
            self._json_response({"counts": {}, "source": "error", "error": str(e)})

    def _json_response(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self, upstream_url, cache_key):
        try:
            data, from_cache = cached_fetch(upstream_url, cache_key)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Cache", "HIT" if from_cache else "MISS")
            self.send_cors()
            self.end_headers()
            self.wfile.write(data)
            status = "CACHED" if from_cache else "LIVE"
            print(f"[Proxy] {status} → {cache_key}")
        except Exception as e:
            print(f"[Proxy] ERROR {cache_key}: {e}")
            self._json_error(502, str(e))

    def _json_error(self, code, msg):
        body = json.dumps({"error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_cors()
        self.end_headers()
        self.wfile.write(body)


def open_browser():
    time.sleep(0.8)
    url = f"http://localhost:{PORT}/dashboard/index.html"
    print(f"[Server] Opening {url}")
    webbrowser.open(url)


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("", PORT), Handler)
    print("=" * 55)
    print("  SOLANA SMART MONEY — LOCAL SERVER")
    print("=" * 55)
    print(f"  Dashboard:  http://localhost:{PORT}/dashboard/index.html")
    print(f"  Proxy base: http://localhost:{PORT}/proxy/gmgn/...")
    print(f"  Base dir:   {BASE_DIR}")
    print(f"  Cache TTL:  {CACHE_TTL}s")
    print("  Press Ctrl+C to stop")
    print("=" * 55)

    threading.Thread(target=open_browser, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] Stopped.")
