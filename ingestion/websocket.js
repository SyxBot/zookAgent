import { WebSocket } from 'ws'
import { CONFIG } from '../config/index.js'
import { processEvent } from '../core/eventProcessor.js'

let ws = null
let reconnectAttempt = 0

const SUBSCRIPTIONS = [
  { op: 'subscribe', channel: 'token_launches', chain: 'sol' },
  { op: 'subscribe', channel: 'new_pools',      chain: 'sol' },
  { op: 'subscribe', channel: 'pair_updates',   chain: 'sol' },
]

export function createGMGNStream() {
  const uri = `wss://gmgn.ai/ws?access_token=${CONFIG.GMGN_ACCESS_TOKEN}`
  ws = new WebSocket(uri)

  ws.on('open', () => {
    reconnectAttempt = 0
    console.log('[WS] Connected to GMGN stream')
    for (const sub of SUBSCRIPTIONS) ws.send(JSON.stringify(sub))
  })

  ws.on('message', (raw) => {
    let event
    try { event = JSON.parse(raw) } catch { return }
    // Synchronous: fast, non-blocking on the event loop
    processEvent(event)
  })

  ws.on('close', (code) => {
    console.warn(`[WS] Closed (code ${code}) — scheduling reconnect`)
    scheduleReconnect()
  })

  ws.on('error', (err) => {
    // 'close' fires automatically after 'error' — just log
    console.error('[WS] Error:', err.message)
  })

  return ws
}

export function subscribeWallet(address) {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ op: 'subscribe', channel: 'wallet_trades', wallets: [address] }))
  }
}

function scheduleReconnect() {
  const delay = Math.min(30_000, 1_000 * 2 ** reconnectAttempt)
  reconnectAttempt++
  setTimeout(createGMGNStream, delay)
}
