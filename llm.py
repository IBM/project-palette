"""RITS chat-completion client.

OpenAI-compatible: one POST per call, per-model slug in the URL path, the
RITS_API_KEY in a header. All five model roles go through chat(); the only
thing that varies is the ModelSpec passed in (see config.ROSTER).
"""
from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path

import httpx

import config

log = logging.getLogger("llm")
# Full request / response bodies go here; the app routes this through each
# session's FileHandler so bodies land in workspace/<thread_id>/session.log
# (never stderr — too verbose). See app.py's logging setup.
_body_log = logging.getLogger("llm.responses")

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _post_with_retry(url: str, headers: dict, payload: dict, timeout: float,
                     label: str) -> httpx.Response:
    """POST to RITS, retrying transient failures (timeout, connection error,
    HTTP 429/5xx) with exponential backoff. Non-transient errors (401, 400, …)
    raise immediately — no point retrying those. Keeps an intermittent RITS
    overload (common on Qwen-VL) from failing the whole call."""
    backoff = 2.0
    for attempt in range(1, config.RITS_RETRIES + 1):
        last = attempt == config.RITS_RETRIES
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            log.warning("%s: attempt %d/%d failed (%s)",
                        label, attempt, config.RITS_RETRIES, type(e).__name__)
            if last:
                raise
        else:
            if resp.status_code in _RETRYABLE_STATUS and not last:
                log.warning("%s: attempt %d/%d got HTTP %d — retrying",
                            label, attempt, config.RITS_RETRIES, resp.status_code)
            else:
                resp.raise_for_status()
                return resp
        time.sleep(backoff)
        backoff *= 2
    raise RuntimeError(f"{label}: exhausted {config.RITS_RETRIES} retries")


def chat(spec: config.ModelSpec, messages: list[dict], *,
         max_tokens: int | None = None, temperature: float | None = None,
         timeout: float = 600.0) -> tuple[str, str]:
    """One chat completion against RITS. Returns (content, reasoning).

    `content` is the final-channel text; `reasoning` is the model's reasoning
    trace when the server exposes it separately (gpt-oss / Qwen reasoning
    models). Either may be empty.
    """
    api_key = os.environ.get("RITS_API_KEY")
    if not api_key:
        raise RuntimeError("RITS_API_KEY is not set — export it before running.")

    url = f"{config.RITS_BASE_URL}/{spec.slug}/v1/chat/completions"
    payload = {
        "model": spec.payload_model,
        "messages": messages,
        "max_tokens": max_tokens or spec.max_tokens,
        "temperature": spec.temperature if temperature is None else temperature,
    }

    t0 = time.time()
    log.info("-> %s (%d msgs, max_tokens=%d)",
             spec.slug, len(messages), payload["max_tokens"])
    _last_user = next((m.get("content") for m in reversed(messages)
                       if m.get("role") == "user"), "") or ""
    _body_log.info("--- request %s ---\n%s", spec.slug,
                   _last_user if isinstance(_last_user, str) else str(_last_user))
    resp = _post_with_retry(url, {"RITS_API_KEY": api_key}, payload, timeout,
                            spec.slug)
    data = resp.json()

    msg = data["choices"][0]["message"]
    usage = data.get("usage") or {}
    finish = data["choices"][0].get("finish_reason")
    log.info("<- %s %.1fs %d+%d tok finish=%s", spec.slug, time.time() - t0,
             usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
             finish)
    if finish == "length":
        log.warning("%s hit the max_tokens ceiling — output may be truncated",
                    spec.slug)

    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    _body_log.info("--- response %s ---\n%s", spec.slug, content)
    if reasoning:
        _body_log.info("--- reasoning %s ---\n%s", spec.slug, reasoning)
    return content, reasoning


def _image_data_uri(path: Path) -> str:
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


def chat_vision(spec: config.ModelSpec, system: str, user_text: str,
                image_path: Path, *, max_tokens: int | None = None,
                timeout: float = 120.0) -> tuple[str, str]:
    """Multimodal chat — one system prompt, one user turn with text + one
    image. Used for the Stage-3 visual critic. Returns (content, reasoning)."""
    api_key = os.environ.get("RITS_API_KEY")
    if not api_key:
        raise RuntimeError("RITS_API_KEY is not set — export it before running.")

    url = f"{config.RITS_BASE_URL}/{spec.slug}/v1/chat/completions"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": [
            {"type": "text", "text": user_text},
            {"type": "image_url",
             "image_url": {"url": _image_data_uri(image_path)}},
        ]},
    ]
    payload = {
        "model": spec.payload_model,
        "messages": messages,
        "max_tokens": max_tokens or spec.max_tokens,
        "temperature": spec.temperature,
    }

    t0 = time.time()
    log.info("-> %s (vision: %s)", spec.slug, Path(image_path).name)
    _body_log.info("--- request %s (vision) ---\n%s", spec.slug, user_text)
    resp = _post_with_retry(url, {"RITS_API_KEY": api_key}, payload, timeout,
                            spec.slug)
    data = resp.json()
    msg = data["choices"][0]["message"]
    usage = data.get("usage") or {}
    log.info("<- %s %.1fs %d+%d tok", spec.slug, time.time() - t0,
             usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    _body_log.info("--- response %s ---\n%s", spec.slug, content)
    if reasoning:
        _body_log.info("--- reasoning %s ---\n%s", spec.slug, reasoning)
    return content, reasoning
