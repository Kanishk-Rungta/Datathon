import { useState } from 'react'

/* Rendering an answer is a truth-preserving operation here, not a styling one.
 *
 * Three things must survive the trip to the screen or the console is lying:
 *   1. the "(inferred)" and "(synthetic extension)" markers the composer added;
 *   2. every evidence locator, as a clickable citation rather than prose;
 *   3. the computation trace, one click away and never further.
 */

const MARKER_PATTERN = /\s*\((inferred|synthetic extension)\)/gi
const CITATION_PATTERN = /\s*\[([^\]]+)\]/g

function parseLine(line) {
  const markers = []
  const citations = []

  let text = line.replace(MARKER_PATTERN, (_match, kind) => {
    markers.push(kind.toLowerCase())
    return ''
  })
  text = text.replace(CITATION_PATTERN, (_match, locator) => {
    citations.push(locator)
    return ''
  })
  return { text: text.trim(), markers, citations }
}

export function AnswerBody({ answer, onSelectEvidence, activeLocator }) {
  const source = answer.answer_text_display || answer.answer_text || ''
  const lines = source.split('\n').filter((line) => line.trim().length > 0)
  const isKannada = answer.language === 'kn'

  return (
    <div className={`turn__body${isKannada ? ' kn' : ''}`}>
      {lines.map((line, index) => {
        const { text, markers, citations } = parseLine(line)
        if (!text) return null
        return (
          <p key={index}>
            {text}
            {markers.map((marker) => (
              <span
                key={marker}
                className={`marker marker--${marker === 'inferred' ? 'inferred' : 'extension'}`}
                title={
                  marker === 'inferred'
                    ? 'Derived from shared records, not stated in any FIR.'
                    : 'From the synthetic financial extension, not the source FIR schema.'
                }
              >
                {marker}
              </span>
            ))}
            {citations.length > 0 && ' '}
            {citations.map((locator) => (
              <button
                key={locator}
                type="button"
                className={`chip${activeLocator === locator ? ' chip--active' : ''}`}
                title={`Show the record behind this statement (${locator})`}
                onClick={() => onSelectEvidence(locator)}
              >
                {shortLocator(locator)}
              </button>
            ))}
          </p>
        )
      })}
    </div>
  )
}

function shortLocator(locator) {
  if (locator.startsWith('AGG:')) return `agg ${locator.slice(4).split(':')[0]}`
  if (locator.startsWith('PERSON:')) return `person ${locator.slice(7)}`
  if (locator.startsWith('EDGE:')) return 'link'
  if (locator.startsWith('ALERT:')) return `alert ${locator.slice(6, 12)}`
  if (locator.startsWith('TXN:')) return `txn ${locator.slice(4)}`
  return locator.length > 20 ? `FIR ${locator.slice(-9)}` : locator
}

export function TraceList({ traces }) {
  const [open, setOpen] = useState(false)
  if (!traces || traces.length === 0) return null

  return (
    <>
      <button type="button" className="linkish" onClick={() => setOpen((value) => !value)}>
        {open ? 'Hide how this was computed' : `How this was computed (${traces.length})`}
      </button>
      {open &&
        traces.map((trace, index) => (
          <div className="trace" key={index}>
            <div className="trace__op">
              {trace.operation}
              {trace.row_count !== null && trace.row_count !== undefined && ` · ${trace.row_count} rows`}
            </div>
            <div>{trace.description}</div>
            {trace.formula && <div className="trace__formula">{trace.formula}</div>}
            {trace.components?.length > 0 && (
              <table className="components">
                <tbody>
                  {trace.components.map((component, position) => (
                    <tr key={position}>
                      <td>{component.name}</td>
                      <td>{String(component.value)}</td>
                      <td>{component.weight}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ))}
    </>
  )
}
