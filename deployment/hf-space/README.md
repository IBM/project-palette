---
title: Palette
emoji: 🎨
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: Markdown plan to PowerPoint deck, fronting a Code Engine app
---

# Palette — Hugging Face Space

Palette turns a request or a markdown plan into a PowerPoint deck.

**This Space is only a front door.** It runs nginx and nothing else: every
request is forwarded to the Palette app on IBM Code Engine, which does all the
work. The Space has no model keys, no palette code, and no data of its own.

```
you → https://ibm-research-palette-agent.hf.space            (this Space: nginx)
    → https://palette.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud   (Palette app)
         ├─ designer + coder → palette model on a Code Engine GPU fleet
         └─ crafter + editor → gpt-oss-120b on watsonx.ai
```

If the Code Engine app or the GPU fleet is down, the Space shows errors — fix
them there, not here.

---

## Deploying (maintainers)

The source of truth is `deployment/hf-space/` in the
[IBM/project-palette](https://github.com/IBM/project-palette/tree/main/deployment/hf-space) repo; the Space is only a deploy target.
Change files there, never in the Hugging Face web editor (the next publish
would replace your edit).

**What you need:** a Hugging Face login with write access to the
`ibm-research` org. That's all — **no `.env` file, no IBM Cloud or model keys.**

```bash
cd project-palette                                 # repo root
uv pip install -U huggingface_hub                  # once, into palette's .venv
.venv/bin/hf auth login                            # once per machine

make hf-dry-run                                    # 1. see what would change (changes nothing)
make hf-publish ARGS=--yes                         # 2. publish (replaces our own files)
```

The Space rebuilds by itself in ~1-2 minutes. Check it by opening the Space
page while logged in (it's private), or with curl as shown in
[DEPLOY.md §4](DEPLOY.md#4-verify).

**When do you need to publish?** Only when the files in this folder change or
the Code Engine app gets a new URL. Redeploying the palette app, restarting the
GPU fleet, or rotating keys needs nothing here.

### What publishing can and can't do

| It can | It never does |
|---|---|
| Create the Space (private) if it doesn't exist | Delete anything — files, settings, or the Space |
| Replace the 7 files listed in `publish.py` — only with `--yes`, after the dry run shows them | Upload anything else from this folder (it's an allowlist: no `.venv`, no `.env`) |
| Set `UPSTREAM_URL` or the password — only with the explicit `--upstream` / `--auth` flags | Change settings from variables you happen to have exported |
| | Change an existing Space's visibility |

Every publish is one commit in the Space's history, so older versions are
always recoverable.

---

## Settings (optional)

Set in the Space under **Settings → Variables and secrets**, or with
`publish.py` flags (see [DEPLOY.md](DEPLOY.md)):

| Name | Kind | Default | What |
|---|---|---|---|
| `UPSTREAM_URL` | variable | the Code Engine URL above (from the `Dockerfile`) | Where requests are forwarded |
| `PROXY_USER`, `PROXY_PASSWORD` | secrets | not set | Adds a password prompt in front of Palette |

Keep the Space **private** (the default) so only signed-in org members can use
the watsonx budget and GPU fleet.

## Using it

- Drafts and builds run as background jobs the page polls, so long decks work
  through the proxy.
- One shared backend: everyone sees the same model menu, and sessions live in
  the Code Engine app's memory — download your `.pptx` before you leave.

## Files

| File | Purpose |
|---|---|
| `Dockerfile`, `entrypoint.sh`, `nginx.conf.template` | The proxy (nginx on port 7860, runs as uid 1000) |
| `publish.py` | Creates/updates the Space safely (see above) |
| `.gitignore` | Keeps local files (`.venv`, `.env`, caches) out of git |
| `README.md` | This page (also the Space's card on Hugging Face) |
| `DEPLOY.md` | Full guide: settings, verifying, troubleshooting, rollback, local testing |
