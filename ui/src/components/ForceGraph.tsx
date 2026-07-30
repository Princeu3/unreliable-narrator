import { useEffect, useMemo, useRef, useState } from "react"
import {
  forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide, forceX, forceY,
  type SimulationNodeDatum,
} from "d3-force"

export type GNode = SimulationNodeDatum & {
  id: string; label: string; kind: "video" | "entity"
  type?: string; hits?: number; channels?: number; mods?: string[]; url?: string
}
type GLink = { source: string | GNode; target: string | GNode; modality: string; w: number }

const MOD: Record<string, string> = {
  speech: "var(--color-speech)", visual: "var(--color-visual)", ocr: "var(--color-ocr)",
}

/** The graph as the artifact, not a decoration. Entity nodes size by how often they are
 *  mentioned; edges take the colour of the channel the mention came through, so a node fed by
 *  three colours is one that was seen, said, AND written. */
export function ForceGraph({ nodes, links, height = 660, onSelect, selectedId }:
  { nodes: GNode[]; links: GLink[]; height?: number
    onSelect?: (n: GNode) => void; selectedId?: string | null }) {
  const wrap = useRef<HTMLDivElement>(null)
  const [, tick] = useState(0)
  const [simLinks, setSimLinks] = useState<GLink[]>([])
  const [hover, setHover] = useState<GNode | null>(null)
  const sim = useRef<ReturnType<typeof forceSimulation<GNode>> | null>(null)
  const [w, setW] = useState(880)

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width))
    if (wrap.current) ro.observe(wrap.current)
    return () => ro.disconnect()
  }, [])

  useEffect(() => {
    if (!nodes.length) return
    // d3 rewrites source/target from id-strings to node objects IN PLACE. Render from these
    // copies, not from `links` — that mismatch is why the graph drew no edges at all.
    const ls: GLink[] = links.map(l => ({ ...l }))
    setSimLinks(ls)
    const s = forceSimulation<GNode>(nodes)
      .force("link", forceLink<GNode, any>(ls).id((d: any) => d.id).distance(92).strength(0.3))
      .force("charge", forceManyBody().strength(-340))
      .force("center", forceCenter(w / 2, height / 2))
      .force("collide", forceCollide<GNode>().radius(d => radius(d) + 7))
      .force("x", forceX<GNode>(w / 2).strength(0.055))
      .force("y", forceY<GNode>(height / 2).strength(0.09))
      .alphaDecay(0.035)
    // clamp inside the frame. forceCenter pulls the mean to the middle but says nothing about
    // extremes, so long-tail nodes were drifting out over the card border.
    s.on("tick.bounds", () => {
      for (const d of nodes) {
        const r = radius(d) + 2
        d.x = Math.max(r + 8, Math.min(w - r - 8, d.x ?? w / 2))   // safety net, not the layout
        d.y = Math.max(r + 8, Math.min(height - r - 8, d.y ?? height / 2))
      }
    })
    // d3 fires ~300 ticks; repainting React on each one reconciles 276 elements 300 times.
    // Coalesce to one repaint per animation frame — same motion, ~5x fewer renders.
    let queued = false
    s.on("tick", () => {
      if (queued) return
      queued = true
      requestAnimationFrame(() => { queued = false; tick(t => t + 1) })
    })
    sim.current = s
    return () => { s.stop() }
  }, [nodes, links, w, height])

  const radius = (d: GNode) => d.kind === "video" ? 9 : 3 + Math.min(11, (d.hits ?? 1) * 1.15)

  // O(links) once per hover, not O(nodes x links) every render
  const near = useMemo(() => {
    if (!hover) return null
    const s = new Set<string>([hover.id])
    for (const l of simLinks) {
      const a = (l.source as GNode).id ?? (l.source as unknown as string)
      const b = (l.target as GNode).id ?? (l.target as unknown as string)
      if (a === hover.id) s.add(b as string)
      if (b === hover.id) s.add(a as string)
    }
    return s
  }, [hover, simLinks])

  return (
    <div ref={wrap} className="relative w-full">
      <svg width="100%" height={height} className="overflow-hidden">
        {/* edges are decoration; only nodes take clicks */}
        {simLinks.map((l, i) => {
          const s = l.source as GNode, t = l.target as GNode
          if (!s || !t || typeof s !== "object" || typeof t !== "object") return null
          return <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y}
            pointerEvents="none"
            stroke={MOD[l.modality] ?? "var(--color-border)"}
            strokeOpacity={hover ? (s.id === hover.id || t.id === hover.id ? 0.75 : 0.06) : 0.28}
            strokeWidth={Math.min(2.5, 0.6 + l.w * 0.35)} />
        })}
        {nodes.map(d => {
          const dim = near ? !near.has(d.id) : false
          return (
            <g key={d.id} transform={`translate(${d.x ?? 0},${d.y ?? 0})`}
              opacity={dim ? 0.2 : 1}
              onMouseEnter={() => setHover(d)} onMouseLeave={() => setHover(null)}
              onClick={() => onSelect?.(d)}
              className="cursor-pointer transition-opacity">
              {/* generous invisible hit target: the visible ring is as small as 3px, and the
                  group's bbox includes its label, so the geometric centre often sits in dead
                  space between circle and text. */}
              {selectedId === d.id && (
                <circle r={radius(d) + 5} fill="none" stroke="var(--color-accent)" strokeWidth={1.5} />
              )}
              <circle r={radius(d)}
                fill={d.kind === "video" ? "var(--color-foreground)" : "white"}
                stroke={d.kind === "video" ? "var(--color-foreground)"
                  : (d.mods?.length ?? 0) > 1 ? "var(--color-accent)" : "var(--color-border)"}
                strokeWidth={d.kind === "video" ? 0 : 1.5} />
              {(d.kind === "video" || (d.hits ?? 0) >= 9 || hover?.id === d.id) && (
                <text x={radius(d) + 5} y={3.5}
                  className="pointer-events-none text-[10px]"
                  paintOrder="stroke" stroke="white" strokeWidth={3} strokeLinejoin="round"
                  style={{ fill: d.kind === "video" ? "var(--color-foreground)" : "var(--color-muted-foreground)",
                           fontWeight: d.kind === "video" ? 500 : 400 }}>
                  {d.label.length > 22 ? d.label.slice(0, 22) + "…" : d.label}
                </text>
              )}
              {/* topmost, so the whole neighbourhood of a 3px ring is clickable */}
              <circle r={Math.max(15, radius(d) + 9)} fill="transparent" data-node={d.id} />
            </g>
          )
        })}
      </svg>
      {hover && hover.kind === "entity" && (
        <div className="bg-card pointer-events-none absolute right-0 top-0 rounded-md border px-3 py-2 text-xs shadow-sm">
          <div className="font-medium">{hover.label}</div>
          <div className="text-muted-foreground mt-0.5">
            {hover.hits} mentions · {hover.channels} channels
          </div>
          <div className="mt-1 flex gap-1">
            {hover.mods?.map(m => (
              <span key={m} className="rounded px-1 py-0.5 text-[10px]"
                style={{ background: `color-mix(in oklch, ${MOD[m]} 14%, white)`, color: MOD[m] }}>
                {m}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
