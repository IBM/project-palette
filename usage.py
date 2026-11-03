"""Usage tracking — who uses Palette, and what they ask for.

Everything lands in one local SQLite file, PALETTE_DATA_DIR/usage.db
(default ~/.local/share/palette; /app/data in the container, bind-mounted
from that same host folder). Each step links to the step that caused it, so one utterance
can be followed all the way to the deck it produced (see journeys()):

  utterances     what a user typed (deck request, plan edit, slide edit)
    └ plans          utterance_id → the plan it produced, or the error
        └ builds         plan_id → the deck: slides, title, timing, quality, error
            └ deck_versions  build_id → each later slide edit / retry
                             (utterance_id for an edit)
  requests       every tracked POST: endpoint, status, duration, who

A build from a plan loaded from file has no plan_id; `plan_edited` marks a
build whose plan the user changed by hand after it was drafted. Text is
secret-scrubbed and length-capped before it is written.

Every successful build is also archived to disk, next to the db:
  PALETTE_DATA_DIR/decks/<YYYY-MM-DD>/<HHMMSS>Z-<build id>-[example-]<title>/
      deck.pptx, plan.md, slide-N.png
  (UTC; a day's folders sort in the order the decks were built, and decks
  from the UI's example prompts are marked so they are easy to skip)
Slide edits and retries refresh that copy, so it is always the latest version
of the deck — the one the user would have downloaded. The workspace copy is
not enough: it is outside the data volume and "New" deletes it.

"Who" is anonymous. There is no login, so a user is:
  uid      a random id in the `palette_uid` cookie, set on the first page
           load and kept for a year — one browser, across sessions
  visitor  sha256(salt | client IP)[:16] — never the IP itself
  client   "ui" (browser with the cookie) or the API caller's user agent
           ("python-httpx", "curl", …) — the skill and scripts show up here

The dashboard is app.py's /stats, gated by PALETTE_STATS_TOKEN.

Pattern borrowed from cuga-apps' _usage.py (salted visitor hash, secret
scrubbing, utterance cap). Tracking is best-effort: every write swallows its
own errors, so a broken or locked database can never fail a request.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path

import config

log = logging.getLogger("usage")

# One folder on the host holds the db and the deck archive, outside any
# checkout so re-cloning the repo keeps them. The image sets
# PALETTE_DATA_DIR=/app/data and the host folder is bind-mounted there, so
# `make dev` and the container read and write the same files.
DATA_DIR = Path(os.environ.get("PALETTE_DATA_DIR")
                or "~/.local/share/palette").expanduser()
DB_PATH = Path(os.environ.get("PALETTE_USAGE_DB") or (DATA_DIR / "usage.db")).expanduser()
# Where DATA_DIR really is on the host, when it is a container mount — so the
# dashboard and /health point people at a path they can open.
HOST_DATA_DIR = os.environ.get("PALETTE_HOST_DATA_DIR", "").strip()


def host_path(path: Path | str) -> str:
    """`path` as seen from the host (unchanged outside a container)."""
    path = Path(path)
    if HOST_DATA_DIR:
        try:
            return str(Path(HOST_DATA_DIR) / path.relative_to(DATA_DIR))
        except ValueError:
            pass
    return str(path)


def locations() -> dict:
    return {"data_dir": host_path(DATA_DIR), "db": host_path(DB_PATH),
            "decks": host_path(DECKS_DIR)}
_SALT = os.environ.get("PALETTE_USAGE_SALT", "palette-usage")
_TRUST_FWD = os.environ.get("PALETTE_TRUST_FORWARDED", "1") != "0"
_UTT_MAX = int(os.environ.get("PALETTE_UTTERANCE_MAXLEN", "4000"))
_PLAN_MAX = int(os.environ.get("PALETTE_PLAN_MAXLEN", "20000"))
DECKS_DIR = Path(os.environ.get("PALETTE_DECK_ARCHIVE")
                 or (DB_PATH.parent / "decks")).expanduser()

COOKIE = "palette_uid"
COOKIE_MAX_AGE = 365 * 24 * 3600

# Who made the request in flight. The middleware sets it; the endpoints read
# it when they record an utterance or a build (which may finish later, in a
# background task — create_task copies the context, so it carries over).
_who: contextvars.ContextVar[dict | None] = contextvars.ContextVar("palette_who", default=None)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, day TEXT NOT NULL,
  method TEXT, path TEXT, status INTEGER, ms INTEGER,
  thread_id TEXT, uid TEXT, visitor TEXT, client TEXT);
CREATE TABLE IF NOT EXISTS utterances (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, day TEXT NOT NULL,
  kind TEXT, text TEXT, thread_id TEXT, uid TEXT, visitor TEXT, client TEXT,
  slide_n INTEGER, sources INTEGER);
CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, day TEXT NOT NULL,
  utterance_id INTEGER, kind TEXT, started_at REAL, seconds REAL,
  ok INTEGER, error TEXT, plan TEXT, sources TEXT, model TEXT,
  thread_id TEXT, uid TEXT, visitor TEXT, client TEXT);
CREATE TABLE IF NOT EXISTS builds (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, day TEXT NOT NULL,
  ok INTEGER, error TEXT, slides INTEGER, seconds REAL, title TEXT,
  palette TEXT, plan TEXT, thread_id TEXT, uid TEXT, visitor TEXT, client TEXT);
CREATE TABLE IF NOT EXISTS deck_versions (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL, day TEXT NOT NULL,
  build_id INTEGER, reason TEXT, slides INTEGER, slide_n INTEGER);
"""

# Columns added after a table first shipped. ALTER only what is missing, so an
# existing usage.db upgrades in place.
_COLUMNS = {
    "utterances": [("attachments", "TEXT")],
    "builds": [("deck_dir", "TEXT"), ("versions", "INTEGER DEFAULT 1"),
               ("plan_id", "INTEGER"), ("started_at", "REAL"),
               ("plan_edited", "INTEGER"), ("models", "TEXT"), ("details", "TEXT")],
    "deck_versions": [("utterance_id", "INTEGER"), ("thread_id", "TEXT"),
                      ("ok", "INTEGER DEFAULT 1"), ("error", "TEXT"),
                      ("started_at", "REAL"), ("seconds", "REAL")],
}

_INDEXES = """
CREATE INDEX IF NOT EXISTS ix_req_day ON requests(day);
CREATE INDEX IF NOT EXISTS ix_utt_ts ON utterances(ts);
CREATE INDEX IF NOT EXISTS ix_bld_ts ON builds(ts);
CREATE INDEX IF NOT EXISTS ix_plan_utt ON plans(utterance_id);
CREATE INDEX IF NOT EXISTS ix_bld_plan ON builds(plan_id);
CREATE INDEX IF NOT EXISTS ix_ver_build ON deck_versions(build_id);
CREATE INDEX IF NOT EXISTS ix_ver_utt ON deck_versions(utterance_id);
"""

# Best-effort secret redaction before text is stored (same idea as cuga-apps).
# The UI also tells people not to enter confidential information.
_SECRET_RE = re.compile(
    r"\b("
    r"sk-[A-Za-z0-9]{16,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|xox[baprs]-[A-Za-z0-9-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"
    r"|[A-Za-z0-9+/_-]{40,}"
    r")\b"
)


def scrub(text: str) -> str:
    return _SECRET_RE.sub("«redacted»", text)


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        # Rollback journal, not WAL: WAL's shared-memory file is unreliable
        # on the macOS-to-VM file sharing a bind-mounted container goes
        # through. One process writes, so WAL's concurrency buys nothing.
        _conn.execute("PRAGMA journal_mode=DELETE")
        _conn.executescript(_SCHEMA)
        for table, cols in _COLUMNS.items():
            have = {r[1] for r in _conn.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols:
                if col not in have:
                    _conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        _conn.executescript(_INDEXES)
        log.info("usage db: %s   decks: %s", host_path(DB_PATH), host_path(DECKS_DIR))
    return _conn


def init() -> None:
    try:
        with _lock:
            _db()
    except Exception as exc:  # noqa: BLE001
        log.warning("usage db unavailable (%s) — tracking disabled", exc)
        return
    _rename_legacy_deck_folders()


def _rename_legacy_deck_folders() -> None:
    """Early archives were named <day>/<build id:06d>; give them the readable,
    time-ordered name new builds get (see _deck_folder)."""
    try:
        for b in query("SELECT id, ts, title, deck_dir FROM builds WHERE deck_dir IS NOT NULL"):
            if not re.fullmatch(r"(.*/)?\d{4}-\d{2}-\d{2}/\d{6}", b["deck_dir"]):
                continue
            old = _deck_path(b["deck_dir"])
            new_rel = _deck_folder(b["id"], b["title"], at=b["ts"])
            new = DECKS_DIR / new_rel
            if old.is_dir() and not new.exists():
                new.parent.mkdir(parents=True, exist_ok=True)
                old.rename(new)
                _exec("UPDATE builds SET deck_dir = ? WHERE id = ?", (new_rel, b["id"]))
                log.info("renamed deck archive %s -> %s", old.name, new_rel)
    except Exception as exc:  # noqa: BLE001
        log.warning("deck folder rename skipped: %s", exc)


def _insert(table: str, row: dict) -> int | None:
    try:
        now = time.time()
        row = {"ts": now, "day": time.strftime("%Y-%m-%d", time.gmtime(now)), **row}
        cols = ", ".join(row)
        marks = ", ".join("?" * len(row))
        with _lock:
            cur = _db().execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                                tuple(row.values()))
            return cur.lastrowid
    except Exception as exc:  # noqa: BLE001 — tracking must never break a request
        log.debug("usage write failed: %s", exc)
    return None


def query(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        cur = _db().execute(sql, args)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]


# -- identity --------------------------------------------------------------

def new_uid() -> str:
    return uuid.uuid4().hex[:16]


def _client_ip(request) -> str:
    if _TRUST_FWD:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        xri = request.headers.get("x-real-ip")
        if xri:
            return xri.strip()
    return request.client.host if request.client else "unknown"


def _client_kind(request, uid: str | None) -> str:
    if uid:
        return "ui"
    ua = request.headers.get("user-agent", "")
    return (ua.split("/")[0].strip() or "unknown")[:32]


def identify(request) -> dict:
    """Who sent this request; also stored in the context for later records."""
    uid = request.cookies.get(COOKIE)
    ip = _client_ip(request)
    who = {
        "uid": uid,
        "visitor": hashlib.sha256(f"{_SALT}|{ip}".encode()).hexdigest()[:16],
        "client": _client_kind(request, uid),
    }
    _who.set(who)
    return who


def note_thread(thread_id: str) -> None:
    """Tag the in-flight request with its session. Endpoints call this (via
    app._bind_session) after the middleware identified the caller; the dict is
    shared by reference, so the middleware sees it when it writes the row."""
    who = _who.get()
    if who is not None:
        who["thread_id"] = thread_id


def _current() -> dict:
    who = _who.get() or {}
    return {k: who.get(k) for k in ("uid", "visitor", "client")}


# -- records ---------------------------------------------------------------

def record_request(who: dict, method: str, path: str, status: int, ms: int) -> None:
    _insert("requests", {"method": method, "path": path, "status": status, "ms": ms,
                         "thread_id": who.get("thread_id"), "uid": who.get("uid"),
                         "visitor": who.get("visitor"), "client": who.get("client")})


def record_utterance(kind: str, text: str, thread_id: str | None, *,
                     slide_n: int | None = None,
                     attachments: list[str] | None = None) -> int | None:
    """Store what the user typed. Returns its id, which the plan or edit it
    leads to points back at."""
    clean = scrub((text or "").strip())[:_UTT_MAX]
    if not clean:
        return None
    return _insert("utterances", {
        "kind": kind, "text": clean, "thread_id": thread_id, "slide_n": slide_n,
        "sources": len(attachments) if attachments is not None else None,
        "attachments": json.dumps(attachments) if attachments else None,
        **_current()})


def record_plan(utterance_id: int | None, thread_id: str, kind: str, started_at: float, *,
                ok: bool, plan: str | None = None, error: str | None = None,
                sources: list[str] | None = None, model: str | None = None) -> int | None:
    """The outcome of drafting (or revising) a plan from an utterance."""
    return _insert("plans", {
        "utterance_id": utterance_id, "kind": kind, "started_at": started_at,
        "seconds": round(time.time() - started_at, 1), "ok": int(ok),
        "error": (error or "")[:2000] or None,
        "plan": scrub(plan)[:_PLAN_MAX] if plan else None,
        "sources": json.dumps(sources) if sources else None, "model": model,
        "thread_id": thread_id, **_current()})


def start_build(thread_id: str, plan: str, palette: str, *, started_at: float,
                plan_id: int | None = None, models: str | None = None) -> int | None:
    """Open a build row (ok = NULL while it runs). finish_build closes it."""
    clean = scrub(plan or "")[:_PLAN_MAX]
    edited = None
    if plan_id:
        row = query("SELECT plan FROM plans WHERE id = ?", (plan_id,))
        if row and row[0]["plan"] is not None:
            edited = int(row[0]["plan"].strip() != clean.strip())
    return _insert("builds", {
        "ok": None, "started_at": started_at, "palette": palette, "plan": clean,
        "plan_id": plan_id, "plan_edited": edited, "models": models,
        "thread_id": thread_id, **_current()})


def finish_build(build_id: int | None, *, ok: bool, started_at: float,
                 slides: int | None = None, title: str | None = None,
                 error: str | None = None, details: dict | None = None,
                 out_dir: Path | None = None, plan: str | None = None) -> None:
    """Close a build row; for a successful build, archive its deck."""
    if not build_id:
        return
    _exec("UPDATE builds SET ok = ?, error = ?, slides = ?, title = ?, seconds = ?, "
          "details = ?, ts = ?, day = ? WHERE id = ?",
          (int(ok), (error or "")[:2000] or None, slides, title,
           round(time.time() - started_at, 1),
           json.dumps(details, default=str)[:50000] if details else None,
           time.time(), time.strftime("%Y-%m-%d", time.gmtime()), build_id))
    if ok and out_dir is not None:
        dest = DECKS_DIR / _deck_folder(build_id, title)
        if _copy_deck(out_dir, dest, plan=plan):
            _exec("UPDATE builds SET deck_dir = ? WHERE id = ?",
                  (str(dest.relative_to(DECKS_DIR)), build_id))


def record_edit(build_id: int | None, thread_id: str, out_dir: Path, *, reason: str,
                started_at: float, ok: bool, error: str | None = None,
                utterance_id: int | None = None, slides: int | None = None,
                slide_n: int | None = None) -> None:
    """A slide edit or retry on a built deck. On success the archived deck is
    refreshed, so it always holds the latest version."""
    try:
        _insert("deck_versions", {
            "build_id": build_id, "thread_id": thread_id, "reason": reason,
            "utterance_id": utterance_id, "slides": slides, "slide_n": slide_n,
            "ok": int(ok), "error": (error or "")[:2000] or None,
            "started_at": started_at, "seconds": round(time.time() - started_at, 1)})
        if not (ok and build_id):
            return
        row = query("SELECT deck_dir FROM builds WHERE id = ?", (build_id,))
        if row and row[0]["deck_dir"] and _copy_deck(out_dir, _deck_path(row[0]["deck_dir"])):
            _exec("UPDATE builds SET versions = COALESCE(versions, 1) + 1, "
                  "slides = COALESCE(?, slides) WHERE id = ?", (slides, build_id))
    except Exception as exc:  # noqa: BLE001
        log.debug("edit record failed: %s", exc)


def _copy_deck(out_dir: Path, dest: Path, *, plan: str | None = None) -> bool:
    """Copy deck.pptx + slide previews (and the plan) into dest. Best-effort."""
    try:
        pptx = Path(out_dir) / "deck.pptx"
        if not pptx.is_file():
            return False
        dest.mkdir(parents=True, exist_ok=True)
        for old in dest.glob("slide-*.png"):      # slide count may have changed
            old.unlink()
        shutil.copy2(pptx, dest / "deck.pptx")
        for png in sorted(Path(out_dir).glob("slide-*.png")):
            shutil.copy2(png, dest / png.name)
        if plan is not None:
            (dest / "plan.md").write_text(scrub(plan), encoding="utf-8")
        return True
    except Exception as exc:  # noqa: BLE001 — archiving must never fail a build
        log.warning("deck archive failed (%s): %s", dest, exc)
        return False


def _deck_folder(build_id: int, title: str | None, at: float | None = None) -> str:
    """<YYYY-MM-DD>/<HHMMSS>Z-<id>-[example-]<title-slug>, in UTC."""
    now = time.gmtime(at)
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "untitled").lower()).strip("-")[:48] or "untitled"
    kind = query("SELECT u.kind FROM builds b JOIN plans p ON p.id = b.plan_id "
                 "JOIN utterances u ON u.id = p.utterance_id WHERE b.id = ?", (build_id,))
    if kind and kind[0]["kind"] == "example":
        slug = "example-" + slug
    return f"{time.strftime('%Y-%m-%d', now)}/{time.strftime('%H%M%S', now)}Z-{build_id:04d}-{slug}"


def _deck_path(stored: str) -> Path:
    """A build's archive folder. Stored relative to DECKS_DIR so the same db
    works in the container (/app/data) and on the host; rows from before
    that hold an absolute path, which is re-rooted at DECKS_DIR."""
    p = Path(stored)
    if not p.is_absolute():
        return DECKS_DIR / p
    if p.exists():
        return p
    parts = p.parts
    return DECKS_DIR.joinpath(*parts[parts.index("decks") + 1:]) if "decks" in parts else p


def _exec(sql: str, args: tuple = ()) -> None:
    try:
        with _lock:
            _db().execute(sql, args)
    except Exception as exc:  # noqa: BLE001
        log.debug("usage write failed: %s", exc)


def deck_file(build_id: int, name: str) -> Path | None:
    """A file from a build's archive (deck.pptx, plan.md, slide-NN.png)."""
    if not re.fullmatch(r"deck\.pptx|plan\.md|slide-\d+\.png", name):
        return None
    row = query("SELECT deck_dir FROM builds WHERE id = ?", (build_id,))
    if not row or not row[0]["deck_dir"]:
        return None
    path = _deck_path(row[0]["deck_dir"]) / name
    return path if path.is_file() else None


# -- dashboard data ----------------------------------------------------------

def _archive_size() -> dict:
    try:
        files = [f for f in DECKS_DIR.rglob("*") if f.is_file()]
        return {"decks": len(list(DECKS_DIR.glob("*/*/deck.pptx"))),
                "mb": round(sum(f.stat().st_size for f in files) / 1e6, 1)}
    except Exception:  # noqa: BLE001
        return {"decks": 0, "mb": 0}


def _since(days: int) -> float:
    return time.time() - days * 86400


def summary(days: int = 30) -> dict:
    """Everything the /stats page shows, as one JSON-able dict."""
    def kpis(since: float) -> dict:
        q = lambda sql: query(sql, (since,))[0]  # noqa: E731
        users = q("SELECT COUNT(DISTINCT COALESCE(uid, visitor)) n FROM requests WHERE ts >= ?")["n"]
        sessions = q("SELECT COUNT(DISTINCT thread_id) n FROM requests "
                     "WHERE ts >= ? AND thread_id IS NOT NULL")["n"]
        utt = q("SELECT COUNT(*) n FROM utterances WHERE ts >= ?")["n"]
        b = q("SELECT COUNT(*) n, SUM(ok) ok, AVG(CASE WHEN ok=1 THEN seconds END) s, "
              "SUM(CASE WHEN ok=1 THEN slides END) slides FROM builds WHERE ts >= ? AND ok IS NOT NULL")
        return {"users": users, "sessions": sessions, "utterances": utt,
                "builds": b["n"], "builds_ok": b["ok"] or 0,
                "build_failures": b["n"] - (b["ok"] or 0),
                "avg_build_s": round(b["s"], 1) if b["s"] else None,
                "slides": b["slides"] or 0}

    now = time.time()
    day_start = now - now % 86400          # UTC midnight, matching the `day` column
    daily = query(
        "SELECT day, COUNT(DISTINCT COALESCE(uid, visitor)) users, COUNT(*) requests "
        "FROM requests WHERE ts >= ? GROUP BY day ORDER BY day", (_since(days),))
    per_day = {r["day"]: r for r in daily}
    for r in query("SELECT day, COUNT(*) n FROM utterances WHERE ts >= ? GROUP BY day", (_since(days),)):
        per_day.setdefault(r["day"], {"day": r["day"], "users": 0, "requests": 0})["utterances"] = r["n"]
    for r in query("SELECT day, COUNT(*) n FROM builds WHERE ts >= ? GROUP BY day", (_since(days),)):
        per_day.setdefault(r["day"], {"day": r["day"], "users": 0, "requests": 0})["builds"] = r["n"]
    series = []
    for i in range(days - 1, -1, -1):
        d = time.strftime("%Y-%m-%d", time.gmtime(time.time() - i * 86400))
        r = per_day.get(d, {})
        series.append({"day": d, "users": r.get("users", 0), "requests": r.get("requests", 0),
                       "utterances": r.get("utterances", 0), "builds": r.get("builds", 0)})

    users = query("""
        WITH who AS (
          SELECT COALESCE(uid, visitor) who, uid, visitor, client, ts, thread_id FROM requests)
        SELECT who, MAX(uid) uid, MAX(client) client, MIN(ts) first_seen, MAX(ts) last_seen,
               COUNT(DISTINCT thread_id) sessions, COUNT(*) requests,
               COUNT(DISTINCT visitor) networks
        FROM who GROUP BY who ORDER BY last_seen DESC LIMIT 200""")
    utt_counts = {r["who"]: r for r in query(
        "SELECT COALESCE(uid, visitor) who, COUNT(*) n FROM utterances GROUP BY who")}
    bld_counts = {r["who"]: r for r in query(
        "SELECT COALESCE(uid, visitor) who, COUNT(*) n, SUM(ok) ok FROM builds GROUP BY who")}
    for u in users:
        u["utterances"] = utt_counts.get(u["who"], {}).get("n", 0)
        b = bld_counts.get(u["who"], {})
        u["builds"] = b.get("n", 0)
        u["builds_ok"] = b.get("ok") or 0

    builds = [_build_view(b, []) for b in query(
        "SELECT * FROM builds ORDER BY ts DESC LIMIT 200")]
    for b in builds:
        b["who"] = b["uid"] or b["visitor"]

    return {
        "db": host_path(DB_PATH),
        "locations": locations(),
        "generated_at": time.time(),
        "kpis": {"today": kpis(day_start), "7d": kpis(_since(7)),
                 "30d": kpis(_since(30)), "all": kpis(0)},
        "daily": series,
        "users": users,
        "utterances": query(
            "SELECT ts, kind, text, thread_id, COALESCE(uid, visitor) who, client, slide_n, sources "
            "FROM utterances ORDER BY ts DESC LIMIT 500"),
        "builds": builds,
        "journeys": journeys(),
        "archive": {"dir": host_path(DECKS_DIR), **_archive_size()},
    }


def _loads(v):
    try:
        return json.loads(v) if v else None
    except Exception:  # noqa: BLE001
        return None


_STALE_S = 30 * 60   # a build still "running" after this was cut off (restart, crash)


def _build_view(b: dict, versions: list[dict], aborts: list[float] = ()) -> dict:
    if b["ok"] is None and time.time() - (b["started_at"] or b["ts"]) > _STALE_S:
        b["interrupted"] = True
    end = b["ts"] if b["ok"] is not None else time.time()
    b["aborted_by_user"] = any((b["started_at"] or 0) <= t <= end for t in aborts)
    d = b.pop("deck_dir", None)
    d = _deck_path(d) if d else None
    b["pngs"] = sorted(f.name for f in d.glob("slide-*.png")) if d else []
    b["archived"] = bool(d) and (d / "deck.pptx").is_file()
    b["folder"] = host_path(d) if b["archived"] else None
    details = _loads(b.pop("details", None)) or {}
    b["lint"] = details.get("lint") or []
    b["unrepaired"] = details.get("unrepaired") or []
    b["retries"] = details.get("retries") or {}
    b["geometry"] = details.get("geometry") or {}
    b["edits"] = versions
    return b


def _build_status(b: dict) -> str:
    if b["ok"] is None:
        return "interrupted" if b.get("interrupted") else "building"
    return "built" if b["ok"] else "build_failed"


def journeys(limit: int = 200) -> list[dict]:
    """Each utterance followed to its outcome, newest first.

    request / plan_edit → its plan (text, timing, error) → every build of
    that plan (deck, timing, quality, error) → the edits/retries on each build.
    slide_edit → the edit's outcome and the build it changed. Builds from a
    plan loaded from file (no utterance) appear as their own "loaded_plan"
    journeys so no deck is missing.
    """
    utts = query("SELECT * FROM utterances ORDER BY ts DESC LIMIT ?", (limit,))
    if not utts:
        utts = []
    oldest = utts[-1]["ts"] if len(utts) == limit else 0

    plans = query("SELECT * FROM plans WHERE ts >= ? ORDER BY ts", (oldest,))
    builds = query("SELECT * FROM builds WHERE ts >= ? ORDER BY ts", (oldest,))
    versions = query("SELECT * FROM deck_versions WHERE ts >= ? ORDER BY ts", (oldest,))
    aborts: dict = {}
    for r in query("SELECT thread_id, ts FROM requests WHERE path LIKE '/abort/%' AND ts >= ?",
                   (oldest,)):
        aborts.setdefault(r["thread_id"], []).append(r["ts"])

    ver_by_build: dict = {}
    ver_by_utt: dict = {}
    for v in versions:
        ver_by_build.setdefault(v["build_id"], []).append(v)
        if v["utterance_id"]:
            ver_by_utt[v["utterance_id"]] = v
    build_views = {b["id"]: _build_view(dict(b), ver_by_build.get(b["id"], []),
                                        aborts.get(b["thread_id"], [])) for b in builds}
    builds_by_plan: dict = {}
    for b in build_views.values():
        if b["plan_id"]:
            builds_by_plan.setdefault(b["plan_id"], []).append(b)
    plan_by_utt = {p["utterance_id"]: p for p in plans if p["utterance_id"]}

    out = []
    for u in utts:
        j = {"ts": u["ts"], "kind": u["kind"], "text": u["text"], "thread_id": u["thread_id"],
             "who": u["uid"] or u["visitor"], "client": u["client"], "slide_n": u["slide_n"],
             "attachments": _loads(u.get("attachments")) or [], "utterance_id": u["id"]}
        if u["kind"] == "slide_edit":
            v = ver_by_utt.get(u["id"])
            j["edit"] = v
            b = build_views.get(v["build_id"]) if v else None
            j["builds"] = [b] if b else []
            j["status"] = "pending" if not v else ("ok" if v["ok"] else "failed")
        else:
            p = plan_by_utt.get(u["id"])
            if p:
                p = dict(p)
                p["sources"] = _loads(p["sources"]) or []
            j["plan"] = p
            j["builds"] = builds_by_plan.get(p["id"], []) if p else []
            if not p:
                j["status"] = "planning" if time.time() - u["ts"] < _STALE_S else "no_plan"
            elif not p["ok"]:
                j["status"] = "plan_failed"
            elif not j["builds"]:
                j["status"] = "planned"
            else:
                j["status"] = _build_status(j["builds"][-1])
        out.append(j)

    for b in build_views.values():
        if not b["plan_id"]:
            out.append({"ts": b["started_at"] or b["ts"], "kind": "loaded_plan", "text": None,
                        "thread_id": b["thread_id"], "who": b["uid"] or b["visitor"],
                        "client": b["client"], "plan": None, "builds": [b],
                        "status": _build_status(b)})
    out.sort(key=lambda j: j["ts"], reverse=True)
    return out[:limit]
