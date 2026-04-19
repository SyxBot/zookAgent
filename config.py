"""
config.py — Centralized configuration for all data sources.
All tunable values live here. Override via environment variables or .env file.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── GMGN ─────────────────────────────────────────────────────────────────────
# GMGN_API_KEY enables the official Agent API (gmgn-portfolio/gmgn-token skills).
# Get it at https://gmgn.ai/ai — requires uploading an Ed25519 public key.
# Without a key, GMGN is used for trending + top_traders discovery only.
GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
GMGN_DELAY_SECONDS = 2.5        # min time between requests to any GMGN endpoint
GMGN_CLI_DELAY_SECONDS = 1.5    # delay between gmgn-cli subprocess calls
GMGN_JITTER = 1.0               # add uniform random(0, JITTER) to each delay
GMGN_COOLDOWN_MINUTES = 5       # how long to back off after a 429 or repeated errors
GMGN_MAX_CONSECUTIVE_ERRORS = 3 # trigger cooldown after this many errors in a row

GMGN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://gmgn.ai/",
    "Origin": "https://gmgn.ai",
}

# ── Helius ────────────────────────────────────────────────────────────────────
# Best wallet source. Requires free API key from dev.helius.xyz.
# Returns enhanced parsed swap transactions with tokenTransfers + nativeTransfers.
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
HELIUS_DELAY_SECONDS = 0.5
HELIUS_COOLDOWN_MINUTES = 2
HELIUS_MAX_CONSECUTIVE_ERRORS = 5
HELIUS_MAX_TXS = 100            # max transactions to fetch per wallet

# ── Solana public RPC ─────────────────────────────────────────────────────────
# Free fallback. Gives raw pre/postTokenBalances — no USD prices, but hold times work.
# Replaces dead Solscan public API (all endpoints 404 as of 2026-04).
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
SOLANA_RPC_DELAY_SECONDS = 2.5   # between getTransaction calls — public RPC is strict
SOLANA_RPC_COOLDOWN_MINUTES = 3
SOLANA_RPC_MAX_CONSECUTIVE_ERRORS = 8  # only cooldown after sustained source-level failures
SOLANA_RPC_MAX_SIGNATURES = 30   # signatures to fetch per wallet (fewer = fewer RPC calls)
SOLANA_RPC_MAX_TXS_TO_PARSE = 10 # decode at most this many txs per wallet

# ── Apify ─────────────────────────────────────────────────────────────────────
# Last resort only — costs credits. Only fires when all free sources are cooling down.
# If APIFY_TOKEN is not set the ApifySource is silently skipped, never crashes.
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
APIFY_ACTOR_ID = os.getenv(
    "APIFY_ACTOR_ID", "muhammetakkurtt/gmgn-token-traders-scraper"
)
APIFY_COOLDOWN_MINUTES = 0      # Apify doesn't rate-limit, just costs credits

# ── Price oracle ──────────────────────────────────────────────────────────────
# Used to convert SOL-denominated Helius PnL to USD.
# CoinGecko free endpoint — no key required. Fetched once per pipeline run.
COINGECKO_SOL_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
)
SOL_PRICE_FALLBACK_USD = 150.0  # used if CoinGecko is unreachable

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "smart_money.db")

# ── Source priority order ─────────────────────────────────────────────────────
# DataRouter tries sources in this order, skipping any that are cooling down.
# "gmgn" provides token discovery only — it never returns wallet trade data.
SOURCE_PRIORITY = ["helius", "solana_rpc", "apify"]

# Quick-flip threshold used by the RPC parser (in minutes)
QUICK_FLIP_MINUTES = 30

# Bot detection thresholds (kept in sync with smart_money_filter.THRESHOLDS)
# GMGN explicitly flags "Buy/Sell within 10 seconds" as a bot indicator.
BOT_SUB10S_FLIP_MINUTES = 10 / 60   # 10 seconds in minutes

# ── Top traders (GMGN) ────────────────────────────────────────────────────────
# Real smart money wallet seeds come from /tokens/{mint}/top_traders.
# We cap tokens and wallets per run to stay well within free-tier limits.
GMGN_TOP_TRADERS_PER_TOKEN = 10   # wallets to pull per token mint
GMGN_TOP_TRADERS_TOKENS = 5       # how many trending tokens to query for traders
GMGN_TOP_TRADERS_DELAY = 3.0      # seconds between each top_traders call
GMGN_TOP_TRADERS_JITTER = 1.0     # uniform random added on top

# ── Wallet enrichment budget (free-tier guard) ────────────────────────────────
# After deduplication, cap enrichment calls per pipeline run.
# Helius free: ~100k credits/month. One enrichment = ~1-5 credits.
# 40 wallets × 4x/day × 30 days = 4,800 — safe headroom.
MAX_WALLETS_PER_RUN = 40

# ── Wallet cache TTL ──────────────────────────────────────────────────────────
# If a wallet was successfully enriched within this window, skip the API call.
# This is the primary free-tier protection: repeat runs cost 0 API credits.
WALLET_CACHE_TTL_HOURS = 24

# ── Tighter RPC limits for public endpoint ────────────────────────────────────
# Override the defaults above with safer values for the public Solana RPC.
SOLANA_RPC_MAX_SIGNATURES = 20    # was 30
SOLANA_RPC_MAX_TXS_TO_PARSE = 8  # was 10
