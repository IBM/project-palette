"""The /stats dashboard page (served by app.py, token-gated). Data comes from
/stats/api — see usage.summary(). Every value from the database is escaped
before it reaches the DOM: utterances are user-typed text."""

STATS_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Palette Usage</title>
<link rel="icon" type="image/png" href="/asset/favicon.png">
<style>
:root{
  --bg:#faf9f5; --surface:#ffffff; --sunk:#f1efe7;
  --text:#20201d; --text2:#55524b; --muted:#86827a; --border:#e7e4db;
  --accent:#c25e3a; --accent-soft:#c25e3a22;
  --good:#3f7d52; --bad:#b03a2e;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#1c1b19; --surface:#252421; --sunk:#2d2b28;
    --text:#ecebe6; --text2:#c4c1b8; --muted:#8f8b82; --border:#3a3833;
    --accent:#e07a55; --accent-soft:#e07a5533;
    --good:#6fb487; --bad:#e2766a;
  }
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:var(--bg);color:var(--text);font-size:14px;line-height:1.5}
.wrap{max-width:1180px;margin:0 auto;padding:22px 16px 60px}
header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:18px}
header img{width:32px;height:32px}
h1{font-size:19px;font-weight:650}
.sub{color:var(--muted);font-size:12.5px}
.spacer{flex:1}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:8px;overflow:hidden;background:var(--surface)}
.seg button{border:none;background:none;color:var(--text2);font:inherit;font-size:12.5px;
  padding:5px 12px;cursor:pointer}
.seg button.on{background:var(--sunk);color:var(--text);font-weight:600}
.where{font-size:12px;color:var(--text2);margin:-6px 0 14px;line-height:1.7}
.where code,.kv code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;
  background:var(--sunk);border:1px solid var(--border);border-radius:4px;padding:1px 5px;word-break:break-all}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:18px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.tile .k{font-size:11.5px;color:var(--muted);letter-spacing:.03em}
.tile .v{font-size:24px;font-weight:650;font-variant-numeric:tabular-nums;margin-top:2px}
.tile .n{font-size:11.5px;color:var(--text2)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:14px 16px;margin-bottom:18px}
.card h2{font-size:14px;font-weight:650;margin-bottom:2px}
.card .hint{font-size:12px;color:var(--muted);margin-bottom:10px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
button.ghost{font:inherit;font-size:13px;padding:5px 10px;border:1px solid var(--border);border-radius:7px;
  background:var(--surface);color:var(--text);cursor:pointer}
input[type=search],select{font:inherit;font-size:13px;padding:5px 9px;border:1px solid var(--border);
  border-radius:7px;background:var(--bg);color:var(--text)}
input[type=search]{flex:1;min-width:180px}
a{color:var(--accent)}
.chart{position:relative}
.chart svg{display:block;width:100%;height:180px}
.chart .axis{fill:var(--muted);font-size:10.5px}
.chart .grid{stroke:var(--border);stroke-width:1}
.chart .bar{fill:var(--accent)}
.chart .hit{fill:transparent;cursor:default}
.chart .bar.hover{opacity:.75}
.tip{position:absolute;pointer-events:none;background:var(--text);color:var(--bg);
  font-size:12px;padding:6px 9px;border-radius:7px;white-space:nowrap;transform:translate(-50%,-100%);
  margin-top:-8px;display:none}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;font-weight:600;color:var(--muted);font-size:11.5px;letter-spacing:.02em;
  padding:6px 8px;border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:7px 8px;border-bottom:1px solid var(--border);vertical-align:top}
td.num{text-align:right;font-variant-numeric:tabular-nums}
td.t{white-space:nowrap;color:var(--text2)}
td.text{min-width:320px;white-space:pre-wrap;word-break:break-word}
.id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:var(--text2)}
.pill{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px;
  background:var(--sunk);border:1px solid var(--border);color:var(--text2);white-space:nowrap}
.ok{color:var(--good)} .bad{color:var(--bad)}
.more{font-size:12px;color:var(--muted);margin-top:8px}
.err{padding:30px;text-align:center;color:var(--muted)}
tr.j{cursor:pointer}
tr.j:hover td{background:var(--sunk)}
tr.j td.text{max-width:520px}
tr.j .caret{display:inline-block;width:12px;color:var(--muted);transition:transform .12s}
tr.j.open .caret{transform:rotate(90deg)}
tr.jd > td{background:var(--bg);padding:14px 16px 18px}
.st{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:10px;white-space:nowrap;border:1px solid}
.st.built,.st.ok{color:var(--good);border-color:currentColor}
.st.plan_failed,.st.build_failed,.st.failed,.st.interrupted{color:var(--bad);border-color:currentColor}
.st.planned,.st.planning,.st.building,.st.pending,.st.no_plan{color:var(--muted);border-color:var(--border)}
.steps{display:grid;gap:12px}
.step{border:1px solid var(--border);border-radius:9px;background:var(--surface);padding:10px 12px}
.step h3{font-size:12.5px;font-weight:650;display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.step h3 .sub{font-weight:400}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;font-size:12.5px;margin-bottom:6px}
.kv dt{color:var(--muted)}
.kv dd{word-break:break-word}
pre.plan{white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:11.5px;line-height:1.5;background:var(--bg);border:1px solid var(--border);border-radius:7px;
  padding:10px 12px;max-height:340px;overflow:auto;color:var(--text)}
pre.err{white-space:pre-wrap;word-break:break-word;font-size:12px;color:var(--bad);background:var(--bg);
  border:1px solid var(--border);border-radius:7px;padding:8px 10px;max-height:200px;overflow:auto}
details summary{cursor:pointer;font-size:12px;color:var(--text2);margin:4px 0}
.strip{display:flex;gap:6px;overflow-x:auto;padding:4px 0 2px}
.strip img{width:150px;aspect-ratio:16/9;object-fit:cover;border:1px solid var(--border);border-radius:5px;cursor:zoom-in;flex:0 0 auto;background:var(--sunk)}
ul.lint{font-size:11.5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--text2);
  padding-left:16px;max-height:160px;overflow:auto}
.thumb{width:96px;aspect-ratio:16/9;object-fit:cover;border:1px solid var(--border);
  border-radius:5px;display:block;cursor:zoom-in;background:var(--sunk)}
.links a{white-space:nowrap;margin-right:8px;font-size:12px}
.viewer{position:fixed;inset:0;background:rgba(20,18,15,.82);z-index:50;display:flex;
  flex-direction:column;align-items:center;justify-content:center;padding:16px;gap:10px}
.viewer[hidden]{display:none}
.viewer img{max-width:min(1100px,100%);max-height:78vh;border-radius:6px;background:#fff}
.viewer .bar{display:flex;gap:10px;align-items:center;color:#fff;font-size:13px;flex-wrap:wrap;justify-content:center}
.viewer button{font:inherit;font-size:13px;background:#ffffff22;color:#fff;border:1px solid #ffffff44;
  border-radius:7px;padding:5px 12px;cursor:pointer}
.viewer a{color:#ffb99e}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <img src="/asset/logo.png" alt="">
    <div>
      <h1>Palette usage</h1>
      <div class="sub" id="meta">loading…</div>
    </div>
    <div class="spacer"></div>
    <div class="seg" id="range">
      <button data-r="today">Today</button><button data-r="7d" class="on">7 days</button>
      <button data-r="30d">30 days</button><button data-r="all">All time</button>
    </div>
  </header>

  <div class="where" id="where"></div>
  <div class="tiles" id="tiles"></div>

  <div class="card">
    <h2>Daily active users</h2>
    <div class="hint">Distinct browsers (or API clients) per UTC day, last 30 days. Hover a bar for the day's detail.</div>
    <div class="chart" id="chart"><svg></svg><div class="tip" id="tip"></div></div>
  </div>

  <div class="card">
    <h2>Utterances &rarr; plan &rarr; deck</h2>
    <div class="hint">Every utterance followed to its outcome: the plan it produced, the deck built from that plan, and the edits after. Click a row for the full plan text, timings, quality notes and errors. Obvious secrets are redacted before storage.</div>
    <div class="row">
      <input type="search" id="uq" placeholder="Search utterance, plan, deck title, user or session…">
      <select id="uk"><option value="">All kinds</option></select>
      <select id="us"><option value="">All outcomes</option></select>
      <button class="ghost" id="expAll" type="button">Expand all</button>
      <a id="csv" href="#">Utterances CSV</a>
    </div>
    <div class="scroll"><table id="utt"></table></div>
    <div class="more" id="uttMore"></div>
  </div>

  <div class="card">
    <h2>Users</h2>
    <div class="hint">One row per browser (<span class="id">palette_uid</span> cookie) or, for API callers without a cookie, per hashed IP. Anonymous — no names or IPs are stored.</div>
    <div class="scroll"><table id="users"></table></div>
  </div>

  <div class="card">
    <h2>Builds</h2>
    <div class="hint" id="archHint">Most recent 200 deck builds. Every successful deck is archived (latest version, after edits and retries) — click a thumbnail to page through its slides.</div>
    <div class="scroll"><table id="builds"></table></div>
  </div>
</div>

<div class="viewer" id="viewer" hidden onclick="if(event.target===this)closeViewer()">
  <img id="vImg" alt="">
  <div class="bar">
    <button onclick="step(-1)">&larr; Prev</button>
    <span id="vPos"></span>
    <button onclick="step(1)">Next &rarr;</button>
    <a id="vPptx" href="#">Download .pptx</a>
    <a id="vPlan" href="#" target="_blank">Plan</a>
    <button onclick="closeViewer()">Close (Esc)</button>
  </div>
</div>

<script>
const TOKEN = new URLSearchParams(location.search).get('token') || ''
const q = '?token=' + encodeURIComponent(TOKEN)
let DATA = null, RANGE = '7d'
try { RANGE = localStorage.getItem('palette-stats-range') || '7d' } catch (e) {}

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))
const fmtT = ts => {
  const d = new Date(ts * 1000)
  return d.toLocaleDateString(undefined, {month:'short', day:'numeric'}) + ' ' +
         d.toLocaleTimeString(undefined, {hour:'2-digit', minute:'2-digit'})
}
const ago = ts => {
  const s = Date.now() / 1000 - ts
  if (s < 60) return 'just now'
  if (s < 3600) return Math.floor(s / 60) + 'm ago'
  if (s < 86400) return Math.floor(s / 3600) + 'h ago'
  return Math.floor(s / 86400) + 'd ago'
}
const short = id => id ? esc(String(id).slice(0, 8)) : '—'
const n = v => (v ?? 0).toLocaleString()

function tiles() {
  const k = DATA.kpis[RANGE]
  const t = [
    ['USERS', n(k.users), 'distinct browsers / clients'],
    ['SESSIONS', n(k.sessions), 'conversations'],
    ['UTTERANCES', n(k.utterances), 'things people typed'],
    ['BUILDS', n(k.builds), k.build_failures ? `<span class="bad">${n(k.build_failures)} failed</span>` : 'none failed'],
    ['SLIDES BUILT', n(k.slides), 'in successful builds'],
    ['AVG BUILD', k.avg_build_s != null ? k.avg_build_s + 's' : '—', 'successful builds'],
  ]
  document.getElementById('tiles').innerHTML = t.map(([a, b, c]) =>
    `<div class="tile"><div class="k">${a}</div><div class="v">${b}</div><div class="n">${c}</div></div>`).join('')
}

function chart() {
  const box = document.getElementById('chart'), svg = box.querySelector('svg'), tip = document.getElementById('tip')
  const rows = DATA.daily, W = box.clientWidth, H = 180, L = 30, B = 22, T = 8
  const max = Math.max(1, ...rows.map(r => r.users))
  const step = max <= 4 ? 1 : Math.ceil(max / 4)
  const top = Math.ceil(max / step) * step
  const y = v => T + (H - T - B) * (1 - v / top)
  const bw = (W - L) / rows.length, w = Math.max(2, Math.min(18, bw - 4))
  let s = ''
  for (let v = 0; v <= top; v += step)
    s += `<line class="grid" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/>` +
         `<text class="axis" x="${L - 6}" y="${y(v) + 3.5}" text-anchor="end">${v}</text>`
  rows.forEach((r, i) => {
    const cx = L + bw * i + bw / 2, h = Math.max(4, y(0) - y(r.users))
    if (r.users) s += `<path class="bar" data-i="${i}" d="M${cx - w/2},${y(0)} v${-(h - 4)} q0,-4 4,-4 h${w - 8} q4,0 4,4 v${h - 4} z"/>`
    s += `<rect class="hit" data-i="${i}" x="${L + bw * i}" y="${T}" width="${bw}" height="${H - T - B}"/>`
    if (i % 5 === 4 || i === rows.length - 1)
      s += `<text class="axis" x="${cx}" y="${H - 6}" text-anchor="middle">${r.day.slice(5)}</text>`
  })
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`)
  svg.innerHTML = s
  svg.querySelectorAll('.hit').forEach(el => {
    el.addEventListener('mouseenter', () => {
      const i = +el.dataset.i, r = rows[i]
      svg.querySelectorAll('.bar').forEach(b => b.classList.toggle('hover', +b.dataset.i === i))
      tip.innerHTML = `<b>${esc(r.day)}</b><br>${r.users} users · ${r.utterances} utterances · ${r.builds} builds`
      tip.style.left = (L + bw * i + bw / 2) + 'px'
      tip.style.top = y(r.users) + 'px'
      tip.style.display = 'block'
    })
    el.addEventListener('mouseleave', () => {
      tip.style.display = 'none'
      svg.querySelectorAll('.bar').forEach(b => b.classList.remove('hover'))
    })
  })
}

const STATUS = {built: 'deck built', planned: 'plan only', planning: 'planning…', building: 'building…',
  plan_failed: 'plan failed', build_failed: 'build failed', interrupted: 'interrupted', ok: 'applied',
  failed: 'failed', pending: 'running…', no_plan: 'no plan recorded'}
const KIND = {request: 'deck request', example: 'example prompt', plan_edit: 'plan edit', slide_edit: 'slide edit', loaded_plan: 'loaded plan'}
const OPEN = new Set()
const dur = s => s == null ? '—' : s < 90 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
const iso = ts => ts ? new Date(ts * 1000).toLocaleString() : '—'
const jkey = j => j.utterance_id ? 'u' + j.utterance_id : 'b' + j.builds[0].id
const lastBuild = j => j.builds.length ? j.builds[j.builds.length - 1] : null

function utterances() {
  const term = document.getElementById('uq').value.toLowerCase()
  const kind = document.getElementById('uk').value
  const st = document.getElementById('us').value
  const rows = DATA.journeys.filter(j => (!kind || j.kind === kind) && (!st || j.status === st) &&
    (!term || [j.text, j.plan?.plan, j.who, j.thread_id, j.client, ...j.builds.map(b => b.title)]
      .some(v => String(v ?? '').toLowerCase().includes(term))))
  const shown = rows.slice(0, 200)
  document.getElementById('utt').innerHTML =
    '<tr><th></th><th>When</th><th>Kind</th><th>Utterance</th><th>Outcome</th><th>Plan</th><th>Deck</th><th>User</th></tr>' +
    (shown.map(j => {
      const b = lastBuild(j), k = jkey(j), open = OPEN.has(k)
      const planCell = j.kind === 'slide_edit' ? (j.edit ? `slide ${j.slide_n} · ${dur(j.edit.seconds)}` : '—')
        : j.plan ? `${j.plan.ok ? '' : '<span class="bad">✕</span> '}${dur(j.plan.seconds)}` : (j.kind === 'loaded_plan' ? 'from file' : '—')
      const deckCell = b ? `${b.pngs.length ? `<img class="thumb" loading="lazy" src="${deckUrl(b.id, b.pngs[0])}" alt="" onclick="event.stopPropagation();openViewer(${b.id})">` : ''}
          <div class="sub">${b.ok ? `${b.slides} slides · ${dur(b.seconds)}` : b.ok === null ? (b.interrupted ? 'interrupted' : 'building…') : '<span class="bad">failed</span> · ' + dur(b.seconds)}${j.builds.length > 1 ? ` · ${j.builds.length} builds` : ''}</div>` : '—'
      return `<tr class="j${open ? ' open' : ''}" data-k="${k}">
        <td><span class="caret">▸</span></td>
        <td class="t" title="${esc(iso(j.ts))}">${fmtT(j.ts)}</td>
        <td><span class="pill">${esc(KIND[j.kind] || j.kind)}</span>${j.attachments?.length ? `<div class="sub">📎 ${j.attachments.length}</div>` : ''}</td>
        <td class="text">${j.text != null ? esc(j.text.length > 260 ? j.text.slice(0, 260) + '…' : j.text) : '<span class="sub">(plan loaded from a file — no utterance)</span>'}</td>
        <td><span class="st ${esc(j.status)}">${esc(STATUS[j.status] || j.status)}</span></td>
        <td class="t">${planCell}</td>
        <td>${deckCell}</td>
        <td><span class="id">${short(j.who)}</span><div><span class="pill">${esc(j.client)}</span></div></td></tr>` +
        (open ? `<tr class="jd"><td colspan="8">${detail(j)}</td></tr>` : '')
    }).join('') || '<tr><td colspan="8" class="err">No utterances yet.</td></tr>')
  document.getElementById('uttMore').textContent =
    rows.length > shown.length ? `Showing ${shown.length} of ${rows.length} — narrow the search.` : ''
}

function detail(j) {
  let h = '<div class="steps">'
  // 1. utterance
  h += `<div class="step"><h3>1 · Utterance <span class="pill">${esc(KIND[j.kind] || j.kind)}</span></h3><dl class="kv">
    <dt>When</dt><dd>${esc(iso(j.ts))}</dd>
    <dt>User</dt><dd><span class="id">${esc(j.who || '—')}</span> · ${esc(j.client || '—')}</dd>
    <dt>Session</dt><dd class="id">${esc(j.thread_id || '—')}</dd>
    ${j.slide_n ? `<dt>Slide</dt><dd>${j.slide_n}</dd>` : ''}
    ${j.attachments?.length ? `<dt>Attached</dt><dd>${j.attachments.map(esc).join(', ')}</dd>` : ''}
    </dl>${j.text != null ? `<pre class="plan">${esc(j.text)}</pre>` : '<div class="sub">No utterance: the user loaded a plan from a file and built it.</div>'}</div>`
  // 2. plan (or edit outcome)
  if (j.kind === 'slide_edit') {
    const e = j.edit
    h += `<div class="step"><h3>2 · Slide edit ${e ? `<span class="st ${e.ok ? 'ok' : 'failed'}">${e.ok ? 'applied' : 'failed'}</span>` : '<span class="st pending">running…</span>'}</h3>` +
      (e ? `<dl class="kv"><dt>Started</dt><dd>${esc(iso(e.started_at))}</dd><dt>Took</dt><dd>${dur(e.seconds)}</dd>
        <dt>Slide</dt><dd>${e.slide_n ?? '—'}</dd><dt>Deck slides after</dt><dd>${e.slides ?? '—'}</dd></dl>
        ${e.error ? `<pre class="err">${esc(e.error)}</pre>` : ''}` : '') + '</div>'
  } else if (j.kind !== 'loaded_plan') {
    const p = j.plan
    h += `<div class="step"><h3>2 · Plan ${p ? `<span class="st ${p.ok ? 'ok' : 'failed'}">${p.ok ? 'made' : 'failed'}</span>` : '<span class="st planning">planning… (or not recorded)</span>'}</h3>` +
      (p ? `<dl class="kv"><dt>Started</dt><dd>${esc(iso(p.started_at))}</dd><dt>Took</dt><dd>${dur(p.seconds)}</dd>
        <dt>Model</dt><dd>${esc(p.model || '—')}</dd>
        ${p.sources?.length ? `<dt>Sources</dt><dd>${p.sources.map(esc).join(', ')}</dd>` : ''}
        ${p.plan ? `<dt>Plan size</dt><dd>${p.plan.length.toLocaleString()} chars · ${(p.plan.match(/^## /gm) || []).length} slide sections</dd>` : ''}</dl>
        ${p.error ? `<pre class="err">${esc(p.error)}</pre>` : ''}
        ${p.plan ? `<pre class="plan">${esc(p.plan)}</pre>` : ''}` : '') + '</div>'
  }
  // 3. builds
  if (!j.builds.length && j.kind !== 'slide_edit')
    h += `<div class="step"><h3>3 · Deck <span class="st planned">not built</span></h3><div class="sub">No deck was built from this plan.</div></div>`
  j.builds.forEach((b, i) => {
    const st = b.ok === null ? (b.interrupted ? 'interrupted' : 'building') : b.ok ? 'built' : 'build_failed'
    const lbl = j.kind === 'slide_edit' ? 'Deck that was edited' : `Deck${j.builds.length > 1 ? ` (build ${i + 1} of ${j.builds.length})` : ''}`
    h += `<div class="step"><h3>3 · ${lbl} <span class="st ${st}">${STATUS[st]}</span>
      ${b.aborted_by_user ? '<span class="pill">user pressed stop</span>' : ''}
      ${b.title ? `<span class="sub">${esc(b.title)}</span>` : ''}</h3>
      <dl class="kv">
        <dt>Started</dt><dd>${esc(iso(b.started_at))}</dd>
        <dt>Finished</dt><dd>${b.ok === null ? '—' : esc(iso(b.ts))}</dd>
        <dt>Took</dt><dd>${dur(b.seconds)}</dd>
        <dt>Slides</dt><dd>${b.slides ?? '—'}</dd>
        <dt>Palette</dt><dd>${esc(b.palette || '—')}</dd>
        <dt>Models</dt><dd>${esc(b.models || '—')}</dd>
        <dt>Plan used</dt><dd>${b.plan_edited === 1 ? '<b>edited by the user</b> after drafting (shown below)' : b.plan_edited === 0 ? 'as drafted' : 'loaded from file / unknown'}</dd>
        <dt>Quality</dt><dd>${b.lint.length} lint note${b.lint.length === 1 ? '' : 's'} · ${b.unrepaired.length} unrepaired layout issue${b.unrepaired.length === 1 ? '' : 's'}${Object.keys(b.retries).length ? ` · coder retries on slide${Object.keys(b.retries).length > 1 ? 's' : ''} ${Object.keys(b.retries).map(esc).join(', ')}` : ''}</dd>
        ${b.folder ? `<dt>Folder</dt><dd><code>${esc(b.folder)}</code></dd>` : ''}
        ${b.archived ? `<dt>Files</dt><dd class="links"><a href="${deckUrl(b.id, 'deck.pptx')}">deck.pptx</a><a href="${deckUrl(b.id, 'plan.md')}" target="_blank">plan.md</a> <span class="sub">(latest version${b.versions > 1 ? `, ${b.versions - 1} edit${b.versions > 2 ? 's' : ''} since build` : ''})</span></dd>` : ''}
      </dl>
      ${b.error ? `<pre class="err">${esc(b.error)}</pre>` : ''}
      ${b.pngs.length ? `<div class="strip">${b.pngs.map((n, k) => `<img loading="lazy" src="${deckUrl(b.id, n)}" alt="slide ${k + 1}" onclick="openViewer(${b.id}, ${k})">`).join('')}</div>` : ''}
      ${b.plan_edited === 1 || j.kind === 'loaded_plan' ? `<details><summary>Plan this deck was built from</summary><pre class="plan">${esc(b.plan)}</pre></details>` : ''}
      ${b.lint.length ? `<details><summary>Lint notes (${b.lint.length})</summary><ul class="lint">${b.lint.map(l => `<li>${esc(l)}</li>`).join('')}</ul></details>` : ''}
      ${b.unrepaired.length ? `<details><summary>Unrepaired layout issues (${b.unrepaired.length})</summary><ul class="lint">${b.unrepaired.map(l => `<li>${esc(typeof l === 'string' ? l : JSON.stringify(l))}</li>`).join('')}</ul></details>` : ''}
      ${Object.keys(b.geometry).length ? `<details><summary>Geometry report</summary><pre class="plan">${esc(JSON.stringify(b.geometry, null, 2))}</pre></details>` : ''}
      ${b.edits.length ? `<details open><summary>Edits &amp; retries after the build (${b.edits.length})</summary><table>
        <tr><th>When</th><th>Kind</th><th>Slide</th><th>Result</th><th>Took</th></tr>
        ${b.edits.map(e => `<tr><td class="t">${fmtT(e.ts)}</td><td>${esc(e.reason === 'retry' ? 'retry' : 'slide edit')}</td>
          <td>${e.slide_n ?? '—'}</td><td>${e.ok ? '<span class="ok">✓</span>' : `<span class="bad" title="${esc(e.error)}">✕ ${esc((e.error || '').slice(0, 80))}</span>`}</td>
          <td>${dur(e.seconds)}</td></tr>`).join('')}</table></details>` : ''}
    </div>`
  })
  return h + '</div>'
}

function users() {
  document.getElementById('users').innerHTML =
    '<tr><th>User</th><th>Client</th><th>First seen</th><th>Last seen</th><th class="num">Sessions</th>' +
    '<th class="num">Utterances</th><th class="num">Builds</th><th class="num">Networks</th></tr>' +
    (DATA.users.map(u => `<tr><td class="id">${short(u.who)}</td><td><span class="pill">${esc(u.client)}</span></td>
      <td class="t">${fmtT(u.first_seen)}</td><td class="t">${ago(u.last_seen)}</td>
      <td class="num">${n(u.sessions)}</td><td class="num">${n(u.utterances)}</td>
      <td class="num">${n(u.builds_ok)}${u.builds > u.builds_ok ? ` <span class="bad">(+${u.builds - u.builds_ok} failed)</span>` : ''}</td>
      <td class="num">${n(u.networks)}</td></tr>`).join('')
     || '<tr><td colspan="8" class="err">No users yet.</td></tr>')
}

const deckUrl = (id, name) => `/stats/deck/${id}/${encodeURIComponent(name)}` + q

function builds() {
  const a = DATA.archive
  document.getElementById('archHint').textContent =
    `Most recent 200 deck builds. ${a.decks} deck${a.decks === 1 ? '' : 's'} archived (${a.mb} MB in ${a.dir}) — ` +
    'the latest version of each, after edits and retries. Click a thumbnail to page through its slides.'
  document.getElementById('builds').innerHTML =
    '<tr><th>Deck</th><th>When</th><th>Result</th><th>Title</th><th class="num">Slides</th><th class="num">Seconds</th>' +
    '<th>Files</th><th>User</th></tr>' +
    (DATA.builds.map(b => `<tr>
      <td>${b.pngs.length ? `<img class="thumb" loading="lazy" src="${deckUrl(b.id, b.pngs[0])}" alt="" onclick="openViewer(${b.id})">` : ''}</td>
      <td class="t">${fmtT(b.ts)}</td>
      <td>${b.ok ? '<span class="ok">✓ ok</span>' : `<span class="bad" title="${esc(b.error)}">✕ failed</span>`}</td>
      <td>${esc(b.title || (b.ok ? '' : b.error))}${b.versions > 1 ? ` <span class="pill">${b.versions - 1} edit${b.versions > 2 ? 's' : ''}</span>` : ''}</td>
      <td class="num">${b.slides ?? '—'}</td>
      <td class="num">${b.seconds != null ? Math.round(b.seconds) : '—'}</td>
      <td class="links">${b.archived ? `<a href="${deckUrl(b.id, 'deck.pptx')}">.pptx</a><a href="${deckUrl(b.id, 'plan.md')}" target="_blank">plan</a>` : '<span class="sub">—</span>'}</td>
      <td class="id">${short(b.who)}</td></tr>`).join('')
     || '<tr><td colspan="8" class="err">No builds yet.</td></tr>')
}

let V = null
function openViewer(id, start = 0) {
  const b = DATA.builds.find(x => x.id === id) ||
            DATA.journeys.flatMap(j => j.builds).find(x => x.id === id)
  if (!b || !b.pngs.length) return
  V = { b, i: start }
  document.getElementById('vPptx').href = deckUrl(id, 'deck.pptx')
  document.getElementById('vPlan').href = deckUrl(id, 'plan.md')
  document.getElementById('viewer').hidden = false
  step(0)
}
function step(d) {
  if (!V) return
  V.i = (V.i + d + V.b.pngs.length) % V.b.pngs.length
  document.getElementById('vImg').src = deckUrl(V.b.id, V.b.pngs[V.i])
  document.getElementById('vPos').textContent = `${esc(V.b.title || 'Deck')} · slide ${V.i + 1} of ${V.b.pngs.length}`
}
function closeViewer() { V = null; document.getElementById('viewer').hidden = true }
document.addEventListener('keydown', e => {
  if (!V) return
  if (e.key === 'Escape') closeViewer()
  if (e.key === 'ArrowRight') step(1)
  if (e.key === 'ArrowLeft') step(-1)
})

function render() {
  document.querySelectorAll('#range button').forEach(b => b.classList.toggle('on', b.dataset.r === RANGE))
  tiles(); chart(); utterances(); users(); builds()
  document.getElementById('meta').textContent =
    `updated ${new Date(DATA.generated_at * 1000).toLocaleTimeString()} · refreshes every minute`
  const L = DATA.locations
  document.getElementById('where').innerHTML =
    `<b>Database</b> <code>${esc(L.db)}</code> &nbsp;·&nbsp; <b>Decks</b> <code>${esc(L.decks)}/&lt;date&gt;/&lt;time&gt;Z-&lt;id&gt;-&lt;title&gt;/</code>`
}

async function load() {
  try {
    const r = await fetch('/stats/api' + q)
    if (!r.ok) throw new Error('HTTP ' + r.status)
    DATA = await r.json()
    const fill = (id, all, vals, names) => {
      const sel = document.getElementById(id), cur = sel.value
      sel.innerHTML = `<option value="">${all}</option>` +
        vals.map(v => `<option value="${esc(v)}">${esc(names[v] || v)}</option>`).join('')
      sel.value = vals.includes(cur) ? cur : ''
    }
    fill('uk', 'All kinds', [...new Set(DATA.journeys.map(j => j.kind))], KIND)
    fill('us', 'All outcomes', [...new Set(DATA.journeys.map(j => j.status))], STATUS)
    if (ALL_OPEN) DATA.journeys.forEach(j => OPEN.add(jkey(j)))
    render()
  } catch (e) {
    document.getElementById('meta').textContent = 'could not load stats: ' + e.message
  }
}

document.getElementById('range').addEventListener('click', e => {
  const r = e.target.dataset.r
  if (!r || !DATA) return
  RANGE = r
  try { localStorage.setItem('palette-stats-range', r) } catch (e) {}
  render()
})
document.getElementById('uq').addEventListener('input', () => DATA && utterances())
document.getElementById('uk').addEventListener('change', () => DATA && utterances())
document.getElementById('us').addEventListener('change', () => DATA && utterances())
let ALL_OPEN = location.hash === '#expand'
function syncExpand() {
  document.getElementById('expAll').textContent = ALL_OPEN ? 'Collapse all' : 'Expand all'
  if (!DATA) return
  OPEN.clear()
  if (ALL_OPEN) DATA.journeys.forEach(j => OPEN.add(jkey(j)))
  utterances()
}
document.getElementById('expAll').addEventListener('click', () => { ALL_OPEN = !ALL_OPEN; syncExpand() })
document.getElementById('utt').addEventListener('click', e => {
  const tr = e.target.closest('tr.j')
  if (!tr || e.target.closest('a,img')) return
  const k = tr.dataset.k
  OPEN.has(k) ? OPEN.delete(k) : OPEN.add(k)
  utterances()
})
document.getElementById('csv').href = '/stats/utterances.csv' + q
window.addEventListener('resize', () => DATA && chart())
load()
setInterval(load, 60000)
</script>
</body>
</html>
"""
