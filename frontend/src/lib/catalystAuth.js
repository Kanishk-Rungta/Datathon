/*
 * Catalyst Authentication — the identity provider the challenge rules name
 * (#17), wired so it can only ever be additive.
 *
 * The Web SDK is not a plain npm import. It needs two scripts that only exist
 * when the page is served from a Catalyst-hosted origin:
 *
 *   https://static.zohocdn.com/catalyst/sdk/js/<v>/catalystWebSDK.js
 *   /__catalyst/sdk/init.js      <- injected by Catalyst hosting, project-bound
 *
 * The second is why this cannot be exercised locally: served from Vite there
 * is no /__catalyst, so `catalyst.auth` never appears. Rather than guess at a
 * shim, isCatalystAuthAvailable() reports plainly whether the SDK is really
 * present, and the console falls back to its own credential form. The division
 * of responsibility is unchanged either way: Catalyst decides *who* the person
 * is, KSP-CIP decides *what they may see* from cip_user_account -- never from
 * a claim in the token.
 */

const SDK_URL = 'https://static.zohocdn.com/catalyst/sdk/js/4.6.2/catalystWebSDK.js'
const INIT_URL = '/__catalyst/sdk/init.js'

function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) return resolve()
    const el = document.createElement('script')
    el.src = src
    el.async = true
    el.onload = () => resolve()
    el.onerror = () => reject(new Error(`failed to load ${src}`))
    document.head.appendChild(el)
  })
}

/** True only when the SDK actually loaded and exposes an auth surface. */
export async function isCatalystAuthAvailable() {
  if (typeof window === 'undefined') return false
  if (window.catalyst?.auth) return true
  try {
    await loadScript(SDK_URL)
    await loadScript(INIT_URL)
  } catch {
    return false
  }
  return Boolean(window.catalyst?.auth)
}

/**
 * Render Catalyst's hosted sign-in into `elementId`.
 *
 * Returns false when the SDK is unavailable so the caller can keep its own
 * form on screen; throwing here would strand the reviewer on a dead page.
 */
export async function renderCatalystSignIn(elementId, config = {}) {
  if (!(await isCatalystAuthAvailable())) return false
  try {
    window.catalyst.auth.signIn(elementId, config)
    return true
  } catch {
    return false
  }
}
