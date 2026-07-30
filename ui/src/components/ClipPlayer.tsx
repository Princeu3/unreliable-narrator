import { useEffect, useRef, useState } from "react"

/** Plays the actual ingested clip at the actual second. The whole pitch is that a claim points
 *  at footage — a link that leaves the page makes the audience take that on trust. */
export function ClipPlayer({ videoId, start, end, className = "" }:
  { videoId: string; start: number; end?: number; className?: string }) {
  const ref = useRef<HTMLVideoElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const [playing, setPlaying] = useState(false)
  const [near, setNear] = useState(false)   // 20 players x metadata fetch = a stalled first paint

  useEffect(() => {
    if (!box.current || near) return
    const io = new IntersectionObserver(([e]) => e.isIntersecting && setNear(true),
      { rootMargin: "300px" })
    io.observe(box.current)
    return () => io.disconnect()
  }, [near])

  useEffect(() => {
    const v = ref.current
    if (!v) return
    const onTime = () => { if (end && v.currentTime >= end) { v.pause(); setPlaying(false) } }
    v.addEventListener("timeupdate", onTime)
    return () => v.removeEventListener("timeupdate", onTime)
  }, [end])

  function toggle() {
    const v = ref.current!
    if (playing) { v.pause(); setPlaying(false); return }
    if (v.currentTime < start || (end && v.currentTime > end)) v.currentTime = start
    v.play(); setPlaying(true)
  }

  return (
    <div ref={box} className={`group relative overflow-hidden rounded-md border bg-black ${className}`}>
      <video ref={ref} src={near ? `/media/${videoId}.mp4#t=${start}` : undefined} preload="none"
        poster={`/api/thumb/${videoId}?t=${Math.floor(start) + 1}`}
        className="aspect-video w-full object-cover" playsInline onClick={toggle} />
      <button onClick={toggle}
        className="absolute inset-0 flex items-center justify-center transition-opacity"
        style={{ opacity: playing ? 0 : 1 }}>
        <span className="flex h-10 w-10 items-center justify-center rounded-full bg-white/90 shadow-sm backdrop-blur group-hover:bg-white">
          <svg width="12" height="14" viewBox="0 0 12 14" className="ml-0.5 fill-black">
            <path d="M0 0l12 7-12 7z" />
          </svg>
        </span>
      </button>
      <span className="pointer-events-none absolute bottom-1.5 left-1.5 rounded bg-black/65 px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-white">
        {fmt(start)}{end ? `–${fmt(end)}` : ""}
      </span>
    </div>
  )
}

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`
