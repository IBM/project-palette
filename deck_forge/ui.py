HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Palette</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:#0f1117;color:#e2e2e8;min-height:100vh}

header{background:#1a1a2e;border-bottom:1px solid #2d2d4a;padding:14px 28px;
  display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:10}
header h1{font-size:17px;font-weight:700;color:#fff;letter-spacing:.01em}
.sub{font-size:12px;color:#6b6b7e}.sub span{color:#7c3aed;font-weight:600}
.spacer{flex:1}
.badge{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;
  background:#1e1b4b;color:#a5b4fc}
button.dl{background:#7c3aed;color:#fff;border:none;border-radius:8px;
  padding:7px 14px;font-size:12px;font-weight:600;cursor:pointer;
  transition:background .15s}
button.dl:hover{background:#6d28d9}
button.dl:disabled{opacity:.4;cursor:default}
button.clear{background:#1a1a2e;color:#94a3b8;border:1px solid #2d2d4a;
  border-radius:8px;padding:7px 12px;font-size:12px;font-weight:600;
  cursor:pointer;transition:all .15s;margin-right:8px}
button.clear:hover{background:#3b1818;color:#fca5a5;border-color:#7f1d1d}

.layout{display:grid;grid-template-columns:minmax(360px,440px) 1fr;gap:20px;
  max-width:1500px;margin:0 auto;padding:20px 24px;height:calc(100vh - 57px)}
@media(max-width:900px){.layout{grid-template-columns:1fr;height:auto}}

.panel{display:flex;flex-direction:column;gap:14px;overflow:hidden;min-height:0}
.card{background:#1a1a2e;border:1px solid #2d2d4a;border-radius:12px;padding:14px}

.chat-log{flex:1;overflow-y:auto;background:#1a1a2e;border:1px solid #2d2d4a;
  border-radius:12px;padding:14px;min-height:120px}
.chat-log .msg{margin-bottom:10px;font-size:13px;line-height:1.55}
.chat-log .msg.user{color:#e2e2e8}
.chat-log .msg.user::before{content:"› ";color:#7c3aed;font-weight:700}
.chat-log .msg.agent{color:#b5b5d0;padding-left:14px;border-left:2px solid #2d2d4a;
  margin-left:2px;padding-top:2px;padding-bottom:2px;white-space:pre-wrap}
.chat-log .msg.status{color:#6b6b7e;font-style:italic;font-size:12px}
.chat-empty{color:#4a4a60;font-size:12px;text-align:center;padding:24px 0;
  font-style:italic}

.chat-row{display:flex;gap:8px}
.chat-input{flex:1;padding:9px 14px;border-radius:8px;font-size:13px;
  background:#0f1117;border:1px solid #2d2d4a;color:#e2e2e8;outline:none;
  transition:border-color .15s}
.chat-input:focus{border-color:#7c3aed;box-shadow:0 0 0 3px rgba(124,58,237,.15)}
.chat-input::placeholder{color:#4a4a60}
button.send{background:#7c3aed;color:#fff;border:none;border-radius:8px;
  padding:9px 16px;font-size:13px;font-weight:600;cursor:pointer;
  transition:background .15s;white-space:nowrap}
button.send:hover{background:#6d28d9}
button.send:disabled{opacity:.45;cursor:default}

.preview{display:flex;flex-direction:column;gap:12px;min-height:0}
.preview-main{flex:1;background:#1a1a2e;border:1px solid #2d2d4a;border-radius:12px;
  display:flex;align-items:center;justify-content:center;overflow:hidden;
  min-height:280px;position:relative}
.preview-main img{max-width:100%;max-height:100%;border-radius:6px;
  box-shadow:0 10px 40px rgba(0,0,0,.5)}
.preview-empty{color:#4a4a60;font-size:13px;font-style:italic;text-align:center;
  padding:40px 24px;line-height:1.7}
.preview-empty strong{display:block;color:#6b6b7e;margin-bottom:8px;font-size:14px;
  font-style:normal}
.preview-counter{position:absolute;top:12px;right:14px;background:#0f1117aa;
  padding:3px 10px;border-radius:12px;font-size:11px;color:#94a3b8;
  border:1px solid #2d2d4a}

.thumbs{display:flex;gap:8px;overflow-x:auto;padding:4px 2px;
  scrollbar-width:thin;scrollbar-color:#2d2d4a #0f1117}
.thumbs::-webkit-scrollbar{height:6px}
.thumbs::-webkit-scrollbar-thumb{background:#2d2d4a;border-radius:3px}
.thumb{flex:0 0 auto;width:128px;height:72px;background:#1a1a2e;
  border:2px solid #2d2d4a;border-radius:6px;cursor:pointer;overflow:hidden;
  transition:border-color .15s;position:relative}
.thumb.active{border-color:#7c3aed}
.thumb img{width:100%;height:100%;object-fit:cover}
.thumb-num{position:absolute;top:2px;left:4px;background:#0f1117cc;color:#94a3b8;
  font-size:10px;padding:1px 5px;border-radius:3px;font-weight:600}
</style>
</head>
<body>

<header>
  <h1>Palette</h1>
  <p class="sub">Conversational deck builder · <span>gpt-oss-120b</span></p>
  <div class="spacer"></div>
  <span class="badge" id="slideCount">0 slides</span>
  <button class="clear" id="clearBtn" onclick="clearAll()">Clear</button>
  <button class="dl" id="dlBtn" onclick="downloadDeck()" disabled>Download .pptx</button>
</header>

<div class="layout">
  <div class="panel">
    <div class="chat-log" id="chatLog">
      <div class="chat-empty">Start by describing the deck you want — a topic, audience, or goal.</div>
    </div>
    <div class="card">
      <div class="chat-row">
        <input class="chat-input" id="chatInput" type="text"
          placeholder="Ask for a deck, or describe a change…"
          onkeydown="if(event.key==='Enter')send()">
        <button class="send" id="sendBtn" onclick="send()">Send</button>
      </div>
    </div>
  </div>

  <div class="preview">
    <div class="preview-main" id="previewMain">
      <div class="preview-empty">
        <strong>No deck yet</strong>
        Your slides will appear here once Palette builds them.
      </div>
    </div>
    <div class="thumbs" id="thumbs"></div>
  </div>
</div>

<script>
const THREAD_ID = 'session-' + Math.random().toString(36).slice(2, 10)
let currentSlide = 1
let slideCount = 0

function logMsg(cls, text) {
  const log = document.getElementById('chatLog')
  if (log.querySelector('.chat-empty')) log.innerHTML = ''
  const div = document.createElement('div')
  div.className = 'msg ' + cls
  div.textContent = text
  log.appendChild(div)
  log.scrollTop = log.scrollHeight
  return div
}

async function send() {
  const inp = document.getElementById('chatInput')
  const btn = document.getElementById('sendBtn')
  const q = inp.value.trim()
  if (!q) return

  logMsg('user', q)
  inp.value = ''
  btn.disabled = true; btn.textContent = 'Thinking…'
  const status = logMsg('status', '⟳ Working…')

  let pollerActive = true
  async function poll() {
    while (pollerActive) {
      try {
        const r = await fetch('/progress/' + THREAD_ID)
        if (r.ok) {
          const p = await r.json()
          if (pollerActive && p && p.message) {
            const counter = (p.total > 0 && p.current > 0)
              ? ` (${p.current}/${p.total})` : ''
            status.textContent = '⟳ ' + p.message + counter
          }
        }
      } catch(e) {}
      await new Promise(res => setTimeout(res, 700))
    }
  }
  poll()

  try {
    const r = await fetch('/ask', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ question: q, thread_id: THREAD_ID })
    })
    const data = await r.json()
    pollerActive = false
    status.remove()
    if (!r.ok) throw new Error(data.error || r.statusText)
    logMsg('agent', data.answer || '(no response)')
    await refreshDeck()
  } catch(err) {
    pollerActive = false
    status.remove()
    logMsg('agent', 'Error: ' + err.message)
  } finally {
    btn.disabled = false; btn.textContent = 'Send'
  }
}

async function refreshDeck() {
  try {
    const r = await fetch('/deck/' + THREAD_ID)
    if (!r.ok) return
    const deck = await r.json()
    slideCount = deck.slide_count || 0
    document.getElementById('slideCount').textContent =
      slideCount === 1 ? '1 slide' : slideCount + ' slides'
    renderPreview()
    document.getElementById('dlBtn').disabled = slideCount === 0
  } catch(err) {
    console.warn('refreshDeck failed', err)
  }
}

function renderPreview() {
  const main = document.getElementById('previewMain')
  const thumbs = document.getElementById('thumbs')

  if (slideCount === 0) {
    main.innerHTML = `
      <div class="preview-empty">
        <strong>No deck yet</strong>
        Your slides will appear here once Palette builds them.
      </div>`
    thumbs.innerHTML = ''
    return
  }

  if (currentSlide > slideCount) currentSlide = slideCount
  if (currentSlide < 1) currentSlide = 1

  const ts = Date.now()
  main.innerHTML = `
    <img src="/preview/${THREAD_ID}/${currentSlide}?t=${ts}" alt="Slide ${currentSlide}">
    <div class="preview-counter">${currentSlide} / ${slideCount}</div>`

  const out = []
  for (let i = 1; i <= slideCount; i++) {
    const active = i === currentSlide ? ' active' : ''
    out.push(`
      <div class="thumb${active}" onclick="selectSlide(${i})">
        <img src="/preview/${THREAD_ID}/${i}?t=${ts}" alt="Slide ${i}">
        <div class="thumb-num">${i}</div>
      </div>`)
  }
  thumbs.innerHTML = out.join('')
}

function selectSlide(i) {
  currentSlide = i
  renderPreview()
}

function downloadDeck() {
  window.location = '/download/' + THREAD_ID
}

async function clearAll() {
  if (!confirm('Clear chat history and deck? This cannot be undone.')) return
  try { await fetch('/clear/' + THREAD_ID, { method: 'POST' }) } catch(e) {}
  window.location.reload()
}
</script>
</body>
</html>
"""
