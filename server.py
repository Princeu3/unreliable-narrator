#!/usr/bin/env python3
"""API for the UI. Thin read layer over the same graph the CLI uses.

  .venv/bin/uvicorn server:app --reload --port 8000

Ingest is the one write endpoint, and it runs the same deterministic pipeline as ingest.py —
the browser cannot send Cypher, and neither can the agent.
"""
import json, os, subprocess, sys, pathlib, threading
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent as A

app = FastAPI(title="Unreliable Narrator")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# the clips themselves — so a citation plays in the page instead of linking away
pathlib.Path("out").mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory="data"), name="media")
app.mount("/out", StaticFiles(directory="out"), name="out")


# One driver for the process. It owns a connection pool; building a new one per request meant a
# fresh TLS handshake to Aura every time, which was ~0.6s of the ~0.6s each endpoint took.
_DRIVER = None
_LOCK = threading.Lock()


def driver():
    global _DRIVER
    if _DRIVER is None:
        with _LOCK:
            if _DRIVER is None:
                _DRIVER = A._driver()
                _DRIVER.verify_connectivity()
    return _DRIVER


def rows(cypher, **params):
    with driver().session() as s:
        return [dict(r) for r in s.run(cypher, **params)]


@app.on_event("startup")
def warm():
    driver()          # pay the handshake once, at boot, not on the first visitor


@app.on_event("shutdown")
def close():
    if _DRIVER:
        _DRIVER.close()


@app.get("/api/stats")
def stats():
    # one round trip instead of seven: counts are cheap, the latency was all connection setup
    r = rows("""
      CALL (){ MATCH (n) RETURN count(n) AS nodes }
      CALL (){ MATCH ()-[x]->() RETURN count(x) AS rels }
      CALL (){ MATCH (v:Video) RETURN count(v) AS videos }
      CALL (){ MATCH (s:Scene) RETURN count(s) AS scenes }
      CALL (){ MATCH ()-[x:CONTRADICTS]->() RETURN count(x) AS cons }
      CALL (){ MATCH ()-[x:VERIFIED_AS]->() RETURN count(x) AS ver }
      CALL (){ MATCH ()-[x:MENTIONS]->() RETURN collect([x.modality, 1]) AS raw }
      RETURN nodes, rels, videos, scenes, cons, ver, raw
    """)[0]
    mods = {}
    for k, _ in r["raw"]:
        mods[k] = mods.get(k, 0) + 1
    return {"nodes": r["nodes"], "relationships": r["rels"], "videos": r["videos"],
            "scenes": r["scenes"], "contradictions": r["cons"], "verified": r["ver"],
            "modalities": mods}


@app.get("/api/modality-gap")
def modality_gap():
    return rows("""
      MATCH (e:Entity)<-[m:MENTIONS]-(:Scene)
      WITH e, collect(DISTINCT m.modality) AS mods, count(m) AS hits
      WHERE NOT 'speech' IN mods AND hits > 1
      RETURN e.name AS name, e.type AS type, mods AS shownVia, hits
      ORDER BY hits DESC, name LIMIT 24
    """)


@app.get("/api/contradictions")
def contradictions():
    return rows(A.CONTRADICTIONS)        # same query, but over the pooled driver


@app.get("/api/all")
def all_data(min_hits: int = 3):
    """Everything the page needs, in one request. Six round trips over one TLS connection beats
    six connections, and the browser stops waterfalling."""
    return {"stats": stats(), "graph": graph(min_hits), "contradictions": contradictions(),
            "modalityGap": modality_gap(), "evidenceRate": evidence_rate(), "clips": clips()}


@app.get("/api/evidence-rate")
def evidence_rate():
    return rows("""
      MATCH (v:Video)-[:HAS_SCENE]->(s:Scene)
      WITH v, count(s) AS scenes,
           sum(CASE WHEN s.evidenceShown <> 'none' THEN 1 ELSE 0 END) AS evidenced
      CALL (v) {
        MATCH (v)-[:HAS_SCENE]->(:Scene)-[:ASSERTS]->(c:Claim {modality:'speech'})
        RETURN count(c) AS claims
      }
      RETURN v.channel AS channel, v.url AS url, claims, evidenced, scenes,
             round(1.0*evidenced/scenes, 2) AS evidenceRate
      ORDER BY evidenceRate ASC, claims DESC
    """)


@app.get("/api/videos")
def videos():
    return rows("""
      MATCH (v:Video)-[:HAS_SCENE]->(s:Scene)
      RETURN v.id AS id, v.channel AS channel, v.title AS title, v.url AS url,
             count(s) AS scenes ORDER BY channel
    """)


@app.get("/api/graph")
def graph(min_hits: int = 2):
    """Node-link view for the canvas. Trimmed to entities that actually connect things —
    the full 489 nodes is a hairball, and a hairball proves nothing."""
    ents = rows("""
      MATCH (v:Video)-[:HAS_SCENE]->(:Scene)-[m:MENTIONS]->(e:Entity)
      WITH e, count(DISTINCT v) AS channels, count(m) AS hits,
           collect(DISTINCT m.modality) AS mods
      WHERE hits >= $min_hits
      RETURN e.name AS id, e.type AS type, channels, hits, mods
      ORDER BY hits DESC LIMIT 70
    """, min_hits=min_hits)
    keep = {e["id"] for e in ents}
    vids = rows("MATCH (v:Video) RETURN v.id AS id, v.channel AS channel, v.url AS url")
    links = rows("""
      MATCH (v:Video)-[:HAS_SCENE]->(:Scene)-[m:MENTIONS]->(e:Entity)
      WHERE e.name IN $keep
      WITH v, e, m.modality AS modality, count(*) AS w
      RETURN v.id AS source, e.name AS target, modality, w
    """, keep=list(keep))
    nodes = ([{"id": v["id"], "label": v["channel"], "kind": "video", "url": v["url"]} for v in vids]
             + [{"id": e["id"], "label": e["id"], "kind": "entity", "type": e["type"],
                 "hits": e["hits"], "channels": e["channels"], "mods": e["mods"]} for e in ents])
    return {"nodes": nodes, "links": links}


@app.get("/api/clips")
def clips(verdict: str | None = None):
    """Scenes involved in a contradiction — the edit decision list, with playable sources."""
    q = """
      MATCH (v:Video)-[:HAS_SCENE]->(s:Scene)-[:ASSERTS]->(c:Claim)-[:CONTRADICTS]-()
      OPTIONAL MATCH (c)-[ver:VERIFIED_AS]->(ev:Evidence)
      WITH s, v, collect(c.text)[0] AS claim, collect(ver.verdict)[0] AS verdict,
           collect(ev.url)[0] AS sourceUrl
      RETURN s.videoId AS videoId, s.startSec AS startSec, s.endSec AS endSec,
             v.channel AS channel, claim, verdict, sourceUrl
      ORDER BY channel, startSec
    """
    out = rows(q)
    return [r for r in out if not verdict or r["verdict"] == verdict]


@app.get("/api/node-clips")
def node_clips(id: str, kind: str = "entity"):
    """Scenes behind a node, so clicking the graph plays the footage that put it there.

    For an entity we also return which channel each mention came through, because the whole
    point is that the same node can be reached by three different channels.
    """
    if kind == "video":
        return rows("""
          MATCH (v:Video {id:$id})-[:HAS_SCENE]->(s:Scene)
          OPTIONAL MATCH (s)-[:ASSERTS]->(c:Claim {modality:'speech'})
          WITH v, s, collect(c.text)[0] AS claim
          RETURN s.videoId AS videoId, s.startSec AS startSec, s.endSec AS endSec,
                 v.channel AS channel, coalesce(claim, s.summary) AS label,
                 s.evidenceShown AS evidence, null AS modality
          ORDER BY s.startSec LIMIT 8
        """, id=id)
    return rows("""
      MATCH (e:Entity {name:$id})<-[m:MENTIONS]-(s:Scene)<-[:HAS_SCENE]-(v:Video)
      WITH s, v, collect(DISTINCT m.modality) AS mods
      OPTIONAL MATCH (s)-[:ASSERTS]->(c:Claim {modality:'speech'})
      WITH s, v, mods, collect(c.text)[0] AS claim
      RETURN s.videoId AS videoId, s.startSec AS startSec, s.endSec AS endSec,
             v.channel AS channel, coalesce(claim, s.summary) AS label,
             s.evidenceShown AS evidence, mods AS modality
      ORDER BY s.startSec LIMIT 8
    """, id=id)


THUMBS = pathlib.Path("out/thumbs"); THUMBS.mkdir(parents=True, exist_ok=True)


@app.get("/api/thumb/{video_id}")
def thumb(video_id: str, t: float = 0):
    """A poster frame at the claim's own timestamp.

    Lazy-loading the video means no frame ever decodes, so the players render as black boxes.
    A ~20KB still is cheaper than video metadata AND shows the moment being cited.
    """
    src = pathlib.Path(f"data/{video_id}.mp4")
    if not src.exists():
        return Response(status_code=404)
    out = THUMBS / f"{video_id}_{int(t)}.jpg"
    if not out.exists():
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", str(t),
                        "-i", str(src), "-frames:v", "1", "-vf", "scale=520:-2",
                        "-q:v", "6", str(out)], check=False)
    if not out.exists():
        return Response(status_code=404)
    return FileResponse(out, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/supercut")
def make_supercut():
    rs = clips()
    if not rs:
        return {"error": "no clips"}
    msg = A.supercut([{"videoId": r["videoId"], "startSec": r["startSec"], "endSec": r["endSec"]}
                      for r in rs], "contradictions")
    return {"message": msg, "src": "/out/contradictions.mp4", "count": len(rs)}


class Link(BaseModel):
    url: str


@app.post("/api/ingest")
def ingest(link: Link):
    """Download + analyze + write, streaming progress so the graph is visibly growing.

    ponytail: shells out to the CLI rather than importing it, so the UI and the terminal run
    exactly the same code path. Swap to an in-process call if you need structured progress.
    """
    vid = link.url.strip().split("v=")[-1].split("&")[0]

    def run():
        def ev(**kw): return f"data: {json.dumps({'id': vid, **kw})}\n\n"

        mp4 = pathlib.Path(f"data/{vid}.mp4")
        if not mp4.exists():
            yield ev(step="fetching")
            # YouTube needs all three: a current yt-dlp, the JS-challenge solver, and browser
            # cookies. Drop any one and it is either "sign in to confirm you're not a bot"
            # or a 403 on the media fetch.
            dl = subprocess.run(["yt-dlp", "--remote-components", "ejs:github",
                                 "--cookies-from-browser", "chrome",
                                 "-f", "bv*[height<=480]+ba/b[height<=480]/b",
                                 "--merge-output-format", "mp4",
                                 "-o", f"data/{vid}.raw.%(ext)s", "--write-info-json",
                                 f"https://www.youtube.com/watch?v={vid}"],
                                capture_output=True, text=True)
            raw = pathlib.Path(f"data/{vid}.raw.mp4")
            if not raw.exists():
                yield ev(step="error", error="download failed",
                         detail=(dl.stderr or dl.stdout)[-300:])
                return
            yield ev(step="clipping")
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", "60",
                            "-i", str(raw), "-t", "180", "-c:v", "libx264",
                            "-crf", "23", "-preset", "veryfast", "-c:a", "aac",
                            "-movflags", "+faststart", str(mp4)], check=False)
            raw.unlink(missing_ok=True)
            if not mp4.exists():
                yield ev(step="error", error="clip failed")
                return

        yield ev(step="analyzing")
        p = subprocess.run([sys.executable, "ingest.py", str(mp4)],
                           capture_output=True, text=True)
        # a non-zero exit, or a SKIPPED line, means nothing reached the graph. Say so —
        # reporting "done" on a failed ingest is worse than failing.
        if p.returncode != 0 or "SKIPPED" in p.stdout:
            yield ev(step="error", error="analysis failed",
                     detail=(p.stderr or p.stdout)[-300:])
            return
        yield ev(step="done", log=p.stdout[-400:])

    return StreamingResponse(run(), media_type="text/event-stream")
