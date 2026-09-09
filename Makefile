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
        serve-install serve-uninstall \
        bench-inputs bench-cuga-ready bench-setup bench-check \
        bench-cuga bench-react bench-claude bench-collect bench-all \
        bench-compare bench-report bench-show bench-react-test \
        bench-react-check bench-clean

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

# --- Benchmark ---------------------------------------------------------------
#
# Three hosts run the same 33 conversations and are scored by one judge
# (benchmark/verdict.py). See benchmark/BENCHMARK.md.
#
#   make bench-setup CUGA=<path>     once: install the skill where hosts read it
#   make bench-check CUGA=<path>     verify every host, run nothing  ← always first
#   make bench-all   CUGA=<path>     both automated hosts, then the comparison
#
# Two things every bench target needs:
#   PALETTE_BENCH_INPUTS   directory holding the corpus documents (not in git)
#   CUGA=<path>            a checkout whose venv has the runners' dependencies
#
# The CUGA interpreter is used throughout, including for the ReAct host: this
# repo's venv has neither CUGA nor langgraph, and the failure that produces is
# an unhelpful ModuleNotFoundError several imports deep.

BENCH_PY := $(CUGA)/.venv/bin/python
CASES_ARG = $(if $(CASES),--cases $(CASES),)

# Per-turn ceiling. The default suits the interaction cases; the corpus
# documents are up to 11KB and legitimately need longer, and a turn cut short
# while still working is recorded as a failure of the host rather than of the
# clock. Note the flag differs per runner — run.py's --timeout *is* per turn,
# while the ReAct runner separates --turn-timeout from --command-timeout.
#   make bench-react CUGA=<path> TURN_TIMEOUT=2700
REACT_TIMEOUT_ARG = $(if $(TURN_TIMEOUT),--turn-timeout $(TURN_TIMEOUT),)
CUGA_TIMEOUT_ARG  = $(if $(TURN_TIMEOUT),--timeout $(TURN_TIMEOUT),)

# One file for everything the benchmark needs. `~/.config/palette/env` is the
# file `serve-init` already creates — mode 600, and the documented home for the
# RITS key — so it is the single place. Override with PALETTE_ENV=<path>.
#
# What belongs in it:
#   RITS_API_KEY=…            CUGA's model calls
#   WATSONX_APIKEY=…          the ReAct host
#   WATSONX_URL=…
#   WATSONX_PROJECT_ID=…
#   PALETTE_BENCH_INPUTS=…    the corpus documents
#
# PALETTE_HOME is not needed here: the Makefile always passes $(PWD), which is
# right by construction and cannot go stale the way a written-down path can.
PALETTE_ENV ?= $(HOME)/.config/palette/env

# Only this file is *sourced*. CUGA's .env deliberately is not: it holds values
# a shell tries to execute (`channels:read` on one line is a Slack scope, not a
# command), so sourcing it prints errors and sets nothing. The ReAct runner
# still reads it via `--env-file`, which parses rather than evaluates, and which
# skips any variable already set — so it is a fallback, not a second source of
# truth. Put WATSONX_* here and it stops being consulted at all.
#
# Note the precedence, because it is the opposite of what dotenv usually does:
# **this file beats your shell.** `set -a; . file` assigns unconditionally, so
# `FOO=x make bench-cuga` loses to a FOO in the file. To override for one run,
# edit the file or point PALETTE_ENV somewhere else.
LOAD_ENV = set -a; [ -f "$(PALETTE_ENV)" ] && . "$(PALETTE_ENV)"; set +a;

bench-inputs: ## Check $PALETTE_BENCH_INPUTS points at a corpus (used by every bench target)
	@$(LOAD_ENV) \
	test -n "$$PALETTE_BENCH_INPUTS" || { \
	  echo "error: PALETTE_BENCH_INPUTS is not set."; \
	  echo "       It names the directory holding the corpus documents, which"; \
	  echo "       are not in this repository."; \
	  echo; \
	  echo "       Set it in $(PALETTE_ENV), or export it:"; \
	  echo "       export PALETTE_BENCH_INPUTS=/path/to/benchmark-inputs"; \
	  exit 1; }
	@$(LOAD_ENV) \
	test -d "$$PALETTE_BENCH_INPUTS" || { \
	  echo "error: PALETTE_BENCH_INPUTS=$$PALETTE_BENCH_INPUTS is not a directory"; exit 1; }
	@$(LOAD_ENV) \
	n=$$(ls -1 "$$PALETTE_BENCH_INPUTS"/*.md 2>/dev/null | wc -l | tr -d ' '); \
	  test "$$n" -gt 0 || { \
	    echo "error: no .md documents in $$PALETTE_BENCH_INPUTS"; exit 1; }; \
	  echo "corpus: $$n document(s) in $$PALETTE_BENCH_INPUTS"

bench-cuga-ready: ## Check the CUGA checkout can run a benchmark at all
	@test -x "$(BENCH_PY)" || { \
	  echo "error: no interpreter at $(BENCH_PY)"; \
	  echo "       pass CUGA=<path-to-cuga-agent-checkout>, and make sure it is installed."; \
	  exit 1; }

bench-setup: bench-cuga-ready ## Install the skill into CUGA and Claude Code, then verify
	@$(MAKE) --no-print-directory skill-install CUGA=$(CUGA)
	@$(MAKE) --no-print-directory skill-install-claude
	@echo
	@$(MAKE) --no-print-directory bench-check CUGA=$(CUGA)

bench-check: bench-inputs bench-cuga-ready ## Verify every host without running anything
	@# One shell per recipe line, so each needs its own $(LOAD_ENV) — a single
	@# one at the top would set variables that nothing after it can see.
	@echo "--- cuga ---"
	@$(LOAD_ENV) PALETTE_HOME=$(PWD) CUGA_HOME=$(CUGA) \
	  $(BENCH_PY) benchmark/run.py --check || true
	@echo "--- react ---"
	@$(LOAD_ENV) PALETTE_HOME=$(PWD) \
	  $(BENCH_PY) benchmark/react_run.py --check --env-file $(CUGA)/.env || true
	@echo "--- claude ---"
	@test -f $(HOME)/.claude/skills/palette/SKILL.md \
	  && echo "ready: skill installed at ~/.claude/skills/palette (a person types the utterances)" \
	  || echo "not ready: run \`make skill-install-claude\`"

bench-cuga: bench-inputs bench-cuga-ready ## CUGA, via its agent SDK (make bench-cuga CUGA=<path> [CASES=core])
	@$(LOAD_ENV) \
	PALETTE_HOME=$(PWD) CUGA_HOME=$(CUGA) \
	$(BENCH_PY) benchmark/run.py $(CASES_ARG) $(CUGA_TIMEOUT_ARG)

bench-react: bench-inputs bench-cuga-ready ## LangGraph ReAct on watsonx (make bench-react CUGA=<path> [CASES=core] [EAGER=1])
	@# Needs CUGA only for an interpreter with langgraph + langchain-ibm and for
	@# the .env holding WATSONX_*. It does not drive CUGA, and the skill does not
	@# need installing anywhere: it reads skills/palette out of this checkout.
	@$(LOAD_ENV) \
	PALETTE_HOME=$(PWD) \
	$(BENCH_PY) benchmark/react_run.py --env-file $(CUGA)/.env \
	  $(CASES_ARG) $(REACT_TIMEOUT_ARG) $(if $(EAGER),--eager,)

bench-react-check: bench-inputs bench-cuga-ready ## Verify only the ReAct host (no skill install needed)
	@# Separate from bench-check because that one also checks CUGA's installed
	@# copy of the skill. Working on this host alone should not require it.
	@$(LOAD_ENV) \
	PALETTE_HOME=$(PWD) \
	$(BENCH_PY) benchmark/react_run.py --check --env-file $(CUGA)/.env

bench-claude: bench-inputs bench-cuga-ready ## Claude Code: write the run sheet you paste (then bench-collect)
	@# Claude Code has no headless CLI here, so this half is prepare -> you paste
	@# -> collect. Same cases, same judge; what differs is who types.
	@$(LOAD_ENV) \
	PALETTE_HOME=$(PWD) CUGA_HOME=$(CUGA) \
	$(BENCH_PY) benchmark/claude_run.py prepare $(CASES_ARG)

bench-collect: bench-inputs bench-cuga-ready ## Claude Code: harvest the decks and judge them
	@$(LOAD_ENV) \
	PALETTE_HOME=$(PWD) CUGA_HOME=$(CUGA) \
	$(BENCH_PY) benchmark/claude_run.py collect

bench-all: bench-inputs bench-cuga-ready ## Every automated host in sequence, then the comparison
	@# Sequential on purpose: both hosts render decks, and two builds competing
	@# for the machine turns a measurement into a measurement of contention.
	@# `- ` prefixes so one failing host still leaves the other's numbers and
	@# still reaches the comparison.
	@echo "########## react ##########"
	-@$(MAKE) --no-print-directory bench-react CUGA=$(CUGA) CASES=$(CASES)
	@echo
	@echo "########## cuga ##########"
	-@$(MAKE) --no-print-directory bench-cuga CUGA=$(CUGA) CASES=$(CASES)
	@echo
	@echo "########## comparison ##########"
	@$(MAKE) --no-print-directory bench-compare CUGA=$(CUGA)

bench-compare: ## Side by side: newest run of each host, and where they disagree
	@PALETTE_BENCH_INPUTS=$${PALETTE_BENCH_INPUTS:-/nonexistent} \
	$(BENCH_PY) benchmark/compare.py

bench-report: ## Write one Markdown report across every host -> benchmark/runs/REPORT.md
	@$(LOAD_ENV) $(BENCH_PY) benchmark/report.py $(if $(RUN),--run $(RUN),)

bench-show: ## Read the newest run in depth (every call, every argument)
	@PALETTE_BENCH_INPUTS=$${PALETTE_BENCH_INPUTS:-/nonexistent} \
	$(BENCH_PY) benchmark/show.py $(if $(FAILURES),--failures,)

bench-react-test: ## The ReAct host's own tests (offline, no model, no deck)
	PALETTE_HOME=$(PWD) $(BENCH_PY) -m pytest agents/tests -q

bench-clean: ## Delete every recorded run (benchmark/runs/)
	@# Reproducible, large, and per-machine — but they are also the only copy of
	@# a result, and runs/ is gitignored. Says what it is about to remove.
	@test -d benchmark/runs || { echo "nothing to clean"; exit 0; }
	@du -sh benchmark/runs 2>/dev/null | sed 's/^/removing /'
	@rm -rf benchmark/runs
	@echo "removed benchmark/runs"

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
