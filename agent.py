#!/usr/bin/env python3
"""Phase 3 — Strands orchestration over the video context graph.

  python agent.py "which claims are shown on screen but never spoken?"
  python agent.py --supercut "every scene where seed oils are linked to inflammation"
  python agent.py --selftest

Elizabeth's slide is the rubric: Ingestion (TwelveLabs) -> Analysis (OpenAI) -> Indexing (Neo4j),
"one agent calls the next as a tool". That is agents-as-tools below, not a main() in a costume.

The write path is the part judges asked about twice: deterministic Python writes to Neo4j
(ingest.py / verify.py); the agent gets a Cypher tool that refuses anything but reads. The agent
can read the graph, but it can never write an edge the footage does not support.
"""
import os, re, subprocess, sys, pathlib, json
os.environ.setdefault("OTEL_SDK_DISABLED", "true")   # before strands imports, keeps the demo terminal clean

from strands import Agent, tool
from strands.models.openai_responses import OpenAIResponsesModel

TERRA = "gpt-5.6-terra"
MODEL = OpenAIResponsesModel(model_id=TERRA)   # /v1/responses: GPT-5.6 + function tools needs it
OUT = pathlib.Path("out"); OUT.mkdir(exist_ok=True)
MAX_CLIP_SEC = 12.0   # per-clip cap in a supercut; scene boundaries are far longer than a demo

# Word boundaries, not substrings: plain `in` matching rejects legitimate reads, because OFFSET
# contains SET, DROPPED contains DROP, and ASSET contains SET. Failing closed on a valid query
# still looks broken on stage.
WRITE_RE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH)\b"
    r"|\bLOAD\s+CSV\b"
    r"|\bCALL\s+(DB|APOC)\.")


def _driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(os.environ["NEO4J_URI"],
                                auth=(os.environ.get("NEO4J_USER", "neo4j"),
                                      os.environ["NEO4J_PASSWORD"]))


def is_read_only(q):
    """Reject writes before they reach the database. Fails closed on anything ambiguous."""
    return not WRITE_RE.search(re.sub(r"\s+", " ", (q or "").upper()))


@tool
def query_graph(cypher_query: str) -> str:
    """Run a READ-ONLY Cypher query against the video context graph and return rows as JSON.

    The graph holds health/nutrition videos split into scenes, where every mention keeps the
    channel it came from. That `modality` property is the point: 'visual' means it was seen,
    'speech' means it was said aloud, 'ocr' means it was written on screen.

    Nodes:
      (:Video  {id, title, url, channel})
      (:Scene  {id, videoId, startSec, endSec, summary, evidenceShown})
      (:Entity {name, type})            type: substance|biomarker|study|topic|claim-subject
      (:Claim  {id, text, modality})    modality: speech|ocr
      (:Evidence {url, title, snippet})

    Relationships:
      (Video)-[:HAS_SCENE]->(Scene)
      (Scene)-[:NEXT]->(Scene)                        ordered within one video
      (Scene)-[:MENTIONS {modality, raw}]->(Entity)   modality: visual|speech|ocr
      (Scene)-[:ASSERTS]->(Claim)
      (Claim)-[:CONTRADICTS {about, rationale}]->(Claim)
      (Claim)-[:VERIFIED_AS {verdict, checkedAt}]->(Evidence)   verdict: SUPPORTED|DISPUTED|NO_SOURCE_FOUND

    Useful shapes:
      shown but never said:
        MATCH (e:Entity)<-[m:MENTIONS]-(:Scene)
        WITH e, collect(DISTINCT m.modality) AS mods
        WHERE 'ocr' IN mods AND NOT 'speech' IN mods RETURN e.name, mods
      who disagrees with whom:
        MATCH (v1:Video)-[:HAS_SCENE]->(:Scene)-[:ASSERTS]->(a:Claim)-[c:CONTRADICTS]->(b:Claim)
        MATCH (v2:Video)-[:HAS_SCENE]->(:Scene)-[:ASSERTS]->(b)
        RETURN v1.channel, a.text, v2.channel, b.text, c.about
      connection between two videos:
        MATCH p=shortestPath((:Video {id:$a})-[:HAS_SCENE|MENTIONS*..8]-(:Video {id:$b}))
        RETURN [n IN nodes(p) | coalesce(n.title, n.name, n.summary)]

    ALWAYS return v.url + '&t=' + toString(toInteger(s.startSec)) as a `jumpTo` column so every
    answer cites a video and a second. An answer without a timestamp is an opinion.

    Video.id is an opaque YouTube id ('FDIgoBusMxY'), never a channel name. Do not build URLs
    yourself — select v.url from the graph and reproduce what comes back exactly.

    Args:
        cypher_query: A read-only Cypher query. Writes are refused.
    """
    if not is_read_only(cypher_query):
        return "Refused: this tool is read-only. Only the ingest pipeline writes to the graph."
    drv = _driver()
    try:
        with drv.session() as s:
            rows = [dict(r) for r in s.run(cypher_query)]
    except Exception as e:
        return f"Query error: {e}"
    finally:
        drv.close()
    return json.dumps(rows[:40], default=str, indent=2) if rows else "No results."


CONTRADICTIONS = """
MATCH (v1:Video)-[:HAS_SCENE]->(s1:Scene)-[:ASSERTS]->(a:Claim)-[x:CONTRADICTS]->(b:Claim)
MATCH (v2:Video)-[:HAS_SCENE]->(s2:Scene)-[:ASSERTS]->(b)
OPTIONAL MATCH (a)-[ver:VERIFIED_AS]->(ev:Evidence)
// one row per CONTRADICTS edge. A claim checked against two papers has two VERIFIED_AS edges,
// and without this collect the same disagreement came back — and rendered — once per source.
WITH x, v1, s1, a, v2, s2, b,
     [v IN collect(DISTINCT ver.verdict) WHERE v IS NOT NULL] AS verdicts,
     [u IN collect(DISTINCT ev.url) WHERE u IS NOT NULL] AS sources
RETURN x.about AS about,
       v1.channel AS channelA, a.text AS claimA,
       v1.url + '&t=' + toString(toInteger(s1.startSec)) AS jumpToA,
       v2.channel AS channelB, b.text AS claimB,
       v2.url + '&t=' + toString(toInteger(s2.startSec)) AS jumpToB,
       CASE size(verdicts) WHEN 0 THEN null WHEN 1 THEN verdicts[0] ELSE 'MIXED' END AS verdict,
       sources[0] AS sourceUrl,      // kept so existing callers still read one URL
       sources AS sources
ORDER BY about
"""


@tool
def find_contradictions() -> str:
    """Every pair of claims from different channels that genuinely contradict each other.

    Returns both channels, both claim texts, a real timestamp link for each, and — where the
    claim was checked against the literature — the verdict and the source URL.

    Prefer this over writing the join yourself: the links come back ready to print, so there is
    nothing to assemble and nothing to guess.
    """
    drv = _driver()
    try:
        with drv.session() as s:
            rows = [dict(r) for r in s.run(CONTRADICTIONS)]
    finally:
        drv.close()
    return json.dumps(rows, default=str, indent=2) if rows else "No contradictions recorded yet."


@tool
def build_supercut(cypher_query: str, name: str = "supercut") -> str:
    """Run a read-only Cypher query and stitch the matching clips into one video file.

    The query must return `videoId`, `startSec` and `endSec` columns. Those rows ARE an edit
    decision list, so the graph query assembles the cut directly.

    Args:
        cypher_query: Read-only Cypher returning videoId, startSec, endSec.
        name: Output filename stem.
    """
    if not is_read_only(cypher_query):
        return "Refused: this tool is read-only."
    drv = _driver()
    with drv.session() as s:
        rows = [dict(r) for r in s.run(cypher_query)]
    drv.close()
    clips = [r for r in rows if r.get("videoId") and r.get("startSec") is not None]
    if not clips:
        return "No clips matched — nothing to cut."
    return supercut(clips, name)


def supercut(clips, name="supercut"):
    """ffmpeg concat over (videoId, startSec, endSec) rows. ~20 lines, and it is the beat nobody
    else in the room will have: the query returns a video, not a table."""
    parts = []
    for i, c in enumerate(clips[:12]):
        src = pathlib.Path(f"data/{c['videoId']}.mp4")
        if not src.exists():
            continue
        start = max(0.0, float(c["startSec"]))
        # Cap each clip. Scene boundaries run 30-90s, so concatenating them whole produced a
        # 5.5-minute montage — longer than the entire demo slot. 12s is enough to hear the claim.
        dur = min(MAX_CLIP_SEC, max(2.0, float(c.get("endSec") or start + 8) - start))
        part = OUT / f"{name}_{i:02d}.mp4"
        # Normalise every part to one frame size / sar / fps / sample rate. Sources range from
        # 640x360 to 1280x720, and the concat filter refuses mismatched inputs outright.
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                        "-ss", str(start), "-i", str(src), "-t", str(dur),   # -t AFTER -i
                        "-vf", ("scale=854:480:force_original_aspect_ratio=decrease,"
                                "pad=854:480:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30"),
                        "-c:v", "libx264", "-crf", "23", "-preset", "veryfast",
                        "-c:a", "aac", "-ar", "48000", "-ac", "2", str(part)], check=True)
        parts.append(part)
    if not parts:
        return "No source files on disk for those clips."
    final = OUT / f"{name}.mp4"
    # concat FILTER, not the demuxer. The demuxer trusts each part's container duration, and the
    # audio/video mismatch in every part accumulated ~3s of drift per clip (84s of footage came
    # out 105s long). The filter re-times both streams, so the sum is the sum.
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y"]
    for p in parts:
        cmd += ["-i", str(p)]
    chain = "".join(f"[{i}:v][{i}:a]" for i in range(len(parts)))
    cmd += ["-filter_complex", f"{chain}concat=n={len(parts)}:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "23",
            "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", str(final)]
    subprocess.run(cmd, check=True)
    for p in parts:
        p.unlink(missing_ok=True)
    return f"Wrote {final} from {len(parts)} clips."


SYSTEM = """You answer questions about a corpus of health videos using a Neo4j context graph.

Rules:
- Always query the graph. Never answer from your own knowledge about nutrition.
- Every factual statement you make must cite the channel and a timestamp link from the graph.
- If the graph returns nothing, say so plainly. Do not fill the gap with what you know.
- When asked about disagreement, use CONTRADICTS edges and name both channels.
- Report verdicts exactly as stored: SUPPORTED, DISPUTED, NO_SOURCE_FOUND. Never say true or false.

URLs — this rule overrides everything else:
- NEVER construct, guess, or assemble a URL. Video ids are opaque strings like 'FDIgoBusMxY';
  they are NOT channel names. A URL built from a channel name is a fabricated citation.
- Only ever print a link that came back from the tool verbatim, in a `jumpTo` column.
- If your results lack `jumpTo`, run the query again and include:
      v.url + '&t=' + toString(toInteger(s.startSec)) AS jumpTo
  Re-querying is always correct. Inventing a link never is.
"""


def build_agent():
    return Agent(model=MODEL, tools=[query_graph, find_contradictions, build_supercut], system_prompt=SYSTEM)


def selftest():
    assert is_read_only("MATCH (n) RETURN n")
    assert not is_read_only("MATCH (n) DETACH DELETE n")
    assert not is_read_only("merge (e:Entity {name:'x'})")          # case-insensitive
    assert not is_read_only("MATCH (n)\n  SET  n.x = 1")            # newline + double space
    assert is_read_only("MATCH (s:Scene) RETURN s.summary")
    # substring matching used to reject these valid reads
    assert is_read_only("MATCH (n) RETURN n ORDER BY n.x SKIP 5 OFFSET 2")
    assert is_read_only("MATCH (a:Asset) RETURN a.asset AS asset")
    assert is_read_only("MATCH (n) RETURN n.dropped AS d")
    assert not is_read_only("MATCH (n) FOREACH (i IN [1] | SET n.x=1)")
    assert not is_read_only("CALL apoc.periodic.iterate('MATCH (n) RETURN n','DELETE n',{})")
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest(); sys.exit(0)
    q = " ".join(a for a in sys.argv[1:] if not a.startswith("--"))
    if not q:
        sys.exit('usage: python agent.py "your question"')
    build_agent()(q)
