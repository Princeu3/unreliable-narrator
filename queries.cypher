// Demo queries. Each one fails without the graph — that is the filter for what goes on stage.
// Run in Neo4j Browser, or hand to the agent as few-shot examples.

// ── BEAT 1 ─ the modality gap ────────────────────────────────────────────────
// On screen, never spoken. A fused embedding blends the channels, so it cannot represent this.
MATCH (e:Entity)<-[m:MENTIONS]-(:Scene)
WITH e, collect(DISTINCT m.modality) AS mods, count(m) AS hits
WHERE 'ocr' IN mods AND NOT 'speech' IN mods AND hits > 1
RETURN e.name AS shownButNeverSaid, e.type, mods, hits
ORDER BY hits DESC LIMIT 20;

// ── BEAT 1b ─ claims asserted with NO evidence on screen ─────────────────────
// evidence_shown comes free from the segment call. This is the misinformation angle as structure.
MATCH (v:Video)-[:HAS_SCENE]->(s:Scene)-[:ASSERTS]->(c:Claim)
WHERE s.evidenceShown = 'none' AND c.modality = 'speech'
RETURN v.channel, count(c) AS unevidencedClaims,
       collect(c.text)[0..2] AS examples,
       v.url + '&t=' + toString(toInteger(min(s.startSec))) AS jumpTo
ORDER BY unevidencedClaims DESC;

// Ratio version — who asserts the most with the least on screen to back it?
// NOTE: aggregate scenes and claims SEPARATELY. Joining claims first multiplies the scene rows
// and inflates the evidence count (that is how you get "evidence in 6 of 4 scenes").
MATCH (v:Video)-[:HAS_SCENE]->(s:Scene)
WITH v, count(s) AS scenes,
     sum(CASE WHEN s.evidenceShown <> 'none' THEN 1 ELSE 0 END) AS evidencedScenes
CALL (v) {
  MATCH (v)-[:HAS_SCENE]->(:Scene)-[:ASSERTS]->(c:Claim {modality:'speech'})
  RETURN count(c) AS claims
}
RETURN v.channel, claims, evidencedScenes, scenes,
       round(1.0 * evidencedScenes / scenes, 2) AS evidenceRate
ORDER BY evidenceRate ASC, claims DESC;

// ── BEAT 2 ─ counting. Ask a vector DB "how many" and it hallucinates. ───────
MATCH (e:Entity)<-[m:MENTIONS]-(:Scene)<-[:HAS_SCENE]-(v:Video)
WHERE e.type IN ['substance','biomarker']
RETURN e.name, count(DISTINCT v) AS channels, count(m) AS mentions,
       collect(DISTINCT m.modality) AS seenVia
ORDER BY mentions DESC LIMIT 15;

// ── BEAT 3 ─ who disagrees with whom, with both timestamps ──────────────────
MATCH (v1:Video)-[:HAS_SCENE]->(s1:Scene)-[:ASSERTS]->(a:Claim)-[x:CONTRADICTS]->(b:Claim)
MATCH (v2:Video)-[:HAS_SCENE]->(s2:Scene)-[:ASSERTS]->(b)
RETURN x.about AS about,
       v1.channel AS channelA, a.text AS claimA,
       v1.url + '&t=' + toString(toInteger(s1.startSec)) AS linkA,
       v2.channel AS channelB, b.text AS claimB,
       v2.url + '&t=' + toString(toInteger(s2.startSec)) AS linkB,
       x.rationale;

// ── BEAT 3b ─ verdicts, with the source that decided them ───────────────────
MATCH (c:Claim)-[r:VERIFIED_AS]->(ev:Evidence)
MATCH (v:Video)-[:HAS_SCENE]->(s:Scene)-[:ASSERTS]->(c)
RETURN r.verdict, v.channel, c.text, ev.url,
       v.url + '&t=' + toString(toInteger(s.startSec)) AS jumpTo
ORDER BY r.verdict;

// ── NEGATION ─ vector search cannot express NOT ─────────────────────────────
// Channels that discuss inflammation but never once mention linoleic acid.
MATCH (v:Video)-[:HAS_SCENE]->(:Scene)-[:MENTIONS]->(:Entity {name:'inflammation'})
WITH DISTINCT v
WHERE NOT EXISTS {
  (v)-[:HAS_SCENE]->(:Scene)-[:MENTIONS]->(:Entity {name:'linoleic acid'})
}
RETURN v.channel, v.title, v.url;

// ── SUPERCUT ─ these rows ARE an edit decision list ─────────────────────────
// Feed straight to build_supercut(). Must return videoId, startSec, endSec.
MATCH (v:Video)-[:HAS_SCENE]->(s:Scene)-[:ASSERTS]->(c:Claim)-[:VERIFIED_AS {verdict:'DISPUTED'}]->()
RETURN s.videoId AS videoId, s.startSec AS startSec, s.endSec AS endSec,
       v.channel, c.text
ORDER BY s.videoId, s.startSec;

// ── CLOSER ─ a connection nobody typed ──────────────────────────────────────
MATCH (a:Video), (b:Video) WHERE a.id < b.id
MATCH p = shortestPath((a)-[:HAS_SCENE|MENTIONS*..6]-(b))
RETURN a.channel, b.channel, length(p) AS hops,
       [n IN nodes(p) | coalesce(n.channel, n.name, left(n.summary,40))] AS via
ORDER BY hops DESC LIMIT 5;

// ── counts to say out loud ──────────────────────────────────────────────────
MATCH (n) WITH count(n) AS nodes
MATCH ()-[r]->() RETURN nodes, count(r) AS relationships;
