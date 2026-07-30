import { useEffect, useState } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ForceGraph, type GNode } from "@/components/ForceGraph"
import { ClipPlayer } from "@/components/ClipPlayer"

type Stats = { nodes: number; relationships: number; videos: number; scenes: number
  contradictions: number; verified: number; modalities: Record<string, number> }
type Gap = { name: string; type: string; shownVia: string[]; hits: number }
type Con = { about: string; channelA: string; claimA: string; jumpToA: string
  channelB: string; claimB: string; jumpToB: string; verdict: string | null; sourceUrl: string | null }
type Rate = { channel: string; claims: number; evidenced: number; scenes: number; evidenceRate: number }
type Clip = { videoId: string; startSec: number; endSec: number; channel: string
  claim: string; verdict: string | null; sourceUrl: string | null }

const get = (p: string) => fetch(p).then(r => r.json())
const vidOf = (u: string) => u.split("v=")[1]?.split("&")[0] ?? ""
const secOf = (u: string) => Number(u.split("&t=")[1] ?? 0)

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [gaps, setGaps] = useState<Gap[]>([])
  const [cons, setCons] = useState<Con[]>([])
  const [rates, setRates] = useState<Rate[]>([])
  const [graph, setGraph] = useState<{ nodes: GNode[]; links: any[] }>({ nodes: [], links: [] })
  const [clips, setClips] = useState<Clip[]>([])
  const [cut, setCut] = useState<string | null>(null)
  const [cutting, setCutting] = useState(false)
  const [url, setUrl] = useState("")
  const [step, setStep] = useState<string | null>(null)
  const [sel, setSel] = useState<GNode | null>(null)
  const [selClips, setSelClips] = useState<Clip[]>([])

  function pick(n: GNode) {
    setSel(n); setSelClips([])
    get(`/api/node-clips?id=${encodeURIComponent(n.id)}&kind=${n.kind}`).then(setSelClips)
  }

  // one request instead of six: the browser was waterfalling six connections, each of which
  // used to open its own driver to Aura
  const load = () => get("/api/all?min_hits=3").then(d => {
    setStats(d.stats); setGaps(d.modalityGap); setCons(d.contradictions)
    setRates(d.evidenceRate); setClips(d.clips); setGraph(d.graph)
  })
  useEffect(() => { load() }, [])   // useEffect must not return a promise

  async function ingest() {
    if (!url.trim()) return
    setStep("fetching")
    const res = await fetch("/api/ingest", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }) })
    const reader = res.body!.getReader(), dec = new TextDecoder()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      for (const line of dec.decode(value).split("\n")) {
        if (!line.startsWith("data: ")) continue
        const ev = JSON.parse(line.slice(6))
        setStep(ev.step)
        if (ev.step === "done") { load(); setUrl("") }
      }
    }
    setTimeout(() => setStep(null), 1500)
  }

  async function supercut() {
    setCutting(true)
    const r = await fetch("/api/supercut", { method: "POST" }).then(r => r.json())
    setCut(r.src ? `${r.src}?t=${Date.now()}` : null)
    setCutting(false)
  }

  const m = stats?.modalities ?? {}

  return (
    <div className="min-h-screen">
      {/* hero */}
      <div className="border-b">
        <div className="mx-auto max-w-6xl px-6 py-16">
          <div className="flex items-center gap-2">
            <Badge variant="speech">said</Badge>
            <Badge variant="visual">shown</Badge>
            <Badge variant="ocr">written on screen</Badge>
          </div>
          <h1 className="mt-5 max-w-3xl text-4xl font-semibold leading-[1.1] tracking-tight">
            Video says three things at once.
            <span className="text-muted-foreground"> Everything else blends them into one.</span>
          </h1>
          <p className="text-muted-foreground mt-5 max-w-2xl text-sm leading-relaxed">
            A health video can put a study’s own abstract on screen while the voiceover says
            something the study never found. Both are in the file. Once an embedding fuses the
            channels, that gap stops being a question you can ask. We keep them apart — and the
            disagreements become queryable.
          </p>
          <div className="mt-8 flex flex-wrap items-end gap-x-10 gap-y-4">
            {[["nodes", stats?.nodes], ["relationships", stats?.relationships],
              ["channels", stats?.videos], ["scenes", stats?.scenes],
              ["contradictions", stats?.contradictions], ["verified", stats?.verified]].map(([l, v]) => (
              <div key={l as string} className="flex flex-col">
                <span className="text-3xl font-semibold tabular-nums tracking-tight">{v ?? "—"}</span>
                <span className="text-muted-foreground text-xs">{l as string}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-6xl px-6 py-12">

        {/* graph */}
        <section className="mb-16">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-sm font-medium">The context graph</h2>
              <p className="text-muted-foreground mt-1 max-w-xl text-xs leading-relaxed">
                Dark nodes are channels. Rings are entities, sized by how often they appear.
                Edges take the colour of the channel a mention arrived through — a blue-ringed
                node was seen, said, <em>and</em> written. Hover to isolate.
              </p>
            </div>
            <div className="flex gap-1.5">
              <Badge variant="speech">speech {m.speech ?? 0}</Badge>
              <Badge variant="visual">visual {m.visual ?? 0}</Badge>
              <Badge variant="ocr">on-screen {m.ocr ?? 0}</Badge>
            </div>
          </div>
          <Card><CardContent className="pt-5">
            <div className="flex flex-col gap-5 lg:flex-row">
              <div className="min-w-0 flex-1">
                <ForceGraph nodes={graph.nodes} links={graph.links}
                  onSelect={pick} selectedId={sel?.id ?? null} />
              </div>
              <aside className="w-full shrink-0 lg:w-80">
                {!sel ? (
                  <div className="text-muted-foreground flex h-full min-h-40 items-center justify-center rounded-md border border-dashed px-4 text-center text-xs leading-relaxed">
                    Click any node to play the footage behind it.
                  </div>
                ) : (
                  <div className="flex flex-col gap-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{sel.label}</div>
                        <div className="text-muted-foreground text-[11px]">
                          {sel.kind === "video"
                            ? "channel"
                            : `${sel.hits} mentions · ${sel.channels} channels`}
                        </div>
                      </div>
                      <button onClick={() => setSel(null)}
                        className="text-muted-foreground hover:text-foreground text-xs">✕</button>
                    </div>
                    {sel.kind === "entity" && (
                      <div className="flex flex-wrap gap-1">
                        {sel.mods?.map(x => <Badge key={x} variant={x as any}>{x}</Badge>)}
                      </div>
                    )}
                    <div className="flex max-h-[34rem] flex-col gap-3 overflow-y-auto pr-1">
                      {selClips.length === 0 && (
                        <span className="text-muted-foreground text-xs">loading clips…</span>
                      )}
                      {selClips.map((c, i) => (
                        <div key={i} className="flex flex-col gap-1.5">
                          <ClipPlayer videoId={c.videoId} start={c.startSec}
                            end={Math.min(c.endSec, c.startSec + 14)} />
                          <span className="truncate text-[11px] font-medium">{c.channel}</span>
                          <span className="text-muted-foreground line-clamp-2 text-[11px] leading-snug">
                            {(c as any).label ?? c.claim}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </aside>
            </div>
          </CardContent></Card>
        </section>

        {/* contradictions with real clips */}
        <section className="mb-16">
          <h2 className="text-sm font-medium">Where the channels disagree</h2>
          <p className="text-muted-foreground mt-1 mb-5 max-w-2xl text-xs leading-relaxed">
            Found structurally in Cypher, judged for genuine conflict, then checked against the
            literature. Press play — these are the ingested clips at the exact second, not links.
          </p>
          <div className="flex flex-col gap-4">
            {cons.map((c, i) => (
              <Card key={i}>
                <CardContent className="pt-5">
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <span className="text-muted-foreground text-xs">
                      on <span className="text-foreground font-medium">{c.about}</span>
                    </span>
                    {c.verdict && (
                      <Badge variant={c.verdict === "DISPUTED" ? "disputed" : "supported"}>
                        {c.verdict.toLowerCase().replace("_", " ")}
                      </Badge>
                    )}
                  </div>
                  <div className="grid gap-5 sm:grid-cols-2">
                    {[[c.channelA, c.claimA, c.jumpToA], [c.channelB, c.claimB, c.jumpToB]].map(
                      ([ch, claim, link], j) => (
                        <div key={j} className="flex flex-col gap-2.5">
                          <ClipPlayer videoId={vidOf(link as string)} start={secOf(link as string)}
                            end={secOf(link as string) + 22} />
                          <span className="text-xs font-medium">{ch}</span>
                          <p className="text-muted-foreground text-xs leading-relaxed">“{claim}”</p>
                        </div>
                      ))}
                  </div>
                  {c.sourceUrl && (
                    <a href={c.sourceUrl} target="_blank" rel="noreferrer"
                      className="text-muted-foreground mt-4 block truncate border-t pt-3 text-[11px] hover:underline">
                      checked against · {c.sourceUrl.replace(/^https?:\/\//, "").slice(0, 90)}
                    </a>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        </section>

        {/* supercut */}
        <section className="mb-16">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-sm font-medium">The query returns a video</h2>
              <p className="text-muted-foreground mt-1 max-w-xl text-xs leading-relaxed">
                A Cypher result carrying <code className="text-[11px]">videoId, startSec, endSec</code> is
                already an edit decision list. {clips.length} scenes, assembled by the query itself.
              </p>
            </div>
            <Button onClick={supercut} disabled={cutting}>
              {cutting ? "cutting…" : "Build supercut"}
            </Button>
          </div>
          <Card><CardContent className="pt-5">
            {cut
              ? <video src={cut} controls className="aspect-video w-full rounded-md bg-black" />
              : (
                <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
                  {clips.slice(0, 8).map((c, i) => (
                    <div key={i} className="flex flex-col gap-1.5">
                      <ClipPlayer videoId={c.videoId} start={c.startSec} end={c.endSec} />
                      <span className="truncate text-[11px] font-medium">{c.channel}</span>
                      <span className="text-muted-foreground line-clamp-2 text-[11px] leading-snug">
                        {c.claim}
                      </span>
                    </div>
                  ))}
                </div>
              )}
          </CardContent></Card>
        </section>

        {/* modality gap */}
        <section className="mb-16">
          <h2 className="text-sm font-medium">Shown, never said</h2>
          <p className="text-muted-foreground mt-1 mb-4 max-w-2xl text-xs leading-relaxed">
            Sitting in a chart or a citation on screen, absent from the narration. A fused
            embedding cannot represent this — by query time the channels are already mixed.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {gaps.map(g => (
              <span key={g.name}
                className="bg-card inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs">
                {g.name}
                <span className="text-muted-foreground tabular-nums text-[10px]">{g.hits}</span>
              </span>
            ))}
          </div>
        </section>

        {/* evidence rate */}
        <section className="mb-16">
          <h2 className="text-sm font-medium">Claims vs. evidence on screen</h2>
          <p className="text-muted-foreground mt-1 mb-4 max-w-2xl text-xs leading-relaxed">
            How often each channel puts something on screen behind what it asserts. Worst first.
          </p>
          <Card><CardContent className="flex flex-col gap-3.5 pt-5">
            {rates.map(r => (
              <div key={r.channel} className="flex items-center gap-4">
                <span className="w-28 shrink-0 truncate text-xs sm:w-44">{r.channel}</span>
                <div className="bg-muted h-1.5 flex-1 overflow-hidden rounded-full">
                  <div className="h-full rounded-full transition-all"
                    style={{ width: `${Math.max(2, Math.min(100, r.evidenceRate * 100))}%`,
                             background: r.evidenceRate < 0.34 ? "var(--color-disputed)" : "var(--color-accent)" }} />
                </div>
                <span className="text-muted-foreground w-20 shrink-0 text-right text-[11px] tabular-nums sm:w-36">
                  <span className="hidden sm:inline">{r.claims} claims · </span>{r.evidenced}/{r.scenes}
                </span>
              </div>
            ))}
          </CardContent></Card>
        </section>

        {/* ingest */}
        <section className="mb-16">
          <h2 className="text-sm font-medium">Add a video</h2>
          <p className="text-muted-foreground mt-1 mb-4 max-w-2xl text-xs leading-relaxed">
            The interesting part is not summarising one file. It is that the graph already knows
            things — a new video lands against everything already here, and its conflicts surface.
          </p>
          <div className="flex gap-2">
            <Input placeholder="https://www.youtube.com/watch?v=…" value={url}
              onChange={e => setUrl(e.target.value)}
              onKeyDown={e => e.key === "Enter" && ingest()} className="max-w-lg" />
            <Button onClick={ingest} disabled={!!step}>{step ? step + "…" : "Ingest"}</Button>
          </div>
        </section>

        <footer className="text-muted-foreground border-t pt-6 text-[11px] leading-relaxed">
          TwelveLabs Pegasus 1.5 · OpenAI GPT-5.6 (Luna / Terra / Sol) · Neo4j Aura · Strands Agents.<br />
          Deterministic Python writes the graph. The agent reads it and cannot write an edge the
          footage does not support.
        </footer>
      </div>
    </div>
  )
}
