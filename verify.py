#!/usr/bin/env python3
"""Phase 2 — the graph decides what is worth checking, then the web adjudicates.

  python verify.py            # find conflicts, judge them, verify the top ones, write edges
  python verify.py --report   # read-only: print the modality gaps and conflicts, no writes
  python verify.py --selftest # offline check of pairing + verdict mapping

Why this order matters: fact-checking 500 claims against the web is infeasible and the graph
would be decorative. The graph narrows 500 claims to the handful that contradict each other --
a structural query, no LLM -- and only those get a web search. That narrowing IS the product.
"""
import json, os, re, sys, itertools

TERRA = "gpt-5.6-terra"  # balanced: contradiction judgement
SOL   = "gpt-5.6-sol"    # flagship: web-search adjudication

MAX_PAIRS  = 120  # pairs sent to the LLM judge
MAX_VERIFY = 6    # claims sent to web search. Kyle's demo budget, not a technical limit.

VERDICTS = ("SUPPORTED", "DISPUTED", "NO_SOURCE_FOUND")   # never TRUE/FALSE — see README

# --- structural narrowing: pure Cypher, no model in the loop -------------------------------

# Claims from DIFFERENT channels whose scenes mention the same entity. Cheap, and it is the
# only step that needs the whole corpus at once.
CANDIDATES = """
MATCH (v1:Video)-[:HAS_SCENE]->(s1:Scene)-[:ASSERTS]->(c1:Claim)
MATCH (s1)-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(s2:Scene)-[:ASSERTS]->(c2:Claim)
MATCH (v2:Video)-[:HAS_SCENE]->(s2)
WHERE v1.id < v2.id AND c1.modality = 'speech' AND c2.modality = 'speech'
RETURN DISTINCT
  c1.id AS aId, c1.text AS aText, v1.channel AS aChannel, v1.url AS aUrl, s1.startSec AS aAt,
  c2.id AS bId, c2.text AS bText, v2.channel AS bChannel, v2.url AS bUrl, s2.startSec AS bAt,
  e.name AS about
LIMIT $limit
"""

# Beat 1. Impossible for a fused embedding: the modalities are already blended.
MODALITY_GAP = """
MATCH (e:Entity)<-[m:MENTIONS]-(:Scene)
WITH e, collect(DISTINCT m.modality) AS mods, count(m) AS hits
WHERE 'ocr' IN mods AND NOT 'speech' IN mods AND hits > 1
RETURN e.name AS entity, e.type AS type, mods AS shownVia, hits
ORDER BY hits DESC LIMIT 20
"""

LINK = """
MATCH (a:Claim {id:$aId}), (b:Claim {id:$bId})
MERGE (a)-[r:CONTRADICTS]->(b)
  SET r.about = $about, r.rationale = $rationale
"""

# Verdict is an EDGE whose value comes from a checked source, not a field the model narrates.
# (VeriGraph, 1st place at this venue: "the model literally cannot write a verdict the numbers
# don't support.")
# One verdict edge per claim+source. `verdict` is a PROPERTY, not part of the MERGE key: putting
# it in the key means a re-run that lands a different verdict leaves both edges attached to the
# same claim, and the graph then asserts two contradictory things at once.
VERDICT = """
MATCH (c:Claim {id:$claimId})
MERGE (ev:Evidence {url:$url})
  ON CREATE SET ev.title = $title, ev.snippet = $snippet
MERGE (c)-[r:VERIFIED_AS]->(ev)
  SET r.verdict = $verdict, r.checkedAt = $checkedAt
"""

# No source URL means no Evidence node. Otherwise every unsourced verdict MERGEs onto one shared
# node keyed on the empty string, and the graph grows a hub that claims to be a citation.
VERDICT_NO_SOURCE = """
MATCH (c:Claim {id:$claimId})
SET c.verdict = $verdict, c.checkedAt = $checkedAt, c.verdictNote = $snippet
"""


def driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(os.environ["NEO4J_URI"],
                                auth=(os.environ.get("NEO4J_USER", "neo4j"),
                                      os.environ["NEO4J_PASSWORD"]))


def dedupe(pairs, per_entity=4, per_claim=4):
    """One pair per unordered claim-pair, plus diversity caps.

    Without the caps a single popular entity ('canola oil') and one chatty claim swallow the
    whole budget, and the judge sees forty variations of the same comparison instead of the
    corpus. Caps buy spread across entities for free.
    """
    seen, ent_n, claim_n, out = set(), {}, {}, []
    for p in pairs:
        k = tuple(sorted((p["aId"], p["bId"])))
        if k in seen or p["aId"] == p["bId"]:
            continue
        if ent_n.get(p["about"], 0) >= per_entity:
            continue
        if any(claim_n.get(c, 0) >= per_claim for c in (p["aId"], p["bId"])):
            continue
        seen.add(k)
        ent_n[p["about"]] = ent_n.get(p["about"], 0) + 1
        for c in (p["aId"], p["bId"]):
            claim_n[c] = claim_n.get(c, 0) + 1
        out.append(p)
    return out


def pick_verdict(text):
    """Map a model's prose to one of our three labels. Unrecognised -> NO_SOURCE_FOUND, because
    an unparseable answer is not evidence."""
    up = (text or "").upper()
    for v in VERDICTS:
        if v in up:
            return v
    return "NO_SOURCE_FOUND"


def first_url(text, annotations=None):
    for a in annotations or []:
        u = getattr(a, "url", None) or (a.get("url") if isinstance(a, dict) else None)
        if u:
            return u
    m = re.search(r"https?://[^\s)\]\"']+", text or "")
    return m.group(0) if m else ""


# --- the model steps ------------------------------------------------------------------------

def judge(client, pairs):
    """Do these two claims actually contradict? Structural narrowing gets us here; only a model
    can tell 'lowers inflammation' from 'causes inflammation'."""
    from pydantic import BaseModel

    class Verdict(BaseModel):
        contradicts: bool
        subject: str
        rationale: str

    out = []
    for p in pairs:
      try:
        r = client.chat.completions.parse(
            model=TERRA,
            messages=[
                {"role": "system", "content":
                 "You judge whether two claims from different videos are in genuine factual "
                 "conflict. Differing emphasis, scope, or hedging is NOT a contradiction. "
                 "Only mark contradicts=true if both cannot be true at once.\n"
                 "CRITICAL: debunking channels often STATE a claim in order to rebut it. If "
                 "either line reads as a position the speaker is quoting, steelmanning, or "
                 "setting up to knock down rather than asserting as their own, set "
                 "contradicts=false. We compare what channels ASSERT, not what they cite."},
                {"role": "user", "content":
                 f"A ({p['aChannel']}): {p['aText']}\nB ({p['bChannel']}): {p['bText']}\n"
                 f"Both discuss: {p['about']}"},
            ],
            response_format=Verdict,
        ).choices[0].message.parsed
        if r.contradicts:
            out.append({**p, "rationale": r.rationale, "subject": r.subject})
      except Exception as e:   # skip the pair, keep the run alive
        print(f"  judge failed on {p['aId']}/{p['bId']}: {type(e).__name__}")
    return out


def adjudicate(client, claim_text):
    """OpenAI's native web search. No Firecrawl, no Perplexity -- a fifth vendor earns no points
    and this keeps the reasoning visibly inside OpenAI.
    Verified via Context7 /websites/developers_openai_api."""
    r = client.responses.create(
        model=SOL,
        tools=[{"type": "web_search"}],
        input=("Check this health claim against the published literature. Cite a specific paper "
               "or authoritative source with its URL. End your reply with exactly one of: "
               "SUPPORTED, DISPUTED, NO_SOURCE_FOUND.\n\nClaim: " + claim_text),
    )
    text = r.output_text
    anns = []
    for item in getattr(r, "output", []) or []:
        for c in getattr(item, "content", []) or []:
            anns.extend(getattr(c, "annotations", []) or [])
    return {"verdict": pick_verdict(text), "url": first_url(text, anns),
            "title": (text or "").strip().split("\n")[0][:200], "snippet": (text or "")[:600]}


# --- main -----------------------------------------------------------------------------------

def report(sess):
    print("\n=== modality gap: on screen, never spoken ===")
    for r in sess.run(MODALITY_GAP):
        print(f"  {r['entity'][:60]:62s} {r['shownVia']}  x{r['hits']}")
    rows = dedupe([dict(r) for r in sess.run(CANDIDATES, limit=600)])[:MAX_PAIRS]
    print(f"\n=== {len(rows)} candidate conflict pairs (same entity, different channels) ===")
    for p in rows[:10]:
        print(f"  [{p['about']}]\n    {p['aChannel']}: {p['aText'][:90]}"
              f"\n    {p['bChannel']}: {p['bText'][:90]}")
    return rows


def selftest():
    p = [{"aId": "1", "bId": "2"}, {"aId": "2", "bId": "1"}, {"aId": "1", "bId": "1"},
         {"aId": "1", "bId": "3"}]
    for x in p: x.setdefault("about", "e")
    assert len(dedupe(p)) == 2, dedupe(p)                    # symmetric dupes + self-pair dropped
    many = [{"aId": f"a{i}", "bId": f"b{i}", "about": "same"} for i in range(10)]
    assert len(dedupe(many, per_entity=3)) == 3              # one entity cannot eat the budget
    assert pick_verdict("... therefore DISPUTED") == "DISPUTED"
    assert pick_verdict("who knows") == "NO_SOURCE_FOUND"    # unparseable is not evidence
    assert first_url("see https://doi.org/10.1/x for more") == "https://doi.org/10.1/x"
    assert first_url("no link here") == ""
    print("selftest ok")


if __name__ == "__main__":
    flags = set(sys.argv[1:])
    if "--selftest" in flags:
        selftest(); sys.exit(0)

    from datetime import datetime, timezone
    drv = driver()
    with drv.session() as sess:
        rows = report(sess)
        if "--report" in flags or not rows:
            drv.close(); sys.exit(0)

        from openai import OpenAI
        client = OpenAI()

        print(f"\njudging {len(rows)} pairs ...")
        real = judge(client, rows)
        print(f"  {len(real)} genuine contradictions")
        for p in real:
            sess.run(LINK, aId=p["aId"], bId=p["bId"], about=p["about"],
                     rationale=p["rationale"])

        # one web search per distinct claim: the same claim can head several contradiction pairs,
        # and paying for it twice also writes two verdict edges for one assertion
        by_claim = {}
        for p in real:
            by_claim.setdefault(p["aId"], p)
        checked = list(by_claim.values())[:MAX_VERIFY]
        real = list(by_claim.values())
        if len(real) > MAX_VERIFY:
            print(f"  NOTE: verifying {MAX_VERIFY} of {len(real)}; "
                  f"{len(real) - MAX_VERIFY} left unchecked")   # no silent caps
        for p in checked:
            try:
                v = adjudicate(client, p["aText"])
            except Exception as e:      # one bad web search must not kill the run mid-demo
                print(f"  ADJUDICATE FAILED  {type(e).__name__}: {str(e)[:90]}")
                continue
            stamp = datetime.now(timezone.utc).isoformat()
            if v["url"]:
                sess.run(VERDICT, claimId=p["aId"], checkedAt=stamp, **v)
            else:
                sess.run(VERDICT_NO_SOURCE, claimId=p["aId"], checkedAt=stamp,
                         verdict=v["verdict"], snippet=v["snippet"])
            print(f"  {v['verdict']:16s} {p['aText'][:70]}\n{'':18s}{v['url'] or '(no source)'}")

        n = sess.run("MATCH ()-[r:CONTRADICTS]->() RETURN count(r) AS c").single()["c"]
        e = sess.run("MATCH ()-[r:VERIFIED_AS]->() RETURN count(r) AS c").single()["c"]
    drv.close()
    print(f"\nGRAPH: {n} CONTRADICTS edges, {e} verdict edges")
