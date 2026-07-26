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
    case 'bar': return 'category counts, as computed'
    case 'map': return 'grid-binned concentrations'
    case 'graph': return 'derived link structure'
    case 'timeline': return 'dated events from the FIR record'
    case 'score': return 'transparent weighted sum'
    case 'table': return 'source records'
    default: return ''
  }
}

function render(kind, data, onSelectNode) {
  switch (kind) {
    case 'line': {
      const series = data.series?.[0]
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
    // `heatmap` is declared alongside `map` in domain/models.py and carries
    // the same grid-cell shape. Without this case it fell through to the
    // default and rendered a titled panel with nothing in it, which reads as
    // "no concentrations found" rather than "this console cannot draw that".
    case 'map':
    case 'heatmap':
      return <Cells cells={data.cells || []} gridMetres={data.grid_metres} />
    case 'graph':
      return <GraphView data={data} onSelectNode={onSelectNode} />
    case 'timeline':
      return <Timeline events={data.events || []} priority={data.priority} counts={data.counts} />
    case 'score':
      return <ScoreCard data={data} />
    case 'table':
      return <Table columns={data.columns || []} rows={data.rows || []} note={data.is_extension} />
    default:
      // An unrecognised payload type means the agents emit something this
      // console has not been taught to draw. Say so, rather than showing an
      // empty panel that looks like an empty result.
      return (
        <div className="panel__note">
          This answer returned a “{kind}” view, which this console cannot display yet.
          The figures behind it are listed under Evidence.
        </div>
      )
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
