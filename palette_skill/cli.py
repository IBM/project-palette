"""Command-line front end for :class:`palette_skill.client.PaletteClient`.

Every subcommand prints a single JSON object to stdout and nothing else, so a
caller can ``json.loads`` the output without parsing around log noise. Errors
print ``{"error": ...}`` and exit non-zero.

Agent hosts that execute Python directly should import ``PaletteClient``
instead — this exists for hosts whose only primitive is a shell:

    palette-skill health
    palette-skill draft --request "Deck on RAG for backend engineers"
    palette-skill start-build --plan-file plan.md
    palette-skill wait --thread-id skill-ab12 --max-seconds 25
    palette-skill download --thread-id skill-ab12 --dest ./deck.pptx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from palette_skill import __version__, contract
from palette_skill.client import PaletteClient, PaletteError, Progress


def _emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _plan_text(args: argparse.Namespace) -> str:
    if getattr(args, "plan_file", None):
        return Path(args.plan_file).read_text(encoding="utf-8")
    if getattr(args, "plan", None):
        return args.plan
    raise PaletteError("provide --plan or --plan-file")


def _model_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--palette-family", default="ibm_watsonx")
    parser.add_argument("--planner", default="gpt-oss-120b")
    parser.add_argument("--designer-coder", default="palette-lora")
    parser.add_argument(
        "--critic",
        default="gpt-oss-120b",
        help="Geometry-repair (editor) model; named 'critic' for API compatibility.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="palette-skill",
        description="Drive the Palette deck builder over HTTP.",
    )
    parser.add_argument("--version", action="version", version=f"palette-skill {__version__}")
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"Palette server root (default: ${contract.BASE_URL_ENV} or {contract.DEFAULT_BASE_URL})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Server liveness, model roster, RITS key status")
    sub.add_parser("capabilities", help="Route templates the live server advertises")
    sub.add_parser("examples", help="List curated starter plans")

    p = sub.add_parser("serve", help="Manage a local Palette service (host-side, not for sandboxes)")
    p.add_argument(
        "action",
        choices=["status", "start", "stop", "restart", "ensure", "logs", "doctor", "init", "install", "uninstall"],
        help=(
            "status: is it up | start/stop/restart: manage it | ensure: start if down and wait | "
            "logs: tail | doctor: preflight checks | init: write the env file | "
            "install/uninstall: launchd service"
        ),
    )
    p.add_argument("--mode", choices=["process", "container", "launchd"], default=None,
                   help="Backend to use (default: container if an image exists, else process)")
    p.add_argument("--home", default=None, help="Path to the Palette checkout")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--env-file", default=None, help="Default: ~/.config/palette/env")
    p.add_argument("--timeout", type=float, default=90.0, help="Seconds to wait for health on ensure")
    p.add_argument("--lines", type=int, default=50, help="Log lines for the logs action")
    p.add_argument("--force", action="store_true", help="Overwrite an existing env file on init")

    p = sub.add_parser("example", help="Fetch one curated plan as markdown")
    p.add_argument("--name", required=True)
    p.add_argument("--dest", default=None, help="Write the markdown here instead of stdout")

    p = sub.add_parser("draft", help="Stage 1 — request to editable plan")
    p.add_argument("--request", required=True)
    p.add_argument("--thread-id", default=None)
    p.add_argument("--planner", default="gpt-oss-120b")
    p.add_argument("--file", dest="files", action="append", default=[], help="Reference doc; repeatable")
    p.add_argument("--dest", default=None, help="Write the plan markdown here")

    p = sub.add_parser("start-draft", help="Start Stage 1 in the background and return immediately")
    p.add_argument("--request", required=True)
    p.add_argument("--thread-id", default=None)
    p.add_argument("--planner", default="gpt-oss-120b")
    p.add_argument("--file", dest="files", action="append", default=[], help="Reference doc; repeatable")

    p = sub.add_parser("wait-draft", help="Poll a background draft until it ends or --max-seconds elapses")
    p.add_argument("--thread-id", required=True)
    p.add_argument("--max-seconds", type=float, default=25.0)
    p.add_argument("--poll-interval", type=float, default=2.0)

    p = sub.add_parser("draft-result", help="Plan markdown from a finished background draft")
    p.add_argument("--thread-id", required=True)
    p.add_argument("--dest", default=None, help="Write the plan markdown here")

    p = sub.add_parser(
        "deck",
        help="Make a deck, resumably. Call repeatedly with the same --dest until done.",
    )
    p.add_argument("--request", help="What the deck is about (drafts a plan first)")
    p.add_argument("--plan-file", help="Existing plan markdown; skips drafting")
    p.add_argument("--dest", default="./deck", help="Where the deck and previews land")
    p.add_argument("--max-seconds", type=float, default=25.0)
    p.add_argument(
        "--pause-after-plan",
        action="store_true",
        help="Stop once the plan is drafted so the user can approve it before the build",
    )
    p.add_argument(
        "--approve",
        action="store_true",
        help="Approve the paused plan (as it now stands in <dest>/plan.md) and build it",
    )

    p = sub.add_parser("build", help="Build a deck and block until it is rendered")
    p.add_argument("--plan")
    p.add_argument("--plan-file")
    p.add_argument("--thread-id", default=None)
    p.add_argument("--max-seconds", type=float, default=float(contract.BUILD.budget_seconds))
    _model_args(p)

    p = sub.add_parser("start-build", help="Start a background build and return immediately")
    p.add_argument("--plan")
    p.add_argument("--plan-file")
    p.add_argument("--thread-id", default=None)
    _model_args(p)

    p = sub.add_parser("progress", help="One progress snapshot")
    p.add_argument("--thread-id", required=True)

    p = sub.add_parser("wait", help="Poll until the build ends or --max-seconds elapses")
    p.add_argument("--thread-id", required=True)
    p.add_argument("--max-seconds", type=float, default=25.0)
    p.add_argument("--poll-interval", type=float, default=2.0)

    p = sub.add_parser("result", help="Terminal outcome of a background build")
    p.add_argument("--thread-id", required=True)

    p = sub.add_parser("deck-status", help="Slide count, title, and build state")
    p.add_argument("--thread-id", required=True)

    p = sub.add_parser("edit", help="Apply an instruction to one slide")
    p.add_argument("--thread-id", required=True)
    p.add_argument("--slide", type=int, required=True)
    p.add_argument("--instruction", required=True)

    p = sub.add_parser("retry", help="Re-roll one slide")
    p.add_argument("--thread-id", required=True)
    p.add_argument("--slide", type=int, required=True)

    p = sub.add_parser("download", help="Save the .pptx")
    p.add_argument("--thread-id", required=True)
    p.add_argument("--dest", default="deck.pptx")

    p = sub.add_parser("previews", help="Save every slide PNG into a directory")
    p.add_argument("--thread-id", required=True)
    p.add_argument("--dest-dir", default=".")

    p = sub.add_parser("clear", help="Drop a session and its workspace")
    p.add_argument("--thread-id", required=True)

    p = sub.add_parser("abort", help="Mark a session not-building")
    p.add_argument("--thread-id", required=True)

    return parser


def _dispatch_serve(args: argparse.Namespace) -> Any:
    """Local service management. Host-side only — see palette_skill.service."""
    from palette_skill import service

    cfg = service.resolve_config(home=args.home, port=args.port, env_file=args.env_file)
    action = args.action

    if action == "status":
        return service.status(cfg)
    if action == "doctor":
        return {"env_file": str(cfg.env_file), "home": str(cfg.home or ""), **service.doctor(cfg)}
    if action == "init":
        return service.init_env_file(cfg, force=args.force)
    if action == "start":
        return {**service.start(cfg, args.mode), "url": cfg.url}
    if action == "stop":
        return service.stop(cfg)
    if action == "restart":
        service.stop(cfg)
        return {**service.start(cfg, args.mode), "url": cfg.url}
    if action == "ensure":
        return service.ensure(cfg, mode=args.mode, timeout=args.timeout)
    if action == "logs":
        return {"log": str(cfg.log_file), "tail": service.read_logs(cfg, lines=args.lines)}
    if action == "install":
        return service.install_launchd(cfg)
    if action == "uninstall":
        return service.uninstall_launchd(cfg)
    raise PaletteError(f"unknown serve action {action!r}")


def _dispatch(args: argparse.Namespace, pal: PaletteClient) -> Any:
    command = args.command

    if command == "serve":
        return _dispatch_serve(args)

    if command == "health":
        return pal.health()
    if command == "capabilities":
        caps = sorted(pal.capabilities())
        return {
            "base_url": pal.base_url,
            "paths": caps,
            "background_build": pal.supports("build_async"),
        }
    if command == "examples":
        return {"examples": pal.examples()}
    if command == "example":
        content = pal.example(args.name)
        if args.dest:
            Path(args.dest).write_text(content, encoding="utf-8")
            return {"name": args.name, "path": str(Path(args.dest).resolve()), "chars": len(content)}
        return {"name": args.name, "content": content}

    if command == "draft":
        plan = pal.draft(
            args.request,
            thread_id=args.thread_id,
            planner=args.planner,
            files=args.files,
        )
        out: dict[str, Any] = {"chars": len(plan)}
        if args.dest:
            Path(args.dest).write_text(plan, encoding="utf-8")
            out["path"] = str(Path(args.dest).resolve())
        else:
            out["plan"] = plan
        return out

    if command == "start-draft":
        thread_id = pal.start_draft(
            args.request, thread_id=args.thread_id, planner=args.planner, files=args.files
        )
        return {"started": True, "thread_id": thread_id}

    if command == "wait-draft":
        snapshot = pal.wait_draft(
            args.thread_id, max_seconds=args.max_seconds, poll_interval=args.poll_interval
        )
        payload = _progress_json(snapshot)
        payload["terminal"] = snapshot.stage in contract.DRAFT_TERMINAL_STAGES
        return payload

    if command == "draft-result":
        plan = pal.draft_result(args.thread_id)
        out: dict[str, Any] = {"chars": len(plan)}
        if args.dest:
            Path(args.dest).write_text(plan, encoding="utf-8")
            out["path"] = str(Path(args.dest).resolve())
        else:
            out["plan"] = plan
        return out

    if command == "deck":
        from palette_skill.client import run_deck

        return run_deck(
            pal,
            dest=args.dest,
            request=args.request,
            plan_file=args.plan_file,
            max_seconds=args.max_seconds,
            pause_after_plan=args.pause_after_plan,
            approve=args.approve,
        )

    if command == "build":
        outcome = pal.build_and_wait(
            _plan_text(args),
            thread_id=args.thread_id,
            palette_family=args.palette_family,
            planner=args.planner,
            designer_coder=args.designer_coder,
            critic=args.critic,
            on_progress=lambda p: print(f"# {p}", file=sys.stderr),
            max_seconds=args.max_seconds,
        )
        return {"thread_id": outcome.thread_id, **outcome.raw}

    if command == "start-build":
        thread_id = pal.start_build(
            _plan_text(args),
            thread_id=args.thread_id,
            palette_family=args.palette_family,
            planner=args.planner,
            designer_coder=args.designer_coder,
            critic=args.critic,
        )
        return {"started": True, "thread_id": thread_id}

    if command == "progress":
        return _progress_json(pal.progress(args.thread_id))
    if command == "wait":
        snapshot = pal.wait(
            args.thread_id,
            max_seconds=args.max_seconds,
            poll_interval=args.poll_interval,
        )
        return _progress_json(snapshot)
    if command == "result":
        outcome = pal.result(args.thread_id)
        return {"thread_id": outcome.thread_id, **outcome.raw}
    if command == "deck-status":
        return pal.deck(args.thread_id)

    if command == "edit":
        return pal.edit(args.thread_id, args.slide, args.instruction)
    if command == "retry":
        return pal.retry(args.thread_id, args.slide)

    if command == "download":
        path = pal.download(args.thread_id, args.dest)
        return {"path": str(path.resolve()), "bytes": path.stat().st_size}
    if command == "previews":
        paths = pal.previews(args.thread_id, args.dest_dir)
        return {"count": len(paths), "paths": [str(p.resolve()) for p in paths]}

    if command == "clear":
        return {"cleared": pal.clear(args.thread_id)}
    if command == "abort":
        return {"aborted": pal.abort(args.thread_id)}

    raise PaletteError(f"unknown command {command!r}")


def _progress_json(snapshot: Progress) -> dict[str, Any]:
    return {
        "stage": snapshot.stage,
        "message": snapshot.message,
        "current": snapshot.current,
        "total": snapshot.total,
        "terminal": snapshot.terminal,
        "failed": snapshot.failed,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pal = PaletteClient(args.base_url)
    try:
        _emit(_dispatch(args, pal))
    except PaletteError as exc:
        _emit({"error": str(exc), "type": type(exc).__name__})
        return 1
    except OSError as exc:
        _emit({"error": str(exc), "type": type(exc).__name__})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
