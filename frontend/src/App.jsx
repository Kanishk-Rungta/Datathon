import { useCallback, useEffect, useState } from 'react'
import ChatPanel from './components/ChatPanel.jsx'
import Inspector from './components/Inspector.jsx'
import LoginPage from './components/LoginPage.jsx'
import { api, setToken, setUnauthorizedHandler } from './lib/api.js'

function newSessionId() {
  return `s-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`
}

export default function App() {
  const [principal, setPrincipal] = useState(null)
  const [sessionId] = useState(newSessionId)
  const [turns, setTurns] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [language, setLanguage] = useState('en')
  const [activeLocator, setActiveLocator] = useState(null)
  const [capabilities, setCapabilities] = useState(null)

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setToken(null)
      setPrincipal(null)
    })
  }, [])

  useEffect(() => {
    if (!principal) return
    api.capabilities().then(setCapabilities).catch(() => setCapabilities(null))
  }, [principal])

  const handleAuthenticated = useCallback((result) => {
    setToken(result.access_token)
    setPrincipal(result.principal)
  }, [])

  const send = useCallback(async (message) => {
    setTurns((current) => [...current, { role: 'user', text: message }])
    setBusy(true)
    setError(null)
    try {
      const answer = await api.chat(message, sessionId, language)
      setTurns((current) => [...current, { role: 'agent', answer }])
      setActiveLocator(answer.evidence?.[0]?.locator || null)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }, [sessionId, language])

  const exportPdf = useCallback(async () => {
    try {
      const result = await api.exportPdf({ session_id: sessionId })
      window.open(result.url, '_blank', 'noopener')
    } catch (err) {
      setError(err.message)
    }
  }, [sessionId])

  if (!principal) return <LoginPage onAuthenticated={handleAuthenticated} />

  const lastAnswer = [...turns].reverse().find((turn) => turn.role === 'agent')?.answer || null

  return (
    <div className="shell">
      <header className="masthead">
        <h1 className="masthead__title">Crime Intelligence Console</h1>
        <span className="masthead__sub">Karnataka State Police</span>
        <div className="masthead__spacer" />
        <div className="masthead__meta">
          <span>{principal.display_name}</span>
          <span className="pill">{principal.role}</span>
          <span>{principal.scope_summary}</span>
          <span className="classification">Synthetic data</span>
          <button className="linkish" onClick={() => { setToken(null); setPrincipal(null) }}>Sign out</button>
        </div>
      </header>

      <div className="workspace">
        <ChatPanel
          turns={turns}
          busy={busy}
          error={error}
          language={language}
          onLanguageChange={setLanguage}
          onSend={send}
          onSelectEvidence={setActiveLocator}
          activeLocator={activeLocator}
          onExport={exportPdf}
        />
        <Inspector
          answer={lastAnswer}
          activeLocator={activeLocator}
          onSelectEvidence={setActiveLocator}
          principal={principal}
        />
      </div>

      <footer className="status-bar">
        <span>session {sessionId}</span>
        <span>agents: supervisor + 4 specialists</span>
        {capabilities && <span>language: {capabilities.language_provider}</span>}
        {capabilities && !capabilities.language_full_fidelity && (
          <span title="Kannada is handled by an offline glossary in this build">
            kannada: offline glossary
          </span>
        )}
        <div className="status-bar__spacer" />
        <span>intelligence product — not evidence</span>
      </footer>
    </div>
  )
}
