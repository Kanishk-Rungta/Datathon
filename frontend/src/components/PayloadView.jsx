import GraphView from './GraphView.jsx'
import { BarChart, LineChart } from './Charts.jsx'
import { bandClass, formatDateTime, formatNumber } from '../lib/format.js'

/* One renderer per payload type the agents can emit. The agent decides what
 * kind of thing an answer is; the console only decides how it looks. */

export default function PayloadView({ payload, onSelectNode }) {
  if (!payload || payload.payload_type === 'none') return null
  const { payload_type: kind, title, data } = payload

  return (
    <div className="panel">
      <div className="panel__head">
        <h3 className="panel__title">{title || 'Result'}</h3>
        <span className="panel__note">{describe(kind)}</span>
      </div>
      {render(kind, data, onSelectNode)}
    </div>
  )
}

function describe(kind) {
  switch (kind) {
    case 'line': return 'monthly counts, as computed'
    case 'forecast': return 'projection, not recorded data'
    case 'bar': return 'category counts, as computed'
    case 'map': return 'grid-binned concentrations'
    case 'graph': return 'derived link structure'
    case 'timeline': return 'dated events from the FIR record'
    case 'score': return 'transparent weighted sum'
    case 'table': return 'source records'
    case 'socioeconomic_correlation': return 'cross-district statistical correlation (synthetic extension)'
    case 'early_warning': return 'statistical deviation alerts — not a prediction'
    case 'spatiotemporal_forecast': return 'spatial Poisson intensity forecast — not recorded data'
    default: return ''
  }
}

function render(kind, data, onSelectNode) {
  switch (kind) {
    case 'line': {
      const series = data.series?.[0]
      /* A projection must never be mistaken for a record. It is banded and
         labelled before the chart, and its range is shown as a table rather
         than drawn as a line, so nothing about it looks like observed data. */
      return (
        <>
          <LineChart labels={data.labels || []} values={series?.values || []} />
          {data.breakdown?.length > 0 && (
            <table className="table" style={{ marginTop: 12 }}>
              <thead><tr><th>Crime type</th><th>Cases</th><th>Share</th></tr></thead>
              <tbody>
                {data.breakdown.slice(0, 8).map((row) => (
                  <tr key={row.sub_head}>
                    <td>{row.sub_head}</td>
                    <td className="num">{formatNumber(row.case_count)}</td>
                    <td className="num">{row.share_percent?.toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )
    }
    case 'bar': {
      const series = data.series?.[0]
      return (
        <>
          <BarChart labels={data.labels || []} values={series?.values || []} />
          {data.seasonal_detail?.length > 0 && (
            <table className="table" style={{ marginTop: 12 }}>
              <thead><tr><th>Month</th><th>Latest</th><th>Baseline</th><th>Deviation</th></tr></thead>
              <tbody>
                {data.seasonal_detail.map((row) => (
                  <tr key={row.month}>
                    <td>{row.month} {row.current_period ? `(${row.current_period})` : ''}</td>
                    <td className="num">{formatNumber(row.current_count)}</td>
                    <td className="num">
                      {row.insufficient_history ? 'insufficient history' : row.baseline_mean.toFixed(1)}
                    </td>
                    <td className="num">
                      {row.insufficient_history || row.deviation_percent == null
                        ? '—'
                        : `${row.deviation_percent >= 0 ? '+' : ''}${row.deviation_percent.toFixed(1)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )
    }
    case 'map':
      return <Cells cells={data.cells || []} gridMetres={data.grid_metres} />
    case 'graph':
      return <GraphView data={data} onSelectNode={onSelectNode} />
    case 'timeline':
      return <Timeline events={data.events || []} priority={data.priority} counts={data.counts} />
    case 'score':
      return <ScoreCard data={data} />
    case 'table':
      return <Table columns={data.columns || []} rows={data.rows || []} note={data.is_extension} />
    case 'socioeconomic_correlation':
      return <SocioEconomicCorrelation data={data} />
    case 'forecast':
      return <ForecastProjection data={data} />
    case 'early_warning':
      return <EarlyWarningAlerts data={data} />
    case 'spatiotemporal_forecast':
      return <SpatioTemporalForecast data={data} />
    default:
      return null
  }
}

function Table({ columns, rows, note }) {
  if (rows.length === 0) return <div className="empty">No rows.</div>
  return (
    <>
      <table className="table">
        <thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
        <tbody>
          {rows.slice(0, 40).map((row, index) => (
            <tr key={index}>
              {row.map((cell, position) => (
                <td key={position} className={position === 0 ? 'mono' : ''}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 40 && <div className="panel__note" style={{ marginTop: 8 }}>Showing 40 of {rows.length} rows.</div>}
      {note && <div className="notice">These rows come from the synthetic financial extension, not the FIR schema.</div>}
    </>
  )
}

function Cells({ cells, gridMetres }) {
  if (cells.length === 0) return <div className="empty">No concentration met the threshold.</div>
  const maxCount = Math.max(...cells.map((cell) => cell.case_count))
  return (
    <>
      <div className="cells">
        {cells.slice(0, 10).map((cell, index) => (
          <div className="cell-row" key={cell.cell_id}>
            <div className="cell-row__rank">{index + 1}</div>
            <div>
              <div className="cell-row__bar" style={{ width: `${(cell.case_count / maxCount) * 100}%` }} />
              <div className="cell-row__meta">
                {cell.case_count} cases · {cell.top_crime_sub_head || 'mixed types'} ·{' '}
                <span className="mono">{cell.lat?.toFixed(4)}, {cell.lon?.toFixed(4)}</span>
              </div>
            </div>
            <div className="mono" style={{ fontSize: 12 }}>{cell.intensity?.toFixed(1)}×</div>
          </div>
        ))}
      </div>
      <div className="panel__note" style={{ marginTop: 10 }}>
        Cells are {gridMetres} m squares. Intensity compares a cell with the average occupied cell;
        a boundary can split one real concentration in two.
      </div>
    </>
  )
}

function Timeline({ events, priority, counts }) {
  return (
    <>
      {priority && (
        <div style={{ marginBottom: 16 }}>
          <div className="score">
            <span className="score__value">{Math.round(priority.score)}</span>
            <span className={`score__band ${bandClass(priority.band)}`}>{priority.band} priority</span>
          </div>
          <div className="panel__note" style={{ marginTop: 4 }}>
            Largest driver: {priority.top_driver}. This orders attention; it does not judge the case.
          </div>
        </div>
      )}
      {counts && (
        <div className="panel__note" style={{ marginBottom: 12 }}>
          {counts.accused} accused · {counts.victims} victims · {counts.arrests} arrests ·{' '}
          {counts.chargesheets} final reports
        </div>
      )}
      <div className="timeline">
        {events.map((event, index) => (
          <div className={`timeline__item timeline__item--${event.kind}`} key={index}>
            <div className="timeline__when">{formatDateTime(event.at)}</div>
            <div className="timeline__label">{event.label}</div>
            <div className="timeline__detail">{event.detail}</div>
          </div>
        ))}
      </div>
    </>
  )
}

function ScoreCard({ data }) {
  return (
    <>
      <div className="score">
        <span className="score__value">{Math.round(data.score)}</span>
        <span className={`score__band ${bandClass(data.band)}`}>{data.band}</span>
        <span className="panel__note">out of {data.max || 100}, for {data.subject}</span>
      </div>
      <table className="components" style={{ marginTop: 14 }}>
        <tbody>
          {(data.components || []).map((component, index) => (
            <tr key={index}>
              <td>
                {component.name}
                <div className="panel__note">{component.rationale}</div>
              </td>
              <td>{String(component.value)}</td>
              <td>{component.weight}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="notice">
        This summarises recorded history only. It is not a prediction about anyone's future
        behaviour and must not be used as one.
      </div>
    </>
  )
}

function SocioEconomicCorrelation({ data }) {
  if (!data || !data.correlations) return null
  return (
    <>
      <div className="forecast-banner">
        Extension: Socio-Economic Data ({data.data_source || 'synthetic'}). Quality: {data.data_quality}.
      </div>
      <table className="table" style={{ marginTop: 8 }}>
        <thead>
          <tr>
            <th>Indicator</th>
            <th>Pearson r</th>
            <th>Districts</th>
            <th>Interpretation</th>
          </tr>
        </thead>
        <tbody>
          {data.correlations.map((row) => (
            <tr key={row.indicator}>
              <td><strong>{row.label}</strong></td>
              <td className="num" style={{ fontWeight: 'bold', color: Math.abs(row.pearson_r) >= 0.4 ? '#2563eb' : 'inherit' }}>
                {row.pearson_r >= 0 ? `+${row.pearson_r.toFixed(2)}` : row.pearson_r.toFixed(2)}
              </td>
              <td className="num">{row.district_count}</td>
              <td style={{ fontSize: '0.85rem' }}>{row.interpretation}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.district_profiles?.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className="panel__head">
            <h4 className="panel__title">Top District Crime Rates (per 100k)</h4>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>District</th>
                <th>Cases</th>
                <th>Population</th>
                <th>Rate / 100k</th>
              </tr>
            </thead>
            <tbody>
              {data.district_profiles.slice(0, 8).map((p) => (
                <tr key={p.district_id}>
                  <td>{p.district_name}</td>
                  <td className="num">{formatNumber(p.case_count)}</td>
                  <td className="num">{formatNumber(p.population)}</td>
                  <td className="num">{p.crime_rate_per_100k.toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

/* ------------------------------------------------------------------
   Early Warning Alerts
   Renders colour-coded alert cards with severity badge, sigma deviation,
   district, crime type, and baseline vs current count.
   ------------------------------------------------------------------ */
/* A projection, rendered deliberately unlike observed data: banded, tabular,
   and always showing the range beside the midpoint. It is never drawn as a
   line chart, because a line is what recorded history looks like here. */
function ForecastProjection({ data }) {
  const points = data?.points || []
  if (points.length === 0) {
    return <div className="empty">Not enough recorded history to project from.</div>
  }
  return (
    <>
      <div className="forecast-banner">
        Projection — not recorded data. Method: {data.method}.
      </div>
      <table className="table">
        <thead><tr><th>Month</th><th>Expected</th><th>Likely range</th></tr></thead>
        <tbody>
          {points.map((point) => (
            <tr key={point.period}>
              <td>{point.period}</td>
              <td className="num">{Math.round(point.expected)}</td>
              <td className="num">{Math.round(point.lower)}–{Math.round(point.upper)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.backtests?.length > 0 && (
        <>
          <h4 className="panel__subtitle">How the method was chosen</h4>
          <table className="table">
            <thead><tr><th>Method</th><th>Mean error</th><th>Origins</th><th>Beat flat average</th></tr></thead>
            <tbody>
              {data.backtests.map((entry) => (
                <tr key={entry.method}>
                  <td>{entry.method}{entry.method === data.method ? ' (selected)' : ''}</td>
                  <td className="num">{entry.mean_absolute_error?.toFixed(2)}</td>
                  <td className="num">{entry.origins_tested}</td>
                  <td className="num">{entry.beat_constant_baseline ? 'yes' : 'no'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {data.caveat && <div className="notice">{data.caveat}</div>}
    </>
  )
}

function EarlyWarningAlerts({ data }) {
  const alerts = data?.alerts || []
  if (alerts.length === 0) {
    return (
      <div className="empty">
        No early warning alerts match the current filter. This is reassuring but not a guarantee.
      </div>
    )
  }
  return (
    <>
      <div className="forecast-banner" style={{ marginBottom: 10 }}>
        These alerts are statistical deviations above a rolling baseline — not predictions of future crime.
      </div>
      <div className="alerts">
        {alerts.map((alert, index) => {
          const sev = (alert.severity || 'low').toLowerCase()
          return (
            <div className="alert-card" key={alert.alert_id || index}>
              <div className={`alert-card__stripe alert-card__stripe--${sev}`} />
              <div className="alert-card__body">
                <div className="alert-card__header">
                  <span className={`alert-card__severity alert-card__severity--${sev}`}>{sev}</span>
                  <span className="alert-card__title">{alert.crime_sub_head || alert.crime_head || 'Multiple crime types'}</span>
                </div>
                <div className="alert-card__meta">
                  {[alert.district_name, alert.unit_name].filter(Boolean).join(' · ')}
                  {alert.period && <> · {alert.period}</>}
                </div>
                {alert.description && (
                  <div className="alert-card__description">{alert.description}</div>
                )}
                <div className="alert-card__stat-row">
                  {alert.sigma != null && (
                    <div className="alert-card__stat">
                      <span className="alert-card__stat-label">σ deviation</span>
                      <span className="alert-card__stat-value">{alert.sigma?.toFixed(2)}σ</span>
                    </div>
                  )}
                  {alert.observed_count != null && (
                    <div className="alert-card__stat">
                      <span className="alert-card__stat-label">Observed</span>
                      <span className="alert-card__stat-value">{formatNumber(alert.observed_count)}</span>
                    </div>
                  )}
                  {alert.baseline_mean != null && (
                    <div className="alert-card__stat">
                      <span className="alert-card__stat-label">Baseline avg</span>
                      <span className="alert-card__stat-value">{alert.baseline_mean?.toFixed(1)}</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
      {data.caveat && <div className="notice" style={{ marginTop: 10 }}>{data.caveat}</div>}
    </>
  )
}

/* ------------------------------------------------------------------
   Spatio-Temporal Predictive Forecast
   Renders a KPI summary row (horizon, total projected, model) and a
   ranked list of grid cells with expected counts, Poisson bounds,
   hotspot probability, and risk classification.
   ------------------------------------------------------------------ */
function SpatioTemporalForecast({ data }) {
  if (!data) return null
  const cells = data.predicted_cells || []
  const maxExpected = Math.max(...cells.map((c) => c.expected_count), 0.01)

  return (
    <>
      <div className="forecast-banner">
        Spatial Poisson projection — not recorded data. Model: {data.model_name || 'Spatial-Poisson-Holt-Winters'}.
      </div>

      <div className="stf-summary">
        <div className="stf-kpi">
          <span className="stf-kpi__label">Horizon</span>
          <span className="stf-kpi__value">{data.horizon_days}d</span>
        </div>
        <div className="stf-kpi">
          <span className="stf-kpi__label">Window</span>
          <span className="stf-kpi__value" style={{ fontSize: '0.8rem' }}>
            {data.window_start} → {data.window_end}
          </span>
        </div>
        <div className="stf-kpi">
          <span className="stf-kpi__label">Historical cases</span>
          <span className="stf-kpi__value">{formatNumber(data.total_historical_cases)}</span>
        </div>
        <div className="stf-kpi">
          <span className="stf-kpi__label">Projected total</span>
          <span className="stf-kpi__value">{Math.round(data.projected_total_cases)}</span>
        </div>
        <div className="stf-kpi">
          <span className="stf-kpi__label">Grid cells</span>
          <span className="stf-kpi__value">{cells.length}</span>
        </div>
        <div className="stf-kpi">
          <span className="stf-kpi__label">Grid size</span>
          <span className="stf-kpi__value">{data.grid_metres}m</span>
        </div>
      </div>

      {cells.length === 0 ? (
        <div className="empty">No historical geo-coded cases found for this filter. Cannot project.</div>
      ) : (
        <div className="stf-cells">
          {cells.slice(0, 15).map((cell, index) => (
            <div className={`stf-cell stf-cell--${cell.risk_level}`} key={cell.cell_id}>
              <div className="stf-cell__rank">#{index + 1}</div>
              <div className="stf-cell__body">
                <div className="stf-cell__name">
                  {cell.top_crime_sub_head || 'Mixed crime types'}
                  {cell.district_id && <span style={{ color: 'var(--text-dim)', marginLeft: 6 }}>· dist {cell.district_id}</span>}
                </div>
                <div className="stf-cell__meta">
                  <span className="mono">{cell.lat?.toFixed(4)}, {cell.lon?.toFixed(4)}</span>
                  {' · '}{cell.historical_count} historical cases
                </div>
                <div className="stf-bar-container">
                  <div className="stf-bar" style={{ width: `${Math.min(100, (cell.expected_count / maxExpected) * 100)}%` }} />
                </div>
              </div>
              <div className="stf-cell__right">
                <span className="stf-cell__expected">{cell.expected_count?.toFixed(1)}</span>
                <span className="stf-cell__range">{cell.lower_bound?.toFixed(1)}–{cell.upper_bound?.toFixed(1)}</span>
                <span className={`stf-risk-badge stf-risk-badge--${cell.risk_level}`}>
                  {cell.risk_level} · {(cell.hotspot_probability * 100).toFixed(0)}%
                </span>
              </div>
            </div>
          ))}
          {cells.length > 15 && (
            <div className="panel__note" style={{ marginTop: 4 }}>
              Showing top 15 of {cells.length} projected grid cells by expected incident count.
            </div>
          )}
        </div>
      )}

      {data.caveat && <div className="notice" style={{ marginTop: 12 }}>{data.caveat}</div>}
    </>
  )
}
