/* AppSail static host for the console.
 *
 * Only two responsibilities: serve the built React console, and forward
 * `/api` to the cip-api AppSail service (see
 * docs/deployment/catalyst-runtime.md for why this is an AppSail service and
 * not a Function) so the browser sees a single origin. Anything else belongs
 * in the backend, not here.
 */
const http = require('http')
const https = require('https')
const fs = require('fs')
const path = require('path')

const PORT = process.env.X_ZOHO_CATALYST_LISTEN_PORT || 9000
// Two layouts, resolved the same way `catalyst/_bootstrap.py` resolves the
// API's package path: a `dist` sibling staged by
// `scripts/build_catalyst_artifact.py --target console` (deployment), or the
// repo-relative `frontend/dist` when running this file straight out of the
// checkout (local dev, e.g. `scripts/dev.sh` / the D1 console smoke test).
// Don't collapse this to one path -- the staged layout has no repo above it.
const STAGED_DIST = path.resolve(__dirname, 'dist')
const CHECKOUT_DIST = path.resolve(__dirname, '../../../frontend/dist')
const DIST = fs.existsSync(path.join(STAGED_DIST, 'index.html')) ? STAGED_DIST : CHECKOUT_DIST
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
    // An internal AppSail-to-AppSail call is not guaranteed to be TLS the way
    // a public endpoint is, so the client module must match the target's own
    // scheme rather than always assuming https.
    const client = target.protocol === 'http:' ? http : https
    // The inbound Host names *this* service. Forwarding it verbatim to a
    // different origin makes AppSail's front end reject the request with a
    // bare 400 before it reaches the API at all. Locally both sides were
    // 127.0.0.1, so this only shows up once the two are really separate hosts.
    const headers = { ...req.headers, host: target.host }
    const proxied = client.request(target, { method: req.method, headers }, (upstream) => {
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
