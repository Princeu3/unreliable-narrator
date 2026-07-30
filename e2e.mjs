// End-to-end verification. Drives the real page against the real graph.
//   node e2e.mjs            (expects api:8000 + ui:5173 already running)
//
// Checks behaviour and performance, not just HTTP 200s: a page that returns 200 while rendering
// zero contradictions is exactly the failure this project is about.
import { chromium } from 'playwright'

const UI = process.env.UI ?? 'http://localhost:5173'
let pass = 0, fail = 0
const ok = (name, cond, detail = '') => {
  cond ? (pass++, console.log(`  ok   ${name}${detail && ' — ' + detail}`))
       : (fail++, console.log(`  FAIL ${name}${detail && ' — ' + detail}`))
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })

const errors = []
page.on('console', m => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', e => errors.push(String(e)))
const failed = []
page.on('response', r => r.status() >= 400 && failed.push(`${r.status()} ${r.url()}`))

// ── load + paint ────────────────────────────────────────────────────────────
const t0 = Date.now()
await page.goto(UI, { waitUntil: 'domcontentloaded' })
await page.waitForFunction(() => {
  const el = [...document.querySelectorAll('span')].find(s => /^\d+$/.test(s.textContent ?? ''))
  return !!el
}, null, { timeout: 20000 })
const tData = Date.now() - t0
ok('data painted under 3s', tData < 3000, `${tData}ms`)

const nav = await page.evaluate(() => {
  const n = performance.getEntriesByType('navigation')[0]
  return { dcl: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd) }
})
ok('DOMContentLoaded under 1.5s', nav.dcl < 1500, `${nav.dcl}ms`)

// ── the numbers are real ────────────────────────────────────────────────────
const stats = await page.evaluate(() => fetch('/api/all').then(r => r.json()))
ok('graph has nodes', stats.stats.nodes > 100, `${stats.stats.nodes} nodes`)
ok('all three modalities present',
  ['speech', 'visual', 'ocr'].every(m => stats.stats.modalities[m] > 0),
  JSON.stringify(stats.stats.modalities))
ok('contradictions found', stats.contradictions.length > 0, `${stats.contradictions.length}`)
ok('every contradiction cites two real clips',
  stats.contradictions.every(c => /watch\?v=[\w-]{6,}&t=\d+/.test(c.jumpToA) &&
                                  /watch\?v=[\w-]{6,}&t=\d+/.test(c.jumpToB)))
ok('no fabricated channel-name URLs',
  !stats.contradictions.some(c => /v=(Dr|Nutrition|Huberman|Adam|Business|Physionic|Ben)/i.test(c.jumpToA + c.jumpToB)))
ok('verdicts are hedged labels only',
  stats.contradictions.every(c => c.verdict === null ||
    ['SUPPORTED', 'DISPUTED', 'NO_SOURCE_FOUND'].includes(c.verdict)))
ok('modality gap non-empty', stats.modalityGap.length > 0, `${stats.modalityGap.length} entities`)

// ── graph canvas rendered ───────────────────────────────────────────────────
const circles = await page.locator('svg circle').count()
const lines = await page.locator('svg line').count()
ok('force graph drew nodes', circles > 40, `${circles} circles`)
ok('force graph drew edges', lines > 50, `${lines} lines`)

// simulation settles rather than spinning forever
await page.waitForTimeout(2500)
const p1 = await page.locator('svg circle').first().boundingBox()
await page.waitForTimeout(1200)
const p2 = await page.locator('svg circle').first().boundingBox()
const drift = Math.abs((p1?.x ?? 0) - (p2?.x ?? 0)) + Math.abs((p1?.y ?? 0) - (p2?.y ?? 0))
ok('simulation settles', drift < 6, `${drift.toFixed(1)}px drift after 2.5s`)

// ── graph is playable in place ──────────────────────────────────────────────
ok('graph shows an empty-state hint',
  await page.getByText('Click any node to play').isVisible())
const hits = page.locator('circle[data-node]')
ok('nodes have generous hit targets', await hits.count() > 30, `${await hits.count()}`)
await hits.nth(3).click({ timeout: 15000 })
await page.waitForTimeout(2200)
const panelClips = await page.locator('aside video').count()
ok('clicking a node loads its clips', panelClips > 0, `${panelClips} clips`)
const panelPosters = await page.locator('aside video').evaluateAll(v => v.filter(x => x.poster).length)
ok('panel clips have posters', panelPosters === panelClips, `${panelPosters}/${panelClips}`)
const panelPlay = await page.locator('aside video').first().evaluate(async v => {
  if (!v.src) return { ok: false, why: 'no src' }
  try { await v.play() } catch (e) { return { ok: false, why: String(e).slice(0, 50) } }
  await new Promise(r => setTimeout(r, 900))
  return { ok: v.currentTime > 0, t: v.currentTime }
})
ok('graph clip plays in place', panelPlay.ok, panelPlay.why ?? `t=${panelPlay.t?.toFixed(1)}s`)
await page.locator('aside button').first().click()
await page.waitForTimeout(400)
ok('panel closes back to hint', await page.getByText('Click any node to play').isVisible())

// ── supercut assembles to the right length ─────────────────────────────────
const cut = await page.evaluate(() => fetch('/api/supercut', { method: 'POST' }).then(r => r.json()))
const expect = await page.evaluate(() => fetch('/api/clips').then(r => r.json())
  .then(cs => cs.reduce((a, c) => a + Math.min(12, Math.max(2, c.endSec - c.startSec)), 0)))
ok('supercut built', !!cut.src, cut.message)
const cutDur = await page.evaluate(src => new Promise(res => {
  const v = document.createElement('video'); v.src = src
  v.addEventListener('loadedmetadata', () => res(v.duration))
  v.addEventListener('error', () => res(-1))
}), cut.src)
ok('supercut length matches the edit list', Math.abs(cutDur - expect) < 2,
   `${cutDur.toFixed(1)}s vs ${expect}s expected`)

// ── videos: lazy at first, loaded when scrolled to ──────────────────────────
// fresh page: earlier steps scrolled the document, so the observers have already fired
// fresh navigation, not reload: reload restores the prior scroll position first, so the
// observers fire before any scrollTo(0,0) can land
await page.goto(UI, { waitUntil: 'networkidle' })
await page.waitForTimeout(900)
const videos = page.locator('video')
const nVid = await videos.count()
ok('clip players present', nVid > 4, `${nVid} players`)
// posters must render even while the video itself stays lazy — otherwise: black boxes
const posters = await videos.evaluateAll(v => v.filter(x => x.poster).length)
ok('every clip has a poster frame', posters === nVid, `${posters}/${nVid}`)
const thumb = await page.evaluate(() =>
  fetch('/api/thumb/8ETN1lmMve4?t=47').then(r => ({ s: r.status, ct: r.headers.get('content-type') })))
ok('thumbnail endpoint serves jpeg', thumb.s === 200 && thumb.ct === 'image/jpeg',
   `${thumb.s} ${thumb.ct}`)

const srcsBefore = await videos.evaluateAll(v => v.filter(x => x.getAttribute('src')).length)
ok('videos lazy above the fold', srcsBefore === 0, `${srcsBefore}/${nVid} eagerly sourced`)

await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight / 3))
await page.waitForTimeout(1100)
const srcsAfter = await videos.evaluateAll(v => v.filter(x => x.getAttribute('src')).length)
ok('videos load on scroll', srcsAfter > srcsBefore, `${srcsBefore} → ${srcsAfter}`)

// a clip actually plays from its timestamp
const first = videos.first()
await first.scrollIntoViewIfNeeded()
await page.waitForTimeout(600)
const played = await first.evaluate(async v => {
  if (!v.src) return { ok: false, why: 'no src' }
  try { await v.play() } catch (e) { return { ok: false, why: String(e).slice(0, 60) } }
  await new Promise(r => setTimeout(r, 900))
  return { ok: v.currentTime > 0, t: v.currentTime, dur: v.duration }
})
ok('clip plays from its offset', played.ok, played.why ?? `t=${played.t?.toFixed(1)}s`)

// ── layout + theme ──────────────────────────────────────────────────────────
const scrollX = await page.evaluate(() =>
  document.documentElement.scrollWidth - document.documentElement.clientWidth)
ok('no horizontal overflow', scrollX <= 0, `${scrollX}px`)

const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
ok('light theme', /255|25[0-4]|oklch\(0\.9/.test(bg), bg)

for (const w of [390, 768, 1440]) {
  await page.setViewportSize({ width: w, height: 900 })
  await page.waitForTimeout(250)
  const over = await page.evaluate(() =>
    document.documentElement.scrollWidth - document.documentElement.clientWidth)
  ok(`no overflow at ${w}px`, over <= 1, `${over}px`)
}
await page.setViewportSize({ width: 1280, height: 900 })

// ── console + network clean ────────────────────────────────────────────────
ok('no console errors', errors.length === 0, errors.slice(0, 2).join(' | '))
ok('no failed requests', failed.length === 0, failed.slice(0, 2).join(' | '))

await page.screenshot({ path: 'out/ui-full.png', fullPage: true })
await browser.close()

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
