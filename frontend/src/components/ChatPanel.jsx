import { useCallback, useEffect, useRef, useState } from 'react'
import { AnswerBody, TraceList } from './AnswerBody.jsx'
import { api } from '../lib/api.js'

const SUGGESTIONS_EN = [
  'What are the crime trends in Mysuru this year?',
  'Where are the current hotspots?',
  'Show me any early warning alerts',
  'Who are the repeat offenders in my area?',
  'Break down complainants by occupation',
]

const SUGGESTIONS_KN = [
  'ಮೈಸೂರಿನಲ್ಲಿ ಕಳ್ಳತನ ಪ್ರಕರಣಗಳು',
  'ಬೆಂಗಳೂರು ನಗರದಲ್ಲಿ ಅಪರಾಧ ಪ್ರವೃತ್ತಿ',
]

/* Voice input uses the browser's own speech recognition when it exists, and
 * says plainly when it does not. Sending audio to the server for Bhashini ASR
 * is available too, but the browser path keeps a demo working offline. */
function useSpeechRecognition(language, onResult) {
  const recognitionRef = useRef(null)
  const [supported, setSupported] = useState(false)
  const [listening, setListening] = useState(false)

  useEffect(() => {
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!Recognition) {
      setSupported(false)
      return undefined
    }
    setSupported(true)
    const recognition = new Recognition()
    recognition.continuous = false
    recognition.interimResults = false
    recognition.lang = language === 'kn' ? 'kn-IN' : 'en-IN'
    recognition.onresult = (event) => {
      const transcript = Array.from(event.results).map((result) => result[0].transcript).join(' ')
      onResult(transcript)
    }
    recognition.onend = () => setListening(false)
    recognition.onerror = () => setListening(false)
    recognitionRef.current = recognition
    return () => {
      recognition.onresult = null
      recognition.onend = null
      try { recognition.abort() } catch { /* already stopped */ }
    }
  }, [language, onResult])

  return {
    supported,
    listening,
    toggle: () => {
      const recognition = recognitionRef.current
      if (!recognition) return
      if (listening) {
        recognition.stop()
        setListening(false)
      } else {
        try {
          recognition.start()
          setListening(true)
        } catch { setListening(false) }
      }
    },
  }
}

/* Playback for a synthesised answer.
 *
 * The audio lives behind `/files/`, which authorizes the caller, so it is
 * fetched with the bearer token and handed to the player as a blob rather than
 * set as a plain `src` the browser would request unauthenticated. The object
 * URL is revoked on unmount so a long session does not leak blobs. */
function AnswerAudio({ url }) {
  const [objectUrl, setObjectUrl] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let revoked = null
    let cancelled = false
    api.fetchBlobUrl(url)
      .then((blobUrl) => {
        if (cancelled) { URL.revokeObjectURL(blobUrl); return }
        revoked = blobUrl
        setObjectUrl(blobUrl)
      })
      .catch(() => { if (!cancelled) setFailed(true) })
    return () => {
      cancelled = true
      if (revoked) URL.revokeObjectURL(revoked)
    }
  }, [url])

  // A missing recording must not imply a missing answer.
  if (failed) return <span className="pill">spoken answer unavailable</span>
  if (!objectUrl) return <span className="pill">preparing audio…</span>
  return <audio controls preload="none" src={objectUrl} className="turn__audio" />
}

/* Live server-side dictation.
 *
 * Used only when the deployment reports a full-fidelity speech provider. On
 * any failure it reports back so the caller can fall through to the browser's
 * own recogniser, which needs no server and works offline — a speech outage
 * should cost fidelity, never the ability to dictate at all. */
function useStreamingDictation(language, { onPartial, onFinal, onUnavailable }) {
  const sessionRef = useRef(null)
  const [listening, setListening] = useState(false)

  const stop = useCallback(() => {
    sessionRef.current?.recorder?.stop()
    sessionRef.current?.stream?.getTracks().forEach((track) => track.stop())
    sessionRef.current?.socket?.stop()
    sessionRef.current = null
    setListening(false)
  }, [])

  const start = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' })
      const socket = api.streamAsr({
        language,
        mimeType: 'audio/webm',
        onPartial,
        onFinal: (text) => { onFinal?.(text); stop() },
        onError: (message) => { stop(); onUnavailable?.(message) },
      })
      recorder.ondataavailable = async (event) => {
        if (event.data?.size) socket.send(await event.data.arrayBuffer())
      }
      // Emit roughly every half second so partials feel live.
      recorder.start(500)
      sessionRef.current = { stream, recorder, socket }
      setListening(true)
    } catch {
      onUnavailable?.('Microphone access was refused.')
    }
  }, [language, onPartial, onFinal, onUnavailable, stop])

  useEffect(() => stop, [stop])
  return { listening, start, stop }
}

export default function ChatPanel({
  turns, busy, error, language, onLanguageChange, onSend, onSelectEvidence, activeLocator, onExport,
  serverSpeech = false,
}) {
  const [draft, setDraft] = useState('')
  const scrollRef = useRef(null)
  const appendToDraft = useCallback(
    (text) => setDraft((current) => (current ? `${current} ${text}` : text)),
    [],
  )
  const speech = useSpeechRecognition(language, appendToDraft)

  /* Server dictation is preferred when the deployment has a real provider,
     because the browser recogniser handles Kannada poorly. If it drops out we
     fall back rather than leaving the officer with no microphone at all. */
  const [serverSpeechDown, setServerSpeechDown] = useState(false)
  const [interim, setInterim] = useState('')
  const dictation = useStreamingDictation(language, {
    onPartial: setInterim,
    onFinal: (text) => { setInterim(''); if (text) appendToDraft(text) },
    onUnavailable: () => { setInterim(''); setServerSpeechDown(true) },
  })
  const useServer = serverSpeech && !serverSpeechDown
  const micActive = useServer ? dictation.listening : speech.listening
  const micAvailable = useServer || speech.supported
  const toggleMic = () => {
    if (useServer) { dictation.listening ? dictation.stop() : dictation.start() }
    else speech.toggle()
  }

  useEffect(() => {
    const element = scrollRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [turns.length, busy])

  function submit() {
    const message = draft.trim()
    if (!message || busy) return
    setDraft('')
    onSend(message)
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  const suggestions = language === 'kn' ? SUGGESTIONS_KN : SUGGESTIONS_EN

  return (
    <div className="column column--conversation">
      <div className="transcript" ref={scrollRef}>
        {turns.length === 0 && (
          <div className="empty">
            Ask about cases, trends, locations, people or links.<br />
            Every answer will carry the records it rests on.
          </div>
        )}

        {turns.map((turn, index) => (
          <div className={`turn turn--${turn.role}`} key={index}>
            <div className="turn__role">{turn.role === 'user' ? 'You' : 'Intelligence platform'}</div>

            {turn.role === 'user' ? (
              <div className={`turn__body${/[\u0C80-\u0CFF]/.test(turn.text) ? ' kn' : ''}`}>{turn.text}</div>
            ) : (
              <>
                <AnswerBody
                  answer={turn.answer}
                  onSelectEvidence={onSelectEvidence}
                  activeLocator={activeLocator}
                />
                {turn.answer.needs_clarification && (
                  <div className="notice">{turn.answer.needs_clarification}</div>
                )}
                <div className="turn__meta">
                  <span className="pill">{turn.answer.intent?.replace(/_/g, ' ').toLowerCase()}</span>
                  {(turn.answer.agents_used || []).map((agent) => (
                    <span className="pill" key={agent}>{agent.replace('Agent', '')}</span>
                  ))}
                  <span>{turn.answer.evidence?.length || 0} evidence records</span>
                  <TraceList traces={turn.answer.traces} />
                </div>
                {turn.answer.audio_url && <AnswerAudio url={turn.answer.audio_url} />}
              </>
            )}
          </div>
        ))}

        {busy && (
          <div className="turn turn--agent">
            <div className="turn__role">Intelligence platform</div>
            <div className="thinking"><i /><i /><i /></div>
          </div>
        )}

        {error && <div className="error-note">{error}</div>}
      </div>

      <div className="composer">
        <div className="composer__row">
          <textarea
            value={draft}
            placeholder={language === 'kn' ? 'ನಿಮ್ಮ ಪ್ರಶ್ನೆಯನ್ನು ಟೈಪ್ ಮಾಡಿ…' : 'Ask a question about the case records…'}
            className={language === 'kn' ? 'kn' : ''}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
          />
          {interim && (
            <div className="composer__interim" title="Live transcript — not yet inserted">
              {interim}
            </div>
          )}
          {micAvailable && (
            <button
              type="button"
              className={`btn btn--ghost btn--mic${micActive ? ' btn--recording' : ''}`}
              onClick={toggleMic}
              title={micActive ? 'Stop dictation' : (useServer ? 'Dictate (server speech)' : 'Dictate your question')}
              aria-label="Voice input"
            >
              {micActive ? '◼' : '🎙'}
            </button>
          )}
          <button type="button" className="btn" onClick={submit} disabled={busy || !draft.trim()}>
            Ask
          </button>
        </div>

        <div className="composer__hint">
          <div className="lang-toggle">
            <button type="button" aria-pressed={language !== 'kn'} onClick={() => onLanguageChange('en')}>EN</button>
            <button type="button" aria-pressed={language === 'kn'} onClick={() => onLanguageChange('kn')}>ಕನ್ನಡ</button>
          </div>
          {turns.length === 0
            ? suggestions.map((suggestion) => (
                <button type="button" className={`suggestion${language === 'kn' ? ' kn' : ''}`}
                        key={suggestion} onClick={() => setDraft(suggestion)}>
                  {suggestion}
                </button>
              ))
            : <button type="button" className="suggestion" onClick={onExport}>Export this conversation as PDF</button>}
          {!speech.supported && <span>Dictation needs Chrome or Edge.</span>}
        </div>
      </div>
    </div>
  )
}
