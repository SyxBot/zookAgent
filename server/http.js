import { createServer }                from 'http'
import { readFileSync }                from 'fs'
import { fileURLToPath }               from 'url'
import path                            from 'path'
import { addSubscriber, getRecentSignals, getSubscriberCount } from '../output/alertSystem.js'
import { getStats }                    from '../core/eventProcessor.js'
import { getTokenCount }               from '../state/tokenState.js'
import { getWalletCount }              from '../state/walletState.js'
import { CONFIG }                      from '../config/index.js'
import { addRugMint }                  from '../filters/fastReject.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND  = path.resolve(__dirname, '../frontend')

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css':  'text/css',
  '.js':   'application/javascript',
  '.ico':  'image/x-icon',
  '.png':  'image/png',
}

// ── Router ────────────────────────────────────────────────────────────────────

function route(req, res) {
  const url = new URL(req.url, `http://localhost`)
  res.setHeader('Access-Control-Allow-Origin', '*')

  // ── SSE stream ─────────────────────────────────────────────────────────
  if (req.method === 'GET' && url.pathname === '/api/stream') {
    res.writeHead(200, {
      'Content-Type':      'text/event-stream',
      'Cache-Control':     'no-cache',
      'X-Accel-Buffering': 'no',
      'Connection':        'keep-alive',
    })
    res.write(': connected\n\n')
    addSubscriber(res)

    const hb = setInterval(() => {
      try { res.write(': heartbeat\n\n') } catch { clearInterval(hb) }
    }, 30_000)
    req.on('close', () => clearInterval(hb))
    return
  }

  // ── REST: recent signals ────────────────────────────────────────────────
  if (req.method === 'GET' && url.pathname === '/api/signals') {
    const limit = Math.min(parseInt(url.searchParams.get('limit') || '50'), 200)
    return json(res, getRecentSignals(limit))
  }

  // ── REST: pipeline stats ────────────────────────────────────────────────
  if (req.method === 'GET' && url.pathname === '/api/stats') {
    return json(res, {
      pipeline:    getStats(),
      state:       { tokens: getTokenCount(), wallets: getWalletCount() },
      subscribers: getSubscriberCount(),
    })
  }

  // ── REST: add rug mint (runtime blocklist update) ───────────────────────
  if (req.method === 'POST' && url.pathname === '/api/rug') {
    let body = ''
    req.on('data', c => (body += c))
    req.on('end', () => {
      try {
        const { mint } = JSON.parse(body)
        if (mint) { addRugMint(mint); return json(res, { ok: true }) }
        error(res, 400, 'Missing mint field')
      } catch { error(res, 400, 'Invalid JSON') }
    })
    return
  }

  // ── Health check ────────────────────────────────────────────────────────
  if (req.method === 'GET' && url.pathname === '/health') {
    return json(res, { status: 'ok', uptime: Math.round(process.uptime()) })
  }

  // ── Static frontend files ───────────────────────────────────────────────
  if (req.method === 'GET') {
    const safePath = url.pathname === '/' ? '/index.html' : url.pathname
    const filePath = path.join(FRONTEND, safePath.replace(/\.\./g, ''))
    const ext      = path.extname(filePath)

    try {
      const content = readFileSync(filePath)
      res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' })
      res.end(content)
    } catch {
      error(res, 404, 'Not found')
    }
    return
  }

  error(res, 405, 'Method not allowed')
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function json(res, data) {
  res.writeHead(200, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(data))
}

function error(res, code, msg) {
  res.writeHead(code, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify({ error: msg }))
}

// ── Exports ───────────────────────────────────────────────────────────────────

export function createHTTPServer() {
  const server = createServer(route)
  server.listen(CONFIG.PORT, () => {
    console.log(`[HTTP] Listening on http://localhost:${CONFIG.PORT}`)
  })
  return server
}
