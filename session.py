"""Per-conversation state.

Phase 1 holds a single deck build. Stage 3 will extend SlideSession to carry
the plan, the brief, and per-slide JS across edit turns so the refine loop
(VL critique, NL edits) can patch an existing deck.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _idle() -> dict[str, Any]:
    return {"stage": "idle", "message": "", "current": 0, "total": 0}


@dataclass
class SlideSession:
    session_id: str
    root: Path
    deck: dict[str, Any] | None = None
    pptx_path: Path | None = None
    previews: list[Path] = field(default_factory=list)
    lint: list[str] = field(default_factory=list)
    building: bool = False
    progress: dict[str, Any] = field(default_factory=_idle)
    # The FileHandler app.py installs at session creation, writing this
    # session's logs to root/session.log. Stashed so /clear can detach +
    # close it cleanly. Not part of the dataclass equality contract.
    log_handler: Any = None

    @classmethod
    def create(cls, workspace: Path, session_id: str) -> "SlideSession":
        root = workspace / session_id
        root.mkdir(parents=True, exist_ok=True)
        return cls(session_id=session_id, root=root)

    @property
    def out_dir(self) -> Path:
        return self.root

    def reset(self) -> None:
        """Clear prior build artifacts so a re-build starts from a clean dir.
        session.log is preserved -- it's history of this session, not an
        artifact of a single build."""
        for child in list(self.root.iterdir()):
            if child.name == "session.log":
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        self.deck = None
        self.pptx_path = None
        self.previews = []
        self.lint = []
        self.progress = _idle()
