"""Local-service supervisor for the Palette web UI.

Not the agent skill. Agents drive `palette.py` directly and the skill lives in
`skills/palette/` — a folder you drop into a skills root, with no package to
install. This module only exists to start, stop and health-check the FastAPI
server behind the browser UI.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
