/* Thin API client.
 *
 * Two deliberate properties:
 *  - the bearer token lives in module scope, not localStorage, so a stolen
 *    XSS payload cannot read it back out of storage after the tab closes;
 *  - every error is surfaced as a real Error carrying the RFC 9457 problem
 *    body, so the UI can show the server's own wording instead of inventing
 *    a friendlier lie.
 */

const BASE = '/api/v1'

let token = null
let onUnauthorized = () => {}

export function setToken(value) { token = value }
export function getToken() { return token }
export function setUnauthorizedHandler(fn) { onUnauthorized = fn }

async function request(path, { method = 'GET', body, signal } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })

  if (response.status === 401) {
    onUnauthorized()
    throw new Error('Your session has expired. Please sign in again.')
  }

  const text = await response.text()
  const payload = text ? JSON.parse(text) : null

  if (!response.ok) {
    const error = new Error(payload?.detail || payload?.title || `Request failed (${response.status})`)
    error.problem = payload
    error.status = response.status
    throw error
  }
  return payload
}

export const api = {
  login: (username, password) => request('/auth/login', { method: 'POST', body: { username, password } }),
  me: () => request('/auth/me'),
  demoAccounts: () => request('/auth/demo-accounts'),
  capabilities: () => request('/capabilities'),
  health: () => request('/health'),

  chat: (message, sessionId, language, options, wantAudio = false) =>
    request('/chat', {
      method: 'POST',
      body: {
        message, session_id: sessionId, language: language || null,
        options: options || {}, want_audio: wantAudio,
      },
    }),

  /* Fetch an owner-scoped artefact as an object URL.
   *
   * `/files/...` authorizes the caller, so the bearer token has to travel with
   * the request. A bare <audio src="/files/..."> cannot carry it, which is why
   * audio is fetched here and handed to the player as a blob. */
  fetchBlobUrl: async (url) => {
    const headers = {}
    if (token) headers.Authorization = `Bearer ${token}`
    const response = await fetch(url, { headers })
    if (response.status === 401) {
      onUnauthorized()
      throw new Error('Your session has expired. Please sign in again.')
    }
    if (!response.ok) throw new Error(`Could not load audio (${response.status})`)
    return URL.createObjectURL(await response.blob())
  },

  /* Live speech-to-text over a WebSocket.
   *
   * The token travels in the first frame, not the query string: a browser
   * cannot set headers on a WebSocket handshake, and a bearer token in a URL
   * ends up in proxy logs and browser history.
   *
   * Returns a handle with `send(chunk)` and `stop()`. `onPartial` is called as
   * transcripts firm up; `onError` fires once, and the caller falls back to the
   * browser recogniser. */
  streamAsr: ({ language = 'kn', mimeType = 'audio/webm', onPartial, onFinal, onError }) => {
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${scheme}://${window.location.host}${BASE}/voice/stream`)
    socket.binaryType = 'arraybuffer'
    let ready = false

    socket.onopen = () => {
      socket.send(JSON.stringify({ token, language, mime_type: mimeType }))
    }
    socket.onmessage = (event) => {
      let payload
      try { payload = JSON.parse(event.data) } catch { return }
      if (payload.error) { onError?.(payload.error); return }
      if (payload.ready) { ready = true; return }
      if (payload.is_final) onFinal?.(payload.text || '')
      else onPartial?.(payload.text || '')
    }
    socket.onerror = () => onError?.('The live transcription connection failed.')

    return {
      get ready() { return ready },
      send: (chunk) => { if (socket.readyState === WebSocket.OPEN && ready) socket.send(chunk) },
      stop: () => {
        if (socket.readyState === WebSocket.OPEN) socket.send('"stop"')
        setTimeout(() => socket.close(), 400)
      },
    }
  },

  /* Server-side speech recognition. Used only when the deployment reports a
   * full-fidelity provider; otherwise the console stays on the browser's own
   * recogniser, which works offline. */
  transcribe: async (blob, language) => {
    const headers = {}
    if (token) headers.Authorization = `Bearer ${token}`
    const form = new FormData()
    form.append('audio', blob, 'utterance.webm')
    form.append('language', language || 'kn')
    const response = await fetch(`${BASE}/chat/transcribe`, { method: 'POST', headers, body: form })
    const payload = await response.json().catch(() => null)
    if (!response.ok) throw new Error(payload?.detail || `Transcription failed (${response.status})`)
    return payload
  },
  transcript: (sessionId) => request(`/chat/${encodeURIComponent(sessionId)}/transcript`),
  sessions: () => request('/chat/sessions'),

  searchCases: (filters) => request('/cases/search', { method: 'POST', body: filters }),
  caseDetail: (crimeNo) => request(`/cases/${encodeURIComponent(crimeNo)}`),
  similarCases: (crimeNo) => request(`/cases/${encodeURIComponent(crimeNo)}/similar`),
  masters: () => request('/cases/reference/masters'),

  trend: (body) => request('/analytics/trend', { method: 'POST', body }),
  hotspots: (body) => request('/analytics/hotspots', { method: 'POST', body }),
  earlyWarning: () => request('/analytics/early-warning'),
  sociology: (body) => request('/analytics/sociology', { method: 'POST', body }),
  summary: () => request('/analytics/summary'),

  expandGraph: (body) => request('/graph/expand', { method: 'POST', body }),
  graphPath: (body) => request('/graph/path', { method: 'POST', body }),
  offenders: (limit = 15) => request(`/graph/offenders?limit=${limit}`),
  communities: () => request('/graph/communities'),
  reviewQueue: () => request('/graph/entity-resolution/review'),
  decideReview: (body) => request('/graph/entity-resolution/review', { method: 'POST', body }),
  financial: (identityId) => request(`/graph/financial/${encodeURIComponent(identityId)}`),

  briefing: (crimeNo) => request(`/investigation/briefing/${encodeURIComponent(crimeNo)}`),
  priority: (limit = 15) => request(`/investigation/priority?limit=${limit}`),

  exportPdf: (body) => request('/export/pdf', { method: 'POST', body }),

  pipeline: () => request('/admin/pipeline'),
  dataQuality: () => request('/admin/data-quality'),
  audit: () => request('/admin/audit'),
  llmUsage: () => request('/admin/llm-usage'),
  seed: (body) => request('/admin/seed', { method: 'POST', body }),
}
