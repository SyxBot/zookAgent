// zookAgent Engine v2 — Frontend
// Uses relative URLs so it works whether served by the Node.js engine or a dev server.

const MAX_CARDS    = 100       // max signal cards to keep in DOM
const STATS_POLL   = 5_000     // ms between stats fetches

// ── State ─────────────────────────────────────────────────────────────────────
let signals   = []             // ordered array of signal objects
let threshold = 60             // client-side score filter
let es        = null

// ── SSE Connection ────────────────────────────────────────────────────────────
function connectSSE() {
  setStatus('connecting', 'Connecting…')
  es = new EventSource('/api/stream')

  es.onopen = () => setStatus('connected', 'Live')

  es.onmessage = (e) => {
    let msg
    try { msg = JSON.parse(e.data) } catch { return }

    if (msg.type === 'signal')   handleSignal(msg.signal)
    if (msg.type === 'backfill') msg.signals.forEach(handleSignal)
  }

  es.onerror = () => {
    setStatus('disconnected', 'Disconnected')
    es.close()
    setTimeout(connectSSE, 5_000)
  }
}

// ── Signal handling ───────────────────────────────────────────────────────────
function handleSignal(signal) {
  if (signal.score < threshold) return   // client-side threshold filter

  // Deduplicate: update in-place if same token arrives again
  const idx = signals.findIndex(s => s.token === signal.token)
  if (idx !== -1) {
    signals[idx] = signal
    const existing = document.getElementById(`card-${esc(signal.token)}`)
    if (existing) { existing.replaceWith(buildCard(signal)); return }
  } else {
    signals.unshift(signal)
    if (signals.length > MAX_CARDS) signals.pop()
  }

  renderSignals()
}

// ── Render ────────────────────────────────────────────────────────────────────
function renderSignals() {
  const grid  = document.getElementById('signal-grid')
  const empty = document.getElementById('empty-msg')
  const visible = signals.filter(s => s.score >= threshold)

  empty.style.display = visible.length ? 'none' : 'block'
  grid.innerHTML = ''
  visible.forEach(s => grid.appendChild(buildCard(s)))
}

function buildCard(s) {
  const scoreClass = s.score >= 75 ? 'high' : s.score >= 60 ? 'medium' : 'low'
  const el = document.createElement('div')
  el.className = `signal-card score-${scoreClass}`
  el.id = `card-${esc(s.token)}`

  el.innerHTML = `
    <div class="card-top">
      <div>
        <div class="card-symbol">${esc(s.symbol || '???')}</div>
        <div class="card-name">${esc(s.name || s.token.slice(0, 8) + '…')}</div>
      </div>
      <div class="score-badge ${scoreClass === 'high' ? 'hi' : scoreClass === 'medium' ? 'mid' : 'lo'}">
        ${s.score}
      </div>
    </div>

    <div class="card-meta">
      ${setupBadge(s.setup)}
      ${confBadge(s.confidence)}
      ${riskBadge(s.risk)}
    </div>

    ${s.reasons?.length ? `
    <div class="card-reasons">
      ${s.reasons.map(r => `<span>${esc(r)}</span>`).join('')}
    </div>` : ''}

    <div class="card-footer">
      <span class="card-mint" title="${esc(s.token)}">${esc(s.token)}</span>
      <span>${relTime(s.timestamp)}</span>
    </div>
  `
  return el
}

// ── Badge builders ────────────────────────────────────────────────────────────
function setupBadge(setup) {
  const cls = setup === 'EARLY_SNIPER' ? 'setup-early'
            : setup === 'MOMENTUM'     ? 'setup-mom'
            :                           'setup-late'
  return `<span class="badge ${cls}">${esc(setup || '—')}</span>`
}
function confBadge(c) {
  const cls = c === 'HIGH' ? 'conf-high' : c === 'MEDIUM' ? 'conf-medium' : 'conf-low'
  return `<span class="badge ${cls}">${esc(c || '—')}</span>`
}
function riskBadge(r) {
  const cls = r === 'LOW' ? 'risk-low' : r === 'MEDIUM' ? 'risk-medium' : 'risk-high'
  return `<span class="badge ${cls}">Risk: ${esc(r || '—')}</span>`
}

// ── Stats polling ─────────────────────────────────────────────────────────────
async function fetchStats() {
  try {
    const r = await fetch('/api/stats')
    if (!r.ok) return
    const { pipeline: p, state: st } = await r.json()

    document.getElementById('hs-total').textContent   = fmtNum(p.total)
    document.getElementById('hs-reject').textContent  = p.rejectRatePct + '%'
    document.getElementById('hs-scored').textContent  = fmtNum(p.scored)
    document.getElementById('hs-emitted').textContent = fmtNum(p.emitted)
    document.getElementById('hs-tokens').textContent  = fmtNum(st.tokens)
    document.getElementById('hs-wallets').textContent = fmtNum(st.wallets)
  } catch { /* ignore */ }
}

// ── Controls ──────────────────────────────────────────────────────────────────
const slider = document.getElementById('score-threshold')
const thVal  = document.getElementById('threshold-val')

slider.addEventListener('input', () => {
  threshold = parseInt(slider.value)
  thVal.textContent = threshold
  renderSignals()
})

document.getElementById('clear-btn').addEventListener('click', () => {
  signals = []
  renderSignals()
})

// ── Helpers ───────────────────────────────────────────────────────────────────
function setStatus(state, text) {
  const dot  = document.getElementById('status-dot')
  const span = document.getElementById('status-text')
  dot.className  = `dot ${state}`
  span.textContent = text
}

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function fmtNum(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'K'
  return String(n)
}

function relTime(ts) {
  if (!ts) return ''
  const secs = Math.round((Date.now() - ts) / 1_000)
  if (secs < 60)  return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  return `${Math.floor(secs / 3600)}h ago`
}

// ── Boot ──────────────────────────────────────────────────────────────────────
connectSSE()
setInterval(fetchStats, STATS_POLL)
fetchStats()
