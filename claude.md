https://docs.gmgn.ai/index/~gitbook/mcp

# GMGN Agent API

### I. Why choose GMGN AI Agent?

⚡️ Real-time On-chain Data\
&#x20;· Provides multi-chain real-time market quotes and liquidity pool status, enabling your AI Agent to capture market opportunities with zero latency

🔍 Comprehensive Exclusive Data \
· The out-of-the-box API eliminates the need for tedious low-level data parsing. It directly outputs advanced research metrics, instantly identifying Snipers, Insiders, Bundled wallets, and more

🛡️ Secure and Convenient Trading \
· Adopts the GMGN hosted wallet architecture where private keys are not stored locally, eliminating leakage risks at the source. IP whitelist automatically intercepts abnormal trading requests, and developers no longer need to manually construct complex on-chain transactions

***

Here is the English translation, perfectly formatted for your GitBook documentation. I have also cleaned up the random drag-and-drop text from your copy-paste.

***

### II. Run Your First GMGN AI Agent

Empower your AI Agent with on-chain capabilities — query quotes, fetch market data, and execute Swap transactions, all entirely through natural language.

#### Quick Start: Get API Key and Key Pair

Before using, please complete the following preparations:

* Generate a Key Pair: Use an asymmetric key generator to locally generate a key pair (**Public & Private Key**). Generation tutorial: [🔑 Generate Public Key](https://docs.gmgn.ai/index/generate-public-key)
* Create an API Key: Go to 👉 <https://gmgn.ai/ai>, upload your public key, and create your exclusive GMGN API Key. (*Note: Only IPv4 requests are supported; IPv6 is currently not supported.*)

The permissions required for using Skills are as follows:

| Skill                                         | Required Credentials    |
| --------------------------------------------- | ----------------------- |
| Query (`gmgn-market` / `token` / `portfolio`) | ✅ API Key               |
| Trading (`gmgn-swap`)                         | ✅ API Key + Private Key |

***

#### 1. Add GMGN Skills to Your Agent

Copy and send the following prompt to OpenClaw, Claude Code, or other AI Agent tools:

```
Install GMGN skills by running: npx skills add GMGNAI/gmgn-skills
```

***

#### 2. Configure API Key and Private Key

Send the following prompt to your AI for global configuration. Once the Agent replies, fill in your **API Key and Private Key** as needed:

```
Create a .env file in the ~/.config/gmgn/ directory containing the GMGN_API_KEY variable. 
If trading features are needed, please also add the GMGN_PRIVATE_KEY variable. 
Open this file for me, remind me to paste the actual GMGN API Key and locally generated private key, and save it. Note: Please ensure this file is not committed to git.
```

> ⚠️ Note: Never commit your `.env` file to git (please add it to `.gitignore`), and absolutely never expose your private key in chats, logs, or screenshots. The private key used when executing a Swap must belong to the exact same key pair as the public key you uploaded when creating the API Key

***

#### 3. Try It!

Once configured, send the following prompt directly to test the query capabilities:

```
Get the candlestick data for 6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN and analyze if it's worth buying.
```

***

#### Usage:

Once set up, you can execute tasks using just natural language:

> * `"Buy 0.1 SOL of <token_address>"`
> * `"Sell 50% of <token_address> "`
> * `"Get the candlestick data for <token_address> and analyze if it's worth buying"`
> * `"Check the contract security and pool status for <token_address>"`
> * `"Check the recent trading activity of  <wallet_address>"`
> * `"Show the holders of <token_address> "`

***

### III. What are GMGN MCP & Skills?

#### Introduction

GMGN MCP & Skills is an on-chain development suite built specifically for AI Agents. It enables Agents to natively read multi-chain market data, analyze tokens across multiple dimensions, manage wallet portfolios, and execute Swap transactions directly

#### Flexible Integration Methods

* AI Skills — Built-in system-level commands like `/gmgn-token` and `/gmgn-market`. Agents can call them directly via natural language with zero API integration cost
* CLI Toolkit — Provides the ready-to-use `npx gmgn-cli` terminal command, supporting formatted JSON or single-line JSON (`--raw`) output, perfectly suited for script processing

#### Core Capabilities

GMGN's underlying capabilities cover the most essential on-chain interaction scenarios:

* 🪙 Token: Query basic info, real-time prices, contract security, liquidity pool status, and profiles of Top Holders and Traders
* 📈 Market: Fetch precise candlestick data, supporting multiple resolutions from `1m` to `1d`&#x20;
* 🔥 Trending: Fetch the current trending tokens list
* 💰 Portfolio: Query wallet holdings and token balances, and track historical transaction activity and PnL data
* 🔄 Swap: Submit token exchange transactions via optimal routing, support custom slippage, and poll on-chain order status in real-time (requires private key configuration)

***

### **IV. Supported Chains**

Integration for ETH and other new chains is currently in progress...

| Supported Chains | Trading Base Tokens |
| ---------------- | ------------------- |
| SOL              | SOL, USDC           |
| BSC              | BNB, USDC           |
| Base             | ETH, USDC           |

> ⚠️ Security & Disclaimer:
>
> While using GMGN Skills, your AI Agent may make mistakes. Therefore, before authorizing the AI Agent to execute queries or on-chain asset trading commands (such as a `swap`), please ensure you manually verify key parameters like the token contract, amount, and slippage. Any unexpected risks caused by the AI model must be evaluated and borne solely by the user
claude mcp add gmgn-tutorial --scope user --transport http https://docs.gmgn.ai/index/~gitbook/mcp