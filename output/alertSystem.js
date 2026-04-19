/**
 * LAYER 6 — OUTPUT / ALERT SYSTEM
 *
 * - Maintains a ring buffer of recent signals (for REST backfill)
 * - Fans scored signals out to all active SSE subscribers
 * - Exposes an EventEmitter bus for in-process listeners
 */
import { EventEmitter } from 'events'

export const bus = new EventEmitter()
bus.setMaxListeners(100)

const MAX_SIGNALS = 200
const recentSignals = []

// Set of active Express response objects (SSE connections)
const subscribers = new Set()

/**
 * Register a new SSE subscriber.
 * Immediately backfills the last 10 signals so the client has context.
 */
export function addSubscriber(res) {
  subscribers.add(res)
  res.on('close', () => subscribers.delete(res))

  // Backfill — send last 10 signals
  const backfill = recentSignals.slice(-10)
  if (backfill.length > 0) {
    res.write(`data: ${JSON.stringify({ type: 'backfill', signals: backfill })}\n\n`)
  }
}

/**
 * Emit a scored signal to all subscribers and store in ring buffer.
 *
 * @param {object} signal  — { token, symbol, score, confidence, setup, risk, reasons }
 */
export function emit(signal) {
  const entry = { ...signal, timestamp: Date.now() }

  recentSignals.push(entry)
  if (recentSignals.length > MAX_SIGNALS) recentSignals.shift()

  // SSE push
  const payload = `data: ${JSON.stringify({ type: 'signal', signal: entry })}\n\n`
  for (const res of subscribers) {
    try {
      res.write(payload)
    } catch {
      subscribers.delete(res)
    }
  }

  bus.emit('signal', entry)
}

export function getRecentSignals(limit = 50) {
  return recentSignals.slice(-Math.min(limit, MAX_SIGNALS))
}

export function getSubscriberCount() {
  return subscribers.size
}
