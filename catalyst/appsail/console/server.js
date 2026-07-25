/* AppSail static host for the console.
 *
 * Only two responsibilities: serve `frontend/dist`, and forward `/api` to the
 * function so the browser sees a single origin. Anything else belongs in the
 * backend, not here.
 */
const http = require('http')
const https = require('https')
const fs = require('fs')
const path = require('path')

const PORT = process.env.X_ZOHO_CATALYST_LISTEN_PORT || 9000
const DIST = path.resolve(__dirname, '../../../frontend/dist')
const API_TARGET = process.env.CIP_API_URL

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.json': 'application/json',
  '.woff2': 'font/woff2',
}

http.createServer((req, res) => {
  if (req.url.startsWith('/api')) {
    if (!API_TARGET) {
      res.writeHead(502, { 'Content-Type': 'application/problem+json' })
      res.end(JSON.stringify({ title: 'API target not configured', status: 502 }))
      return
    }
    const target = new URL(req.url, API_TARGET)
    const proxied = https.request(target, { method: req.method, headers: req.headers }, (upstream) => {
      res.writeHead(upstream.statusCode, upstream.headers)
      upstream.pipe(res)
    })
    proxied.on('error', () => {
      res.writeHead(502, { 'Content-Type': 'application/problem+json' })
      res.end(JSON.stringify({ title: 'Upstream API unavailable', status: 502 }))
    })
    req.pipe(proxied)
    return
  }

  const requested = req.url.split('?')[0]
  const candidate = path.join(DIST, requested === '/' ? 'index.html' : requested)
  const resolved = candidate.startsWith(DIST) ? candidate : path.join(DIST, 'index.html')

  fs.readFile(resolved, (error, body) => {
    if (error) {
      // Unknown paths fall back to index.html so client routing works.
      fs.readFile(path.join(DIST, 'index.html'), (fallbackError, fallback) => {
        if (fallbackError) { res.writeHead(404); res.end('Not found'); return }
        res.writeHead(200, { 'Content-Type': TYPES['.html'] })
        res.end(fallback)
      })
      return
    }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(resolved)] || 'application/octet-stream' })
    res.end(body)
  })
}).listen(PORT, () => console.log(`cip-console listening on ${PORT}`))
