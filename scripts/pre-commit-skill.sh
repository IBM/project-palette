#!/usr/bin/env bash
# Refuse a commit that moves Palette without moving the skill with it.
#
# Installed by `make hooks`. Only runs when something the skill is generated
# from is staged, so commits that touch the renderer or the prompts pay
# nothing. Checks the skill *source* is self-consistent; shipping it to an
# agent stays a deliberate `make skill-install`.

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

# Files the skill is derived from. contract.py and payload/ are the skill
# itself; app.py, config.py and requirements.txt are what it describes.
RELEVANT='^(app\.py|config\.py|session\.py|requirements\.txt|pyproject\.toml|palette_skill/)'

STAGED="$(git diff --cached --name-only --diff-filter=ACMR)"
if ! grep -qE "$RELEVANT" <<<"$STAGED"; then
  exit 0
fi

PY="$(test -x .venv/bin/python && echo .venv/bin/python || echo python3)"

echo "› skill guard: Palette source changed, checking the skill still matches"

if ! "$PY" -m palette_skill.build_skill --check >/dev/null 2>&1; then
  "$PY" -m palette_skill.build_skill --check || true
  cat <<'MSG'

  The generated parts of SKILL.md are stale.

      make skill-build && git add palette_skill/payload

MSG
  exit 1
fi

if ! "$PY" -m pytest tests/test_skill_contract.py -q >/tmp/skill_guard.log 2>&1; then
  tail -25 /tmp/skill_guard.log
  cat <<'MSG'

  The skill no longer describes this server.

  A route, a request field, or a model menu moved. Update
  palette_skill/contract.py to match, then:

      make skill-build && git add palette_skill

MSG
  exit 1
fi

echo "› skill guard: ok"
