"""Single-file HTML/CSS/JS UI for Palette — a light-themed chat interface.

One conversation drives everything. Describe a deck (optionally attaching
source docs) and the crafter returns an editable, expandable plan card; Build
it; edit the plan and Regenerate at any time, or send a chat message to change
the slide you are viewing. A settings bar picks the model for each stage.
"""

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Palette</title>
<link rel="icon" type="image/png" sizes="32x32" href="/asset/favicon.png">
<link rel="icon" type="image/png" sizes="16x16" href="/asset/favicon-16.png">
<style>
:root{
  --bg:#faf9f5; --surface:#ffffff; --sunk:#f1efe7;
  --text:#20201d; --muted:#86827a; --border:#e7e4db;
  --accent:#c25e3a; --accent-hover:#a94e2f; --user:#eeebe2;
  --ok:#3f7d52; --warn:#b4843a;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:var(--bg);color:var(--text);font-size:14px;line-height:1.55;
  display:flex;flex-direction:column}

header{display:flex;align-items:center;gap:14px;padding:18px 26px;
  background:var(--bg);border-bottom:1px solid var(--border);
  position:relative;z-index:20}
.tips-pop{position:absolute;top:100%;right:22px;margin-top:7px;
  width:380px;max-height:74vh;overflow-y:auto;background:var(--surface);
  border:1px solid var(--border);border-radius:11px;
  box-shadow:0 14px 40px rgba(40,36,28,.16),0 2px 6px rgba(40,36,28,.08);
  padding:14px 18px 12px;z-index:30}
.tips-pop h4{font-size:11px;font-weight:650;letter-spacing:.05em;
  color:var(--muted);margin:12px 0 4px}
.tips-pop h4:first-child{margin-top:0}
.tips-pop p{font-size:13px;line-height:1.5;color:var(--text);margin:0 0 4px}
.tips-pop code{background:var(--sunk);border:1px solid var(--border);
  border-radius:4px;padding:1px 5px;font-size:11.5px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#8a4500}
.tips-pop b{font-weight:650}
header h1{font-size:19px;font-weight:650;letter-spacing:.01em}
header .logo{width:40px;height:40px;flex:0 0 auto;display:block;
  margin-right:-2px}
header .tag{font-size:13px;color:var(--muted)}
.spacer{flex:1}
.settings-pop{position:absolute;top:100%;right:22px;margin-top:7px;
  width:300px;background:var(--surface);border:1px solid var(--border);
  border-radius:11px;
  box-shadow:0 14px 40px rgba(40,36,28,.16),0 2px 6px rgba(40,36,28,.08);
  padding:14px 16px 12px;z-index:30}
.settings-pop h4{font-size:11px;font-weight:650;letter-spacing:.05em;
  color:var(--muted);margin:0 0 10px}
.settings-pop label{display:flex;flex-direction:column;gap:4px;
  font-size:11px;color:var(--muted);text-transform:uppercase;
  letter-spacing:.04em;margin-bottom:10px;font-weight:600}
.settings-pop label:last-child{margin-bottom:0}
.settings-pop select{font-size:13px;padding:6px 9px;text-transform:none;
  letter-spacing:normal;font-weight:400;color:var(--text)}
.settings-pop .hint{font-size:11px;color:var(--muted);margin-top:8px;
  font-weight:400;text-transform:none;letter-spacing:normal;font-style:italic}
.chip{font-size:11px;color:var(--muted);background:var(--sunk);
  border:1px solid var(--border);border-radius:20px;padding:3px 10px}
button{font-family:inherit;cursor:pointer;border-radius:8px;font-size:13px}
.btn{background:var(--accent);color:#fff;border:none;padding:7px 14px;
  font-weight:600;transition:background .15s}
.btn:hover{background:var(--accent-hover)}
.btn:disabled{opacity:.45;cursor:default}
.btn.ghost{background:var(--surface);color:var(--text);
  border:1px solid var(--border)}
.btn.ghost:hover{background:var(--sunk)}

main{display:grid;grid-template-columns:var(--left-w,480px) 6px 1fr;
  flex:1;min-height:0}
.divider{background:var(--border);cursor:col-resize;transition:background .12s}
.divider:hover,.divider.dragging{background:var(--accent)}
@media(max-width:980px){main{grid-template-columns:1fr}}

/* ---- chat ---- */
.chat{display:flex;flex-direction:column;min-height:0;
  border-right:1px solid var(--border)}
.messages{flex:1;overflow-y:auto;padding:26px 0}
.thread{max-width:680px;margin:0 auto;padding:0 24px;
  display:flex;flex-direction:column;gap:18px}

.msg.user{align-self:flex-end;max-width:85%;background:var(--user);
  border-radius:14px 14px 4px 14px;padding:9px 14px;white-space:pre-wrap}
.msg.bot{align-self:flex-start;max-width:92%;white-space:pre-wrap;
  color:var(--text)}
.msg.status{align-self:flex-start;color:var(--muted);font-size:13px;
  font-style:italic;display:flex;align-items:center;gap:9px}
.msg.status::before{content:'';flex:0 0 auto;width:13px;height:13px;
  border:2px solid var(--border);border-top-color:var(--accent);
  border-radius:50%;animation:spin .9s linear infinite}
.msg.status.err{color:var(--accent);font-style:normal}
.msg.status.err::before{display:none}
@keyframes spin{to{transform:rotate(360deg)}}

.empty{max-width:580px;margin:7vh auto 0;text-align:center;padding:0 24px}
.empty h2{font-size:22px;font-weight:600;margin-bottom:10px;
  letter-spacing:-.005em}
.empty .lede{color:var(--muted);font-size:14px;line-height:1.55;
  margin-bottom:22px}
.ex-cards{display:flex;flex-direction:column;gap:8px;text-align:left;
  margin:0 auto 26px;max-width:520px}
.ex-card{background:var(--surface);border:1px solid var(--border);
  border-radius:9px;padding:11px 14px;font-size:13px;color:var(--text);
  line-height:1.45;display:flex;align-items:flex-start;gap:9px}
.ex-card .ex-quote{flex:1;font-style:italic}
.ex-card .ex-attach{flex:0 0 auto;color:var(--muted);font-size:12px;
  margin-top:1px}
.or-divider{display:flex;align-items:center;gap:14px;
  color:var(--muted);font-size:12px;letter-spacing:.08em;
  text-transform:uppercase;margin:6px 0 14px}
.or-divider::before,.or-divider::after{content:'';flex:1;height:1px;
  background:var(--border)}
.ex-list{margin-top:12px;display:none;flex-direction:column;gap:5px;
  text-align:left;max-width:380px;margin-left:auto;margin-right:auto}
.ex-list.open{display:flex}
.ex-list button{background:var(--surface);border:1px solid var(--border);
  color:var(--text);font-size:13px;padding:8px 12px;border-radius:7px;
  text-align:left;display:flex;align-items:center;gap:8px}
.ex-list button::before{content:'📄';opacity:.7;font-size:13px}
.ex-list button:hover{background:var(--sunk);border-color:var(--accent)}

/* ---- plan card ---- */
.plan{align-self:stretch;background:var(--surface);
  border:1px solid var(--border);border-radius:12px;overflow:hidden}
.plan-h{display:flex;align-items:center;gap:8px;padding:9px 13px;
  border-bottom:1px solid var(--border);background:var(--sunk)}
.plan-h b{font-size:12px;font-weight:650;letter-spacing:.03em;
  text-transform:uppercase;color:var(--muted)}
.plan-body{padding:14px 18px 6px;font-size:13.5px;line-height:1.55;
  color:var(--text);max-height:60vh;overflow-y:auto}
.plan-body h1{font-size:18px;font-weight:650;margin:0 0 10px;
  border-bottom:1px solid var(--border);padding-bottom:6px}
.plan-body h2{font-size:14.5px;font-weight:600;margin:16px 0 6px;
  color:var(--text)}
.plan-body h3{font-size:13px;font-weight:600;margin:12px 0 4px;
  color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.plan-body p{margin:6px 0}
.plan-body ul,.plan-body ol{margin:6px 0 8px;padding-left:22px}
.plan-body li{margin:3px 0}
.plan-body li>p{margin:2px 0}
.plan-body code{background:var(--sunk);padding:1px 5px;border-radius:4px;
  font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.plan-body pre{background:var(--sunk);padding:10px 12px;border-radius:6px;
  overflow-x:auto;margin:8px 0;border:1px solid var(--border)}
.plan-body pre code{background:none;padding:0;font-size:12px;line-height:1.45}
.plan-body blockquote{margin:8px 0;padding:4px 12px;
  border-left:3px solid var(--border);color:var(--muted)}
.plan-body table{border-collapse:collapse;margin:10px 0;font-size:12.5px}
.plan-body th,.plan-body td{border:1px solid var(--border);
  padding:5px 9px;text-align:left}
.plan-body th{background:var(--sunk);font-weight:600;font-size:11.5px;
  text-transform:uppercase;letter-spacing:.03em;color:var(--muted)}
.plan-body strong{font-weight:650}
.plan-body hr{border:none;border-top:1px solid var(--border);margin:12px 0}
.dir-chip{display:inline-block;background:#fff3e0;color:#8a4500;
  border:1px solid #f0c98a;border-radius:10px;padding:0 8px;
  font-size:11.5px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-weight:500;margin:0 1px;vertical-align:1px}
.plan textarea{display:none;width:100%;border:none;outline:none;resize:vertical;
  padding:12px 14px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:12px;line-height:1.5;height:340px;background:var(--surface);
  color:var(--text);box-sizing:border-box}
.plan.editing .plan-body{display:none}
.plan.editing textarea{display:block}
.plan-f{display:flex;align-items:center;gap:10px;padding:10px 13px;
  border-top:1px solid var(--border)}
.plan-f label{font-size:11px;color:var(--muted)}
select{font-family:inherit;font-size:12px;padding:5px 8px;border-radius:7px;
  border:1px solid var(--border);background:var(--surface);color:var(--text)}
.plan.done{opacity:.6}
.plan-x{background:var(--surface);border:1px solid var(--border);
  color:var(--muted);font-size:11px;padding:2px 9px;border-radius:6px;
  cursor:pointer}
.plan-x:hover{background:var(--bg);color:var(--text)}

/* ---- composer ---- */
.composer{border-top:1px solid var(--border);padding:14px 24px;
  background:var(--bg)}
.composer-in{max-width:680px;margin:0 auto;display:flex;gap:8px;
  align-items:flex-end;background:var(--surface);border:1px solid var(--border);
  border-radius:14px;padding:8px 8px 8px 14px;transition:border-color .15s}
.composer-in:focus-within{border-color:var(--accent)}
.composer textarea{flex:1;border:none;outline:none;resize:none;
  font-family:inherit;font-size:14px;line-height:1.5;background:transparent;
  color:var(--text);max-height:160px;padding:5px 0}
.composer textarea::placeholder{color:var(--muted)}
.icon-btn{background:transparent;border:none;color:var(--muted);
  font-size:17px;padding:4px 7px;border-radius:7px}
.icon-btn:hover{background:var(--sunk);color:var(--text)}
.send{background:var(--accent);color:#fff;border:none;width:32px;height:32px;
  border-radius:9px;font-size:15px;display:flex;align-items:center;
  justify-content:center}
.send:hover{background:var(--accent-hover)}
.send:disabled{opacity:.4;cursor:default}
.attached{max-width:680px;margin:0 auto 6px;display:flex;flex-wrap:wrap;
  gap:6px;justify-content:flex-start}
.att-chip{display:inline-flex;align-items:center;gap:5px;
  background:var(--sunk);border:1px solid var(--border);border-radius:14px;
  padding:3px 6px 3px 10px;font-size:12px;color:var(--text);max-width:240px}
.att-chip .att-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.att-chip button{background:none;border:none;color:var(--muted);
  font-size:14px;line-height:1;padding:0 4px;cursor:pointer;border-radius:50%}
.att-chip button:hover{background:var(--bg);color:var(--text)}
.plan-sources{display:flex;flex-wrap:wrap;gap:5px;padding:7px 13px;
  background:var(--surface);border-bottom:1px solid var(--border);
  font-size:11.5px;color:var(--muted);align-items:center}
.plan-sources .src-label{font-weight:600;letter-spacing:.04em;
  text-transform:uppercase;font-size:10.5px;color:var(--muted)}
.plan-sources .src-chip{background:var(--sunk);border:1px solid var(--border);
  border-radius:10px;padding:1px 8px;font-size:11.5px;color:var(--text);
  max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* ---- preview ---- */
.preview{display:flex;flex-direction:column;min-height:0;min-width:0;
  overflow:hidden;background:var(--bg)}
.stage{flex:1;display:flex;align-items:center;justify-content:center;
  padding:22px;min-height:0;min-width:0;overflow:hidden;position:relative}
.stage img{max-width:100%;max-height:100%;border:1px solid var(--border);
  border-radius:4px;box-shadow:0 6px 24px rgba(40,36,28,.10);cursor:zoom-in}
.stage .none{color:var(--muted);text-align:center;font-size:13px}
.stage .none strong{display:block;color:var(--text);font-size:14px;
  font-weight:600;margin-bottom:6px}
.counter{position:absolute;top:14px;right:18px;background:#ffffffdd;
  border:1px solid var(--border);border-radius:20px;padding:2px 10px;
  font-size:11px;color:var(--muted)}
.slide-actions{display:flex;justify-content:flex-end;align-items:center;
  padding:7px 16px;border-top:1px solid var(--border);background:var(--sunk);
  gap:8px}
.retry-btn{background:var(--accent);border:none;color:#fff;font-size:12px;
  padding:6px 13px;border-radius:7px;cursor:pointer;display:flex;
  align-items:center;gap:5px;font-family:inherit;font-weight:600;
  transition:background .15s}
.retry-btn:hover:not(:disabled){background:var(--accent-hover)}
.retry-btn:disabled{opacity:.5;cursor:wait}
.retry-ico{display:inline-block;font-size:14px;line-height:1}
.retry-btn.spin .retry-ico{animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.thumbs{display:flex;gap:7px;overflow-x:auto;padding:10px 16px 14px;
  border-top:1px solid var(--border);scrollbar-width:thin}
.thumb{flex:0 0 auto;width:104px;height:58px;border:2px solid var(--border);
  border-radius:5px;overflow:hidden;cursor:pointer;background:var(--surface);
  position:relative}
.thumb.active{border-color:var(--accent)}
.thumb img{width:100%;height:100%;object-fit:cover}
.thumb span{position:absolute;top:2px;left:4px;font-size:9px;color:var(--muted);
  background:#ffffffcc;padding:0 4px;border-radius:3px}

/* ---- lightbox (click a slide to enlarge) ---- */
.lightbox{position:fixed;inset:0;background:rgba(28,26,22,.9);display:none;
  align-items:center;justify-content:center;z-index:50;cursor:zoom-out}
.lightbox.open{display:flex}
.lightbox img{max-width:94vw;max-height:92vh;border-radius:4px;
  box-shadow:0 16px 60px rgba(0,0,0,.55)}
.lb-nav{position:absolute;top:50%;transform:translateY(-50%);width:44px;
  height:44px;border-radius:50%;border:1px solid var(--border);
  background:#fffffff2;color:var(--text);font-size:22px;display:flex;
  align-items:center;justify-content:center;cursor:pointer}
.lb-prev{left:26px}.lb-next{right:26px}
.lb-count{position:absolute;bottom:22px;left:50%;transform:translateX(-50%);
  color:#fff;font-size:12px;background:rgba(0,0,0,.45);padding:4px 14px;
  border-radius:20px}
</style>
<script src="https://cdn.jsdelivr.net/npm/marked@13/marked.min.js"></script>
</head>
<body>

<header>
  <img class="logo" src="/asset/logo.png" alt="">
  <h1>Palette</h1>
  <span class="tag">request &rarr; plan &rarr; deck</span>
  <div class="spacer"></div>
  <button class="btn ghost" id="modelsBtn" onclick="toggleSettings(event)"
    title="Pick the model for each pipeline stage">⚙ Models</button>
  <div class="settings-pop" id="settingsPop" hidden>
    <h4>MODEL ROSTER</h4>
    <label>Planner
      <select id="mPlanner">
        <option value="gpt-oss-120b">gpt-oss-120b</option>
        <option value="llama-3.3-70b">Llama-3.3-70B</option>
      </select></label>
    <label>Designer + Coder
      <select id="mDesigner">
        <option value="palette-lora">Palette LoRA</option>
        <option value="gpt-oss-120b">gpt-oss-120b</option>
        <option value="llama-3.3-70b">Llama-3.3-70B</option>
      </select></label>
    <label>Critic
      <select id="mCritic">
        <option value="gpt-oss-120b">gpt-oss-120b</option>
        <option value="llama-3.3-70b">Llama-3.3-70B</option>
      </select></label>
    <div class="hint">Changes apply to your next build.</div>
  </div>
  <button class="btn ghost" id="tipsBtn" onclick="toggleTips(event)">
    Tips</button>
  <div class="tips-pop" id="tipsPop" hidden>
    <h4>WORKING WITH PALETTE</h4>
    <p>Type a request, attach files, or load a plan to start. Once a plan
    exists, edit it directly — Regenerate applies your changes.</p>

    <h4>PLAN DIRECTIVES</h4>
    <p>Inline <code>[[render as donut chart]]</code> or
    <code>[[render as a timeline with 8 nodes]]</code> controls how a slide
    is treated.</p>

    <h4>PER-SLIDE ACTIONS</h4>
    <p><b>↻ Retry this slide</b> — re-runs one slide at a small temperature
    variation (same plan, new sampling). Type into chat for instructed
    edits like "make slide 5 a table".</p>

    <h4>PALETTES</h4>
    <p>IBM is the default and reflects IBM's design guidelines. Neutral and
    Mixed give small visual variations on the same model.</p>

    <h4>WHEN A DECK LOOKS OFF</h4>
    <p>A "⚠ Heads-up" message means the model needed retries — tweak the
    plan slightly and Regenerate. Single bad slide? Click Retry on it.</p>
  </div>
  <input type="file" id="planFile" accept=".md,.txt,.markdown" hidden
    onchange="onPlanFileChosen()">
  <button class="btn ghost" onclick="loadPlanFromFile()"
    title="Load an existing plan .md file directly — skips the crafter">Load plan</button>
  <button class="btn ghost" onclick="newDeck()">New</button>
  <button class="btn" id="dlBtn" onclick="downloadDeck()" disabled>Download</button>
</header>

<main>
  <section class="chat">
    <div class="messages" id="messages"></div>
    <div class="composer">
      <div class="attached" id="attached"></div>
      <div class="composer-in">
        <textarea id="input" rows="1"
          placeholder="Describe a deck to build..."
          onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}"
          oninput="growInput(this)"></textarea>
        <input type="file" id="files" multiple hidden
          accept=".pdf,.docx,.pptx,.md,.txt" onchange="showAttached()">
        <button class="icon-btn" title="Attach source documents"
          onclick="document.getElementById('files').click()">&#128206;</button>
        <button class="send" id="sendBtn" onclick="send()">&uarr;</button>
      </div>
    </div>
  </section>

  <div class="divider" id="divider" title="Drag to resize"></div>

  <aside class="preview">
    <div class="stage" id="stage">
      <div class="none"><strong>No deck yet</strong>
        Build one and the slides appear here.</div>
    </div>
    <div class="slide-actions" id="slideActions" hidden>
      <button class="ghost retry-btn" id="retryBtn" onclick="retrySlide()"
        title="Retry this slide with a small temperature variation. Same plan, same brief — just a different sampling. Use this when the slide looks like a bad roll. For different content, edit the plan or type an instruction.">
        <span class="retry-ico">↻</span> Retry this slide
      </button>
    </div>
    <div class="thumbs" id="thumbs"></div>
  </aside>
</main>

<script>
const TID = 'session-' + Math.random().toString(36).slice(2, 10)
let mode = 'describe'        // describe | plan | deck
let slideCount = 0
let currentSlide = 1
let busy = false
let currentAbort = null
// Attached files we'll send on the next /draft. Maintained separately from
// the <input>'s read-only .files FileList so we can remove individual chips.
let attachedFiles = []

const $ = id => document.getElementById(id)
const messages = $('messages')

function renderEmpty() {
  messages.innerHTML =
    '<div class="empty" id="empty">' +
    '<h2>Build a slide deck with Palette</h2>' +
    '<p class="lede">Describe what you want and, if you have them, attach ' +
    'reference documents (PDF, DOCX, PPTX, MD). Palette will draft a plan ' +
    'you can edit, then turn it into a deck.</p>' +
    '<div class="ex-cards">' +
      '<div class="ex-card"><span class="ex-quote">' +
        '"Build a deck to teach someone about RAG architectures."' +
        '</span></div>' +
      '<div class="ex-card"><span class="ex-quote">' +
        '"Extract and summarize my experiment results into a presentation."' +
        '</span><span class="ex-attach">📎 attach results</span></div>' +
    '</div>' +
    '<div class="or-divider">or</div>' +
    '<button class="btn ghost" onclick="toggleExamples()" id="exBtn">' +
      'Start from an example plan' +
    '</button>' +
    '<div class="ex-list" id="exList"></div>' +
    '</div>'
}

async function toggleExamples() {
  const el = $('exList')
  el.classList.toggle('open')
  if (el.dataset.loaded) return
  el.dataset.loaded = '1'
  try {
    const r = await fetch('/examples')
    const d = await r.json()
    // /examples now returns {file, label} objects so the button shows
    // a human-readable title (e.g. "Engineering All-Hands") and loads
    // the underlying .md by its real name.
    for (const ex of d.examples || []) {
      const b = document.createElement('button')
      b.textContent = ex.label
      b.onclick = () => loadExample(ex.file)
      el.appendChild(b)
    }
  } catch (e) {}
}

function growInput(t) {
  t.style.height = 'auto'
  t.style.height = Math.min(t.scrollHeight, 160) + 'px'
}

function addMsg(role, text) {
  const e = $('empty'); if (e) e.remove()
  const d = document.createElement('div')
  d.className = 'msg ' + role
  d.textContent = text
  thread().appendChild(d)
  scroll()
  return d
}

function thread() {
  let t = messages.querySelector('.thread')
  if (!t) { t = document.createElement('div'); t.className = 'thread'
            messages.appendChild(t) }
  return t
}
function scroll() { messages.scrollTop = messages.scrollHeight }

function setBusy(b) {
  busy = b
  const btn = $('sendBtn')
  if (b) {
    btn.innerHTML = '&#9632;'
    btn.title = 'Stop the current request'
    btn.onclick = cancel
    btn.disabled = false
  } else {
    btn.innerHTML = '&uarr;'
    btn.title = ''
    btn.onclick = send
    btn.disabled = false
  }
}

function cancel() {
  try { if (currentAbort) currentAbort.abort() } catch (e) {}
  fetch('/abort/' + TID, { method: 'POST' }).catch(() => {})
}

function pollProgress(statusEl) {
  let live = true
  ;(async () => {
    while (live) {
      try {
        const r = await fetch('/progress/' + TID)
        if (r.ok) {
          const p = await r.json()
          if (live && p && p.message) {
            const c = (p.total > 0 && p.current > 0)
              ? ' (' + p.current + '/' + p.total + ')' : ''
            statusEl.textContent = p.message + c
          }
        }
      } catch (e) {}
      await new Promise(r => setTimeout(r, 700))
    }
  })()
  return () => { live = false }
}

// ---- the one entry point: route by conversation state ----
function send() {
  if (busy) return
  const inp = $('input')
  const text = inp.value.trim()
  if (mode === 'deck') {
    if (!text) return
    inp.value = ''; growInput(inp)
    applyEdit(text)
  } else {
    const hasFiles = attachedFiles.length > 0
    if (!text && !hasFiles) return
    inp.value = ''; growInput(inp)
    draft(text || '(see attached documents)')
  }
}

async function draft(request) {
  setBusy(true)
  addMsg('user', request)
  const status = addMsg('status', 'Drafting a plan...')
  const fd = new FormData()
  fd.append('request', request)
  fd.append('thread_id', TID)
  fd.append('planner', $('mPlanner').value)
  for (const f of attachedFiles) fd.append('files', f)
  // Snapshot the names BEFORE we clear, so the plan card can render them
  // as a sources strip even after the array is reset.
  const sourceNames = attachedFiles.map(f => f.name)
  currentAbort = new AbortController()
  try {
    const r = await fetch('/draft', { method: 'POST', body: fd,
      signal: currentAbort.signal })
    const d = await r.json()
    status.remove()
    if (!r.ok) throw new Error(d.error || r.statusText)
    attachedFiles = []; renderAttached()
    addMsg('bot', "Here's a plan — review and edit it below, then Build.")
    addPlanCard(d.plan || '', d.sources || sourceNames)
    mode = 'plan'
  } catch (err) {
    if (err.name === 'AbortError') {
      status.className = 'msg status'; status.textContent = 'Stopped.'
    } else {
      status.className = 'msg status err'
      status.textContent = 'Error: ' + err.message
    }
  } finally { setBusy(false); currentAbort = null }
}

function loadExample(name) {
  fetch('/example/' + encodeURIComponent(name))
    .then(r => r.json())
    .then(d => {
      addMsg('user', 'Example: ' + name)
      addMsg('bot', 'Loaded the example plan — review and edit, then Build.')
      addPlanCard(d.content || '')
      mode = 'plan'
    }).catch(() => {})
}

// Load plan directly from any .md file on disk — bypasses the crafter
// entirely. Native file picker, no upload to the server, no /draft call.
function loadPlanFromFile() {
  $('planFile').click()
}

async function onPlanFileChosen() {
  const inp = $('planFile')
  const f = inp.files && inp.files[0]
  inp.value = ''
  if (!f) return
  let text = ''
  try { text = await f.text() } catch (e) { return }
  if (!text.trim()) return
  addMsg('user', 'Loaded plan: ' + f.name)
  addMsg('bot', 'Loaded — review and edit if needed, then Build.')
  addPlanCard(text)
  mode = 'plan'
}

// Wrap [[render as X]] / [[directive]] sequences in chip spans. Runs AFTER
// marked.parse() so the regex sees HTML, not raw markdown — bullet/heading
// structure is already a DOM by then; we only touch text nodes' content.
function _chipifyDirectives(html) {
  return html.replace(/\[\[([^\]\n]+?)\]\]/g,
    (_, inner) => '<span class="dir-chip">[[' + inner.trim() + ']]</span>')
}

function renderPlanMd(text) {
  if (!window.marked) return text.replace(/[&<>]/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))  // graceful fallback
  marked.setOptions({ breaks: false, gfm: true })
  return _chipifyDirectives(marked.parse(text || ''))
}

function addPlanCard(planText, sources) {
  const card = document.createElement('div')
  card.className = 'plan'
  const srcStrip = (sources && sources.length)
    ? '<div class="plan-sources"><span class="src-label">📎 Sources</span>'
      + sources.map(n => '<span class="src-chip" title="' + _escapeHtml(n)
        + '">' + _escapeHtml(n) + '</span>').join('')
      + '</div>'
    : ''
  card.innerHTML =
    '<div class="plan-h"><b>Plan</b><div class="spacer"></div>' +
    '<button class="plan-x" type="button" onclick="togglePlanEdit(this)">Edit</button>' +
    '</div>' +
    srcStrip +
    '<div class="plan-body"></div>' +
    '<textarea spellcheck="false"></textarea>' +
    '<div class="plan-f">' +
    '<label>Palette</label>' +
    '<select class="pal">' +
    // value = palette_family the LoRA expects; label = user-facing name
    [['ibm_watsonx','IBM'], ['neutral','Neutral'], ['cool','Mixed']]
      .map(([v,l]) => '<option value="' + v + '">' + l + '</option>').join('') +
    '</select><div style="flex:1"></div>' +
    '<button class="btn">Build deck</button></div>'
  card.querySelector('textarea').value = planText
  card.querySelector('.plan-body').innerHTML = renderPlanMd(planText)
  card.querySelector('.btn').onclick = () => buildDeck(card)
  thread().appendChild(card)
  scroll()
}

// Flip a plan card between rendered (view) and textarea (edit). On leaving
// edit mode we re-render from the textarea so any edits become visible.
function togglePlanEdit(btn) {
  const card = btn.closest('.plan')
  const ta = card.querySelector('textarea')
  if (card.classList.toggle('editing')) {
    btn.textContent = 'Done'
    // Size the textarea to fit the content the first time edit opens.
    ta.style.height = Math.max(340, ta.scrollHeight + 20) + 'px'
    ta.focus()
  } else {
    btn.textContent = 'Edit'
    card.querySelector('.plan-body').innerHTML = renderPlanMd(ta.value)
  }
}

async function buildDeck(card) {
  if (busy) return
  const plan = card.querySelector('textarea').value.trim()
  if (!plan) return
  const palette = card.querySelector('.pal').value
  setBusy(true)
  card.querySelector('.btn').disabled = true
  const status = addMsg('status', 'Building...')
  const stop = pollProgress(status)
  currentAbort = new AbortController()
  try {
    const r = await fetch('/build', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ plan, thread_id: TID, palette_family: palette,
        planner: $('mPlanner').value, designer_coder: $('mDesigner').value,
        critic: $('mCritic').value }),
      signal: currentAbort.signal
    })
    const d = await r.json()
    stop(); status.remove()
    if (!r.ok) throw new Error(d.error || r.statusText)
    const btn = card.querySelector('.btn')
    btn.disabled = false
    btn.textContent = 'Regenerate deck'
    let line = 'Built "' + (d.title || 'deck') + '" — ' + d.slide_count
      + ' slides in ' + d.elapsed + 's.'
    const g = d.geometry || {}
    if (g.accepted && g.accepted.length) line += ' Layout repair fixed slide(s) '
      + g.accepted.join(', ') + (g.passes > 1 ? ' (over ' + g.passes + ' passes)' : '') + '.'
    if (d.unrepaired && d.unrepaired.length) line += ' '
      + d.unrepaired.length + ' slide(s) could not be auto-repaired.'
    const ret = d.retries || {}
    const designerRetried = (ret.designer_attempt || 1) > 1
    const coderRetried = ret.coder_retried_slides && ret.coder_retried_slides.length
    let warn = ''
    if (designerRetried || coderRetried) {
      const parts = []
      if (designerRetried) parts.push('the brief required ' + ret.designer_attempt + ' attempts')
      if (coderRetried) parts.push('slide(s) ' + ret.coder_retried_slides.join(', ') + ' needed a retry')
      warn = '\n\n⚠ Heads-up — ' + parts.join('; ') + '. The retry was sampled at temperature 0.3 (the first pass didn\'t parse), so the output may have drifted from the brief. If the deck looks off-topic, tweak the plan slightly (even one character changes the tokenization) and Regenerate.'
    }
    addMsg('bot', line + warn + '\nEdit the plan above and Regenerate any time, '
      + 'or click a slide and tell me what to change.')
    mode = 'deck'
    currentSlide = 1
    await refreshDeck()
  } catch (err) {
    stop()
    if (err.name === 'AbortError') {
      status.className = 'msg status'; status.textContent = 'Stopped.'
    } else {
      status.className = 'msg status err'
      status.textContent = 'Error: ' + err.message
    }
    card.querySelector('.btn').disabled = false
  } finally { setBusy(false); currentAbort = null }
}

async function retrySlide() {
  if (busy || !currentSlide) return
  const btn = $('retryBtn')
  setBusy(true)
  btn.disabled = true
  btn.classList.add('spin')
  const status = addMsg('status', 'Retrying slide ' + currentSlide
    + ' at temp 0.3...')
  currentAbort = new AbortController()
  try {
    const r = await fetch('/retry/' + TID + '/' + currentSlide, {
      method: 'POST', signal: currentAbort.signal
    })
    const d = await r.json()
    status.remove()
    if (!r.ok) throw new Error(d.error || r.statusText)
    addMsg('bot', 'Retried slide ' + d.retried
      + '. If it still looks off, try editing the plan or asking for a change.')
    await refreshDeck()
  } catch (err) {
    if (err.name === 'AbortError') {
      status.className = 'msg status'; status.textContent = 'Stopped.'
    } else {
      status.className = 'msg status err'
      status.textContent = 'Error: ' + err.message
    }
  } finally {
    setBusy(false); currentAbort = null
    btn.disabled = false; btn.classList.remove('spin')
  }
}

async function applyEdit(instruction) {
  setBusy(true)
  addMsg('user', instruction)
  const status = addMsg('status', 'Updating slide ' + currentSlide + '...')
  currentAbort = new AbortController()
  try {
    const r = await fetch('/edit', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ thread_id: TID, slide_n: currentSlide,
                             instruction }),
      signal: currentAbort.signal
    })
    const d = await r.json()
    status.remove()
    if (!r.ok) throw new Error(d.error || r.statusText)
    addMsg('bot', 'Updated slide ' + d.edited + '.')
    await refreshDeck()
  } catch (err) {
    if (err.name === 'AbortError') {
      status.className = 'msg status'; status.textContent = 'Stopped.'
    } else {
      status.className = 'msg status err'
      status.textContent = 'Error: ' + err.message
    }
  } finally { setBusy(false); currentAbort = null }
}

async function refreshDeck() {
  try {
    const r = await fetch('/deck/' + TID)
    if (!r.ok) return
    const d = await r.json()
    slideCount = d.slide_count || 0
    $('dlBtn').disabled = slideCount === 0
    if (currentSlide > slideCount) currentSlide = slideCount || 1
    renderPreview()
  } catch (e) {}
}

function renderPreview() {
  const stage = $('stage'), thumbs = $('thumbs'), actions = $('slideActions')
  if (slideCount === 0) {
    stage.innerHTML = '<div class="none"><strong>No deck yet</strong>'
      + 'Build one and the slides appear here.</div>'
    thumbs.innerHTML = ''
    actions.hidden = true
    return
  }
  actions.hidden = false
  const ts = Date.now()
  stage.innerHTML =
    '<img onclick="openLightbox()" src="/preview/' + TID + '/'
      + currentSlide + '?t=' + ts + '">' +
    '<div class="counter">' + currentSlide + ' / ' + slideCount
      + '  ·  click to enlarge</div>'
  const out = []
  for (let i = 1; i <= slideCount; i++) {
    out.push('<div class="thumb' + (i === currentSlide ? ' active' : '')
      + '" onclick="selectSlide(' + i + ')">'
      + '<img src="/preview/' + TID + '/' + i + '?t=' + ts + '">'
      + '<span>' + i + '</span></div>')
  }
  thumbs.innerHTML = out.join('')
  $('input').placeholder = 'Describe a change to slide ' + currentSlide + '...'
}

function selectSlide(i) {
  currentSlide = i
  renderPreview()
}

function downloadDeck() { window.location = '/download/' + TID }

function toggleTips(ev) {
  if (ev) ev.stopPropagation()
  $('tipsPop').hidden = !$('tipsPop').hidden
  $('settingsPop').hidden = true   // popovers are mutually exclusive
}

function toggleSettings(ev) {
  if (ev) ev.stopPropagation()
  $('settingsPop').hidden = !$('settingsPop').hidden
  $('tipsPop').hidden = true
}

// Dismiss any open popover on click-outside or Escape. One handler covers
// both since they share the same dismiss semantics.
document.addEventListener('click', (ev) => {
  for (const [popId, btnId] of [['tipsPop','tipsBtn'],
                                  ['settingsPop','modelsBtn']]) {
    const pop = $(popId), btn = $(btnId)
    if (!pop || pop.hidden) continue
    if (pop.contains(ev.target) || (btn && btn.contains(ev.target))) continue
    pop.hidden = true
  }
})
document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Escape') return
  $('tipsPop').hidden = true
  $('settingsPop').hidden = true
})

// Called when the <input type="file"> picker resolves. Append the picked
// files to attachedFiles[], then clear the input so the user can re-add the
// same name if they removed it earlier.
function showAttached() {
  for (const f of $('files').files) attachedFiles.push(f)
  $('files').value = ''
  renderAttached()
}

function _escapeHtml(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))
}

function renderAttached() {
  const box = $('attached')
  if (!attachedFiles.length) { box.innerHTML = ''; return }
  box.innerHTML = attachedFiles.map((f, i) =>
    '<span class="att-chip" title="' + _escapeHtml(f.name) + '">'
    + '<span class="att-name">📎 ' + _escapeHtml(f.name) + '</span>'
    + '<button onclick="removeAttached(' + i + ')" title="Remove">'
    + '×</button></span>').join('')
}

function removeAttached(i) {
  attachedFiles.splice(i, 1)
  renderAttached()
}

async function newDeck() {
  try { await fetch('/clear/' + TID, { method: 'POST' }) } catch (e) {}
  window.location.reload()
}

function openLightbox() {
  if (slideCount === 0) return
  updateLightbox()
  $('lightbox').classList.add('open')
}
function updateLightbox() {
  $('lbImg').src = '/preview/' + TID + '/' + currentSlide + '?t=' + Date.now()
  $('lbCount').textContent = currentSlide + ' / ' + slideCount
}
function closeLightbox(e) {
  if (e && e.target && e.target.closest && e.target.closest('.lb-nav')) return
  $('lightbox').classList.remove('open')
}
function lbNav(e, dir) {
  if (e) e.stopPropagation()
  currentSlide = Math.min(slideCount, Math.max(1, currentSlide + dir))
  updateLightbox()
  renderPreview()
}
document.addEventListener('keydown', e => {
  if (!$('lightbox').classList.contains('open')) return
  if (e.key === 'Escape') closeLightbox()
  else if (e.key === 'ArrowLeft') lbNav(e, -1)
  else if (e.key === 'ArrowRight') lbNav(e, 1)
})

renderEmpty()
fetch('/health').then(r => r.json()).then(h => {
  if (h && h.roster) $('modelChip').textContent = 'designer: ' + h.roster.designer
}).catch(() => {})

;(function setupDivider() {
  const d = $('divider'); if (!d) return
  let dragging = false
  d.addEventListener('mousedown', e => {
    dragging = true; d.classList.add('dragging')
    document.body.style.userSelect = 'none'
    e.preventDefault()
  })
  document.addEventListener('mousemove', e => {
    if (!dragging) return
    const w = Math.max(320, Math.min(window.innerWidth - 320, e.clientX))
    document.documentElement.style.setProperty('--left-w', w + 'px')
  })
  document.addEventListener('mouseup', () => {
    if (!dragging) return
    dragging = false; d.classList.remove('dragging')
    document.body.style.userSelect = ''
  })
})()
</script>
<div class="lightbox" id="lightbox" onclick="closeLightbox(event)">
  <button class="lb-nav lb-prev" onclick="lbNav(event,-1)">&lsaquo;</button>
  <img id="lbImg" alt="">
  <button class="lb-nav lb-next" onclick="lbNav(event,1)">&rsaquo;</button>
  <div class="lb-count" id="lbCount"></div>
</div>
</body>
</html>
"""
