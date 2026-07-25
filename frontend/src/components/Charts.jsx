import { useMemo } from 'react'
import { periodLabel } from '../lib/format.js'

/* Charts are drawn with plain SVG rather than a charting library.
 *
 * The reason is not weight: it is that these charts must render exactly the
 * numbers the analytics engine returned, with no smoothing, no interpolation
 * and no automatic aggregation that could make the picture disagree with the
 * cited figures in the answer text.
 */

const MARGIN = { top: 12, right: 14, bottom: 26, left: 40 }

function useScales(values, width, height) {
  return useMemo(() => {
    const innerWidth = Math.max(10, width - MARGIN.left - MARGIN.right)
    const innerHeight = Math.max(10, height - MARGIN.top - MARGIN.bottom)
    const max = Math.max(1, ...values)
    const niceMax = niceCeil(max)
    return {
      innerWidth,
      innerHeight,
      x: (index, count) => (count <= 1 ? 0 : (index / (count - 1)) * innerWidth),
      band: (index, count) => (index * innerWidth) / Math.max(1, count),
      bandWidth: (count) => Math.max(2, (innerWidth / Math.max(1, count)) * 0.68),
      y: (value) => innerHeight - (value / niceMax) * innerHeight,
      max: niceMax,
    }
  }, [values, width, height])
}

function niceCeil(value) {
  const magnitude = 10 ** Math.floor(Math.log10(value))
  return Math.ceil(value / magnitude) * magnitude
}

export function LineChart({ labels = [], values = [], height = 168, width = 520, yTitle }) {
  const scales = useScales(values, width, height)
  if (values.length === 0) return <div className="empty">No series to plot.</div>

  const points = values.map((value, index) => [scales.x(index, values.length), scales.y(value)])
  const path = points.map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const area = `${path} L${points[points.length - 1][0].toFixed(1)},${scales.innerHeight} L0,${scales.innerHeight} Z`
  const ticks = [0, 0.5, 1].map((fraction) => Math.round(scales.max * fraction))
  const labelStep = Math.max(1, Math.ceil(labels.length / 8))

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={yTitle || 'Time series'}>
      <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="gridline" x1={0} x2={scales.innerWidth} y1={scales.y(tick)} y2={scales.y(tick)} />
            <text x={-7} y={scales.y(tick) + 3} textAnchor="end">{tick}</text>
          </g>
        ))}
        <path className="area" d={area} />
        <path className="series" d={path} />
        {points.map(([x, y], index) => (
          <circle key={index} cx={x} cy={y} r={2.1} fill="var(--indigo)">
            <title>{`${periodLabel(labels[index])}: ${values[index]}`}</title>
          </circle>
        ))}
        {labels.map((label, index) =>
          index % labelStep === 0 ? (
            <text key={label + index} x={scales.x(index, labels.length)} y={scales.innerHeight + 15} textAnchor="middle">
              {periodLabel(label)}
            </text>
          ) : null,
        )}
      </g>
    </svg>
  )
}

export function BarChart({ labels = [], values = [], height = 200, width = 520 }) {
  const scales = useScales(values, width, height)
  if (values.length === 0) return <div className="empty">No values to plot.</div>
  const barWidth = scales.bandWidth(values.length)

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Category comparison">
      <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
        {[0, 0.5, 1].map((fraction) => {
          const tick = Math.round(scales.max * fraction)
          return (
            <g key={fraction}>
              <line className="gridline" x1={0} x2={scales.innerWidth} y1={scales.y(tick)} y2={scales.y(tick)} />
              <text x={-7} y={scales.y(tick) + 3} textAnchor="end">{tick}</text>
            </g>
          )
        })}
        {values.map((value, index) => (
          <rect
            key={index}
            className="bar"
            x={scales.band(index, values.length) + (scales.innerWidth / values.length - barWidth) / 2}
            y={scales.y(value)}
            width={barWidth}
            height={Math.max(0, scales.innerHeight - scales.y(value))}
          >
            <title>{`${labels[index]}: ${value}`}</title>
          </rect>
        ))}
        {labels.map((label, index) => (
          <text
            key={label + index}
            x={scales.band(index, labels.length) + scales.innerWidth / labels.length / 2}
            y={scales.innerHeight + 15}
            textAnchor="end"
            transform={`rotate(-32 ${scales.band(index, labels.length) + scales.innerWidth / labels.length / 2} ${scales.innerHeight + 15})`}
          >
            {String(label).length > 16 ? `${String(label).slice(0, 15)}…` : label}
          </text>
        ))}
      </g>
    </svg>
  )
}
