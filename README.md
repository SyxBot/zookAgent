# zookAgent

Real-time Solana meme-coin intelligence engine. Connects to the GMGN WebSocket stream, runs every incoming event through a strict 6-layer pipeline, and surfaces only high-probability, high-confidence trading opportunities.

---

## How it works

Every WebSocket event takes the same path. Each layer acts as a gate — only the events that pass all gates reach the scoring engine.

```
GMGN WebSocket
      │
      ▼
[1] Fast Reject        O(1) — drops 80–95% of events
      │
      ▼
[2] State Update       in-memory delta writes only
      │
      ▼
[3] Smart Filter       wallet behavior + wash-trade detection
      │
      ▼
[4] Qualification Gate all-or-nothing hard stop
      │
      ▼
[5] Scoring Engine     runs on < 5% of events
      │
      ▼
[6] Output / Alert     SSE push to frontend
```

### Layer 1 — Fast Reject
The hottest path. Uses only `Set` lookups and property reads — no loops, no async, no DB calls. Rejects an event immediately if any of the following are true:

- Event channel is not in the recognised set
- Token mint is in the runtime rug blocklist
- Liquidity is present and below `MIN_LIQUIDITY`
- Mint authority or freeze authority is enabled

### Layer 2 — State Update
Maintains two in-memory `Map` stores. Writes are incremental delta-only — no raw event history is ever stored.

| Store | Key | Fields |
|---|---|---|
| `tokenState` | mint address | liquidity, volume, liquidityDelta, volumeDelta, security flags, createdAt |
| `walletState` | wallet address | classification, tradeCount, winCount, totalPnl, avgHoldMs |

Both maps cap at configurable limits and evict the oldest entry in O(1) using Map insertion order.

### Layer 3 — Smart Filter
Checks behavioral signals:

- **Wash-trade detection** — rejects tokens where `volume / liquidity > 50`
- **Wallet quality gate** — rejects tokens where all observed wallets are classified as `FARMER` or `RUGGER`
- **Clustering gate** — if 5+ wallets are observed and none are high-quality, the token is downgraded

Wallet classifications:

| Class | Criteria |
|---|---|
| `SNIPER` | Entry < 30 s after launch, hold < 5 min |
| `SCALPER` | ≥ 20 trades, avg hold < 30 min |
| `SWING` | Avg hold ≥ 1 h, win rate ≥ 60% |
| `FARMER` | Win rate < 35%, < 15 trades |
| `RUGGER` | Avg hold < 1 min, ≥ 10 trades |
| `UNKNOWN` | Insufficient history (< 3 trades) |

### Layer 4 — Qualification Gate
Hard stop. **All** conditions must be true or the event is dropped immediately:

1. Liquidity ≥ `QUAL_MIN_LIQUIDITY`
2. No honeypot, mint authority, or freeze authority flag
3. At least one `SNIPER`, `SCALPER`, or `SWING` wallet — OR token is within the early launch window (`QUAL_EARLY_LAUNCH_SECS`)
4. Volume spike (≥ `QUAL_VOLUME_SPIKE_RATIO` increase) — OR token is within the early launch window

### Layer 5 — Scoring Engine
Only runs on events that pass the qualification gate. Computes a 0–100 score from five weighted components:

| Component | Weight | What it measures |
|---|---|---|
| Wallet quality | 35% | Fraction of buyers that are high-quality wallet types |
| Wallet clustering | 25% | Multiple quality wallets buying the same token in a time window |
| Entry timing | 20% | How early this detection is relative to token launch |
| Liquidity stability | 10% | Whether liquidity is growing or declining |
| Volume momentum | 10% | Volume-to-liquidity ratio health |

Output shape:

```json
{
  "token": "mint_address",
  "symbol": "TICKER",
  "score": 74,
  "confidence": "HIGH",
  "setup": "EARLY_SNIPER",
  "risk": "LOW",
  "reasons": [
    "Early entry timing window",
    "Wallet clustering detected (60%)",
    "Strong volume momentum"
  ]
}
```

**Confidence** levels: `HIGH` (score ≥ 70) · `MEDIUM` (≥ 50) · `LOW` (< 50)

**Setup** types: `EARLY_SNIPER` · `MOMENTUM` · `LATE_EXIT`

**Risk** levels: based on LP burned status, contract renounced, top-10 holder concentration, and rugger wallet presence

### Layer 6 — Output
A signal is emitted only if `score > SCORE_THRESHOLD` **and** `confidence ≠ LOW`. Signals are:

- Pushed via SSE to all connected frontend clients
- Stored in a 200-entry ring buffer (available at `/api/signals`)
- Emitted on an internal EventEmitter bus (`output/alertSystem.js`) for extensions

---

## Project structure

```
zookAgent/
│
├── index.js                   Entry point
├── package.json
├── .env.example
│
├── config/
│   └── index.js               Centralised env-var config
│
├── ingestion/
│   └── websocket.js           GMGN WebSocket client (exponential reconnect)
│
├── filters/
│   ├── fastReject.js          Layer 1 — O(1) reject
│   └── smartFilter.js         Layer 3 — behavioral analysis
│
├── state/
│   ├── tokenState.js          In-memory token Map with delta updates
│   └── walletState.js         In-memory wallet Map + token-buyer index
│
├── engine/
│   ├── qualificationGate.js   Layer 4 — hard-stop filter
│   └── scoringEngine.js       Layer 5 — weighted 0–100 score
│
├── core/
│   └── eventProcessor.js      Pipeline orchestrator
│
├── output/
│   └── alertSystem.js         SSE fan-out + signal ring buffer
│
├── server/
│   └── http.js                HTTP server (no framework) — serves API + frontend
│
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js                 SSE client, signal cards, live stats
│
└── backend/                   Legacy Python/FastAPI backend (see below)
    ├── main.py
    ├── requirements.txt
    └── ...
```

---

## Quick start

### Prerequisites

- Node.js 18+
- A GMGN API access token ([docs.gmgn.ai](https://docs.gmgn.ai))

### Setup

```bash
# 1. Clone and install
git clone <repo-url>
cd zookAgent
npm install

# 2. Configure
cp .env.example .env
# Edit .env and set GMGN_ACCESS_TOKEN

# 3. Start the engine
npm start

# or for development with auto-restart:
npm run dev

# 4. Open the frontend
open http://localhost:3001
```

The engine serves both the API and the frontend on the same port.

---

## API

All endpoints are on `http://localhost:3001` (configurable via `PORT`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/stream` | SSE stream — pushes `signal` and `backfill` events |
| `GET` | `/api/signals?limit=50` | Recent scored signals (ring buffer, max 200) |
| `GET` | `/api/stats` | Live pipeline counters and state sizes |
| `POST` | `/api/rug` | Add a mint to the runtime rug blocklist `{ "mint": "..." }` |
| `GET` | `/health` | Health check + uptime |

### SSE event format

```
data: {"type":"signal","signal":{...}}
data: {"type":"backfill","signals":[...]}
: heartbeat
```

### Stats response

```json
{
  "pipeline": {
    "total": 45820,
    "rejected": 38200,
    "filtered": 5100,
    "gated": 2100,
    "scored": 420,
    "emitted": 38,
    "rejectRatePct": 83.4
  },
  "state": {
    "tokens": 1240,
    "wallets": 3870
  },
  "subscribers": 2
}
```

---

## Configuration

All values are set via environment variables (copy `.env.example` to `.env`).

| Variable | Default | Description |
|---|---|---|
| `GMGN_ACCESS_TOKEN` | — | Required. Your GMGN API bearer token |
| `MIN_LIQUIDITY` | `5000` | Layer 1: minimum liquidity to pass fast reject (USD) |
| `QUAL_MIN_LIQUIDITY` | `10000` | Layer 4: minimum liquidity for qualification |
| `QUAL_VOLUME_SPIKE_RATIO` | `2.0` | Layer 4: minimum volume increase ratio to qualify as a spike |
| `QUAL_EARLY_LAUNCH_SECS` | `300` | Layer 4: seconds after launch that a token is considered "early" |
| `SCORE_THRESHOLD` | `60` | Layer 6: minimum score for a signal to be emitted |
| `MAX_TOKENS` | `5000` | Max tokens to hold in memory before eviction |
| `MAX_WALLETS` | `10000` | Max wallets to hold in memory before eviction |
| `CLUSTER_WINDOW_MS` | `300000` | Window (ms) for wallet clustering analysis (5 min default) |
| `PORT` | `3001` | HTTP server port |

---

## Frontend

The frontend connects to the engine's SSE stream and renders scored signals as cards. No framework — plain HTML/CSS/JS served directly by the engine.

Each signal card shows:

- Token symbol and name
- Score (0–100) with colour-coded severity
- Setup type, confidence level, and risk level as badges
- Up to 3 human-readable reasons
- Truncated mint address and relative timestamp

A **score threshold slider** in the header filters cards client-side without disconnecting the stream. The **live stats ticker** polls `/api/stats` every 5 seconds and shows the pipeline efficiency in real time.

---

## Legacy Python backend

The `backend/` directory contains the original FastAPI backend built during v1. It uses polling (not WebSocket-only) and stores state in SQLite. It remains functional and its test suite still passes.

```bash
# Run the Python backend (optional, independent of the engine)
cd backend
pip install -r requirements.txt
cp .env.example .env   # set GMGN_ACCESS_TOKEN
uvicorn main:app --reload --port 8000

# Run tests
pytest tests/ -v
```

The Python backend exposes `/api/tokens`, `/api/filters/apply`, `/api/smart_money`, and `/api/stream` on port 8000.

---

## Legal

GMGN does not publish an official public API. This project uses the official GMGN Agent API ([docs.gmgn.ai](https://docs.gmgn.ai)) which requires an approved access token. Review GMGN's Terms of Service before use. Rate-limit aggressively and do not use this software to disrupt their service.
