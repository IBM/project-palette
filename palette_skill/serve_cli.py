"""`python -m palette_skill.serve_cli <action>` — manage the local Palette server.

This supervises the FastAPI app behind the web UI. It is **not** part of the
agent skill: agents drive `palette.py` directly, and the skill is a folder
under `skills/palette`. The two were once the same thing, back when the skill
spoke HTTP to a running server.

    init | doctor | status | start | ensure | stop | restart | logs
    install | uninstall        (launchd agent)
"""

from __future__ import annotations

import argparse
import json
import sys

from palette_skill import service

ACTIONS = (
    "init", "doctor", "status", "start", "ensure", "stop",
    "restart", "logs", "install", "uninstall",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--mode", default=None, choices=["process", "container", "launchd"])
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="init: overwrite an existing env file")
    args = parser.parse_args(argv)

    cfg = service.resolve_config(port=args.port)
    try:
        if args.action == "logs":
            return service.tail_logs(cfg)
        handler = {
            "init": lambda: service.init_env_file(cfg, force=args.force),
            "doctor": lambda: service.doctor(cfg),
            "status": lambda: service.status(cfg),
            "start": lambda: service.start(cfg, mode=args.mode),
            "ensure": lambda: service.ensure(cfg, mode=args.mode),
            "stop": lambda: service.stop(cfg),
            "restart": lambda: service.restart(cfg, mode=args.mode),
            "install": lambda: service.install_agent(cfg),
            "uninstall": lambda: service.uninstall_agent(cfg),
        }[args.action]
        print(json.dumps(handler(), indent=2, default=str))
        return 0
    except service.ServiceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
