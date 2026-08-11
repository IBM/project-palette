# Palette — common dev + deploy targets.
# Run `make help` for the menu.
#
# Code Engine targets shell out to scripts in deployment/; they read
# config from deployment/config.sh (override via env vars — see
# deployment/DEPLOYMENT.md).

.PHONY: help install dev docker-build docker-run clean \
        ce-build ce-push ce-buildpush ce-deploy ce-release \
        skill-test skill-install clean-state distclean hooks \
        skill-install-claude skill-package \
        serve-init serve-doctor serve-start serve-stop serve-status serve-logs \
        serve-install serve-uninstall

PORT ?= 18814

# Agent project root the skill installs into. Override per invocation:
#   make skill-install CUGA=~/code/some-other-agent
CUGA ?= ../cuga-agent-july25

# Python used for skill tooling. Falls back to the repo venv when present.
PY ?= $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)

# Agent host to install/uninstall for: cuga | claude-code | generic.
HOST ?= cuga

# Service state (workspace, logs, pid) and config. The config directory holds
# your RITS key and is never removed by any target here — it is the one thing
# on the machine that cannot be rebuilt from source. `distclean PURGE_CONFIG=1`
# is the explicit opt-out, and it backs the file up before removing it.
STATE_DIR ?= $(HOME)/.local/state/palette
CONFIG_DIR ?= $(HOME)/.config/palette

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install Python + Node deps into .venv (creates it if missing)
	@# Three things this has to get right, each of which broke it before:
	@#  1. `uv venv` makes a venv with NO pip, so bare `pip` here resolves to
	@#     whatever is on PATH — Homebrew's, or another project's active venv —
	@#     and installs Palette's dependencies somewhere else entirely.
	@#  2. `distclean` deletes .venv, and `make install` is what the docs tell
	@#     you to run next, so it must cope with no venv at all.
	@#  3. `-r requirements.txt` alone never installs *this package*, so the
	@#     `make serve-*` targets lose their supervisor module.
	@test -x .venv/bin/python || uv venv
	uv pip install --python .venv/bin/python -e '.[dev]'
	npm install
	@echo
	@echo "installed. activate with:  source .venv/bin/activate"
	@# Verify the two things the docs actually tell you to run. This used to
	@# check a console script that had been deleted, so `make install` failed
	@# at the last line having installed everything correctly.
	@$(PY) palette.py --help >/dev/null && echo "ok: palette.py"
	@$(PY) skills/palette/scripts/deck.py --help >/dev/null && echo "ok: the skill's deck.py"

dev: ## Run the server on http://localhost:$(PORT)
	$(PY) app.py --port $(PORT)

# --- Local Docker (native arch) ---

docker-build: ## Build the container image for your local arch
	docker build -t palette .

docker-run: ## Run the local container, mapping $(PORT) -> 8080
	docker run --rm -p $(PORT):8080 -e RITS_API_KEY=$$RITS_API_KEY palette

# --- IBM Code Engine (linux/amd64) ---

ce-build: ## Cross-build linux/amd64 image and load it locally
	./deployment/build.sh

ce-push: ## Push the built image to IBM Container Registry
	./deployment/push.sh

ce-buildpush: ## Build linux/amd64 and push to ICR in one step
	./deployment/buildpush-ce.sh

ce-deploy: ## Create or update the Code Engine application
	./deployment/deploy.sh

ce-release: ce-buildpush ce-deploy ## Build, push, and deploy in sequence

# --- Local service ---
# Run Palette as a background service on this machine. `serve-start` picks the
# container image when one exists and a detached local process otherwise;
# `serve-install` adds a launchd agent that survives login. The RITS key lives
# in ~/.config/palette/env, never in the plist or your shell history.

serve-init: ## Write ~/.config/palette/env (add your RITS_API_KEY there)
	$(PY) -m palette_skill.serve_cli init

serve-doctor: ## Check what a local build needs, per mode
	$(PY) -m palette_skill.serve_cli doctor

serve-start: ## Start the service and wait until it answers /health
	$(PY) -m palette_skill.serve_cli ensure

serve-stop: ## Stop the service (process and container)
	$(PY) -m palette_skill.serve_cli stop

serve-status: ## Is it up, in which mode, with which workspace
	$(PY) -m palette_skill.serve_cli status

serve-logs: ## Tail the service log
	$(PY) -m palette_skill.serve_cli logs

serve-install: ## Install the launchd agent (starts at login, restarts on crash)
	$(PY) -m palette_skill.serve_cli install

serve-uninstall: ## Remove the launchd agent
	$(PY) -m palette_skill.serve_cli uninstall

# --- Agent skill (skills.sh style: a folder you drop into a skills root) ---

SKILL_SRC := skills/palette

skill-install: ## Install the skill into an agent (make skill-install CUGA=<path>)
	@# A skill is a folder. No build step, no wheel, no generated content --
	@# copy it where the host scans and it is installed. Same as `npx skills add`.
	@test -f $(SKILL_SRC)/SKILL.md || { echo "missing $(SKILL_SRC)/SKILL.md"; exit 1; }
	@mkdir -p $(CUGA)/.cuga/skills
	@find $(SKILL_SRC) -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@rm -rf $(CUGA)/.cuga/skills/palette
	@cp -R $(SKILL_SRC) $(CUGA)/.cuga/skills/palette
	@echo "installed -> $(CUGA)/.cuga/skills/palette"
	@echo "the skill runs palette.py from \$$PALETTE_HOME; export it to $(PWD)"

skill-install-claude: ## Install into Claude Code (~/.claude/skills/palette)
	@mkdir -p $(HOME)/.claude/skills
	@find $(SKILL_SRC) -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@rm -rf $(HOME)/.claude/skills/palette
	@cp -R $(SKILL_SRC) $(HOME)/.claude/skills/palette
	@echo "installed -> $(HOME)/.claude/skills/palette"

skill-package: ## Package the skill as dist/palette-skill.tar.gz (droppable into any skills root)
	@mkdir -p dist
	@find skills -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	@tar czf dist/palette-skill.tar.gz --exclude __pycache__ --exclude '*.pyc' -C skills palette
	@echo "dist/palette-skill.tar.gz  ($$(du -h dist/palette-skill.tar.gz | cut -f1))"
	@echo "consume:  tar xzf palette-skill.tar.gz -C <skills-root>/"

skill-test: ## Check the skill is self-consistent (no server, no network)
	$(PY) -m pytest tests/test_skill.py -q

bench: ## Run the CUGA benchmark (make bench CUGA=<path> [CASES=core])
	@# Runs under CUGA's interpreter, not ours: the harness drives CUGA and
	@# needs its dependencies. Everything else it needs is checked by --check.
	PALETTE_HOME=$(PWD) CUGA_HOME=$(CUGA) \
	$(CUGA)/.venv/bin/python benchmark/run.py $(if $(CASES),--cases $(CASES),)

bench-claude: ## Prepare the Claude Code run sheet (make bench-claude CUGA=<path> [CASES=corpus])
	@# Claude Code has no headless CLI here, so this prepares one directory per
	@# case and prints what to paste; `bench-collect` harvests and judges them
	@# with exactly the rules the CUGA runner uses.
	PALETTE_HOME=$(PWD) CUGA_HOME=$(CUGA) \
	$(CUGA)/.venv/bin/python benchmark/claude_run.py prepare $(if $(CASES),--cases $(CASES),)

bench-collect: ## Harvest and judge the Claude Code decks
	PALETTE_HOME=$(PWD) CUGA_HOME=$(CUGA) \
	$(CUGA)/.venv/bin/python benchmark/claude_run.py collect

bench-check: ## Verify the benchmark setup without running anything
	PALETTE_HOME=$(PWD) CUGA_HOME=$(CUGA) \
	$(CUGA)/.venv/bin/python benchmark/run.py --check

verify: ## Check the skill where agents read it (make verify CUGA=<path> [DECK=1])
	@# Takes the three locations as input: this checkout, a CUGA checkout, and
	@# any other skills roots. Skips by name for anything it was not given, so
	@# `make verify` alone still checks Claude Code's copy.
	@#
	@# DECK=1 additionally builds a real deck. Minutes, and needs the VPN.
	PALETTE_HOME=$(PWD) \
	CUGA_HOME=$(CUGA) \
	SKILLS_ROOTS=$(if $(SKILLS_ROOTS),$(SKILLS_ROOTS),$(HOME)/.claude/skills) \
	PALETTE_VERIFY_DECK=$(if $(DECK),1,) \
	$(PY) -m pytest tests/test_installed.py -q



hooks: ## Install the pre-commit guard (blocks commits that leave the skill stale)
	@mkdir -p .git/hooks
	@ln -sf ../../scripts/pre-commit-skill.sh .git/hooks/pre-commit
	@echo "installed .git/hooks/pre-commit -> scripts/pre-commit-skill.sh"

# --- Housekeeping ---

clean: ## Remove pycache + workspace artifacts (preserves source)
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf workspace

clean-state: ## Stop the service and delete its state (workspace, logs, pid)
	-$(PY) -m palette_skill.serve_cli stop >/dev/null 2>&1 || true
	rm -rf $(STATE_DIR)
	@echo "removed $(STATE_DIR)"

distclean: clean clean-state ## Full clean-room: uninstall skills, drop .venv/node_modules
	-$(MAKE) --no-print-directory skill-uninstall HOST=cuga CUGA=$(CUGA) || true
	-$(MAKE) --no-print-directory skill-uninstall HOST=claude-code || true
	rm -rf .venv node_modules build dist *.egg-info
ifeq ($(PURGE_CONFIG),1)
	@if [ -f $(CONFIG_DIR)/env ]; then \
	  cp $(CONFIG_DIR)/env /tmp/palette-env.backup.$$(date +%s); \
	  echo "backed up $(CONFIG_DIR)/env -> /tmp/palette-env.backup.*"; \
	  rm -rf $(CONFIG_DIR); \
	  echo "removed $(CONFIG_DIR)"; \
	fi
else
	@test -f $(CONFIG_DIR)/env \
	  && echo "kept $(CONFIG_DIR)/env (holds your RITS key) — PURGE_CONFIG=1 to remove it too" \
	  || true
endif
	@echo ""
	@echo "clean. rebuild with:  make install     (creates .venv, installs .[dev] + npm)"
