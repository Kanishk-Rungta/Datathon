/* AppSail static host for the console.
 *
 * Only two responsibilities: serve `frontend/dist`, and forward `/api` to the
 * cip-api AppSail service (see docs/deployment/catalyst-runtime.md for why
 * this is an AppSail service and not a Function) so the browser sees a single
 * origin. Anything else belongs in the backend, not here.
 */
const http = require('http')
const https = require('https')
const fs = require('fs')
const path = require('path')

const PORT = process.env.X_ZOHO_CATALYST_LISTEN_PORT || 9000
const API_TARGET = process.env.CIP_API_URL

/* Where the built console lives, resolved in the same spirit as
 * `catalyst/_bootstrap.py`: the staged artifact first, the repo checkout second.
 *
 * Catalyst zips and ships only the directory named by `source` in catalyst.json
 * (`appsail/console`). A path reaching back up to `../../../frontend/dist`
 * therefore resolves on a developer's machine and to nothing at all once
 * deployed -- the same defect P1-03 fixed for the Python entrypoints. So:
 *
 *   1. CIP_CONSOLE_DIST, if an operator names one explicitly;
 *   2. `dist/` beside this file -- what `scripts/build_catalyst_artifact.py
 *      --target console` stages, and the only one that exists once deployed;
 *   3. the repo-relative `frontend/dist`, so `node server.js` still works
 *      straight out of a checkout during development.
 */
function resolveDist() {
  const candidates = [
    process.env.CIP_CONSOLE_DIST,
    path.resolve(__dirname, 'dist'),
    path.resolve(__dirname, '../../../frontend/dist'),
  ].filter(Boolean)
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, 'index.html'))) return candidate
  }
  // Nothing found: keep the staged location so the 404 path names somewhere real.
  return path.resolve(__dirname, 'dist')
}

const DIST = resolveDist()

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
    /* Forward the client's headers, minus `host`.
     *
     * `req.headers.host` is the CONSOLE's hostname. Passing it through means
     * the request arriving at cip-api carries `Host: cip-console-...`, and
     * Catalyst's ingress routes AppSail traffic by hostname -- so it answers
     * 400 Bad Request with a Tomcat error page before the API is ever
     * reached, which this proxy then pipes back verbatim as the console's
     * own response. Deleting the header lets Node derive it from the target
     * URL, which is what any correct reverse proxy does.
     */
    const headers = { ...req.headers }
    delete headers.host
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
  // Compare against DIST *plus a separator*: a bare `startsWith(DIST)` also
  // accepts a sibling whose name merely begins with it (`.../dist-backup/x`
  // starts with `.../dist`), which a `/../dist-backup/x` request reaches.
  const inside = candidate === DIST || candidate.startsWith(DIST + path.sep)
  const resolved = inside ? candidate : path.join(DIST, 'index.html')

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
}).listen(PORT, () => console.log(`cip-console listening on ${PORT}, serving ${DIST}`))
