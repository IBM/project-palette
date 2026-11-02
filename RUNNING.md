# Running Palette — cheat sheet (macOS & Linux)

[← Palette README](README.md) · [developer cheatsheet](CHEATSHEET.md)

Start Palette in a container, find the UI and the usage dashboard, and know
where the database and the decks are. Every command runs from the Palette
checkout. They are the same on macOS and Linux unless a section says otherwise.

```
http://localhost:18814                    Palette
http://localhost:18814/stats?token=…      usage dashboard   (make stats-url prints it)
~/.local/share/palette/usage.db           the database
~/.local/share/palette/decks/             every deck built
```

---

## 1. One-time setup

### macOS

```bash
brew install podman              # or install Docker Desktop instead
podman machine init              # Podman only, once
podman machine start
```

### Linux

```bash
# Docker (Debian/Ubuntu shown) — or Podman, which works the same
sudo apt install docker.io make curl git sqlite3
sudo usermod -aG docker $USER    # then log out and back in: no sudo needed afterwards
```

Don't run the commands below with `sudo`: the data folder would end up owned
by root. On Fedora/RHEL (SELinux), see [Linux notes](#linux-notes).

### Both

```bash
git clone <this-repo-url> project-palette && cd project-palette
cp .env.example .env
```

Edit `.env`. For the RITS-only setup, delete the `PALETTE_CE_*`,
`GPT_OSS_120B_PROVIDER` and `WATSONX_*` lines and set:

```bash
RITS_API_KEY=<your RITS key>
PALETTE_STATS_TOKEN=<paste the output of: python3 -c "import secrets;print(secrets.token_urlsafe(24))">
```

Connect to the **IBM VPN** (RITS needs it), then build the image (~5 min the
first time):

```bash
make docker-build
```

---

## 2. Every day

```bash
make docker-up        # start in the background; prints the URLs and data paths
make docker-status    # up? + Palette URL, dashboard URL, database, decks
make docker-logs      # follow the log (Ctrl-C stops following, not Palette)
make docker-down      # stop (your data stays in ~/.local/share/palette)
make stats-url        # just the dashboard URL
```

`make docker-run` is the foreground alternative: the log in your terminal, and
Ctrl-C stops it.

**After changing code:** `make docker-build && make docker-up`.
**After changing `.env`:** `make docker-up` (it recreates the container;
`docker restart` would not re-read `.env`).

### After a reboot

| | |
|---|---|
| **macOS (Podman)** | `podman machine start`, then `make docker-up` |
| **macOS (Docker Desktop)** | start Docker Desktop; the container comes back by itself |
| **Linux (Docker)** | nothing: the container comes back by itself |
| **Linux (rootless Podman)** | `make docker-up`, or once: `systemctl --user enable podman-restart.service` |

---

## 3. Where everything is

| What | Where | Open it |
|---|---|---|
| Palette UI | http://localhost:18814 | browser |
| Usage dashboard | `make stats-url` | browser; keep the token private |
| Health + data paths | http://localhost:18814/health | `curl -s localhost:18814/health` |
| **Database** | `~/.local/share/palette/usage.db` | `sqlite3 ~/.local/share/palette/usage.db` |
| **Decks** | `~/.local/share/palette/decks/<YYYY-MM-DD>/<HHMMSS>Z-<id>-<title>/` | see below |
| Settings | `.env` in the checkout | editor |
| Log | inside the container | `make docker-logs` |
| Live sessions | inside the container, `/app/workspace/` | temporary: **New** or a restart removes them; finished decks are already in `decks/` |

Each deck folder holds `deck.pptx`, `plan.md` and `slide-1.png`, `slide-2.png`,
…, and always the latest version (slide edits and retries update it). Times in
the folder name are UTC (`Z`). Decks from the UI's example prompts have
`example-` in the name.

```bash
# macOS
open ~/.local/share/palette/decks                     # Finder
open ~/.local/share/palette/decks/*/*/deck.pptx       # every deck in Keynote/PowerPoint

# Linux
xdg-open ~/.local/share/palette/decks                 # file manager
ls -lt ~/.local/share/palette/decks/*/                # newest first
```

In the dashboard, open a row under **Utterances → plan → deck** to see the
deck's **Folder** path, its slides, and download links.

### Quick queries

```bash
DB=~/.local/share/palette/usage.db

# last 10 utterances → plan → deck
sqlite3 -header -column $DB "
  SELECT datetime(u.ts,'unixepoch','localtime') AS at, u.kind, substr(u.text,1,60) AS utterance,
         p.seconds AS plan_s, b.slides, b.seconds AS build_s, b.ok, substr(b.error,1,40) AS error
  FROM utterances u LEFT JOIN plans p ON p.utterance_id = u.id
  LEFT JOIN builds b ON b.plan_id = p.id ORDER BY u.ts DESC LIMIT 10;"

# builds per day
sqlite3 -header -column $DB "SELECT day, count(*) builds, sum(ok) ok FROM builds GROUP BY day;"

# all utterances as CSV (or: Utterances CSV on the dashboard)
sqlite3 -header -csv $DB "SELECT datetime(ts,'unixepoch') at, kind, text FROM utterances;" > utterances.csv
```

**Back up:** `make docker-down`, copy `~/.local/share/palette`, `make docker-up`.

---

## Linux notes

- **Build on the Linux machine.** An image built on an Apple-silicon Mac will
  not run on an x86 server.
- **SELinux** (Fedora, RHEL): `make docker-up` mounts without `:Z`. Run once
  `chcon -Rt container_file_t ~/.local/share/palette`, or start it by hand with
  `-v ~/.local/share/palette:/app/data:Z` (see the README).
- **Other people on a shared server** open `http://<server-name>:18814`; open
  port 18814 in the firewall if needed.
- **Files stay yours.** The container runs as the owner of
  `~/.local/share/palette`, so the database and decks are never root-owned
  (as long as you didn't create that folder with `sudo`).

---

## When something is off

| Symptom | Fix |
|---|---|
| `make docker-status` says not answering | `make docker-logs`; then `make docker-up` |
| Builds fail, `/health` shows `"rits_key_set": false` | `RITS_API_KEY` missing in `.env` → fix it, `make docker-up` |
| Builds hang or time out | connect the IBM VPN |
| Dashboard says Not Found | wrong or missing token → `make stats-url` |
| `port is already allocated` | Palette is already running (`make docker-status`), or another app uses 18814 → `make docker-up PORT=18815` |
| `permission denied` on `/var/run/docker.sock` (Linux) | not in the `docker` group yet: log out and back in |
| `Cannot connect to Podman` (macOS) | `podman machine start` |
| Files in `~/.local/share/palette` owned by root (Linux) | `sudo chown -R $USER: ~/.local/share/palette`, then never use `sudo` with make |
