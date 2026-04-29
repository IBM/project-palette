from __future__ import annotations

import ast
import json
import logging
import os
import re
import time
from types import SimpleNamespace
from typing import Any, Callable

import httpx

log = logging.getLogger("agent")
rits_log = logging.getLogger("rits")
wx_log = logging.getLogger("watsonx")


PROVIDER = os.environ.get("PALETTE_LLM_PROVIDER", "watsonx").lower()

RITS_BASE_URL = os.environ.get(
    "RITS_BASE_URL",
    "https://inference-3scale-apicast-production.apps.rits.fmaas.res.ibm.com",
)
RITS_MODEL_SLUG = os.environ.get("RITS_MODEL_SLUG", "gpt-oss-120b")
RITS_PAYLOAD_MODEL = os.environ.get("RITS_PAYLOAD_MODEL", "openai/gpt-oss-120b")

WATSONX_URL = os.environ.get("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
WATSONX_MODEL_ID = os.environ.get(
    "WATSONX_MODEL_ID", "meta-llama/llama-4-maverick-17b-128e-instruct-fp8",
)

RITS_VISION_MODEL_SLUG = os.environ.get(
    "RITS_VISION_MODEL_SLUG", "qwen3-vl-235b-a22b-instruct")
RITS_VISION_PAYLOAD_MODEL = os.environ.get(
    "RITS_VISION_PAYLOAD_MODEL", "Qwen/Qwen3-VL-235B-A22B-Instruct")


def vision_chat_complete(messages: list[dict], temperature: float = 0,
                         max_tokens: int = 800, timeout: float = 90.0) -> Any:
    """Multimodal RITS call to Qwen3-VL. messages[*].content can be a list
    of {type: 'text'} and {type: 'image_url'} parts."""
    api_key = os.environ.get("RITS_API_KEY")
    if not api_key:
        raise RuntimeError("RITS_API_KEY is not set.")
    url = f"{RITS_BASE_URL}/{RITS_VISION_MODEL_SLUG}/v1/chat/completions"
    payload = {
        "model": RITS_VISION_PAYLOAD_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    rits_log.info("→ %s (vision)", RITS_VISION_MODEL_SLUG)
    resp = httpx.post(url, headers={"RITS_API_KEY": api_key},
                       json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage") or {}
    rits_log.info("← %s %d+%d tok", RITS_VISION_MODEL_SLUG,
                  usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    msg = data["choices"][0]["message"]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=msg.get("content") or "",
    ))])


def rits_chat_complete(messages: list[dict], temperature: float = 0,
                         top_p: float | None = None) -> Any:
    api_key = os.environ.get("RITS_API_KEY")
    if not api_key:
        raise RuntimeError("RITS_API_KEY is not set.")
    url = f"{RITS_BASE_URL}/{RITS_MODEL_SLUG}/v1/chat/completions"
    payload: dict = {
        "model": RITS_PAYLOAD_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if top_p is not None:
        payload["top_p"] = top_p
    rits_log.info("→ %s", RITS_MODEL_SLUG)
    resp = httpx.post(url, headers={"RITS_API_KEY": api_key}, json=payload, timeout=600.0)
    resp.raise_for_status()
    data = resp.json()
    usage = data.get("usage") or {}
    rits_log.info("← %s %d+%d tok", RITS_MODEL_SLUG,
                  usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    msg = data["choices"][0]["message"]
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=msg.get("content") or "",
        reasoning_content=msg.get("reasoning") or msg.get("reasoning_content") or "",
    ))])


_WATSONX_CLIENT = None  # cached across calls


def _watsonx_client():
    global _WATSONX_CLIENT
    if _WATSONX_CLIENT is not None:
        return _WATSONX_CLIENT
    from langchain_ibm import ChatWatsonx
    if not os.environ.get("WATSONX_APIKEY") and not os.environ.get("WATSONX_API_KEY"):
        raise RuntimeError("WATSONX_APIKEY (or WATSONX_API_KEY) is not set.")
    kwargs: dict = {
        "model_id": WATSONX_MODEL_ID,
        "url": WATSONX_URL,
        "params": {"temperature": 0, "max_tokens": 8192},
    }
    if os.environ.get("WATSONX_PROJECT_ID"):
        kwargs["project_id"] = os.environ["WATSONX_PROJECT_ID"]
    elif os.environ.get("WATSONX_SPACE_ID"):
        kwargs["space_id"] = os.environ["WATSONX_SPACE_ID"]
    else:
        raise RuntimeError("Set WATSONX_PROJECT_ID or WATSONX_SPACE_ID.")
    _WATSONX_CLIENT = ChatWatsonx(**kwargs)
    return _WATSONX_CLIENT


def watsonx_chat_complete(messages: list[dict], temperature: float = 0) -> Any:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    lc_messages = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
        else:
            lc_messages.append(HumanMessage(content=content))

    client = _watsonx_client()
    if temperature != 0:
        client = client.bind(params={"temperature": temperature, "max_tokens": 8192})

    wx_log.info("→ %s", WATSONX_MODEL_ID)
    t0 = time.time()
    resp = client.invoke(lc_messages)
    elapsed = time.time() - t0

    content = resp.content if isinstance(resp.content, str) else "".join(
        c if isinstance(c, str) else c.get("text", "") for c in resp.content
    )
    wx_log.info("← %s %.1fs %d chars", WATSONX_MODEL_ID, elapsed, len(content))

    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=content,
        reasoning_content="",
    ))])


def chat_complete(messages: list[dict], temperature: float = 0) -> Any:
    if PROVIDER == "rits":
        return rits_chat_complete(messages, temperature)
    return watsonx_chat_complete(messages, temperature)


_FENCED_PYTHON = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
_FENCED_ANY = re.compile(r"```\s*\n(.*?)```", re.DOTALL)
_BARE_FINAL = re.compile(
    r"""final_answer\s*\(\s*(?P<q>['"])(?P<body>.*?)(?P=q)\s*\)""", re.DOTALL,
)


def extract_code_block(text: str) -> str | None:
    m = _FENCED_PYTHON.search(text) or _FENCED_ANY.search(text)
    if m:
        return m.group(1).strip()
    m = _BARE_FINAL.search(text)
    if m:
        return f"final_answer({json.dumps(m.group('body'))})"
    return None


def extract_reasoning(text: str) -> str:
    m = re.search(r"```", text)
    return text[: m.start()].strip() if m else text.strip()


def build_sandbox(tools: dict[str, Callable]) -> dict:
    import collections
    import itertools
    import math
    import random
    import statistics

    output: list[str] = []
    final: list[str | None] = [None]

    def _print(*args: Any, **_: Any) -> None:
        output.append(" ".join(str(a) for a in args))

    def _final(answer: Any) -> None:
        final[0] = str(answer)

    safe_modules = {
        "math": math, "re": re, "json": json,
        "collections": collections, "itertools": itertools,
        "statistics": statistics, "random": random,
    }

    def _safe_import(name: str, *_: Any, **__: Any) -> Any:
        if name in safe_modules:
            return safe_modules[name]
        for allowed in safe_modules:
            if name.startswith(allowed + "."):
                import importlib
                return importlib.import_module(name)
        raise ImportError(f"Module '{name}' not allowed.")

    builtins_src = __builtins__ if isinstance(__builtins__, dict) else vars(__builtins__)
    ns: dict[str, Any] = {
        "__builtins__": {
            "__import__": _safe_import,
            "__build_class__": builtins_src["__build_class__"],
            "ValueError": ValueError, "TypeError": TypeError, "KeyError": KeyError,
            "IndexError": IndexError, "StopIteration": StopIteration,
            "Exception": Exception, "RuntimeError": RuntimeError,
            "open": open, "FileNotFoundError": FileNotFoundError,
        },
        "print": _print,
        "final_answer": _final,
        **safe_modules,
        "abs": abs, "round": round, "min": min, "max": max,
        "sum": sum, "len": len, "sorted": sorted,
        "int": int, "float": float, "str": str, "bool": bool,
        "list": list, "dict": dict, "set": set, "tuple": tuple,
        "range": range, "enumerate": enumerate, "zip": zip,
        "map": map, "filter": filter, "any": any, "all": all,
        "isinstance": isinstance, "type": type,
        "repr": repr, "next": next, "iter": iter,
        "True": True, "False": False, "None": None,
    }
    ns.update(tools)
    ns["_output"] = output
    ns["_final"] = final
    return ns


def execute_code(code: str, ns: dict) -> tuple[str, str | None]:
    ns["_output"].clear()
    ns["_final"][0] = None
    try:
        tree = ast.parse(code, mode="exec")
        # Jupyter-style: print the value of a bare final expression so the model
        # sees what its tool call returned. Skips constants and print/final_answer
        # to avoid double-printing.
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = tree.body[-1]
            is_const = isinstance(last.value, ast.Constant)
            is_skip = (
                isinstance(last.value, ast.Call)
                and isinstance(last.value.func, ast.Name)
                and last.value.func.id in ("print", "final_answer")
            )
            if not is_const and not is_skip:
                tree.body[-1] = ast.Expr(value=ast.Call(
                    func=ast.Name(id="print", ctx=ast.Load()),
                    args=[last.value], keywords=[],
                ))
                ast.fix_missing_locations(tree)
        exec(compile(tree, "<agent>", "exec"), ns)
    except Exception as e:
        ns["_output"].append(f"Error: {type(e).__name__}: {e}")
    return "\n".join(ns["_output"]), ns["_final"][0]


def run_agent(
    user_message: str,
    namespace: dict,
    message_history: list[dict],
    system_prompt: str,
    max_iterations: int = 6,
) -> tuple[str, list[dict]]:
    if not message_history or message_history[0].get("role") != "system":
        message_history.insert(0, {"role": "system", "content": system_prompt})
    else:
        message_history[0] = {"role": "system", "content": system_prompt}
    message_history.append({"role": "user", "content": user_message})

    trace: list[dict] = []
    for i in range(max_iterations):
        log.info("─── iter %d ───", i + 1)
        resp = chat_complete(messages=message_history)
        msg = resp.choices[0].message
        content = msg.content or ""
        reasoning = (getattr(msg, "reasoning_content", "") or "").strip()
        message_history.append({"role": "assistant", "content": content})

        rec: dict = {"iteration": i + 1, "reasoning": reasoning or None,
                     "content": content.strip() or None, "code": None, "output": None}

        if reasoning:
            log.info("reasoning: %s", reasoning[:240].replace("\n", " "))

        code = extract_code_block(content)
        if not code:
            nudge = (
                "Every turn MUST contain code inside a fenced ```python ... ``` "
                "block. To reply to the user, write:\n\n"
                "```python\nfinal_answer(\"your message\")\n```\n\nTry again."
            )
            message_history.append({"role": "user", "content": nudge})
            rec["output"] = "[no code block — nudged]"
            trace.append(rec)
            continue

        rec["code"] = code
        log.info("code (%d chars):\n%s", len(code),
                 "\n".join(f"    {line}" for line in code.splitlines()[:20]))

        output, final = execute_code(code, namespace)
        rec["output"] = output[:2000] if output else None
        trace.append(rec)

        if output:
            log.info("output: %s", output[:400].replace("\n", " | "))

        if final is not None:
            log.info("final_answer → ending")
            return final, trace

        message_history.append({
            "role": "user",
            "content": f"Observation:\n{output or '(no output)'}",
        })

    return (
        "I'm having trouble completing that in one go — could you rephrase or "
        "break it into a smaller step?",
        trace,
    )
