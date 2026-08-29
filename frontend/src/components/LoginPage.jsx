import { useEffect, useState } from 'react'
import { api } from '../lib/api.js'
import { renderCatalystSignIn } from '../lib/catalystAuth.js'

/* Demo accounts are listed openly because this is a synthetic-data build and
 * the roles are the point: the reviewer should be able to sign in as a station
 * investigator and watch the same question return a narrower answer. */
export default function LoginPage({ onAuthenticated }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [accounts, setAccounts] = useState([])
  const [demoPassword, setDemoPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  // null = not yet decided, false = use our own form, true = Catalyst rendered.
  const [catalystSignIn, setCatalystSignIn] = useState(null)

  /* Catalyst Authentication is used only when the deployment says it is the
   * identity backend AND its Web SDK genuinely loaded. Either condition
   * failing leaves the credential form exactly as it was -- a reviewer must
   * never meet a dead sign-in page because a CDN was unreachable. */
  useEffect(() => {
    let cancelled = false
    api.capabilities()
      .then(async (caps) => {
        if (cancelled) return
        if (caps?.identity_backend !== 'catalyst') return setCatalystSignIn(false)
        const rendered = await renderCatalystSignIn('catalystSignInHost')
        if (!cancelled) setCatalystSignIn(rendered)
      })
      .catch(() => { if (!cancelled) setCatalystSignIn(false) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    api.demoAccounts()
      .then((data) => {
        setAccounts(data.accounts || [])
        setDemoPassword(data.password || '')
      })
      .catch(() => setAccounts([]))
  }, [])

  async function submit(event) {
    event?.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const result = await api.login(username.trim(), password)
      onAuthenticated(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  function useAccount(account) {
    setUsername(account.username)
    setPassword(demoPassword)
    setError(null)
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <h1>Crime Intelligence Console</h1>
        <div className="sub">Karnataka State Police</div>

        {error && <div className="error-note">{error}</div>}

        <div id="catalystSignInHost" hidden={catalystSignIn !== true} />

        {catalystSignIn !== true && (
        <>
        <div className="field">
          <label htmlFor="username">Username</label>
          <input id="username" value={username} autoComplete="username"
                 onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input id="password" type="password" value={password} autoComplete="current-password"
                 onChange={(e) => setPassword(e.target.value)} />
        </div>

        <button className="btn" style={{ width: '100%', marginTop: 6 }} disabled={busy || !username || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        </>
        )}

        {accounts.length > 0 && (
          <div className="demo-accounts">
            <h2>Demonstration accounts</h2>
            {accounts.map((account) => (
              <button type="button" key={account.username} onClick={() => useAccount(account)}>
                {account.display_name}
                <div className="role">{account.username} · {account.role}</div>
              </button>
            ))}
          </div>
        )}

        <div className="notice" style={{ marginTop: 18 }}>
          All data in this build is synthetic. Nothing here is a real case, a real person, or a
          real financial record.
        </div>
      </form>
    </div>
  )
}
