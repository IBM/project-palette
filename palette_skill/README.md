# palette-skill

The Palette deck builder, packaged as an agent skill.

`palette_skill` is a small HTTP client for the Palette service. Its only
dependency is `httpx` — the pipeline it drives (pptxgenjs, LibreOffice,
Poppler, IBM Plex, the RITS model roster) stays on the server.

```python
from palette_skill import PaletteClient

pal = PaletteClient("https://palette.example.cloud")
plan = pal.draft("Deck explaining vector databases to backend engineers")

tid = pal.start_build(plan)
while not (p := pal.wait(tid, max_seconds=25)).terminal:
    print(p)

build = pal.result(tid)
pal.download(tid, "./deck.pptx")
```

## Why it is shaped this way

Agent hosts cap how long a single step may run — CUGA's default is 30 seconds,
and a deck takes two to four minutes. So the client separates *starting* a
build from *waiting* on it, and `wait()` takes a `max_seconds` bound: call it
repeatedly, report progress each time, and a multi-minute build fits inside a
sequence of short steps.

## Layout

| Path | Role |
|---|---|
| `contract.py` | The HTTP contract, written once. Client, docs, and tests all read it. |
| `client.py` | `PaletteClient` — the API surface. |
| `cli.py` | `palette-skill` console script, for hosts whose only primitive is a shell. |
| `service.py` | Host-side supervisor: start/stop/health/doctor for a local service. |
| `server_entry.py` | `palette-serve` — runs the server in the foreground; what launchd supervises. |
| `payload/` | `SKILL.md` + `reference.md`, the files installed into an agent. |
| `build_skill.py` | Regenerates the machine-written regions of those files. |
| `hosts.py` | Agent host profiles — the only host-specific part of the skill. |
| `install.py` | Installs into an agent's skills root; `--check` reports drift. |
| `release.py` | Builds the shippable wheel and per-host tarballs. |

## The supervisor is host-side, on purpose

`service.py` is for humans and shell scripts, not for the agent. An agent
sandbox is the wrong place to start Palette three times over: Seatbelt-style
policies confine writes to the sandbox workspace, a supervised child holding the
shell's stdout pipe blocks until the step limit kills it, and the native
toolchain lives outside the sandbox regardless.

So the skill *detects* the service and tells the user which command to run. It
never starts one.

## Keeping the skill honest

The skill is generated from the server's own code and verified against it:

```bash
make skill-build                      # regenerate payload/*.md
make skill-check                      # fail if it is stale
make skill-test                       # assert the contract matches app.py
make skill-install CUGA=~/code/cuga   # install into an agent
make release                          # shippable wheel + per-host tarballs
```

`make release` is what you hand to someone who has never cloned Palette: a
72 KB tarball per host that untars straight into a skills root, wheel included.
See [GUIDE.md](GUIDE.md) §5c.

`tests/test_skill_contract.py` walks the live FastAPI routes. Change `app.py`
without updating `contract.py` and the tests fail — before an agent ever sees
the wrong instructions. `make hooks` installs a pre-commit guard so that check
is not something you have to remember.

Start with **[GUIDE.md](GUIDE.md)** — what the skill is, what each `make` target
produces, and a clean start-to-deck walkthrough. [TESTING.md](TESTING.md) is the
eight-tier verification ladder.

See [`payload/SKILL.md`](payload/SKILL.md) for what the agent is actually told.
