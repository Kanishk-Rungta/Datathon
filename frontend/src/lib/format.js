/* Formatting helpers shared across panels. */

export function formatDate(value) {
  if (!value) return '—'
  const text = String(value).slice(0, 10)
  const [y, m, d] = text.split('-')
  if (!y || !m || !d) return text
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${d} ${months[Number(m) - 1] || m} ${y}`
}

export function formatDateTime(value) {
  if (!value) return '—'
  const text = String(value)
  const date = formatDate(text)
  const time = text.length > 11 ? text.slice(11, 16) : ''
  return time ? `${date}, ${time}` : date
}

export function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toLocaleString('en-IN')
}

export function formatRupees(value) {
  if (value === null || value === undefined) return '—'
  return `₹${Number(value).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
}

export function periodLabel(period) {
  if (!period) return ''
  const [year, month] = period.split('-')
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${months[Number(month) - 1] || month} ${String(year).slice(2)}`
}

export function bandClass(band) {
  const normalised = String(band || '').toLowerCase()
  if (normalised === 'high') return 'band--high'
  if (normalised === 'medium' || normalised === 'elevated') return 'band--medium'
  return 'band--low'
}

/** Node ids are `kind:value`; the console shows the kind separately. */
export function splitNodeId(nodeId) {
  const index = String(nodeId).indexOf(':')
  if (index < 0) return { kind: 'entity', value: nodeId }
  return { kind: String(nodeId).slice(0, index), value: String(nodeId).slice(index + 1) }
}
