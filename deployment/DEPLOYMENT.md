# Deploying Palette

Two ways to ship Palette: a local Docker container, or IBM Code Engine.
Both use the same `Dockerfile` at the repo root.

---

## 1. Configuration

All scripts under `deployment/` read shared config from
[`deployment/config.sh`](./config.sh). Override the values via env vars
in your shell — nothing is hard-coded to a specific account.

Required:

| Variable | Example | What |
|---|---|---|
| `ICR_NAMESPACE` | `routing_namespace` | Your IBM Container Registry namespace |
| `CE_PROJECT` | `routing` | Your Code Engine project name |

Optional (sensible defaults shown):

| Variable | Default | What |
|---|---|---|
| `IMAGE_NAME` | `palette` | Image repository name |
| `IMAGE_TAG` | `latest` | Image tag |
| `ICR_REGION` | `icr.io` | ICR endpoint (us / de / jp / au / br) |
| `TARGET_PLATFORM` | `linux/amd64` | Code Engine runs amd64 |
| `CE_APP_NAME` | `palette` | Code Engine application name |
| `CE_SECRET_NAME` | `rits-api-key` | Secret holding `RITS_API_KEY` |
| `CE_CPU` | `2` | vCPU per instance |
| `CE_MEMORY` | `4G` | RAM per instance |
| `CE_MIN_SCALE` | `1` | Min instances (1 keeps it warm) |
| `CE_MAX_SCALE` | `1` | Max instances |
| `CE_PORT` | `8080` | Container port |

Quickest way to set them:

```bash
export ICR_NAMESPACE=routing_namespace
export CE_PROJECT=ce-project-routing
```

---

## 2. Local Docker

For a sanity check on your Mac before pushing anywhere:

```bash
make docker-build         # native arch (arm64 on Apple Silicon)
make docker-run           # serves on http://localhost:18814
```

`docker-run` forwards your shell's `RITS_API_KEY` into the container.

---

## 3. IBM Code Engine

### One-time setup

1. **Install CLIs:**
   ```bash
   brew install --cask ibm-cloud-cli
   ibmcloud plugin install code-engine container-registry
   ```

2. **Log in:**
   ```bash
   ibmcloud login --sso
   ibmcloud target -r us-east            # pick your region
   ibmcloud cr region-set us-east
   ibmcloud cr login                     # for Docker Desktop
   # ibmcloud cr login --client podman   # if your `docker` is podman
   ```

   > Podman uses `~/.config/containers/auth.json`, not Docker's config.
   > If `make ce-push` fails with `unauthorized: Authorization required`,
   > rerun with `--client podman`.

3. **Create the Code Engine project (once):**
   ```bash
   ibmcloud target -g routing
   # ibmcloud ce project create --name $CE_PROJECT
   ibmcloud ce project select --name $CE_PROJECT
   ```

4. **Create the RITS secret (once):**
   ```bash
   ibmcloud ce secret create --name rits-api-key \
     --from-literal RITS_API_KEY=<your key>
   ```

### Build + push + deploy

Your Mac is arm64; Code Engine is amd64. The scripts pass
`--platform linux/amd64` to your container CLI so the resulting image
actually runs on Code Engine. Works with either Docker Desktop or
Podman — set `CONTAINER_CMD=podman` if your `docker` command isn't a
symlink to it. (Podman on Mac uses its Fedora VM + qemu for the cross
build, no extra setup needed on recent versions.)

```bash
make ce-build         # cross-build linux/amd64 image locally
make ce-push          # push image to ICR
make ce-deploy        # create/update the Code Engine app
```

Or, one-shot:

```bash
make ce-release       # build + push + deploy in sequence
```

A faster build-and-push that skips loading the image into your local
Docker daemon:

```bash
make ce-buildpush
```

### Updating an existing app

`make ce-deploy` is idempotent — it detects an existing app and runs
`application update` instead of `application create`. After pushing a
new image tag, re-run `ce-deploy` to roll it out.

---

## 4. Operational notes

- **Workspace is ephemeral.** `workspace/<thread_id>/` lives on the
  pod's local disk. Sessions don't survive pod restarts or scaling
  events. Users should download `.pptx` artifacts before walking away.
  If you need durable storage, mount Cloud Object Storage as a
  workspace backing store — not wired in yet.
- **Sizing.** 2 vCPU / 4 GB RAM is comfortable. The container peaks
  during LibreOffice PDF conversion and multi-slide parallel coding.
  Keep `min-scale=1` to avoid cold-start latency on the first request
  (the image is ~1.3 GB).
- **`RITS_API_KEY`** is the only required runtime secret. The deploy
  script refuses to run if `$CE_SECRET_NAME` doesn't exist in the
  project.
- **Port.** Code Engine injects `PORT` into the container; `app.py`
  reads it. Default `8080`.

---

## 5. File map

```
deployment/
  config.sh          shared env/defaults — sourced by every script
  build.sh           buildx → linux/amd64, loaded into local Docker
  push.sh            docker push to ICR
  buildpush-ce.sh    buildx → push to ICR in one step (skips local load)
  deploy.sh          create-or-update the Code Engine app
  DEPLOYMENT.md      this file
```
