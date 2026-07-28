# Palette — common dev + deploy targets.
# Run `make help` for the menu.
#
# Code Engine targets shell out to scripts in deployment/; they read
# config from deployment/config.sh (override via env vars — see
# deployment/DEPLOYMENT.md).

.PHONY: help install dev docker-build docker-run clean \
        ce-build ce-push ce-buildpush ce-deploy ce-release \
        skill skill-build skill-check skill-test skill-install skill-status \
        skill-uninstall clean-state distclean release hooks \
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
	@#     `palette-skill` console script is missing and every documented
	@#     command fails with "command not found".
	@test -x .venv/bin/python || uv venv
	uv pip install --python .venv/bin/python -e '.[dev]'
	npm install
	@echo
	@echo "installed. activate with:  source .venv/bin/activate"
	@.venv/bin/palette-skill --version

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
	$(PY) -m palette_skill.cli serve init

serve-doctor: ## Check what a local build needs, per mode
	$(PY) -m palette_skill.cli serve doctor

serve-start: ## Start the service and wait until it answers /health
	$(PY) -m palette_skill.cli serve ensure

serve-stop: ## Stop the service (process and container)
	$(PY) -m palette_skill.cli serve stop

serve-status: ## Is it up, in which mode, with which workspace
	$(PY) -m palette_skill.cli serve status

serve-logs: ## Tail the service log
	$(PY) -m palette_skill.cli serve logs

serve-install: ## Install the launchd agent (starts at login, restarts on crash)
	$(PY) -m palette_skill.cli serve install

serve-uninstall: ## Remove the launchd agent
	$(PY) -m palette_skill.cli serve uninstall

# --- Agent skill ---
# Palette is exposed to agents as a skill: a generated SKILL.md plus a
# dependency-light HTTP client. Both are produced from this repo, never hand-
# authored on the agent side, so the two cannot drift apart silently.

skill-build: ## Regenerate SKILL.md / reference.md from contract.py + config.py
	$(PY) -m palette_skill.build_skill

skill-check: ## Fail if the generated skill content is stale
	$(PY) -m palette_skill.build_skill --check

skill-test: ## Assert the skill still matches app.py and config.py
	$(PY) -m pytest tests/ -q

skill-install: skill-build ## Install the skill into an agent (make skill-install CUGA=<path>)
	$(PY) -m palette_skill.install --into $(CUGA)

skill-status: ## Report drift between this repo and an installed skill
	$(PY) -m palette_skill.install --into $(CUGA) --check

skill-uninstall: ## Remove an installed skill (HOST=cuga|claude-code, CUGA=<path>)
	$(PY) -m palette_skill.install --uninstall --host $(HOST) --into $(CUGA)

release: skill ## Build artifacts into dist/. VERSION=X.Y.Z cuts a real release.
	@# No VERSION: a throwaway local build, overwrites freely — the dev loop.
	@# With VERSION: writes __version__, demands a clean tree, and refuses to
	@# reuse a version already in dist/. The version is in every artifact's
	@# filename, so two releases sharing one are indistinguishable to whoever
	@# you hand them to.
	$(PY) -m palette_skill.release \
	  $(if $(VERSION),--version $(VERSION),) \
	  $(if $(BASE_URL),--base-url $(BASE_URL),)

skill: skill-check skill-test ## Verify the skill is current and correct

hooks: ## Install the pre-commit guard (blocks commits that leave the skill stale)
	@mkdir -p .git/hooks
	@ln -sf ../../scripts/pre-commit-skill.sh .git/hooks/pre-commit
	@echo "installed .git/hooks/pre-commit -> scripts/pre-commit-skill.sh"

# --- Housekeeping ---

clean: ## Remove pycache + workspace artifacts (preserves source)
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf workspace

clean-state: ## Stop the service and delete its state (workspace, logs, pid)
	-$(PY) -m palette_skill.cli serve stop >/dev/null 2>&1 || true
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
