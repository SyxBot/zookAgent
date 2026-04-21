# solana-listener

**Layer 1 – Data Ingestion (Listening Layer)**

Real-time Solana on-chain event listener powered by the [Helius](https://helius.dev) WebSocket API. This is the foundation layer of a multi-layer Solana alpha detection system. It collects and normalizes raw on-chain events — no filtering, no scoring, no trading logic.

---

## Project Structure

```
solana-listener/
├── listeners/
│   └── helius.js      # WebSocket connection, subscription, normalization
├── core/
│   └── eventBus.js    # Node.js EventEmitter singleton
├── config.js          # Env-based configuration
├── index.js           # Entry point
├── package.json
├── .env.example
├── .gitignore
└── README.md
```

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/solana-listener.git
cd solana-listener
```

### 2. Install dependencies

```bash
npm install
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and add your Helius API key:

```
HELIUS_API_KEY=your_api_key_here
```

Get a free API key at [https://helius.dev](https://helius.dev).

---

## Running

```bash
npm start
```

---

## Environment Variables

| Variable         | Required | Description                        |
|------------------|----------|------------------------------------|
| `HELIUS_API_KEY` | Yes      | Your Helius API key                |

---

## How It Works

1. `index.js` starts the Helius listener and subscribes to `solana:event` on the event bus.
2. `listeners/helius.js` opens a WebSocket to Helius, sends a `transactionSubscribe` RPC call, and streams confirmed transactions.
3. Each raw message is normalized into a standard event shape and emitted via `eventBus`.
4. If the WebSocket disconnects, the listener auto-reconnects with exponential backoff (1s → 2s → 4s … up to 30s).

---

## Event Format

Every emitted event follows this schema:

```json
{
  "timestamp": 1713700000000,
  "signature": "5KQv...abc",
  "event_type": "swap",
  "token_mint": "So11111111111111111111111111111111111111112",
  "wallet": "9xQe...xyz",
  "amount": 1.5,
  "raw": { "...": "full Helius payload" }
}
```

| Field        | Type                                                        | Description                        |
|--------------|-------------------------------------------------------------|------------------------------------|
| `timestamp`  | `number`                                                    | Unix ms                            |
| `signature`  | `string`                                                    | Transaction signature              |
| `event_type` | `"swap" \| "transfer" \| "mint" \| "liquidity_add" \| "unknown"` | Normalized activity type |
| `token_mint` | `string \| null`                                            | Primary token mint address         |
| `wallet`     | `string \| null`                                            | Fee payer / initiating wallet      |
| `amount`     | `number \| null`                                            | Token amount or SOL amount         |
| `raw`        | `object`                                                    | Original Helius payload            |

---

## Example Output

```
[Helius] Starting listener...
[Helius] WebSocket connected
[Event] {
  "timestamp": 1713700123456,
  "signature": "5KQvP3rMnT...aB1",
  "event_type": "swap",
  "token_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "wallet": "9xQeWvG1...xyz",
  "amount": 42.5,
  "raw": { ... }
}
```

---

## Architecture Notes

- **No external API calls** beyond the Helius WebSocket stream.
- **No database writes** — pure in-memory event emission.
- **Downstream layers** (filtering, scoring, trading) subscribe to `solana:event` on the exported `eventBus`.

---

## Git Setup

```bash
git init
git add .
git commit -m "initial commit - solana listener layer 1"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/solana-listener.git
git push -u origin main
```

---

## License

MIT
