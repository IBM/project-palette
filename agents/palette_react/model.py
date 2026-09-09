"""The chat model. watsonx `openai/gpt-oss-120b` by default.

Why watsonx rather than whatever is cheapest to wire: the benchmark's existing
two hosts differ in *both* scaffold and model, so nothing measured between them
can be attributed to either. Running this host on the same 120B that CUGA runs
makes the scaffold the only variable, which is the whole reason for a third
host. `--model` exists so the other direction is available too.

Credentials come from the environment. They are commonly kept in a `.env` that
belongs to some other project, so `load_env_file` reads one on request — it
only ever reads, and it never overwrites a variable already set.
"""

from __future__ import annotations

import os
from pathlib import Path

#: watsonx's id for the same weights CUGA benchmarks against.
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_URL = "https://us-south.ml.cloud.ibm.com"

#: A deck plan is long, and the skill instructions are 14KB before the
#: conversation starts. The default cap truncates mid-plan.
DEFAULT_MAX_TOKENS = 16_000
#: Matches the temperature CUGA uses for the same model, so a difference in
#: results is not a difference in sampling.
DEFAULT_TEMPERATURE = 0.1


def load_env_file(path: str | os.PathLike[str]) -> list[str]:
    """Read `KEY=value` lines into the environment. Returns the names it set.

    Deliberately tiny and dependency-free: this needs to read a credentials
    file, not implement dotenv. Existing variables win, so an explicit export
    always beats the file.
    """
    file = Path(path).expanduser()
    if not file.is_file():
        raise FileNotFoundError(f"no env file at {file}")

    loaded: list[str] = []
    for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ and os.environ[key].strip():
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded


def missing_credentials() -> list[str]:
    """What watsonx needs and does not have. Empty means ready."""
    problems = []
    if not os.environ.get("WATSONX_APIKEY", "").strip():
        problems.append("WATSONX_APIKEY is not set")
    if not os.environ.get("WATSONX_URL", "").strip():
        problems.append(f"WATSONX_URL is not set (usually {DEFAULT_URL})")
    if not (
        os.environ.get("WATSONX_PROJECT_ID", "").strip()
        or os.environ.get("WATSONX_SPACE_ID", "").strip()
    ):
        problems.append("neither WATSONX_PROJECT_ID nor WATSONX_SPACE_ID is set")
    return problems


def build(
    model: str = DEFAULT_MODEL,
    *,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
):
    """A watsonx chat model bound to `model`.

    Imported lazily so the rest of this package — and its whole test suite —
    works without langchain-ibm installed.
    """
    problems = missing_credentials()
    if problems:
        raise RuntimeError(
            "watsonx is not configured:\n  - "
            + "\n  - ".join(problems)
            + "\n\nSet them, or point --env-file at a file that has them."
        )

    try:
        from langchain_ibm import ChatWatsonx
    except ModuleNotFoundError as exc:  # noqa: F841
        raise RuntimeError(
            "langchain-ibm is not installed in this interpreter.\n"
            "  pip install -r agents/requirements.txt"
        ) from exc

    credentials = {
        "url": os.environ.get("WATSONX_URL", DEFAULT_URL).strip(),
        "apikey": os.environ["WATSONX_APIKEY"].strip(),
    }
    # A project and a space are alternatives; passing both is an error at the
    # API, and the project is the one people usually mean.
    if os.environ.get("WATSONX_PROJECT_ID", "").strip():
        credentials["project_id"] = os.environ["WATSONX_PROJECT_ID"].strip()
    else:
        credentials["space_id"] = os.environ["WATSONX_SPACE_ID"].strip()

    return ChatWatsonx(
        model_id=model,
        temperature=temperature,
        max_tokens=max_tokens,
        **credentials,
    )
