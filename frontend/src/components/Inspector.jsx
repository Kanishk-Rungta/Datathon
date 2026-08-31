import { useEffect, useState } from 'react'
import PayloadView from './PayloadView.jsx'
import GraphView from './GraphView.jsx'
import { LineChart } from './Charts.jsx'
import { api } from '../lib/api.js'
import { bandClass, formatDate, formatNumber, formatRupees } from '../lib/format.js'

const TABS = [
  { id: 'result', label: 'Result' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'overview', label: 'Overview' },
  { id: 'network', label: 'Network' },
  { id: 'review', label: 'Review queue' },
]

export default function Inspector({ answer, activeLocator, onSelectEvidence, principal }) {
  const [tab, setTab] = useState('result')

  useEffect(() => {
    if (answer?.payload?.payload_type && answer.payload.payload_type !== 'none') setTab('result')
  }, [answer])

  useEffect(() => {
    if (activeLocator) setTab('evidence')
  }, [activeLocator])

  return (
    <div className="column">
      <div className="tabs" role="tablist">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            role="tab"
            aria-selected={tab === entry.id}
            onClick={() => setTab(entry.id)}
          >
            {entry.label}
          </button>
        ))}
        <div className="tabs__spacer" />
      </div>

      <div className="inspector">
        {tab === 'result' && <ResultTab answer={answer} />}
        {tab === 'evidence' && (
          <EvidenceTab answer={answer} activeLocator={activeLocator} onSelectEvidence={onSelectEvidence} />
        )}
        {tab === 'overview' && <OverviewTab />}
        {tab === 'network' && <NetworkTab principal={principal} />}
        {tab === 'review' && <ReviewTab principal={principal} />}
      </div>
    </div>
  )
}

/* --------------------------------------------------------------- result */

function ResultTab({ answer }) {
  if (!answer) return <div className="empty">Ask a question to see charts, maps and link views here.</div>
  if (!answer.payload || answer.payload.payload_type === 'none') {
    return <div className="empty">This answer is text only — the evidence tab lists the records behind it.</div>
  }
  return <PayloadView payload={answer.payload} />
}

/* ------------------------------------------------------------- evidence */

function EvidenceTab({ answer, activeLocator, onSelectEvidence }) {
  const [detail, setDetail] = useState(null)
  const [detailError, setDetailError] = useState(null)
  const [loading, setLoading] = useState(false)

  const items = answer?.evidence || []
  const active = items.find((item) => item.locator === activeLocator) || items[0]

  useEffect(() => {
    setDetail(null)
    setDetailError(null)
    const crimeNo = active?.crime_nos?.[0]
    if (!crimeNo) return
    let cancelled = false
    setLoading(true)
    api.caseDetail(crimeNo)
      .then((data) => { if (!cancelled) setDetail(data) })
      // Say why the record is missing. Swallowing this rendered an empty
      // panel that looked identical to "this citation has no record behind
      // it" -- the one thing an evidence view must never be ambiguous about.
      .catch((err) => { if (!cancelled) setDetailError(err.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [active?.locator])

  if (items.length === 0) return <div className="empty">No evidence attached to this answer yet.</div>

  return (
    <>
      <div className="panel">
        <div className="panel__head">
          <h3 className="panel__title">Evidence</h3>
          <span className="panel__note">every statement traces to one of these</span>
        </div>
        <table className="table">
          <thead><tr><th>Locator</th><th>What it is</th><th>Records</th></tr></thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.locator}
                  style={{ cursor: 'pointer', background: item.locator === active?.locator ? 'var(--paper-sunk)' : undefined }}
                  onClick={() => onSelectEvidence(item.locator)}>
                <td className="mono">{item.locator.length > 26 ? `…${item.locator.slice(-22)}` : item.locator}</td>
                <td>
                  {item.label}
                  {item.provenance === 'inferred' && <span className="marker marker--inferred">inferred</span>}
                  {item.provenance === 'synthetic_extension' && <span className="marker marker--extension">extension</span>}
                </td>
                <td className="num">{item.case_master_ids?.length || 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {loading && <div className="empty">Loading the source record…</div>}
      {detailError && !loading && (
        <div className="error-note">
          The source record behind this citation could not be loaded: {detailError}
        </div>
      )}
      {detail && <CaseDetail detail={detail} />}
    </>
  )
}

function CaseDetail({ detail }) {
  const caseRow = detail.case
  return (
    <div className="panel">
      <div className="panel__head">
        <h3 className="panel__title">FIR {caseRow.crime_no}</h3>
        <span className="panel__note">source record</span>
      </div>
      <table className="table">
        <tbody>
          <Row label="Registered" value={formatDate(caseRow.crime_registered_date)} />
          <Row label="Police station" value={caseRow.police_station_name} />
          <Row label="District" value={caseRow.district_name} />
          <Row label="Classification" value={`${caseRow.crime_sub_head || '—'} (${caseRow.crime_head || '—'})`} />
          <Row label="Gravity" value={caseRow.gravity} />
          <Row label="Status" value={caseRow.status} />
          <Row label="Court" value={caseRow.court_name} />
          <Row label="Accused" value={(detail.accused || []).map((person) => person.name).join(', ') || 'None named'} />
          <Row label="Victims" value={(detail.victims || []).map((person) => person.name).join(', ') || '—'} />
          <Row label="Sections" value={(detail.act_sections || [])
            .map((row) => `${row.ShortName || row.ActID} §${row.SectionID}`).join(', ') || '—'} />
        </tbody>
      </table>
      {caseRow.brief_facts && (
        <p style={{ marginTop: 12, fontFamily: 'var(--serif)', fontSize: 14.5, lineHeight: 1.5 }}>
          {caseRow.brief_facts}
        </p>
      )}
    </div>
  )
}

function Row({ label, value }) {
  return (
    <tr>
      <td style={{ width: 130, color: 'var(--ink-faint)' }}>{label}</td>
      <td>{value || '—'}</td>
    </tr>
  )
}

/* ------------------------------------------------------------- overview */

function OverviewTab() {
  const [summary, setSummary] = useState(null)
  const [trend, setTrend] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [priority, setPriority] = useState([])
  const [error, setError] = useState(null)
  const [degraded, setDegraded] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    /* allSettled, not all: these four panels are independent, and one of them
     * failing is not a reason to hide the other three. A dashboard that goes
     * blank because a single query is unavailable tells an officer less than
     * one that shows what it does know and names what it could not load. */
    let cancelled = false
    Promise.allSettled([api.summary(), api.trend({ months: 18 }), api.earlyWarning(), api.priority(8)])
      .then(([summaryRes, trendRes, alertRes, priorityRes]) => {
        if (cancelled) return
        const missing = []
        if (summaryRes.status === 'fulfilled') setSummary(summaryRes.value)
        else missing.push('the standing picture')
        if (trendRes.status === 'fulfilled') setTrend(trendRes.value)
        else missing.push('the trend series')
        if (alertRes.status === 'fulfilled') setAlerts(alertRes.value.alerts || [])
        else missing.push('early-warning signals')
        if (priorityRes.status === 'fulfilled') setPriority(priorityRes.value.cases || [])
        else missing.push('investigation priority')

        setDegraded(missing)
        // Only a total failure is an error; anything less is a partial view.
        if (missing.length === 4) setError(summaryRes.reason?.message || 'Nothing could be loaded.')
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  if (error) return <div className="error-note" style={{ margin: 20 }}>{error}</div>
  if (loading) return <div className="empty">Loading the standing picture…</div>
  if (!summary) {
    return (
      <div className="error-note" style={{ margin: 20 }}>
        The standing picture could not be loaded{degraded.length > 1 ? ', along with ' + degraded.filter((d) => d !== 'the standing picture').join(', ') : ''}.
      </div>
    )
  }

  return (
    <>
      {degraded.length > 0 && (
        /* Named explicitly: a panel that is empty because a query failed must
         * not look like a panel that is empty because there is nothing to
         * report. */
        <div className="error-note" style={{ margin: '12px 20px 0' }}>
          Could not load {degraded.join(', ')}. The rest of this view is current.
        </div>
      )}
      <div className="panel">
        <div className="panel__head">
          <h3 className="panel__title">Registered cases in your scope</h3>
          <span className="panel__note">{formatNumber(summary.total_cases)} total</span>
        </div>
        <LineChart labels={trend?.periods || []} values={trend?.counts || []} />
        <div className="panel__note" style={{ marginTop: 8 }}>
          Latest month {summary.latest_period}: {formatNumber(summary.latest_count)} cases
          {summary.change_percent !== null && ` (${summary.change_percent > 0 ? '+' : ''}${summary.change_percent}% on the previous month)`}.
          Series is {summary.direction}.
          {summary.active_alerts > 0 && ` ${summary.active_alerts} early-warning signal(s) active.`}
        </div>
      </div>

      <div className="panel">
        <div className="panel__head">
          <h3 className="panel__title">Early warning</h3>
          <span className="panel__note">z-score against each area's own baseline</span>
        </div>
        {alerts.length === 0 ? (
          <div className="empty">Nothing is currently running above its own baseline.</div>
        ) : (
          <table className="table">
            <thead><tr><th>Area</th><th>Crime type</th><th>Observed</th><th>Baseline</th><th>z</th></tr></thead>
            <tbody>
              {alerts.slice(0, 8).map((alert) => (
                <tr key={alert.alert_id}>
                  <td>{alert.scope_name}</td>
                  <td>{alert.crime_sub_head || 'all'}</td>
                  <td className="num">{alert.observed_count}</td>
                  <td className="num">{Number(alert.baseline_mean).toFixed(1)}</td>
                  <td className="num">
                    <span className={`score__band ${bandClass(alert.severity)}`}>{Number(alert.z_score).toFixed(2)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <div className="panel__head">
          <h3 className="panel__title">Cases by investigation priority</h3>
          <span className="panel__note">published weights, no black box</span>
        </div>
        <table className="table">
          <thead><tr><th>FIR</th><th>Type</th><th>Status</th><th>Score</th></tr></thead>
          <tbody>
            {priority.map((entry) => (
              <tr key={entry.case.crime_no}>
                <td className="mono">{entry.case.crime_no.slice(-9)}</td>
                <td>{entry.case.crime_sub_head}</td>
                <td>{entry.case.status}</td>
                <td className="num">
                  <span className={`score__band ${bandClass(entry.band)}`}>{Math.round(entry.score)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

/* -------------------------------------------------------------- network */

function NetworkTab({ principal }) {
  const canSeeFinancial = (principal?.permissions || []).includes('use_financial_tools')
  const [offenders, setOffenders] = useState([])
  const [graph, setGraph] = useState(null)
  const [selected, setSelected] = useState(null)
  const [financial, setFinancial] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.offenders(12).then((data) => setOffenders(data.offenders || [])).catch((err) => setError(err.message))
  }, [])

  async function inspect(identityId) {
    setSelected(identityId)
    setFinancial(null)
    try {
      const data = await api.expandGraph({ node_id: `person:${identityId}`, hops: 2 })
      setGraph(data)
    } catch (err) { setError(err.message) }
    // Don't ask for what this role cannot have. The financial view is gated
    // on `use_financial_tools`; requesting it anyway produced a guaranteed
    // 403 on every node click, which the browser logs as a failed request
    // whether or not we catch it. The principal already carries its
    // permissions, so the question simply isn't asked.
    if (!canSeeFinancial) return
    try {
      const money = await api.financial(identityId)
      setFinancial(money)
    } catch (err) {
      setFinancial(null)
      if (err.status !== 403) setError(err.message)
    }
  }

  return (
    <>
      <div className="panel">
        <div className="panel__head">
          <h3 className="panel__title">Recorded offence history</h3>
          <span className="panel__note">people named in more than one FIR</span>
        </div>
        {error && <div className="error-note">{error}</div>}
        <table className="table">
          <thead><tr><th>Person</th><th>Cases</th><th>Types</th><th>Score</th></tr></thead>
          <tbody>
            {offenders.map((offender) => (
              <tr key={offender.identity_id} style={{ cursor: 'pointer' }} onClick={() => inspect(offender.identity_id)}>
                <td>{offender.canonical_name}</td>
                <td className="num">{offender.case_count}</td>
                <td className="num">{offender.distinct_crime_heads}</td>
                <td className="num">
                  <span className={`score__band ${bandClass(offender.band)}`}>{Math.round(offender.score)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="notice">
          A history score describes what is already recorded. It is not a prediction about a person.
        </div>
      </div>

      {graph && (
        <div className="panel">
          <div className="panel__head">
            <h3 className="panel__title">Link structure</h3>
            <span className="panel__note">
              {graph.withheld_by_scope > 0 ? `${graph.withheld_by_scope} links withheld by scope` : 'within your scope'}
            </span>
          </div>
          <GraphView data={graph} />
        </div>
      )}

      {financial?.transaction_count > 0 && (
        <div className="panel">
          <div className="panel__head">
            <h3 className="panel__title">Recorded transfers</h3>
            <span className="panel__note">synthetic extension</span>
          </div>
          <table className="table">
            <thead><tr><th>Counterparty</th><th>Kind</th><th>Transfers</th><th>Net</th></tr></thead>
            <tbody>
              {(financial.counterparties || []).slice(0, 10).map((flow) => (
                <tr key={flow.ref}>
                  <td>{flow.label}</td>
                  <td>{flow.kind}</td>
                  <td className="num">{flow.txn_count}</td>
                  <td className="num">{formatRupees(flow.net)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {financial.chains?.length > 0 && (
            <>
              <h4 className="panel__subtitle">Onward movement</h4>
              {financial.chains.slice(0, 3).map((chain) => (
                <div className="flow-chain" key={chain.txn_ids.join('-')}>
                  <div className="flow-chain__path">{chain.path.join(' → ')}</div>
                  <div className="flow-chain__meta">
                    {chain.hops} hop{chain.hops === 1 ? '' : 's'} · {chain.start_date} to {chain.end_date}
                    {' · '}{formatRupees(chain.amounts[0])} first, {formatRupees(chain.amounts[chain.amounts.length - 1])} last
                  </div>
                </div>
              ))}
            </>
          )}

          {financial.concentrations?.length > 0 && (
            <>
              <h4 className="panel__subtitle">Counterparty concentration</h4>
              <table className="table">
                <thead><tr><th>Account</th><th>Direction</th><th>Counterparties</th><th>Total</th></tr></thead>
                <tbody>
                  {financial.concentrations.slice(0, 5).map((spot) => (
                    <tr key={`${spot.label}-${spot.direction}`}>
                      <td>{spot.label}</td>
                      <td>{spot.direction}</td>
                      <td className="num">{spot.counterparty_count}</td>
                      <td className="num">{formatRupees(spot.total_amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="panel__note">
                At or above the {financial.concentrations[0].percentile}th percentile of this dataset
                ({financial.concentrations[0].threshold_degree} counterparties).
              </div>
            </>
          )}

          {financial.bursts?.length > 0 && (
            <>
              <h4 className="panel__subtitle">Days above the account&apos;s own norm</h4>
              <table className="table">
                <thead><tr><th>Account</th><th>Day</th><th>Transfers</th><th>Baseline</th><th>z</th></tr></thead>
                <tbody>
                  {financial.bursts.slice(0, 5).map((burst) => (
                    <tr key={`${burst.label}-${burst.day}`}>
                      <td>{burst.label}</td>
                      <td>{burst.day}</td>
                      <td className="num">{burst.transactions}</td>
                      <td className="num">{burst.baseline_mean}</td>
                      <td className="num">{burst.z_score}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {financial.amount_bands?.some((band) => band.count > 0) && (
            <>
              <h4 className="panel__subtitle">Amounts against the reporting threshold</h4>
              <table className="table">
                <thead><tr><th>Band</th><th>Transfers</th><th>Total</th></tr></thead>
                <tbody>
                  {financial.amount_bands.filter((band) => band.count > 0).map((band) => (
                    <tr key={band.label}>
                      <td>{band.label}</td>
                      <td className="num">{band.count}</td>
                      <td className="num">{formatRupees(band.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          <div className="notice">
            The FIR schema holds no financial records. These rows are a clearly marked synthetic extension.
          </div>
          {financial.interpretation_notice && (
            <div className="notice">{financial.interpretation_notice}</div>
          )}
        </div>
      )}
    </>
  )
}

/* --------------------------------------------------------------- review */

function ReviewTab({ principal }) {
  const [queue, setQueue] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)

  function load() {
    api.reviewQueue().then(setQueue).catch((err) => setError(err.message))
  }
  useEffect(load, [])

  async function decide(linkId, decision) {
    setBusy(linkId)
    try {
      await api.decideReview({ link_id: linkId, decision })
      load()
    } catch (err) { setError(err.message) } finally { setBusy(null) }
  }

  if (error) return <div className="error-note" style={{ margin: 20 }}>{error}</div>
  if (!queue) return <div className="empty">Loading the review queue…</div>

  const pending = queue.pending || []

  return (
    <div className="panel">
      <div className="panel__head">
        <h3 className="panel__title">Identity review queue</h3>
        <span className="panel__note">
          auto-link at {queue.thresholds?.auto_link_at_or_above}, review between{' '}
          {queue.thresholds?.review_between?.[0]} and {queue.thresholds?.review_between?.[1]}
        </span>
      </div>
      <p className="panel__note" style={{ marginBottom: 12 }}>
        These pairs scored close enough to be the same person, but not close enough for the platform
        to decide on its own. Nothing is merged: confirming a pair records a human decision, and the
        underlying records stay separate and reversible.
      </p>
      {pending.length === 0 ? (
        <div className="empty">Nothing awaiting review.</div>
      ) : (
        <table className="table">
          <thead><tr><th>Candidate pair</th><th>Score</th><th>Basis</th><th /></tr></thead>
          <tbody>
            {pending.slice(0, 25).map((link) => (
              <tr key={link.link_id}>
                <td>
                  {link.left_name || `#${link.left_accused_id}`}
                  <div className="panel__note">{link.right_name || `#${link.right_accused_id}`}</div>
                </td>
                <td className="num">{Number(link.score).toFixed(3)}</td>
                <td className="panel__note">
                  {Object.entries(link.features || {})
                    .filter(([key]) => key !== 'score')
                    .slice(0, 3)
                    .map(([key, value]) => `${key.replace(/_/g, ' ')} ${value}`)
                    .join(' · ')}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button className="linkish" disabled={busy === link.link_id}
                          onClick={() => decide(link.link_id, 'confirmed')}>Same</button>
                  {' · '}
                  <button className="linkish" disabled={busy === link.link_id}
                          onClick={() => decide(link.link_id, 'rejected')}>Different</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
