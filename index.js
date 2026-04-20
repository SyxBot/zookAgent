import 'dotenv/config'
import { CONFIG } from './config/index.js'
import { createGMGNStream } from './ingestion/websocket.js'
import { createHTTPServer } from './server/http.js'

console.log('zookAgent Engine v2 starting...')

if (!CONFIG.GMGN_ACCESS_TOKEN) {
  console.warn('[WARN] GMGN_ACCESS_TOKEN not set — stream will not connect to GMGN')
}

createHTTPServer()
createGMGNStream()

process.on('SIGINT', () => {
  console.log('\n[SHUTDOWN] Stopping engine')
  process.exit(0)
})
