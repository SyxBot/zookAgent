# Solana Smart Money Tracker

A low-maintenance, automated system that identifies and tracks "smart money" wallets on Solana — wallets with high PnL, long hold times, and consistent win rates across meme coin trades.

## What It Does

- Pulls trending Solana meme coin tokens from GMGN.ai
- Fetches **real top traders per token** from GMGN (not creator addresses)
- Scores and labels each wallet (Diamond, Long Hold, Flipper, Rug Chaser)
- **Caches enriched wallets for 24h** — repeat runs cost zero API credits
- Maintains a persistent wallet registry in SQLite
- Displays everything in a real-time dashboard with live trade drill-down

## Free-Tier Strategy

This app runs entirely on free tiers. Here's how API budgets are managed:

| Source | Free Limit | Usage per run |
|--------|-----------|---------------|
| GMGN rank | ~20 req/min (Cloudflare) | 1 request (trending tokens) |
| GMGN top_traders | ~10 req/min | 5 requests (1 per token) |
| Helius | ~100k credits/month | ≤40 wallets × ~2 credits |
| Solana RPC | public, no key | fallback, 2.5s delay |
| Apify | ~$5 credit/month | last resort only |

**Wallet cache** (24h TTL) is the primary protection: once a wallet is enriched, it won't trigger another API call for 24 hours regardless of how many times you run the pipeline.

At 4 runs/day × 40 wallets = 160 enrichments/day × 30 days = 4,800 Helius credits/month — well within the 100k free tier.

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set up environment

```bash
cp .env.example .env
# Edit .env — only HELIUS_API_KEY is needed for full PnL data
```

Get your **Helius API key** (free) from [dev.helius.xyz](https://dev.helius.xyz) — no credit card required.  
Get your **Apify token** (optional) from [console.apify.com/account/integrations](https://console.apify.com/account/integrations).

### 3. Run the full pipeline

```bash
python main.py
```

This will:
1. Fetch trending tokens from GMGN
2. Pull **real top traders** per token (5 tokens × 10 wallets = up to 50 unique wallets)
3. Check cache — skip wallets enriched in the last 24h
4. Enrich remaining wallets via Helius → Solana RPC → Apify fallback
5. Score, store in SQLite, export to `exports/wallets_latest.json`

### 4. Open the dashboard

```bash
python server.py   # recommended — enables live GMGN proxy
# then open http://localhost:8080
```

Or open `dashboard/index.html` directly (wallet drill-down requires the server).

## Usage

```bash
# Full pipeline (default: 6h period, smartmoney sort)
python main.py

# Use a specific period
python main.py --period 24h

# Skip Apify entirely (free sources only)
python main.py --no-apify

# Process an existing wallet JSON file (skip fetch)
python main.py --file wallets_20260101_0600.json

# Force a specific enrichment source
python fetcher.py --source helius
```

## Project Structure

```
solana-smart-money/
├── fetcher.py              # Pulls trending tokens + triggers Apify
├── smart_money_filter.py   # Scores wallets, writes to SQLite
├── main.py                 # Orchestrator: fetch → filter → export
├── dashboard/
│   └── index.html          # Single-file dashboard UI
├── exports/
│   └── wallets_latest.json # JSON export for dashboard
├── smart_money.db          # SQLite wallet registry (gitignored)
├── .env                    # Your API keys (gitignored)
├── .env.example            # Template
└── requirements.txt
```

## Wallet Scoring

| Label | Score | Meaning |
|-------|-------|---------|
| 💎 Diamond_Profitable | ≥ 70 | High PnL, long holds, multi-coin winner |
| 🟢 Long_Hold_Green | 45–69 | Solid hold time, positive PnL |
| 🟡 Short_Flipper | 10–44 | Quick trades, mixed results |
| 🔴 Rug_Chaser | < 10 | Buys early, dumps fast at low MCAP |

Scoring factors: PnL (+30 max), hold duration (+25 max), win rate (+18 max), multi-coin consistency (+15 max), bot penalty (-20).

## Scheduling (Optional)

### Local cron (every 6 hours)

```bash
crontab -e
# Add:
0 */6 * * * cd /path/to/solana-smart-money && python main.py >> logs/run.log 2>&1
```

### GitHub Actions

Create `.github/workflows/fetch.yml`:

```yaml
name: Fetch Smart Money
on:
  schedule:
    - cron: '0 */6 * * *'
jobs:
  fetch:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: python main.py
        env:
          APIFY_TOKEN: ${{ secrets.APIFY_TOKEN }}
```

## Known Constraints

- **Cloudflare protection**: GMGN blocks repeated direct calls. Use Apify for scheduled runs.
- **No official wallet API docs**: The wallet endpoints are reverse-engineered and may change.
- **Apify free tier**: ~$5/mo free credits = ~100 actor runs. Each run on 20 tokens × 100 wallets = 2,000 records.
- **Wallet rotation**: Smart money wallets burn out after heavy copy-trading. Track `first_seen`/`last_seen` to detect this.

## Dashboard

Open `dashboard/index.html` directly in any browser — no server required.

**Tab 1 — Coins**: Trending tokens with price, volume, smart money buy counts, risk level.
**Tab 2 — Smart Wallets**: Full wallet registry with scores, PnL, win rate, hold time.
**Tab 3 — Live Activity**: Event feed, label distribution chart, top performers.
**Tab 4 — Setup & API**: Config form, threshold sliders, API reference.

The dashboard loads real data from `exports/wallets_latest.json` automatically if it exists, otherwise shows mock data.
