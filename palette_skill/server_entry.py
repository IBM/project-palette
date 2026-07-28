"""``palette-serve`` — run the Palette server in the foreground.

Loads the env file, locates the checkout, and hands off to the server's own
``main()``. Foreground on purpose: this is what launchd supervises, and what
``serve start`` detaches when it wants a background process.

The server modules stay top-level in the checkout rather than being packaged.
That is deliberate — ``config``, ``session``, and ``render`` are generic names,
and putting them on the import path of every process that installs the client
would collide with the host's own modules. Here they are only ever imported by
this process, which owns its interpreter.

    palette-serve                  # env file decides host, port, workspace
    palette-serve --port 18899
    PALETTE_HOME=~/code/palette palette-serve
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from palette_skill import service


def _fail(message: str) -> int:
    print(f"palette-serve: {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="palette-serve",
        description="Run the Palette server in the foreground.",
    )
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--home", default=None, help="Path to the Palette checkout")
    parser.add_argument(
        "--env-file",
        default=os.environ.get("PALETTE_ENV_FILE"),
        help="Env file to load (default: ~/.config/palette/env)",
    )
    args = parser.parse_args(argv)

    cfg = service.resolve_config(home=args.home, port=args.port, env_file=args.env_file)
    if cfg.home is None:
        return _fail(
            "no Palette checkout found. Pass --home, set PALETTE_HOME, "
            f"or add it to {cfg.env_file}."
        )

    # Export before importing the server: config.py reads PALETTE_WORKSPACE at
    # import time, and app.py reads PORT in main().
    for key, value in cfg.env.items():
        os.environ.setdefault(key, value)
    os.environ["PALETTE_WORKSPACE"] = str(cfg.workspace)
    os.environ["PORT"] = str(cfg.port)
    Path(cfg.workspace).mkdir(parents=True, exist_ok=True)

    home = str(cfg.home)
    if home not in sys.path:
        sys.path.insert(0, home)
    # The server resolves some paths relative to the process cwd.
    os.chdir(home)

    try:
        import app  # noqa: PLC0415 — path-dependent by design
    except ModuleNotFoundError as exc:
        return _fail(
            f"could not import the Palette server ({exc.name!r} is missing).\n"
            f"Install the server dependencies:  pip install -e '{home}[server]'"
        )

    if not os.environ.get("RITS_API_KEY"):
        print(
            f"palette-serve: RITS_API_KEY is not set (checked {cfg.env_file} and the environment).\n"
            "               The server will start, but every build will fail at the first model call.",
            file=sys.stderr,
        )

    # app.main() parses its own argv; PORT already carries the resolved port.
    sys.argv = [sys.argv[0]]
    app.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
