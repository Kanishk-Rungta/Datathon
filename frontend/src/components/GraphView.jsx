import { useEffect, useMemo, useRef, useState } from 'react'
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from 'd3-force'
import { splitNodeId } from '../lib/format.js'

/* Link visualisation.
 *
 * Inferred edges are drawn dashed and in the accent colour, always, and the
 * legend says so. A solid line in this view means "the FIR system records
 * this"; a dashed line means "the platform derived this". Collapsing that
 * distinction into one uniform edge style is the single easiest way for a link
 * chart to mislead an investigator, so it is not available as an option.
 */

const NODE_COLOURS = {
  person: 'var(--indigo)',
  case: 'var(--moss)',
  officer: 'var(--amber)',
  location: 'var(--ink-faint)',
  entity: 'var(--vermillion)',
}

export default function GraphView({ data, height = 380, onSelectNode }) {
  const containerRef = useRef(null)
  const [width, setWidth] = useState(560)
  const [positions, setPositions] = useState([])
  const [hovered, setHovered] = useState(null)

  useEffect(() => {
    const element = containerRef.current
    if (!element) return undefined
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect?.width
      if (next) setWidth(Math.max(280, next))
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const { nodes, links } = useMemo(() => {
    // The chat payload marks the focus node `seed`; the REST endpoint marks
    // it `is_seed`. Normalise once here rather than in two render paths.
    const rawNodes = (data?.nodes || []).map((node) => ({
      ...node,
      is_seed: node.is_seed ?? node.seed ?? false,
    }))
    const present = new Set(rawNodes.map((node) => node.id))
    const rawLinks = (data?.links || [])
      .filter((link) => present.has(link.source) && present.has(link.target))
      .map((link) => ({ ...link }))
    return { nodes: rawNodes, links: rawLinks }
  }, [data])

  useEffect(() => {
    if (nodes.length === 0) {
      setPositions([])
      return undefined
    }
    const simulationNodes = nodes.map((node) => ({ ...node }))
    const simulationLinks = links.map((link) => ({ ...link }))

    const simulation = forceSimulation(simulationNodes)
      .force('charge', forceManyBody().strength(-155))
      .force('link', forceLink(simulationLinks).id((node) => node.id).distance(58).strength(0.35))
      .force('center', forceCenter(width / 2, height / 2))
      .force('collide', forceCollide(15))
      .stop()

    // Run synchronously: a settled layout that appears at once reads as a
    // considered chart, where an animated one reads as a toy.
    for (let tick = 0; tick < 210; tick += 1) simulation.tick()
    setPositions(simulationNodes.map((node) => ({ id: node.id, x: node.x, y: node.y })))
    return () => simulation.stop()
  }, [nodes, links, width, height])

  if (!data || nodes.length === 0) {
    return <div className="empty">No link structure to display.</div>
  }

  const byId = new Map(positions.map((position) => [position.id, position]))
  const maxWeight = Math.max(1, ...links.map((link) => link.weight || 1))

  return (
    <div ref={containerRef}>
      <svg className="graph-canvas" viewBox={`0 0 ${width} ${height}`} style={{ height }}>
        {links.map((link, index) => {
          const source = byId.get(link.source)
          const target = byId.get(link.target)
          if (!source || !target) return null
          return (
            <line
              key={index}
              className={`link${link.inferred ? ' link--inferred' : ''}`}
              x1={source.x} y1={source.y} x2={target.x} y2={target.y}
              strokeWidth={0.8 + ((link.weight || 1) / maxWeight) * 2.2}
            >
              <title>{`${link.type}: ${link.basis || 'derived from shared records'} (${(link.case_master_ids || []).length} case reference(s))`}</title>
            </line>
          )
        })}
        {nodes.map((node) => {
          const position = byId.get(node.id)
          if (!position) return null
          const { kind } = splitNodeId(node.id)
          const radius = node.is_seed ? 9 : 5 + (node.centrality || 0) * 26
          return (
            <g key={node.id}>
              <circle
                className={`node${node.is_seed ? ' node--seed' : ''}`}
                cx={position.x} cy={position.y} r={Math.min(13, radius)}
                fill={NODE_COLOURS[kind] || NODE_COLOURS.entity}
                onMouseEnter={() => setHovered(node.id)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => onSelectNode?.(node)}
              >
                <title>{node.label}</title>
              </circle>
              {(node.is_seed || hovered === node.id) && (
                <text className="node-label" x={position.x + 12} y={position.y + 3}>
                  {node.label?.length > 24 ? `${node.label.slice(0, 23)}…` : node.label}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      <div className="legend">
        <span><i style={{ background: NODE_COLOURS.person }} />Person</span>
        <span><i style={{ background: NODE_COLOURS.case }} />FIR</span>
        <span><i style={{ background: NODE_COLOURS.officer }} />Officer</span>
        <span><i style={{ background: NODE_COLOURS.entity }} />Entity (extension)</span>
        <span style={{ marginLeft: 6 }}>
          <svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke="var(--vermillion)" strokeDasharray="3 3" /></svg>
          Inferred link
        </span>
      </div>
    </div>
  )
}
