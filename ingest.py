#!/usr/bin/env python3
"""Video -> modality-split context graph.

  python ingest.py data/*.mp4        # analyze (cached) + write to Neo4j
  python ingest.py --from-cache      # rebuild graph from cache/, no API calls
  python ingest.py --selftest        # offline check of the parse+merge logic

The point of this file: TwelveLabs returns what was SPOKEN, what was ON SCREEN, and what was
VISIBLE as three separate fields. We keep them separate all the way into Neo4j as
(:Scene)-[:MENTIONS {modality}]->(:Entity). A fused embedding cannot represent that split,
which is what makes "shown but never said" answerable at all.
"""
import json, os, sys, time, pathlib, re

CACHE = pathlib.Path("cache"); CACHE.mkdir(exist_ok=True)
MODEL = "pegasus1.5"
LUNA  = "gpt-5.6-luna"    # cheap tier: high-volume entity extraction

# One segment definition. Three separate channels -> three modalities in the graph.
SEGMENTS = [{
    "id": "scenes",
    "description": (
        "Segment this health/nutrition video into coherent scenes, where each scene covers one "
        "distinct claim, study, or argument. Start a new scene when the topic or the claim changes."
    ),
    "fields": [
        {"name": "summary", "type": "string",
         "description": "One sentence describing what happens in this scene."},
        {"name": "spoken_claims", "type": "array", "items": {"type": "string"},
         "description": ("Factual or causal claims SPOKEN ALOUD in this scene, quoted as closely as "
                         "possible. Only assertions about the world, e.g. 'seed oils cause "
                         "inflammation'. Not opinions, greetings, or sponsor reads. Empty if none.")},
        {"name": "onscreen_text", "type": "array", "items": {"type": "string"},
         "description": ("Text RENDERED ON SCREEN in this scene: study titles, chart axis labels and "
                         "numbers, statistics, citations, journal names, on-screen captions and "
                         "disclaimers. Transcribe verbatim. Empty if none.")},
        {"name": "visible_entities", "type": "array", "items": {"type": "string"},
         "description": ("Substances, foods, biomarkers, studies or organizations VISIBLE on screen "
                         "(in charts, packaging, slides). e.g. 'linoleic acid', 'canola oil', "
                         "'omega-6', 'LDL'. Empty if none.")},
        {"name": "evidence_shown", "type": "string", "enum": ["study", "chart", "anecdote", "none"],
         "description": "What kind of evidence, if any, is displayed on screen during this scene."},
    ],
}]

SCHEMA = """
CREATE CONSTRAINT video_id IF NOT EXISTS FOR (v:Video)  REQUIRE v.id   IS UNIQUE;
CREATE CONSTRAINT scene_id IF NOT EXISTS FOR (s:Scene)  REQUIRE s.id   IS UNIQUE;
CREATE CONSTRAINT ent_name IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE;
CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (c:Claim)  REQUIRE c.id   IS UNIQUE;
"""

# Deterministic code writes. The agent only ever reads. (Jeremy Adams' rubric, and it is also
# the only way a verdict can be trusted.)
WRITE = """
MERGE (v:Video {id:$vid})
  ON CREATE SET v.title=$title, v.url=$url, v.channel=$channel
MERGE (s:Scene {id:$sid})
  SET s.videoId=$vid, s.startSec=$start, s.endSec=$end,
      s.summary=$summary, s.evidenceShown=$evidence
MERGE (v)-[:HAS_SCENE]->(s)
WITH s
UNWIND $mentions AS m
  MERGE (e:Entity {name:m.name})
    ON CREATE SET e.type=m.type
  MERGE (s)-[r:MENTIONS {modality:m.modality}]->(e)
    ON CREATE SET r.raw=m.raw
"""

CLAIMS = """
MATCH (s:Scene {id:$sid})
UNWIND $claims AS c
  MERGE (cl:Claim {id:c.id})
    ON CREATE SET cl.text=c.text, cl.modality=c.modality
  MERGE (s)-[:ASSERTS]->(cl)
"""

CHAIN = """
MATCH (:Video {id:$vid})-[:HAS_SCENE]->(s:Scene)
WITH s ORDER BY s.startSec
WITH collect(s) AS ss
UNWIND range(0, size(ss)-2) AS i
  WITH ss[i] AS a, ss[i+1] AS b
  MERGE (a)-[:NEXT]->(b)
"""


def norm(name):
    """Entity key. Lowercase, strip punctuation/plurals so entities MERGE across videos.

    ponytail: naive singularization. Upgrade to an LLM canonicalization pass if the graph
    shows near-duplicate nodes on the projector.
    """
    n = re.sub(r"[^a-z0-9\s-]", "", name.lower().strip())
    n = re.sub(r"\s+", " ", n)
    return re.sub(r"(?<=[a-z])s$", "", n) if len(n) > 3 else n


def guess_type(name):
    n = name.lower()
    if any(k in n for k in ("study", "trial", "meta-analysis", "review", "journal")): return "study"
    if any(k in n for k in ("oil", "fat", "acid", "omega", "vitamin")):               return "substance"
    if any(k in n for k in ("ldl", "hdl", "crp", "inflammation", "cholesterol")):     return "biomarker"
    return "topic"


def to_rows(video, data, enrich=None):
    """TwelveLabs segments -> (scene, mentions, claims) rows.

    `enrich(spoken, onscreen)` is the OpenAI Analysis Agent: it pulls the *entities* out of each
    channel's text. Without it we would make an Entity node out of a whole sentence, which is how
    "is the video helpful hit the like button" ends up in the graph. Pure otherwise, so
    --selftest runs offline.
    """
    out = []
    for i, seg in enumerate(data.get("scenes", [])):
        meta = seg.get("metadata") or {}
        sid = f"{video['id']}-s{i:02d}"
        mentions, claims = [], []
        spoken = meta.get("spoken_claims") or []
        onscreen = meta.get("onscreen_text") or []

        # the modality split — this is the whole point of the build
        for raw in meta.get("visible_entities") or []:
            mentions.append({"name": norm(raw), "type": guess_type(raw),
                             "modality": "visual", "raw": raw})

        ents = enrich(spoken, onscreen) if enrich else {"speech": [], "ocr": []}
        for modality in ("speech", "ocr"):
            for raw in ents.get(modality) or []:
                mentions.append({"name": norm(raw), "type": guess_type(raw),
                                 "modality": modality, "raw": raw})

        # full text stays as Claims — contradiction detection needs the whole sentence
        for raw in onscreen:
            claims.append({"id": f"{sid}-o{len(claims)}", "text": raw, "modality": "ocr"})
        for raw in spoken:
            claims.append({"id": f"{sid}-c{len(claims)}", "text": raw, "modality": "speech"})

        out.append({
            "sid": sid, "vid": video["id"],
            "start": float(seg.get("start_time", 0)), "end": float(seg.get("end_time", 0)),
            "summary": meta.get("summary", ""), "evidence": meta.get("evidence_shown", "none"),
            "mentions": [m for m in mentions if m["name"]], "claims": claims,
        })
    return out


def analyze(path):
    """Upload + segment one video. Cached — quota is 600 cumulative minutes and does not reset."""
    vid = pathlib.Path(path).stem
    cached = CACHE / f"{vid}.json"
    if cached.exists():
        print(f"  cache hit {vid}")
        return json.loads(cached.read_text())

    from twelvelabs import TwelveLabs
    from twelvelabs.types import AsyncResponseFormat, VideoContext_AssetId
    key = os.environ.get("TWELVELABS_API_KEY") or os.environ["TL_API_KEY"]
    client = TwelveLabs(api_key=key)

    print(f"  uploading {vid} ...")
    asset = client.assets.create(method="direct", file=open(path, "rb"))  # local <=200MB, no S3
    while True:
        a = client.assets.retrieve(asset.id)
        if a.status == "ready": break
        if a.status == "failed": raise RuntimeError(f"asset failed: {vid}")
        time.sleep(5)

    print(f"  segmenting {vid} ...")
    task = client.analyze_async.tasks.create(
        video=VideoContext_AssetId(asset_id=asset.id),
        model_name=MODEL, analysis_mode="time_based_metadata",
        response_format=AsyncResponseFormat(type="segment_definitions",
                                            segment_definitions=SEGMENTS))
    while True:
        t = client.analyze_async.tasks.retrieve(task.task_id)   # task_id, NOT id
        if t.status == "ready": break
        if t.status == "failed": raise RuntimeError(f"segment failed: {vid}")
        time.sleep(5)

    data = json.loads(t.result.data)          # result.data is a JSON-encoded STRING
    cached.write_text(json.dumps(data, indent=2))
    print(f"  {vid}: {len(data.get('scenes', []))} scenes")
    return data


def make_enricher():
    """Analysis Agent (OpenAI) from Elizabeth's pipeline slide: raw scene text -> typed entities,
    kept separate per channel so `modality` stays honest. Cached per (spoken, onscreen) pair."""
    from openai import OpenAI
    from pydantic import BaseModel
    client, memo = OpenAI(), {}

    class Ents(BaseModel):
        spoken_entities: list[str]
        onscreen_entities: list[str]

    def enrich(spoken, onscreen):
        if not spoken and not onscreen:
            return {"speech": [], "ocr": []}
        key = json.dumps([spoken, onscreen], sort_keys=True)
        if key in memo:
            return memo[key]
        r = client.chat.completions.parse(
            model=LUNA,
            messages=[
                {"role": "system", "content":
                 "Extract nutrition/health ENTITIES only: substances (linoleic acid, canola oil), "
                 "biomarkers (LDL, inflammation), study or journal names, organizations. "
                 "Short noun phrases, lowercase, no sentences. Skip channel chrome ('like and "
                 "subscribe'), pleasantries, and bare numbers. Return [] rather than guessing."},
                {"role": "user", "content":
                 f"SPOKEN:\n{chr(10).join(spoken) or '(none)'}\n\nON SCREEN:\n{chr(10).join(onscreen) or '(none)'}"},
            ],
            response_format=Ents,
        ).choices[0].message.parsed
        memo[key] = {"speech": r.spoken_entities, "ocr": r.onscreen_entities}
        return memo[key]

    return enrich


def meta_for(path):
    """Title/channel from the yt-dlp sidecar, so citations name a real source."""
    vid = pathlib.Path(path).stem
    info = pathlib.Path(f"data/{vid}.raw.info.json")
    d = json.loads(info.read_text()) if info.exists() else {}
    return {"id": vid, "title": d.get("title", vid), "channel": d.get("channel", "unknown"),
            "url": f"https://www.youtube.com/watch?v={vid}"}


def write(rows, video):
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
                               auth=(os.environ.get("NEO4J_USER", "neo4j"),
                                     os.environ["NEO4J_PASSWORD"]))
    with drv.session() as s:
        for stmt in (x.strip() for x in SCHEMA.split(";")):
            if stmt:
                s.run(stmt)
        for r in rows:
            s.run(WRITE, **r, **{k: video[k] for k in ("title", "url", "channel")})
            if r["claims"]:
                s.run(CLAIMS, sid=r["sid"], claims=r["claims"])
        s.run(CHAIN, vid=video["id"])
        n = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        e = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    drv.close()
    return n, e


def selftest():
    fake = {"scenes": [{"start_time": 0, "end_time": 12, "metadata": {
        "summary": "Host claims seed oils drive inflammation.",
        "spoken_claims": ["Seed oils cause chronic inflammation"],
        "onscreen_text": ["Ramsden et al. 2013, BMJ"],
        "visible_entities": ["Linoleic Acid", "canola oils"],
        "evidence_shown": "study"}}]}
    rows = to_rows({"id": "vid1"}, fake,
                   lambda sp, on: {"speech": ["inflammation"], "ocr": ["bmj"]})
    r = rows[0]
    assert r["sid"] == "vid1-s00", r["sid"]
    assert {m["modality"] for m in r["mentions"]} == {"visual", "ocr", "speech"}
    assert not any(len(m["name"]) > 40 for m in r["mentions"])   # no sentences as entities
    names = {m["name"] for m in r["mentions"] if m["modality"] == "visual"}
    assert names == {"linoleic acid", "canola oil"}, names   # plural folded -> merges across videos
    assert len(r["claims"]) == 2 and r["evidence"] == "study"
    print("selftest ok")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    if "--selftest" in flags:
        selftest(); sys.exit(0)

    paths = args or sorted(str(p) for p in pathlib.Path("data").glob("*.mp4")
                           if ".raw." not in p.name)   # skip yt-dlp temp files
    if not paths:
        sys.exit("no videos in data/ — run ./fetch_corpus.sh first")

    enricher = None if "--no-enrich" in flags else make_enricher()
    total_n = total_e = 0
    for p in paths:
        print(f"[{pathlib.Path(p).stem}]")
        try:
            data = json.loads((CACHE / f"{pathlib.Path(p).stem}.json").read_text()) \
                if "--from-cache" in flags else analyze(p)
        except Exception as e:
            print(f"  SKIPPED: {type(e).__name__}: {str(e)[:160]}")
            continue
        v = meta_for(p)
        rows = to_rows(v, data, enricher)
        if "--analyze-only" in flags:      # cache now, write to Neo4j when Aura is up
            print(f"  cached {len(rows)} scenes, "
                  f"{sum(len(r['mentions']) for r in rows)} mentions")
            continue
        total_n, total_e = write(rows, v)
        print(f"  wrote {len(rows)} scenes, "
              f"{sum(len(r['mentions']) for r in rows)} mentions")

    if "--analyze-only" not in flags:
        print(f"\nGRAPH: {total_n} nodes, {total_e} relationships")  # say this out loud on stage
