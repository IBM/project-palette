#!/usr/bin/env bash
# Keep the agent skill honest.
#
# The skill is a folder (skills/palette) that tells an agent how to drive
# palette.py. Nothing is generated, so there is no staleness to check -- but
# the skill names commands and flags that live in palette.py, and those drift
# silently. `tests/test_skill.py` reads both and fails when they disagree.
set -euo pipefail

RELEVANT='^(palette\.py|skills/palette/|SKILL\.md)'
STAGED="$(git diff --cached --name-only --diff-filter=ACMR)"
grep -qE "$RELEVANT" <<<"$STAGED" || exit 0

PY="$(test -x .venv/bin/python && echo .venv/bin/python || echo python3)"
echo "› skill guard: palette.py or the skill changed, checking they still agree"

if ! "$PY" -m pytest tests/test_skill.py -q >/tmp/skill_guard.log 2>&1; then
  tail -25 /tmp/skill_guard.log
  cat <<'MSG'

  The skill and palette.py disagree.

  A command or flag the skill tells the agent to run no longer exists, or a
  new one is undocumented. Fix skills/palette/SKILL.md, then commit again.

MSG
  exit 1
fi
echo "› skill guard: ok"
