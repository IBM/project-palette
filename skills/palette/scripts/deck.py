#!/usr/bin/env python3
"""Run Palette's slow commands without blocking, for hosts that cap a step.

Two of them are slow. `build-plan` is one model call: 43-82s locally, ~170s
inside CUGA's sandbox. `build-deck` renders every slide and repairs geometry:
three to ten minutes. Some hosts allow that in one call — Claude Code's Bash
tool permits ten minutes. CUGA does not: it kills a sandbox step at 120s.

A killed step is the worst possible outcome, because it is silent. The work
carries on in its own process and finishes; the caller just never finds out.
Both real failures in the wild were exactly this — an agent reported that
Palette had timed out and gave up, while a good plan and a good 15-slide deck
sat finished in the workspace.

So nothing here can be killed part-way. Both slow commands detach; how they
are collected differs, because the two are not the same shape of wait.

    python scripts/deck.py plan   --request "..." --out plan.md
    python scripts/deck.py start  --plan plan.md --out-dir ./deck
    python scripts/deck.py status --out-dir ./deck             # until done

A plan is ONE model call, so `plan` holds the call open for up to 90 seconds
and usually returns the finished plan itself. Polling it would cost an agent
turn per poll -- eight round trips for a 160s plan -- and those turns come out
of the same step budget the build needs later. Only a slow plan falls back to
`plan-status`.

A build is minutes of rendering, which no step limit will ever cover, so
`start` returns at once and `status` is polled throughout. Polling is right
there: the alternative is not a shorter wait, it is no wait at all.

Each prints one JSON object. Completion is computed from the filesystem, never
from an exit code: a build that exits 0 having written nothing is a failure,
and an agent relaying "done" from a return value reports a deck that does not
exist. Conversely a build is only over once it says so in `.palette-exit` —
liveness cannot be probed by signalling, because a sandbox will not permit it.

Two modes, chosen by one variable:

    local  (default)  $PALETTE_HOME is a Palette checkout on this machine;
                      palette.py runs from it, with that checkout's .env.
    remote            $PALETTE_URL is a running Palette server (the web app);
                      the same commands go over HTTP to its async endpoints,
                      and the finished .pptx + previews are downloaded into
                      --out-dir. Nothing else is needed on this machine.

$PALETTE_URL wins when both are set. Commands, flags and JSON output are the
same in both modes, and both end in the same core functions.

Stdlib only, so it runs wherever the agent does.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

#: Written into --out-dir so `status` can find the build across separate calls.
#: Each poll is its own process; nothing is held in memory between them.
STATE = ".palette-build.json"

#: Holds the build's exit code, written by the shell when the build ends. This
#: is how `status` knows a build is over, because it is the only signal that
#: survives a sandbox: reading a workspace file is always allowed, while asking
#: the kernel about another process is not.
EXIT = ".palette-exit"

#: Below this a .pptx is a stub, not a deck. A failed render can still leave a
#: small well-formed file behind, and reporting that as success is the exact
#: failure this script exists to prevent.
MIN_PPTX_BYTES = 20_000


def palette_home() -> Path:
    """The Palette checkout. $PALETTE_HOME, else walk up from this script."""
    env = os.environ.get("PALETTE_HOME", "").strip()
    if env:
        home = Path(env).expanduser()
        if not (home / "palette.py").is_file():
            raise SystemExit(f"error: $PALETTE_HOME={home} has no palette.py")
        return home
    # skills/palette/scripts/deck.py -> repo root is three levels up. Holds for
    # the in-repo copy; an installed skill elsewhere must set $PALETTE_HOME.
    here = Path(__file__).resolve()
    for candidate in list(here.parents)[:5]:
        if (candidate / "palette.py").is_file():
            return candidate
    raise SystemExit(
        "error: cannot find the Palette checkout. Set one of:\n"
        "  export PALETTE_URL=https://<palette-server>      # remote: a running Palette server\n"
        "  export PALETTE_HOME=~/code/project-palette       # local: a Palette checkout"
    )


def interpreter(home: Path) -> str:
    """Palette's own venv if it has one — it holds the pipeline's dependencies.

    Falling back to whatever `python3` is on PATH is right for a system-wide
    install and wrong for a checkout with a venv, so prefer the venv.
    """
    venv = home / ".venv" / "bin" / "python"
    return str(venv) if venv.is_file() else sys.executable


def verify(out_dir: Path) -> dict:
    """Stat what is on disk. The only thing allowed to say a deck exists."""
    pptx = out_dir / "deck.pptx"
    size = pptx.stat().st_size if pptx.is_file() else 0
    previews = sorted(p.name for p in out_dir.glob("*.png"))
    return {
        "pptx": str(pptx.resolve()) if pptx.is_file() else None,
        "pptx_bytes": size,
        "slide_previews": len(previews),
        "verified": bool(pptx.is_file() and size >= MIN_PPTX_BYTES),
    }


def _finished_at(exit_file: Path) -> int | None:
    """A step's exit code, or None if it has not ended yet.

    The primary signal, because it is the only one a sandbox cannot take away.
    Every slow command is wrapped in `; echo $? > <file>`, so the file appears
    exactly once, when that command is over. Reading a file is allowed
    everywhere this runs; asking the kernel about another process is not.
    """
    try:
        return int(exit_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _finished(out_dir: Path) -> int | None:
    """The deck build's exit code."""
    return _finished_at(out_dir / EXIT)


def _alive(pid: int) -> bool | None:
    """True, False, or None when the host refuses to say.

    None is the important one. CUGA runs each step under Seatbelt with
    `(allow signal (target self))`, so `os.kill(pid, 0)` against the detached
    build raises PermissionError and `ps` cannot even exec. Neither is evidence
    that the build died -- but treating them as such reported `state: error` on
    the first poll of every sandboxed build, minutes before the .pptx existed,
    and the agent then invented reasons for a failure that had not happened.

    So: only a real ProcessLookupError means dead. Anything we cannot determine
    is None, and the caller waits for `.palette-exit` instead.
    """
    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None
    # Alive -- but pids get recycled, so confirm it is still our build.
    try:
        listing = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listing.returncode != 0:
        return None
    return "palette.py" in listing.stdout


def palette_env(home: Path) -> dict[str, str]:
    """The environment palette.py runs with: ours, plus the checkout's .env.

    Model settings (which backend serves each role, and its keys) live in
    $PALETTE_HOME/.env, the same file the web app and the local service read.
    Loading it here means installing the skill needs only $PALETTE_HOME, and
    the skill always uses the backends that checkout is configured for. A
    variable already in the agent's environment wins over the file.
    """
    env = dict(os.environ)
    dotenv = home / ".env"
    try:
        lines = dotenv.read_text(encoding="utf-8").splitlines()
    except OSError:
        return env
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value.startswith("~/"):
            value = str(Path.home() / value[2:])
        env.setdefault(key, value)
    return env


# -- remote mode -------------------------------------------------------------


def remote_url() -> str | None:
    """$PALETTE_URL (a Palette server), or None for local mode."""
    url = os.environ.get("PALETTE_URL", "").strip().rstrip("/")
    return url or None


class RemoteError(Exception):
    """The Palette server could not be reached, or answered with an error."""


def _http(base: str, method: str, path: str, *, body: dict | None = None,
          form: dict | None = None, files: list[tuple[str, str, bytes]] | None = None,
          timeout: float = 60) -> tuple[int, bytes]:
    """One HTTP call to the Palette server. JSON body, or multipart form+files."""
    headers: dict[str, str] = {}
    data = None
    if form is not None or files:
        boundary = uuid.uuid4().hex
        chunks: list[bytes] = []
        for key, value in (form or {}).items():
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"'
                          f'\r\n\r\n'.encode() + str(value).encode("utf-8") + b"\r\n")
        for field, filename, content in files or []:
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
                          f'filename="{filename}"\r\nContent-Type: application/octet-stream'
                          f'\r\n\r\n'.encode() + content + b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode())
        data = b"".join(chunks)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise RemoteError(f"cannot reach the Palette server at {base}: {exc}") from None


def _http_json(base: str, method: str, path: str, **kwargs) -> dict:
    """_http, decoded. Adds `_status`; an HTTP error always carries `error`."""
    code, raw = _http(base, method, path, **kwargs)
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        data = {"error": raw[:300].decode("utf-8", errors="replace")}
    if not isinstance(data, dict):
        data = {"value": data}
    if code >= 400:
        data.setdefault("error", f"HTTP {code}")
    data["_status"] = code
    return data


def _remote_error(message: str, **extra) -> int:
    print(json.dumps({"state": "error", "done": False, "error": message, **extra}, indent=2))
    return 1


def _remote_thread() -> str:
    """A fresh server session per plan/edit/build: nothing is shared by accident."""
    return f"skill-{uuid.uuid4().hex[:12]}"


def _remote_start_plan(out: Path, label: str, submit, hold: int) -> int:
    """Remote twin of _start_plan: submit, then hold for the plan like local does."""
    previous = _load(_sidecar(out, "plan.json"))
    if previous.get("remote") and _finished_at(_sidecar(out, "plan.exit")) is None:
        return _remote_plan_status(out, hold)   # already drafting: collect, don't restart
    base = remote_url()
    thread = _remote_thread()
    try:
        accepted = submit(base, thread)
    except RemoteError as exc:
        return _remote_error(str(exc))
    if accepted["_status"] >= 400:
        return _remote_error(f"{label} was refused by the Palette server: {accepted['error']}")
    _sidecar(out, "plan.exit").unlink(missing_ok=True)
    state = {"state": "running", "stage": label, "remote": base, "thread_id": thread,
             "plan": str(out), "started_at": int(time.time())}
    _sidecar(out, "plan.json").write_text(json.dumps(state, indent=2))
    return _remote_plan_status(out, hold)


def _remote_plan_status(out: Path, hold: int) -> int:
    """Poll /draft_result (plans and edits both land there) for up to *hold* s."""
    state = _load(_sidecar(out, "plan.json"))
    base, thread = state["remote"], state["thread_id"]
    label = state.get("stage", "build-plan")
    deadline = time.time() + max(0, hold)
    while True:
        try:
            answer = _http_json(base, "GET", f"/draft_result/{thread}", timeout=30)
        except RemoteError as exc:
            return _remote_error(str(exc), hint="the plan may still be drafting on the server; poll again")
        if answer["_status"] == 404:
            _sidecar(out, "plan.exit").write_text("1")
            return _remote_error("the Palette server no longer knows this plan (it may have "
                                 "restarted); run the command again")
        if answer.get("stage") in ("done", "error") or time.time() >= deadline:
            break
        time.sleep(2)

    elapsed = int(time.time()) - int(state.get("started_at", time.time()))
    if answer.get("stage") == "done":
        out.write_text(answer.get("plan") or "", encoding="utf-8")
        _sidecar(out, "plan.exit").write_text("0")
        result = {"state": "done", "done": True, "plan": str(out), "elapsed_seconds": elapsed,
                  "text": answer.get("plan") or ""}
    elif answer.get("stage") == "error":
        _sidecar(out, "plan.exit").write_text("1")
        result = {"state": "error", "done": False, "elapsed_seconds": elapsed,
                  "log_tail": answer.get("error") or "",
                  "hint": "the Palette server could not write the plan; the tail above says why"}
    else:
        result = {"state": "running", "done": False, "elapsed_seconds": elapsed,
                  "note": (f"{label} is still running — do NOT run {label.split('-')[0]} again, "
                           f"it would start a second one. Collect this one with plan-status."),
                  "next": f"python {Path(__file__).name} plan-status --out {out}"}
    _sidecar(out, "plan.json").write_text(json.dumps({**state, "state": result["state"]}, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if result["state"] != "error" else 1


def _remote_start(plan: Path, out_dir: Path, palette_family: str | None) -> int:
    """Remote twin of start: POST /build_async, record the session, return at once."""
    state_path = out_dir / STATE
    previous = _load(state_path)
    if previous.get("remote") and _finished(out_dir) is None:
        print(json.dumps({**previous, "note": "already building; poll with status"}))
        return 0
    base = remote_url()
    thread = _remote_thread()
    body = {"plan": plan.read_text(encoding="utf-8"), "thread_id": thread}
    if palette_family:
        body["palette_family"] = palette_family
    try:
        accepted = _http_json(base, "POST", "/build_async", body=body)
    except RemoteError as exc:
        return _remote_error(str(exc))
    if accepted["_status"] >= 400:
        return _remote_error(f"the Palette server refused the build: {accepted['error']}")
    (out_dir / EXIT).unlink(missing_ok=True)
    state = {"state": "running", "remote": base, "thread_id": thread, "plan": str(plan),
             "out_dir": str(out_dir), "log": str(out_dir / "build.log"),
             "started_at": int(time.time())}
    state_path.write_text(json.dumps(state, indent=2))
    print(json.dumps({
        **state,
        "note": "building on the Palette server — takes 3-10 minutes; poll with `deck.py status`",
        "next": f"python {Path(__file__).name} status --out-dir {out_dir}",
    }))
    return 0


def _remote_collect(base: str, thread: str, out_dir: Path, result: dict) -> None:
    """Download the finished deck and its previews, so `verify` sees them on disk."""
    code, pptx = _http(base, "GET", f"/download/{thread}", timeout=120)
    if code != 200:
        raise RemoteError(f"downloading the deck failed (HTTP {code})")
    (out_dir / "deck.pptx").write_bytes(pptx)
    for index in range(1, int(result.get("slide_count") or 0) + 1):
        code, png = _http(base, "GET", f"/preview/{thread}/{index}", timeout=60)
        if code == 200:
            (out_dir / f"slide-{index}.png").write_bytes(png)


def _remote_status(out_dir: Path, state: dict, hold: int) -> int:
    """Remote twin of status: poll /result, then download and verify like local."""
    base, thread = state["remote"], state["thread_id"]
    log = Path(state["log"])
    deadline = time.time() + max(0, hold)
    progress = ""
    while True:
        try:
            answer = _http_json(base, "GET", f"/result/{thread}", timeout=30)
            if answer.get("stage") not in ("done", "error"):
                progress = (_http_json(base, "GET", f"/progress/{thread}", timeout=30)
                            .get("message") or progress)
        except RemoteError as exc:
            return _remote_error(str(exc), hint="the build may still be running on the server; poll again")
        if answer["_status"] == 404:
            (out_dir / EXIT).write_text("1")
            log.write_text("the Palette server no longer knows this build (it may have restarted)\n")
            answer = {"stage": "error", "error": "the Palette server no longer knows this build "
                                                 "(it may have restarted); start it again"}
            break
        if answer.get("stage") in ("done", "error") or time.time() >= deadline:
            break
        time.sleep(3)

    elapsed = int(time.time()) - int(state.get("started_at", time.time()))
    if answer.get("stage") == "done" and _finished(out_dir) is None:
        try:
            _remote_collect(base, thread, out_dir, answer.get("result") or {})
        except RemoteError as exc:
            return _remote_error(str(exc), hint="the deck is built on the server; poll again to retry the download")
        log.write_text(json.dumps(answer.get("result") or {}, indent=2) + "\n")
        (out_dir / EXIT).write_text("0")
    elif answer.get("stage") == "error" and _finished(out_dir) is None:
        log.write_text((answer.get("error") or "build failed") + "\n")
        (out_dir / EXIT).write_text("1")

    checked = verify(out_dir)
    if _finished(out_dir) is None:
        result = {"state": "running", "done": False, "elapsed_seconds": elapsed,
                  "note": f"still rendering after {elapsed}s of a typical 180-600s build",
                  "next": f"python {Path(__file__).name} status --out-dir {out_dir}"}
        if progress:
            result["progress"] = progress
    elif checked["verified"]:
        result = {"state": "done", "done": True, **checked, "elapsed_seconds": elapsed}
    else:
        result = {"state": "error", "done": False, "elapsed_seconds": elapsed, **checked,
                  "log_tail": _tail(log, 15),
                  "hint": "the build ended without a usable deck; the tail above says why"}
    (out_dir / STATE).write_text(json.dumps({**state, "state": result["state"]}, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if result["state"] != "error" else 1


def _run_palette(home: Path, argv: list[str]) -> subprocess.CompletedProcess:
    """Run palette.py from the checkout, whatever the caller's cwd is.

    palette.py imports config/pipeline by relative import, so it only runs with
    cwd set to the checkout — but the agent's cwd is its own workspace, and
    that is where output has to land. Getting this wrong is not hypothetical:
    an agent told to `cd $PALETTE_HOME` and then run `palette.py` instead ran
    `skills/palette/palette.py`, mixing the skill folder with the checkout.
    So no caller ever has to think about it: paths in, paths out, cwd handled.
    """
    return subprocess.run(
        [interpreter(home), "-u", "palette.py", *argv],
        cwd=str(home), capture_output=True, text=True, env=palette_env(home),
    )


def _relay(result: subprocess.CompletedProcess) -> int:
    """palette.py's own stdout/stderr, verbatim. Its error text is the reason."""
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip().splitlines()
        print(json.dumps({"ok": False, "error": message[-1] if message else "failed"}))
    return result.returncode


def _sidecar(target: Path, suffix: str) -> Path:
    """A hidden file beside *target*, so two plans in one directory never clash."""
    return target.parent / f".{target.name}.{suffix}"


def _unindent(text: str) -> str:
    """Undo indentation a host's code layout leaked into a pasted value.

    Agents rarely type a command; they write a program that runs one, and the
    user's pasted text becomes a literal inside it. A literal sits at the
    program's indentation, so every line **except the first** picks that up —
    the first is flush because it follows the opening quote on the same line.

    Measured: a 485-character document arrived as 521, the difference being
    exactly nine continuation lines × four spaces. The deck built from it was
    grounded in mangled text and nobody could see why.

    `textwrap.dedent` cannot help here, and that is the whole reason this
    exists: it strips the *common* prefix, which is empty precisely because
    line one is flush.

    Deliberately narrow. It fires only on that exact signature — first line
    unindented, every other non-blank line sharing an indent — so a document
    that is legitimately indented throughout is left alone.
    """
    lines = text.split("\n")
    if len(lines) < 3 or (lines[0][:1].isspace() if lines[0] else True):
        return text

    body = [line for line in lines[1:] if line.strip()]
    if not body:
        return text

    common = min(len(line) - len(line.lstrip(" ")) for line in body)
    if common == 0:
        return text

    return "\n".join(
        [lines[0]] + [line[common:] if line.strip() else "" for line in lines[1:]]
    )


#: A request longer than this, spread over several lines, is a pasted document
#: rather than something a person typed as an instruction.
_PASTED_REQUEST_CHARS = 400
_PASTED_REQUEST_LINES = 3

#: Line count alone is enough past this, whatever the length.
#:
#: Size was the only test until a document slipped under it: 310 characters —
#: comfortably below the limit — spread over **eighteen lines**, a title, an
#: audience line and a preferences block pasted into `--request`. Short, and
#: unmistakably a document. People type instructions; they do not type
#: eighteen lines of one.
_PASTED_REQUEST_LINES_ALONE = 8


def _looks_like_pasted_material(request: str) -> bool:
    newlines = request.count("\n")
    if newlines >= _PASTED_REQUEST_LINES_ALONE:
        return True
    return len(request) > _PASTED_REQUEST_CHARS and newlines >= _PASTED_REQUEST_LINES


def _detach(home: Path, argv: list[str], log: Path, exit_file: Path) -> subprocess.Popen:
    """Run palette.py in the background, recording its exit code when it ends.

    Everything slow goes through here. A step limit kills the *caller*, not the
    detached child, so the work still lands -- but only this exit file lets a
    later poll find out that it did.
    """
    cmd = [interpreter(home), "-u", "palette.py", *argv]
    quoted = " ".join(shlex.quote(part) for part in cmd)
    exit_file.unlink(missing_ok=True)   # a stale one reads as "already finished"
    with log.open("wb") as handle:
        return subprocess.Popen(
            ["/bin/sh", "-c", f"{quoted}; echo $? > {shlex.quote(str(exit_file))}"],
            cwd=str(home),            # palette.py imports config/pipeline from here
            env=palette_env(home),    # + the checkout's .env (model backends)
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,   # outlive the step that started it
        )


def _summarise_build(out_dir: Path) -> dict:
    """One build's state, without the polling machinery `status` prints."""
    state = _load(out_dir / STATE)
    if not state:
        return {"state": "none"}
    checked = verify(out_dir)
    running_here = state.get("remote") or _alive(state.get("pid", -1)) is not False
    if _finished_at(out_dir / EXIT) is None and running_here:
        elapsed = int(time.time()) - int(state.get("started_at", time.time()))
        return {"state": "running", "elapsed_seconds": elapsed}
    if checked["verified"]:
        return {"state": "done", "pptx": checked["pptx"], "pptx_bytes": checked["pptx_bytes"],
                "slide_previews": checked["slide_previews"]}
    return {"state": "error", "log": str(out_dir / "build.log")}


def find(args: argparse.Namespace) -> int:
    """Where is everything? Every plan and build under a root, in one call.

    This replaces the shell loop people were copy-pasting to answer "where did
    my deck go" -- which hardcoded a path, exported a variable `status` does
    not use, and had to be edited per machine. A question asked this often
    deserves a command rather than a snippet.
    """
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"error: no directory at {root}")

    plans = {p.parent / p.name[1:-len(".plan.json")] : p for p in root.rglob(".*.plan.json")}
    builds = sorted({p.parent for p in root.rglob(STATE)})

    found = []
    for plan_path, sidecar in sorted(plans.items()):
        found.append({
            "kind": "plan",
            "path": str(plan_path),
            "written": plan_path.is_file(),
            "state": _load(sidecar).get("state", "unknown"),
        })
    for out_dir in builds:
        found.append({"kind": "build", "dir": str(out_dir), **_summarise_build(out_dir)})

    done = [f for f in found if f["kind"] == "build" and f.get("state") == "done"]
    print(json.dumps({
        "root": str(root),
        "found": found,
        "note": (
            f"{len(done)} finished deck(s)" if done
            else "no finished deck under this root"
        ),
    }, indent=2))
    return 0


def _start_plan(home: Path, argv: list[str], out: Path, label: str, hold: int) -> int:
    """Detach, then hold the call open for up to *hold* seconds.

    A plan is one model call. Polling for it costs an agent turn per poll --
    eight turns for a 160s plan -- and each turn is a model round trip that
    also eats the step budget the build needs later. So do not poll for it if
    it can be avoided.

    But it cannot simply block either: measured at 43-82s in a shell and
    105-170s inside CUGA's sandbox, against a 120s step. A blocking call gets
    killed part-way, and a killed step says nothing about whether the work
    succeeded -- a real session reported "the palette script timed out" while a
    perfectly good plan landed on disk seconds later.

    So: wait, but bounded, under the tightest step limit we know of. Most plans
    come back in this one call. Slow ones degrade into the polling path rather
    than dying.
    """
    # Already drafting this plan? Collect it instead of starting a second one.
    #
    # When the hold expires the agent gets `state: running` and a `next` telling
    # it to poll -- and re-runs `plan` anyway, because re-running is the thing it
    # just did and knows how to do. Measured: plan twice, then edit twice, and a
    # 4-slide deck for a request that said 3, because two writers raced the same
    # file. `start` has guarded against this since the beginning; this is the
    # same guard for the plan, and prose was never going to be the fix.
    previous = _load(_sidecar(out, "plan.json"))
    if (
        previous
        and _finished_at(_sidecar(out, "plan.exit")) is None
        and _alive(previous.get("pid", -1)) is not False
    ):
        return plan_status(argparse.Namespace(out=str(out), hold_seconds=hold))

    process = _detach(home, argv, _sidecar(out, "plan.log"), _sidecar(out, "plan.exit"))
    state = {
        "state": "running", "stage": label, "pid": process.pid,
        "plan": str(out), "started_at": int(time.time()),
    }
    _sidecar(out, "plan.json").write_text(json.dumps(state, indent=2))

    deadline = time.time() + max(0, hold)
    while time.time() < deadline:
        if _finished_at(_sidecar(out, "plan.exit")) is not None:
            # Finished inside the call. Report it the way plan-status would,
            # so the agent never has to make a second round trip.
            return plan_status(argparse.Namespace(out=str(out)))
        time.sleep(2)

    print(json.dumps({
        **state,
        "elapsed_seconds": int(time.time()) - state["started_at"],
        "note": (f"{label} is still running — do NOT run {label.split('-')[0]} again, "
                 f"it would start a second one. Collect this one with plan-status."),
        "next": f"python {Path(__file__).name} plan-status --out {out}",
    }, indent=2))
    return 0


def plan(args: argparse.Namespace) -> int:
    """build-plan, with --out resolved against *your* cwd rather than the checkout."""
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    request = _unindent(args.request)
    context = _unindent(args.context) if args.context else args.context

    # The document in the wrong slot, and nothing in the right one.
    #
    # Measured: a user pasted "Turn these Q3 notes into an exec deck" followed
    # by the notes. The agent sent the notes as --request, no --context, and
    # dropped the instruction entirely — then built a deck that looked fine.
    # Silent, because a request is allowed to be anything.
    #
    # Refused rather than warned: a warning beside a working build is a warning
    # nobody reads, and the same run three months from now is unexplainable.
    # The fix is always available — there is no request that cannot be split
    # into an instruction and its material.
    if _looks_like_pasted_material(request) and not context and not (args.source or []):
        print(json.dumps({
            "ok": False,
            "error": (
                f"--request is {len(request)} characters over "
                f"{request.count(chr(10)) + 1} lines with no --context. That is "
                f"pasted material in the slot meant for the user's instruction."
            ),
            "fix": (
                "Put what the user asked for in --request (usually one line, "
                "theirs verbatim) and the material they pasted in --context. "
                "A file on disk works too: --source <path>."
            ),
        }))
        return 2

    hold = 3600 if args.wait else args.hold_seconds
    if remote_url():
        def submit(base: str, thread: str) -> dict:
            files = [("files", "context.txt", context.encode("utf-8"))] if context else []
            for source in args.source or []:
                path = Path(source).expanduser().resolve()
                files.append(("files", path.name, path.read_bytes()))
            return _http_json(base, "POST", "/draft_async",
                              form={"request": request, "thread_id": thread}, files=files)
        return _remote_start_plan(out, "build-plan", submit, hold)

    home = palette_home()
    argv = ["build-plan", request, "--out", str(out)]
    if context:
        argv += ["--context", context]
    for source in args.source or []:
        argv += ["--source", str(Path(source).expanduser().resolve())]
    if args.wait:
        return _relay(_run_palette(home, argv))
    return _start_plan(home, argv, out, "build-plan", args.hold_seconds)


def plan_status(args: argparse.Namespace) -> int:
    """Has the plan (or edit) landed? Same filesystem-only test as the build.

    Holds the call open rather than answering instantly. Every poll is an agent
    turn -- a model round trip -- and an agent that can poll for free polls as
    fast as it can: one measured run spent 43 turns on `plan-status`, all
    returning in 0.0s, before it got to the build. Waiting here turns those
    into two or three.
    """
    out = Path(args.out).expanduser().resolve()
    state = _load(_sidecar(out, "plan.json"))
    if not state:
        # Answer, and say where the real plans are. Raising here produced a
        # tight loop: an agent polled `--out ./skills/palette/SKILL.md` twenty
        # three times, each failing instantly, because nothing in the refusal
        # told it which path it should have used. A wrong path is a question,
        # not a crash.
        nearby = sorted(
            str(sidecar.parent / sidecar.name[1:-len(".plan.json")])
            for sidecar in Path.cwd().rglob(".*.plan.json")
        )
        print(json.dumps({
            "state": "none", "done": False, "asked_about": str(out),
            "note": f"no plan was started for {out}",
            "plans_here": nearby,
            "next": (
                f"python {Path(__file__).name} plan-status --out {nearby[0]}"
                if nearby else
                f"python {Path(__file__).name} plan --request '<request>' --out plan.md"
            ),
        }, indent=2))
        return 0

    hold = max(0, getattr(args, "hold_seconds", 0))
    if state.get("remote"):
        return _remote_plan_status(out, hold)
    deadline = time.time() + hold
    while time.time() < deadline and _finished_at(_sidecar(out, "plan.exit")) is None:
        time.sleep(2)

    exit_code = _finished_at(_sidecar(out, "plan.exit"))
    elapsed = int(time.time()) - int(state.get("started_at", time.time()))
    written = out.is_file() and out.stat().st_size > 0

    if exit_code is None and _alive(state.get("pid", -1)) is not False:
        result = {
            "state": "running", "done": False, "elapsed_seconds": elapsed,
            "note": f"still drafting after {elapsed}s of a typical 40-180s call",
            "next": f"python {Path(__file__).name} plan-status --out {out}",
        }
    elif written:
        result = {
            "state": "done", "done": True, "plan": str(out),
            "elapsed_seconds": elapsed,
            "text": out.read_text(encoding="utf-8", errors="replace"),
        }
    else:
        result = {
            "state": "error", "done": False, "exit_code": exit_code,
            "elapsed_seconds": elapsed,
            "log_tail": _tail(_sidecar(out, "plan.log"), 15),
            "hint": "the plan step ended without writing a plan; the tail above says why",
        }

    _sidecar(out, "plan.json").write_text(json.dumps({**state, "state": result["state"]}, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if result["state"] != "error" else 1


def edit(args: argparse.Namespace) -> int:
    """edit-plan, reading and writing the same file by absolute path."""
    plan_path = Path(args.plan).expanduser().resolve()
    if not plan_path.is_file():
        raise SystemExit(f"error: no plan at {plan_path}")
    out = Path(args.out).expanduser().resolve() if args.out else plan_path
    if remote_url():
        plan_text = plan_path.read_text(encoding="utf-8")
        def submit(base: str, thread: str) -> dict:
            return _http_json(base, "POST", "/edit_plan_async", body={
                "plan": plan_text, "instruction": args.instruction, "thread_id": thread})
        return _remote_start_plan(out, "edit-plan", submit,
                                  3600 if args.wait else args.hold_seconds)
    home = palette_home()
    argv = ["edit-plan", args.instruction, "--plan", str(plan_path), "--out", str(out)]
    if args.wait:
        return _relay(_run_palette(home, argv))
    # An edit is the same shape of model call as a plan -- 54s measured, and
    # subject to the same step limit -- so it collects the same way.
    return _start_plan(home, argv, out, "edit-plan", args.hold_seconds)


def start(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = Path(args.plan).expanduser().resolve()
    if not plan.is_file():
        raise SystemExit(f"error: no plan at {plan}")
    if remote_url():
        return _remote_start(plan, out_dir, args.palette_family)
    home = palette_home()

    state_path = out_dir / STATE
    previous = _load(state_path)
    if previous and _finished(out_dir) is None and _alive(previous.get("pid", -1)) is not False:
        print(json.dumps({**previous, "note": "already building; poll with status"}))
        return 0

    log = out_dir / "build.log"
    argv = ["build-deck", "--plan", str(plan), "--out-dir", str(out_dir), "--json"]
    if args.palette_family:
        argv += ["--palette-family", args.palette_family]

    process = _detach(home, argv, log, out_dir / EXIT)

    state = {
        "state": "running",
        "pid": process.pid,
        "plan": str(plan),
        "out_dir": str(out_dir),
        "log": str(log),
        "started_at": int(time.time()),
    }
    state_path.write_text(json.dumps(state, indent=2))
    print(json.dumps({
        **state,
        "note": "building — takes 3-10 minutes; poll with `deck.py status`",
        "next": f"python {Path(__file__).name} status --out-dir {out_dir}",
    }))
    return 0


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def status(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).expanduser().resolve()
    state = _load(out_dir / STATE)
    if not state:
        # "Nothing here yet" is an answer, not an error, because this is also
        # the way to ask *whether* a build exists. An agent has no memory
        # between turns: told only how to start a build, it re-runs the
        # confirmation gate on every turn, asks again, and never polls the one
        # it already started. Seen for 33 minutes with a finished deck on disk.
        print(json.dumps({
            "state": "none", "done": False, "out_dir": str(out_dir),
            "note": "no build has been started in this directory",
            "next": f"python {Path(__file__).name} start --plan <plan.md> --out-dir {out_dir}",
        }, indent=2))
        return 0

    # Hold, for the same reason `plan-status` does: an agent polling for free
    # polls in a tight loop and spends its step budget on round trips that say
    # "still running". A build is minutes, so waiting a minute per call costs
    # nothing and collapses thirty turns into a handful.
    hold = max(0, getattr(args, "hold_seconds", 0))
    if state.get("remote"):
        return _remote_status(out_dir, state, hold)
    deadline = time.time() + hold
    while time.time() < deadline and _finished(out_dir) is None:
        time.sleep(3)

    checked = verify(out_dir)
    exit_code = _finished(out_dir)
    elapsed = int(time.time()) - int(state.get("started_at", time.time()))

    # Has the build ENDED? That question comes first, and a .pptx on disk does
    # not answer it. `build-deck` renders, lints the geometry, and re-renders
    # to the *same path* until the layout settles -- three passes on a plain
    # five-slide deck, the last still fixing overflows. So a complete, valid,
    # correctly-sized .pptx exists minutes before the build is over, and
    # handing it over then means a pre-repair deck or a torn read of a zip
    # being rewritten underneath the user.
    #
    # `.palette-exit` answers it, and `_alive` only corroborates: it returns
    # None wherever the host will not discuss other processes, and None must
    # never read as "dead" -- doing so failed every sandboxed build on poll one.
    if exit_code is None and _alive(state.get("pid", -1)) is not False:
        # `build-deck` prints only when it finishes -- the pipeline's own stage
        # logging goes to a per-session file, not to stdout -- so the log is
        # empty for most of a run. Report elapsed time, which is always true,
        # rather than an empty string the agent is told to relay.
        result = {
            "state": "running", "done": False, "elapsed_seconds": elapsed,
            "note": f"still rendering after {elapsed}s of a typical 180-600s build",
            "next": f"python {Path(__file__).name} status --out-dir {out_dir}",
        }
        latest = _tail(Path(state["log"]), 1)
        if latest:
            result["progress"] = latest
    elif checked["verified"]:
        # How long the build *took*, not how long ago it started. Polling an
        # hour later would otherwise report an hour, and the agent is told to
        # flag a long build -- it would flag a fast one it happened to revisit.
        finished = Path(checked["pptx"]).stat().st_mtime
        took = max(0, int(finished) - int(state.get("started_at", finished)))
        result = {"state": "done", "done": True, **checked, "elapsed_seconds": took}
    else:
        # Build over, no usable .pptx: surface the log, because the reason is
        # in it and the agent cannot see the detached process's output.
        result = {
            "state": "error", "done": False, "elapsed_seconds": elapsed,
            "exit_code": exit_code,
            **checked,
            "log_tail": _tail(Path(state["log"]), 15),
            "hint": "the build ended without writing a usable deck; the tail above says why",
        }

    (out_dir / STATE).write_text(json.dumps({**state, "state": result["state"]}, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if result["state"] != "error" else 1


def _tail(path: Path, lines: int) -> str:
    try:
        content = path.read_text(errors="replace").strip().splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pl = sub.add_parser("plan", help="request -> markdown plan; returns at once, poll plan-status")
    pl.add_argument("--request", required=True, help="the user's request, passed through as-is")
    pl.add_argument("--context", default=None, help="material the user pasted, to ground the plan")
    pl.add_argument("--source", action="append", help="grounding file on disk (repeatable)")
    pl.add_argument("--out", required=True, help="where to write the plan")
    pl.add_argument("--wait", action="store_true",
                    help="block until done (40-180s) instead of detaching; for a terminal, "
                         "not for an agent whose step can be cut short")
    pl.add_argument("--hold-seconds", type=int, default=90,
                    help="how long to hold the call open waiting for the plan before "
                         "handing back to plan-status (default 90, under a 120s step)")
    pl.set_defaults(func=plan)

    ps = sub.add_parser("plan-status", help="is the plan (or edit) written yet?")
    ps.add_argument("--out", required=True, help="the --out you passed to plan or edit")
    ps.add_argument("--hold-seconds", type=int, default=60,
                    help="wait this long for it to finish before answering (default 60)")
    ps.set_defaults(func=plan_status)

    ed = sub.add_parser("edit", help="apply a requested change to an existing plan")
    ed.add_argument("--instruction", required=True, help="the change, passed through as-is")
    ed.add_argument("--plan", required=True)
    ed.add_argument("--out", default=None, help="default: overwrite --plan")
    ed.add_argument("--wait", action="store_true", help="block instead of detaching")
    ed.add_argument("--hold-seconds", type=int, default=90,
                    help="see `plan --hold-seconds`")
    ed.set_defaults(func=edit)

    s = sub.add_parser("start", help="launch build-deck detached and return at once")
    s.add_argument("--plan", required=True, help="path to the approved plan markdown")
    s.add_argument("--out-dir", required=True, help="where deck.pptx and previews land")
    s.add_argument("--palette-family", default=None, help="visual style (default ibm_watsonx)")
    s.set_defaults(func=start)

    q = sub.add_parser("status", help="is it done? poll until state is done or error")
    q.add_argument("--out-dir", required=True)
    q.add_argument("--hold-seconds", type=int, default=60,
                    help="wait this long for it to finish before answering (default 60)")
    q.set_defaults(func=status)

    fd = sub.add_parser("find", help="every plan and build under a root — 'where did my deck go?'")
    fd.add_argument("--root", default=".", help="directory to search (default: cwd)")
    fd.set_defaults(func=find)

    args = parser.parse_args(argv)
    if not os.environ.get("PALETTE_TRACE", "").strip():
        return args.func(args)
    return _traced(args)


def _traced(args: argparse.Namespace) -> int:
    """Run the command, appending what went in and what came out to $PALETTE_TRACE.

    Off unless the variable is set, so it costs nothing in normal use. It exists
    for the benchmark harness: when a run produces a wrong deck, the question is
    always "what did the agent actually ask Palette for", and the agent's own
    account of that is a paraphrase at best.

    Never let tracing break the command it is tracing — a benchmark that changes
    the thing it measures is worse than no benchmark.
    """
    import io
    from contextlib import redirect_stdout

    started = time.time()
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            code = args.func(args)
    except SystemExit as exc:  # argparse-style refusals are a real outcome
        code = int(exc.code) if isinstance(exc.code, int) else 1
        printed = buffer.getvalue()
        _append_trace(args, printed, code, started, time.time() - started, error=str(exc))
        sys.stdout.write(printed)
        raise
    printed = buffer.getvalue()
    sys.stdout.write(printed)
    _append_trace(args, printed, code, started, time.time() - started)
    return code


def _append_trace(args, printed: str, code: int, started: float, seconds: float, error: str = "") -> None:
    path = Path(os.environ["PALETTE_TRACE"]).expanduser()
    # Long free text is what the agent chose to send; it is the interesting part,
    # but a whole pasted document would swamp the trace. Keep the head and say so.
    def clip(value, limit=600):
        if not isinstance(value, str) or len(value) <= limit:
            return value
        return value[:limit] + f"… [+{len(value) - limit} chars]"

    payload = {
        "at": round(started, 3),
        "command": args.command,
        "args": {
            k: clip(v)
            for k, v in vars(args).items()
            if k not in {"func", "command"} and v is not None
        },
        "seconds": round(seconds, 2),
        "exit_code": code,
        "stdout": clip(printed, 4000),
    }
    if error:
        payload["error"] = error
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
    except OSError:
        pass  # tracing must never break the command



if __name__ == "__main__":
    raise SystemExit(main())
