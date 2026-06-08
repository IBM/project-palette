"""Palette configuration — paths and the per-role model roster.

The harness calls five model ROLES. Each is independently pointed at a
RITS-served model, so swapping one is a one-line change here. In particular,
when the fine-tuned palette adapter is live on RITS, point "designer" and
"coder" at PALETTE_ADAPTER — nothing else in the harness changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# --- paths -----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
ICONS_DIR = ROOT / "icons" / "carbon"
ASSETS_DIR = ROOT / "assets"
REFERENCE_PLANS = ROOT / "reference_plans"
WORKSPACE = ROOT / "workspace"

# Curated user-facing example plans for the "Start from an example plan"
# button. Tuple = (filename in REFERENCE_PLANS, display label in the UI).
# Keep this list short and demo-quality — render-check anything we add here
# before promoting it. The rest of reference_plans/ stays as crafter-only
# exemplars (see intake.py EXEMPLAR_NAMES).
USER_FACING_EXAMPLES = [
    ("rag_practical_intro.md",       "Retrieval-Augmented Generation — Explained"),
    ("project_heron_h1_review.md",   "Project Heron — H1 2026 Review"),
    ("state_of_ai_coding_2026.md",   "State of AI Coding Tools — 2026"),
]

# --- RITS ------------------------------------------------------------------
# OpenAI-compatible inference. The per-model slug goes in the URL path:
#   {RITS_BASE_URL}/{slug}/v1/chat/completions
RITS_BASE_URL = os.environ.get(
    "RITS_BASE_URL",
    "https://inference-3scale-apicast-production.apps.rits.fmaas.res.ibm.com",
)


@dataclass(frozen=True)
class ModelSpec:
    """A RITS-served model. `slug` is the URL path segment; `payload_model`
    is the `model` field in the request body."""
    slug: str
    payload_model: str
    max_tokens: int = 8192
    temperature: float = 0.0


# --- models available on RITS ----------------------------------------------
GPT_OSS_120B = ModelSpec("gpt-oss-120b", "openai/gpt-oss-120b", max_tokens=24000)
QWEN3_VL = ModelSpec(
    "qwen3-vl-235b-a22b-instruct", "Qwen/Qwen3-VL-235B-A22B-Instruct",
    max_tokens=1500,
)
# The fine-tuned palette adapter (designer + coder), live on RITS as a LoRA:
# one endpoint serves the base model; the `model` field selects the adapter.
#
# Default migrated 2026-06-05 from gpt-oss-20b base to Qwen2.5-Coder-32B base.
# The new adapter was trained on the same v3+IBM-merged dataset; the cold-base
# evaluation and rendered side-by-side comparison showed qwen25 wins on the
# highest-value treatments (charts, dashboards, multi-card layouts) and ties
# or marginally loses on a small set of dense single-slide layouts. Trade-off
# accepted: ~1.5-2x slower inference per token (dense 32B vs MoE 21B/3.6B-
# active), recovered by RITS-side optimisations (in progress) and worth it
# for the visual-quality gain.
#
# temperature=0.0 — deterministic decoding: reproducible decks, no JSON-syntax
# sampling slips. run_designer and _code_one_slide use a SMALL temperature
# bump (0.3) on retry to resample out of rare degeneracy — NEVER 1.0, which
# sends LoRAs off-topic.
#
# Slug/model overridable via env. To temporarily roll back to the gpt-oss-20b
# adapter, set: PALETTE_ADAPTER_SLUG=gpt-oss-20b-palette-lora and
# PALETTE_ADAPTER_MODEL=palette-gpt-20b.
PALETTE_ADAPTER = ModelSpec(
    os.environ.get("PALETTE_ADAPTER_SLUG", "qwen2-5-coder-32b-palette-lora"),
    os.environ.get("PALETTE_ADAPTER_MODEL", "palette-qwen-32b"),
    max_tokens=24000,
    temperature=0.0,
)
# The OLD fine-tuned palette adapter on gpt-oss-20b base. Kept as a UI option
# for side-by-side comparisons during the v3_qwen25 migration; the LoRA-on-
# gpt-oss endpoint is still running on RITS in parallel with the new one.
PALETTE_ADAPTER_GPT = ModelSpec(
    "gpt-oss-20b-palette-lora",
    "palette-gpt-20b",
    max_tokens=24000,
    temperature=0.0,
)
# Base Qwen2.5-Coder-32B-Instruct (no adapter). Served on the same RITS
# endpoint as the LoRA — the `model` field selects between the bare base
# and the adapter. Used as the default editor (geometry-repair) model from
# 2026-06-05 onward: it's the same family as the palette LoRA, so its
# diagnoses and rewrites stay in-distribution for the codebase the LoRA
# produces. Smaller than gpt-oss-120b (32B dense vs 120B reasoning MoE)
# but more code-specialised and aesthetically closer to our output.
QWEN_CODER_32B = ModelSpec(
    "qwen2-5-coder-32b-palette-lora",
    "Qwen/Qwen2.5-Coder-32B-Instruct",
    max_tokens=24000,
    temperature=0.0,
)
LLAMA_70B = ModelSpec("llama-3-3-70b-instruct",
                      "meta-llama/llama-3-3-70b-instruct", max_tokens=24000)

# --- per-role roster -------------------------------------------------------
# designer + coder run the fine-tuned palette adapter (live on RITS). crafter
# and editor stay on gpt-oss-120b: the editor role is mostly spatial-geometric
# diagnostic reasoning over existing JS, and the 120B reasoning MoE is better
# at that than the 32B code-specialised Qwen. QWEN_CODER_32B remains in
# CORRECTION_MODELS so it's selectable from the UI for A/B comparison on
# specific builds, but not the default. critic stays on Qwen-VL.
# (Considered switching to QWEN_CODER_32B on 2026-06-05 — same family as
# the adapter — but reverted same day: in this session's logs, gpt-oss-120b
# was diagnosing geometry correctly; failures were verify-gate misfires,
# not bad diagnoses. A smaller reasoner would not help those.)
ROSTER: dict[str, ModelSpec] = {
    "crafter": GPT_OSS_120B,      # Stage 1 — intake -> plan.md
    "designer": PALETTE_ADAPTER,  # Stage 2 — plan.md -> deck brief
    "coder": PALETTE_ADAPTER,     # Stage 2 — brief + slide -> JS
    "critic": QWEN3_VL,           # Stage 3 — visual critique
    "editor": GPT_OSS_120B,       # Stage 3 — code edits / geometry repair
}

# --- UI model menu ---------------------------------------------------------
# The app's dropdowns pick the model per editable role; apply_models() points
# ROSTER at the choice for the next build. The palette LoRA is offered only
# for designer+coder. Single-user assumption — the app serialises builds, so
# per-request roster mutation is safe.
PLANNER_MODELS = {"gpt-oss-120b": GPT_OSS_120B, "llama-3.3-70b": LLAMA_70B}
# DESIGNER_MODELS: first entry is the UI default. palette-qwen-32b is the
# current production LoRA; palette-gpt-20b is the prior LoRA, kept for
# side-by-side comparison during the v3_qwen25 rollout. The two non-LoRA
# entries are for ablations / fallbacks. The legacy "palette-lora" alias
# resolves to the Qwen LoRA so existing API clients keep working.
DESIGNER_MODELS = {
    "palette-qwen-32b": PALETTE_ADAPTER,
    "palette-gpt-20b":  PALETTE_ADAPTER_GPT,
    "gpt-oss-120b":     GPT_OSS_120B,
    "llama-3.3-70b":    LLAMA_70B,
    "palette-lora":     PALETTE_ADAPTER,  # backward-compat alias
}
# CORRECTION_MODELS: routes the UI's editor-role dropdown. gpt-oss-120b is
# the default (better diagnostic reasoner for geometry-repair work).
# qwen2.5-coder-32b is selectable for A/B comparison.
CORRECTION_MODELS = {
    "gpt-oss-120b":      GPT_OSS_120B,
    "qwen2.5-coder-32b": QWEN_CODER_32B,
    "llama-3.3-70b":     LLAMA_70B,
    "palette-qwen-32b":  PALETTE_ADAPTER,
    "palette-gpt-20b":   PALETTE_ADAPTER_GPT,
    "palette-lora":      PALETTE_ADAPTER,  # backward-compat alias
}


def apply_models(planner: str = "", designer_coder: str = "",
                 correction: str = "") -> None:
    """Point ROSTER at the UI-selected models for the next build. Unknown or
    empty names are ignored, leaving that role at its current model."""
    if planner in PLANNER_MODELS:
        ROSTER["crafter"] = PLANNER_MODELS[planner]
    if designer_coder in DESIGNER_MODELS:
        ROSTER["designer"] = ROSTER["coder"] = DESIGNER_MODELS[designer_coder]
    if correction in CORRECTION_MODELS:
        ROSTER["editor"] = CORRECTION_MODELS[correction]


# Coder calls fan out across slides; this caps concurrent RITS requests.
CODER_WORKERS = int(os.environ.get("DECK_FORGE_CODER_WORKERS", "8"))

# The Stage-2 build self-heals render errors before handing back the deck:
# render -> feed errors to the editor -> re-render, up to this many passes.
MAX_BUILD_REPAIR = int(os.environ.get("DECK_FORGE_MAX_BUILD_REPAIR", "2"))

# The build runs ONE deterministic geometry-repair pass — the detector flags
# layout defects, the repair model rewrites those slides, a verify gate keeps
# only the measurable wins. No VLM. Set DECK_FORGE_AUTO_GEOMETRY_PASS=0 to skip.
AUTO_GEOMETRY_PASS = os.environ.get("DECK_FORGE_AUTO_GEOMETRY_PASS", "1") != "0"

# The Qwen-VL visual-critique pass is NOT wired into the build — the geometry
# pass replaced it. This flag is retained for when the VLM pass is revisited.
AUTO_VISUAL_PASS = os.environ.get("DECK_FORGE_AUTO_VISUAL_PASS", "0") != "0"

# RITS request retries on transient failures (timeout / 429 / 5xx). RITS
# models — Qwen-VL especially — overload intermittently.
RITS_RETRIES = int(os.environ.get("DECK_FORGE_RITS_RETRIES", "3"))


# The icon list shown to the designer — a SMALL, curated, general-purpose
# subset, NOT the full ~172-icon library.
#
# Why a subset: the fine-tuned designer was trained on short icon lists (the
# catalog gave each deck ~8-20) and it echoes `available_icons` verbatim into
# the brief JSON. Handed the full library it loses the bracket partway through
# the long array and closes it with "}" instead of "]" — the brief then fails
# to parse and the whole deck build dies. Confirmed on the v3_ibm_continued
# eval (6/10 decks failed exactly this way). Keep this list short.
#
# All names are verified to exist in icons/carbon/; available_icons()
# intersects with what is on disk so a typo here can never break a render.
_HARNESS_ICONS = [
    "dashboard", "analytics", "data", "network", "settings",
    "launch", "security", "locked", "checkmark--outline", "warning--outline",
    "document", "light", "user", "chat", "code",
    "terminal", "integration", "api", "calendar", "services",
]


@lru_cache(maxsize=1)
def available_icons() -> list[str]:
    """The curated icon list shown to the designer — see _HARNESS_ICONS.
    Deliberately short (~20). Do NOT widen this to the full icons/carbon/
    library: the fine-tuned designer mangles a long available_icons array and
    the brief fails to parse. Returned names carry the .svg extension and are
    intersected with what is actually on disk."""
    if not ICONS_DIR.is_dir():
        return []
    on_disk = {p.name for p in ICONS_DIR.glob("*.svg")}
    return [f"{n}.svg" for n in _HARNESS_ICONS if f"{n}.svg" in on_disk]


@lru_cache(maxsize=1)
def available_assets() -> dict[str, list[str]]:
    """Valid relative asset paths under assets/, grouped by kind:
    {'covers': ['assets/covers/...', ...], 'logos': ['assets/logos/...']}.
    Used by the deterministic asset-path remap to snap a near-miss path the
    model emitted onto a real file."""
    out: dict[str, list[str]] = {}
    for kind in ("covers", "logos"):
        sub = ASSETS_DIR / kind
        out[kind] = sorted(
            f"assets/{kind}/{p.name}" for p in sub.glob("*")
            if p.suffix.lower() in (".png", ".jpg", ".jpeg")
        ) if sub.is_dir() else []
    return out
