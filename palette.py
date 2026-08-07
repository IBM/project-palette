#!/usr/bin/env python
"""
palette.py — importable/CLI interface to the Palette harness.

A stateless layer over the pipeline functions so an agent or script can drive
Palette without the FastAPI server. The web UI (app.py) is untouched.

    from palette import build_plan
    plan = build_plan("Build me a 5-slide deck on RAG")

    $ python palette.py build-plan "Build me a 5-slide deck on RAG"

Requires the harness env (RITS model access via RITS_API_KEY).
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import config
from intake import craft_plan
from intake import edit_plan as _core_edit_plan
from pipeline import generate_deck


# ---------------------------------------------------------------------------
# build_plan  —  user request (+ optional sources) -> markdown plan
# ---------------------------------------------------------------------------
def build_plan(request: str, context: str | None = None,
               sources: list[str] | None = None, *,
               planner: str | None = None) -> str:
    """Turn a request into a plan (markdown).

    context : pasted material to build the deck from — grounded and shaped into
              slides, not copied verbatim. The path for chat hosts with no
              upload; routed to the grounded source path via a temp file.
    sources : grounding document file paths (.md .txt .pdf .docx .pptx).
    planner : optional crafter-model override.
    """
    if not request or not request.strip():
        raise ValueError("request is empty")
    if planner:
        config.apply_models(planner=planner)

    src_paths = [Path(s) for s in (sources or [])]
    tmp: Path | None = None
    try:
        if context and context.strip():
            # Route pasted context through the grounded source path: write it to
            # a temp .txt (a .txt never trips transcribe mode, so the crafter
            # shapes it into a plan rather than echoing it back).
            fd = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", prefix="palette_context_",
                delete=False, encoding="utf-8")
            fd.write(context)
            fd.close()
            tmp = Path(fd.name)
            src_paths.append(tmp)
        return craft_plan(request.strip(), src_paths or None)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# edit_plan  —  existing plan + change instruction -> revised plan
# ---------------------------------------------------------------------------
def edit_plan(plan_md: str, instruction: str, *,
              planner: str | None = None) -> str:
    """Apply a change to an existing plan; return the full revised plan.

    Surgical — only the requested change is applied, the rest is preserved, and
    no facts are invented beyond the plan and the instruction.
    """
    if planner:
        config.apply_models(planner=planner)
    return _core_edit_plan(plan_md, instruction)


# ---------------------------------------------------------------------------
# build_deck  —  plan -> rendered deck (deck.json + deck.pptx)
# ---------------------------------------------------------------------------
def build_deck(plan_md: str, out_dir: str, *,
               palette_family: str = "ibm_watsonx", deck_id: str = "deck",
               designer_coder: str | None = None,
               correction: str | None = None) -> dict:
    """Turn a plan into a rendered deck: write deck.json + deck.pptx (+ preview
    PNGs) into out_dir. Runs the full pipeline (designer -> coder -> render ->
    repair); rendering is required, as the repair passes depend on it.

    palette_family : visual style (default ibm_watsonx).
    designer_coder / correction : optional model-name overrides.

    Returns {deck, pptx, previews, n_slides, lint} (paths as strings).
    """
    if not plan_md or not plan_md.strip():
        raise ValueError("plan is empty")
    if designer_coder or correction:
        config.apply_models(designer_coder=designer_coder or "",
                            correction=correction or "")
    result = generate_deck(plan_md, out_dir, deck_id=deck_id,
                           palette_family=palette_family)
    deck = result["deck"]
    return {
        "deck": deck,
        "pptx": str(result["pptx"]),
        "previews": [str(p) for p in result["previews"]],
        "n_slides": len(deck.get("slides", [])),
        "lint": result["lint"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _read_request(arg: str | None) -> str:
    """Positional request, or stdin when the arg is '-' or omitted."""
    if arg and arg != "-":
        return arg
    return sys.stdin.read()


def _read_plan(arg: str) -> str:
    """Plan text from a file path, or stdin when the arg is '-'."""
    return sys.stdin.read() if arg == "-" else Path(arg).read_text()


def _emit(plan: str, args) -> int:
    """Shared output handling for build-plan / edit-plan."""
    if args.out:
        Path(args.out).write_text(plan)
    if args.json:
        print(json.dumps({"ok": True, "plan": plan, "out": args.out}))
    elif not args.out:
        print(plan)                       # raw markdown to stdout (pipeable)
    else:
        print(f"wrote plan -> {args.out}", file=sys.stderr)
    return 0


def _cmd_build_plan(args) -> int:
    try:
        plan = build_plan(_read_request(args.request), context=args.context,
                          sources=args.source, planner=args.planner)
    except Exception as exc:  # surface a clean, parseable failure
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    return _emit(plan, args)


def _cmd_edit_plan(args) -> int:
    try:
        plan = edit_plan(_read_plan(args.plan), args.instruction,
                         planner=args.planner)
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    return _emit(plan, args)


def _cmd_build_deck(args) -> int:
    try:
        res = build_deck(_read_plan(args.plan), args.out_dir,
                         palette_family=args.palette_family, deck_id=args.deck_id,
                         designer_coder=args.designer_coder,
                         correction=args.correction)
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, **res}))
    else:
        print(res["pptx"])                # pptx path to stdout (capturable)
        print(f"built {res['n_slides']}-slide deck -> {res['pptx']}",
              file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="palette",
                                 description="High-level CLI over the Palette harness.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    bp = sub.add_parser("build-plan",
                        help="user request (+optional sources) -> markdown plan")
    bp.add_argument("request", nargs="?",
                    help="the request string; use '-' or omit to read from stdin")
    bp.add_argument("--context", metavar="TEXT",
                    help="pasted presentation context — material to build the "
                         "deck from (grounded, shaped into slides, not verbatim)")
    bp.add_argument("--source", action="append", metavar="PATH",
                    help="optional grounding document file (repeatable)")
    bp.add_argument("--planner", metavar="MODEL",
                    help="override the crafter model (default: config.ROSTER)")
    bp.add_argument("--out", metavar="PATH", help="write plan to file (default: stdout)")
    bp.add_argument("--json", action="store_true",
                    help="emit a JSON envelope instead of raw markdown")
    bp.set_defaults(func=_cmd_build_plan)

    ep = sub.add_parser("edit-plan",
                        help="existing plan + change instruction -> revised plan")
    ep.add_argument("instruction", help="the change to apply, e.g. 'make it 3 slides'")
    ep.add_argument("--plan", required=True, metavar="PATH",
                    help="path to the plan.md to edit; use '-' to read from stdin")
    ep.add_argument("--planner", metavar="MODEL",
                    help="override the crafter model (default: config.ROSTER)")
    ep.add_argument("--out", metavar="PATH", help="write revised plan to file (default: stdout)")
    ep.add_argument("--json", action="store_true",
                    help="emit a JSON envelope instead of raw markdown")
    ep.set_defaults(func=_cmd_edit_plan)

    bd = sub.add_parser("build-deck",
                        help="plan -> rendered deck (writes deck.json + deck.pptx)")
    bd.add_argument("--plan", required=True, metavar="PATH",
                    help="path to the plan.md; use '-' to read from stdin")
    bd.add_argument("--out-dir", required=True, metavar="DIR",
                    help="directory to write deck.json + deck.pptx + previews")
    bd.add_argument("--palette-family", default="ibm_watsonx", metavar="FAMILY",
                    help="visual style (default: ibm_watsonx)")
    bd.add_argument("--deck-id", default="deck", metavar="ID",
                    help="id stamped into the deck (default: deck)")
    bd.add_argument("--designer-coder", metavar="MODEL",
                    help="override the designer/coder model")
    bd.add_argument("--correction", metavar="MODEL",
                    help="override the editor/correction model")
    bd.add_argument("--json", action="store_true",
                    help="emit a JSON envelope (default: print pptx path)")
    bd.set_defaults(func=_cmd_build_deck)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
