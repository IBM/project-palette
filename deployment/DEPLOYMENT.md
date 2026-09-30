# Deploying Palette

Two ways to ship Palette: a local container (the `make serve-*` service), or an
IBM Code Engine app. Both use the same `Dockerfile` at the repo root and the
same `.env`.

Current deployment (2026-09-28): Code Engine app `palette` in
`ce-project-routing`, image `icr.io/routing_namespace/palette-agent`, **no
RITS** — designer + coder on the palette model fleet, crafter + editor
(gpt-oss-120b) on watsonx.ai.

---

## 1. Configuration — `.env`

Every script under `deployment/` sources [`config.sh`](./config.sh), which
loads the repo's `.env` (template: [`.env.example`](../.env.example)).
Variables already exported in your shell win over the file, e.g.
`IMAGE_TAG=v2 make ce-deploy`. Keep values on their own lines — no inline
`# comments` after a value.

**Model backends** (runtime; stored as Code Engine secrets by `deploy.sh`):

| Variable | What | CE secret |
|---|---|---|
| `PALETTE_CE_BASE_URL`, `PALETTE_CE_API_KEY` | designer + coder → the palette model fleet (`http://<lb>/v1` + bearer key). Filled in by `palette-model-fleet/sync_palette_env.sh` | `palette-ce-fleet` |
| `GPT_OSS_120B_PROVIDER=watsonx`, `WATSONX_APIKEY`, `WATSONX_PROJECT_ID` (or `WATSONX_SPACE_ID`), `WATSONX_URL` | crafter + editor → watsonx.ai (only us-south hosts gpt-oss-120b) | `palette-watsonx` |
| `CE_SECRET_NAME` | name of an existing secret holding `RITS_API_KEY`; **empty = no RITS** (then the two rows above are required) | (yours) |

**Deployment:**

| Variable | This deployment | What |
|---|---|---|
| `ICR_REGION` / `ICR_NAMESPACE` | `icr.io` / `routing_namespace` | Registry |
| `IMAGE_NAME` / `IMAGE_TAG` | `palette-agent` / `2026-09-28` | Image repository and tag |
| `CE_REGION` / `CE_RESOURCE_GROUP` | `us-east` / `routing` | Targeted before every CE call (avoids "No resource group targeted") |
| `CE_PROJECT` / `CE_APP_NAME` | `ce-project-routing` / `palette` | Project and app |
| `CE_REGISTRY_SECRET` | `icr-secret-1` | Pull secret for the image |
| `CE_CPU` / `CE_MEMORY` / `CE_MIN_SCALE` / `CE_MAX_SCALE` / `CE_PORT` | `4` / `8G` / `1` / `1` / `8080` | Sizing — keep max-scale at 1 (see Operational notes) |

---

## 2. Local service (container)

```bash
make docker-build         # native arch image `palette:latest` (arm64 on Apple Silicon)
make serve-start          # runs it with the .env settings -> http://localhost:18814
make serve-status         # mode, designer backend, whether RITS is needed
make serve-logs           # every model call is tagged [CE-FLEET] / [WATSONX] / [RITS]
make serve-stop
```

The container restarts on crash. After a reboot: `podman machine start`
(if you use podman), then `make serve-start`. Rebuild with `make docker-build`
after code changes.

---

## 3. IBM Code Engine

### One-time setup

```bash
brew install --cask ibm-cloud-cli
ibmcloud plugin install code-engine container-registry
ibmcloud login --sso
```

The project, registry namespace and registry secret must already exist (this
account: `ce-project-routing`, `routing_namespace`, `icr-secret-1`). A RITS
secret is only needed if you deploy with RITS (`CE_SECRET_NAME=rits-api-key`):
`ibmcloud ce secret create --name rits-api-key --from-literal RITS_API_KEY=<key>`.

### Build + deploy

```bash
make ce-cloud-build       # build linux/amd64 on Code Engine, push IMAGE_REF (~5 min)
make ce-deploy            # create/update the app; creates/updates the backend secrets
curl -s https://<app-url>/health | jq '{rits_needed, backends}'
```

`ce-cloud-build` needs no local Docker and no cross-compilation (your Mac is
arm64, Code Engine is amd64). The source upload honours
[`.ceignore`](../.ceignore), which keeps `.env` out of the uploaded bundle.
Local alternatives: `make ce-build ce-push` (buildx/podman `--platform
linux/amd64`, slow under emulation) or `make ce-buildpush`.

What `ce-deploy` does with the backends:

| `.env` has | Effect on the app |
|---|---|
| `PALETTE_CE_BASE_URL` + key | secret `palette-ce-fleet` created/updated and attached |
| `GPT_OSS_120B_PROVIDER=watsonx` + `WATSONX_*` | secret `palette-watsonx` created/updated and attached |
| `CE_SECRET_NAME=` (empty) | no RITS secret; `rits-api-key` detached if an earlier deploy attached it. Refuses to deploy unless both rows above are set |
| `PALETTE_BACKEND=rits` / `GPT_OSS_120B_PROVIDER=rits` | detach the fleet / watsonx secret (back to RITS for those roles) |

The deploy is idempotent — it runs `application update` when the app exists.
Roll out a new image: bump `IMAGE_TAG`, `make ce-cloud-build ce-deploy`.

### Quota

`ce-project-routing` is capped at **40 applications**; creating a 41st fails
with "exceed your quota" (scale-to-zero does not help — it is an app count).
Reuse an existing app name (`CE_APP_NAME`) or delete one first.

### Checking the fleet from palette's own code

Runs palette's real designer/coder code against `PALETTE_CE_BASE_URL` — no
Node, LibreOffice or RITS needed:

```bash
uv pip install -r requirements.txt
set -a; . ./.env; set +a
uv run --no-sync python scripts/ce_stage2_check.py [plan.md] [--slides N]
```

---

### Hugging Face Space (proxy in front of the app)

`deployment/hf-space/` is a Hugging Face Docker Space — nginx forwarding every
request to this Code Engine app — published as `ibm-research/palette-agent`.
It holds no keys, so app redeploys, fleet restarts and key rotations need no
Space change; only a new app URL or proxy-code change does.

```bash
uv pip install -U huggingface_hub && .venv/bin/hf auth login   # once
make hf-dry-run                  # read-only report
make hf-publish                  # create (private) / update; ARGS=--yes to overwrite our files
make hf-local                    # run the proxy on http://localhost:7860
```

Full guide: [`hf-space/DEPLOY.md`](./hf-space/DEPLOY.md).

---

## 4. Operational notes

- **Backends must be up.** If the palette model fleet is stopped, Stage 2
  fails with connection errors; watsonx calls bill to the configured watsonx
  project.
- **Workspace is ephemeral.** `workspace/<thread_id>/` lives on the pod's
  local disk. Sessions don't survive pod restarts or scaling events.
  Download `.pptx` artifacts before walking away. Durable storage (COS) is
  not wired in yet.
- **Sizing.** 4 vCPU / 8 GB (raised from 2 / 4 on 2026-09-28 for several
  concurrent builds: LibreOffice rendering and PDF previews are CPU-bound).
  `min-scale=1` avoids cold starts (the image is ~450 MB compressed). The GPU
  fleet, not this app, limits concurrency — ~5 decks at once on the H100 (fp8); see
  palette-model-fleet `ARCHITECTURE.md` §3.
- **One instance only.** Sessions live in memory and the UI polls for results;
  Code Engine has no session affinity, so `CE_MAX_SCALE` > 1 breaks polling
  until session state is stored outside the instance.
- **Single user.** The UI's model menu mutates a process-wide roster; builds
  are serialised per session but model choices are shared. Fine for a team
  demo, not for many concurrent users.
- **Port.** Code Engine injects `PORT`; `app.py` reads it. Default `8080`.

---

## 5. File map

```
.env.example         every setting, commented (copy to .env)
.ceignore            keeps .env and local state out of cloud-build uploads
deployment/
  config.sh          loads .env, applies defaults — sourced by every script
  cloud-build.sh     Code Engine buildrun → ICR (linux/amd64, no local Docker)
  build.sh           buildx → linux/amd64, loaded into local Docker
  push.sh            docker push to ICR
  buildpush-ce.sh    build.sh + push.sh
  deploy.sh          create-or-update the Code Engine app + backend secrets
  DEPLOYMENT.md      this file
  hf-space/          Hugging Face Space: nginx proxy to the CE app (DEPLOY.md, publish.py)
scripts/
  ce_stage2_check.py designer/coder check against the configured backend
```
