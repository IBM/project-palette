# Palette HF Space — deployment guide

Reference for maintainers. The short version is in [README.md](README.md).

- **Space:** `ibm-research/palette-agent` (private, CPU basic) —
  https://huggingface.co/spaces/ibm-research/palette-agent
- **What it runs:** nginx, forwarding every request to
  `https://palette.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud`
- **Source of truth:** `project-palette/deployment/hf-space/` in GitHub. Edit
  there (via PR), then publish. Don't edit in the Hugging Face web editor.
- **Credentials:** a Hugging Face login only. No `.env`, no IBM Cloud or
  model keys — the Space never sees them.

All commands run from the **project-palette repo root**.

---

## 1. One-time setup (per machine)

```bash
uv pip install -U huggingface_hub   # into palette's .venv (created by `make install`)
.venv/bin/hf auth login             # token from huggingface.co/settings/tokens with "write" role;
                                    # your account needs write access to the ibm-research org
.venv/bin/hf auth whoami            # check
```

For a one-off without storing a login: `HF_TOKEN=hf_... make hf-dry-run`.

## 2. Publish

```bash
make hf-dry-run                 # always first: lists every change; changes nothing
make hf-publish ARGS=--yes      # apply
```

`make hf-publish` without `--yes` only works when nothing existing would be
replaced (e.g. creating a brand-new Space); otherwise it stops and lists what
`--yes` would replace.

Other targets: `HF_SPACE=<org>/<name> make hf-dry-run` for a different Space;
`make hf-publish ARGS=--public` to create a new Space as public.

### When to publish

| What changed | Action here |
|---|---|
| Palette app redeployed (`make ce-deploy`), same app | **Nothing** — the URL is unchanged |
| GPU fleet restarted, keys rotated, watsonx settings changed | **Nothing** — those live in the Code Engine app |
| Files in `deployment/hf-space/` | `make hf-dry-run`, then `make hf-publish ARGS=--yes` |
| Code Engine app URL changed | Point the Space at it — see §3 |

### Safety — what `publish.py` does and doesn't do

- **Never deletes** anything: no files, variables, secrets, or Spaces. (A file
  you remove locally stays in the Space until you remove it in the Space's
  Files tab — relevant only if you rename a file.)
- **Uploads only** the 7 files in its `SPACE_FILES` allowlist; nothing else in
  the folder (`.venv`, `.env`, caches) can be uploaded.
- **Replaces** existing files or settings only with `--yes`, after the dry run
  has listed each one. Old file versions stay in the Space's git history.
- **Changes settings only on request:** `--upstream <url>` sets `UPSTREAM_URL`,
  `--auth` sets the password secrets. Shell variables alone never change the Space.
- Never changes an existing Space's visibility, and never touches a model or
  dataset repo that shares the name.

---

## 3. Settings

### Forward to a different palette URL

```bash
make hf-dry-run ARGS="--upstream https://<new-app-url>"     # hf-dry-run passes ARGS through too
make hf-publish ARGS="--upstream https://<new-app-url> --yes"
```

Or in the browser: **Settings → Variables and secrets → New variable**
`UPSTREAM_URL` (scheme + host, no path). A variable overrides the `Dockerfile`
default; the Space restarts to pick it up.

### Password prompt (optional)

```bash
PROXY_USER=demo PROXY_PASSWORD='<strong-password>' make hf-publish ARGS="--auth --yes"
```

Or set the secrets `PROXY_USER` and `PROXY_PASSWORD` in Settings. To turn it
off, remove both secrets in Settings. Browsers don't show the prompt inside
the huggingface.co page frame — share the direct
`https://ibm-research-palette-agent.hf.space` link instead.

### Visibility, restart, sleep

- **Visibility:** Settings → Change Space visibility. Keep it private or
  org-only unless anonymous use of the watsonx budget and GPU fleet is fine.
- **Restart:** Settings → Restart Space (same image) or Factory rebuild (fresh build).
- **Sleep:** free CPU Spaces sleep after ~48h without visitors and wake on the
  next visit (~1 minute).

---

## 4. Verify

```bash
S=https://ibm-research-palette-agent.hf.space
TOKEN=$(cat ~/.cache/huggingface/token)             # private Space: requests need your HF token
curl -s -H "Authorization: Bearer $TOKEN" $S/__proxy_health               # "ok" — the proxy itself
curl -s -H "Authorization: Bearer $TOKEN" $S/health | jq '{rits_needed, backends}'   # palette via the proxy
```

Checked 2026-09-28 against the live Space: both return 200 with the token.

- **404 without the token** is expected — Hugging Face hides private Spaces
  from anonymous requests. It means the privacy setting works.
- **The first request after the Space has slept** can return a Hugging Face
  502 page while it starts (~1 minute). Retry.

Or open the Space page while logged in. The Space's **Logs** tab shows, at startup:

```
basic auth: off (...)
proxying :7860 -> https://palette.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud (resolver ...)
```

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Building" forever / "Build error" | Dockerfile problem | Logs → Build; reproduce with `make hf-local` |
| Direct URL gives **404** | Private Space, request without your HF token | Expected — add the token (§4) or open the Space page logged in |
| HF "500 / error on our side" page on the first request | Space waking from sleep | Wait ~1 minute and retry |
| `/__proxy_health` keeps failing | Container not running | Logs → Container; Restart Space |
| `/__proxy_health` ok, pages give **502** | Palette app down, or wrong `UPSTREAM_URL` | `curl <app-url>/health`; `ibmcloud ce app get -n palette` |
| Page loads, builds fail at "designing deck" | GPU fleet stopped | palette-model-fleet: `MODEL_PROFILE=palette ./5b_serve_fleet.sh`, then `./5c_expose_lb.sh refresh` |
| Page loads, drafts fail | watsonx credentials or quota | `ibmcloud ce app logs -n palette` (look for `[WATSONX]`) |
| Repeated password prompts | Wrong or mismatched secrets | Re-set both secrets, or remove them |
| Old behaviour after publishing | Build still running | Wait; else Factory rebuild |

## 6. Roll back

Every publish is a commit in the Space. To republish an older version of this
folder **without touching your working copy**, use a temporary git worktree:

```bash
git worktree add ../hf-space-rollback <good-commit>          # separate checkout; your files are untouched
.venv/bin/python ../hf-space-rollback/deployment/hf-space/publish.py ibm-research/palette-agent --dry-run
.venv/bin/python ../hf-space-rollback/deployment/hf-space/publish.py ibm-research/palette-agent --yes
git worktree remove ../hf-space-rollback                     # removes only that temporary checkout
```

Or browse older versions in the Space's **Files → History** tab.

## 7. Test locally

```bash
make hf-local                                    # builds and runs the proxy on http://localhost:7860
docker run --rm -p 7860:7860 -e UPSTREAM_URL=https://<other-url> palette-hf-space
docker run --rm -p 7860:7860 -e PROXY_USER=me -e PROXY_PASSWORD=secret palette-hf-space
```

The container runs as uid 1000 on port 7860, as on Hugging Face. (`--rm`
removes only that test container when it stops.)

## 8. Automating later (optional)

To publish on every merge: a GitHub Actions workflow on changes to
`deployment/hf-space/**` that installs `huggingface_hub` and runs
`python deployment/hf-space/publish.py ibm-research/palette-agent --yes`, with
an `HF_TOKEN` repository secret (write token for the Space). Publishing by hand
is simpler until the proxy changes often.
