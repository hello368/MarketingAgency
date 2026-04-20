"""
MARTS — ModelRouter
Routes AI tasks to the best OpenRouter model for the job.
Falls back through the priority list on any API error.

Task types
----------
high_intelligence : complex logic, debugging, code generation
long_context      : full log analysis, large document reading
fast_reply        : status checks, repetitive notification text

Usage
-----
    from model_router import ModelRouter

    router = ModelRouter(api_key="sk-or-...")

    # Explicit task type
    reply = router.chat("Fix this bug: ...", task_type="high_intelligence")

    # Auto-detect from prompt content
    reply = router.chat("Summarize this 10,000-line log: ...")
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Literal

from openai import OpenAI, APIError, APITimeoutError, RateLimitError

log = logging.getLogger(__name__)

TaskType = Literal["high_intelligence", "long_context", "fast_reply"]

# ── Model priority lists — first entry is preferred, rest are fallbacks ──────
_PRIORITY: dict[TaskType, list[str]] = {
    "high_intelligence": [
        "anthropic/claude-sonnet-4.6",   # best reasoning, code generation
        "google/gemini-2.5-pro",          # strong fallback
        "deepseek/deepseek-chat",         # last resort
    ],
    "long_context": [
        "google/gemini-2.5-pro",          # largest context window
        "anthropic/claude-sonnet-4.6",    # strong long-doc fallback
        "deepseek/deepseek-chat",         # last resort
    ],
    "fast_reply": [
        "deepseek/deepseek-chat",         # cheapest + fastest
        "anthropic/claude-sonnet-4.6",    # fallback
        "google/gemini-2.5-pro",          # last resort
    ],
}

# ── Auto-classification keyword sets ─────────────────────────────────────────
_KEYWORDS: dict[TaskType, list[str]] = {
    "high_intelligence": [
        "debug", "bug", "fix", "code", "function", "class", "implement",
        "refactor", "logic", "algorithm", "generate", "write a script",
        "traceback", "error", "exception", "optimize",
    ],
    "long_context": [
        "entire log", "full log", "whole file", "analyze all", "read the document",
        "summarize the file", "parse this log", "large document", "10000", "100000",
    ],
    "fast_reply": [
        "status", "check", "notify", "alert", "remind", "ping",
        "is it", "quick", "simple", "one line", "short message",
    ],
}

# Prompt length (chars) above which we default to long_context
_LONG_CONTEXT_THRESHOLD = 4_000


@dataclass
class RouterResult:
    content: str
    model_used: str
    task_type: TaskType
    fallback_count: int = 0
    errors: list[str] = field(default_factory=list)


class ModelRouter:
    """
    OpenRouter-backed model selector with automatic fallback.

    Parameters
    ----------
    api_key : str
        OpenRouter API key. Defaults to env var OPENROUTER_API_KEY.
    site_url : str
        Your site URL (sent in X-Title header — optional but good practice).
    timeout : float
        Per-request timeout in seconds.
    """

    _BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        api_key: str | None = None,
        site_url: str = "https://marts-agency.com",
        timeout: float = 30.0,
    ):
        key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            raise ValueError(
                "OpenRouter API key required. Pass api_key= or set OPENROUTER_API_KEY."
            )
        self._client = OpenAI(
            api_key=key,
            base_url=self._BASE_URL,
            timeout=timeout,
            default_headers={
                "HTTP-Referer": site_url,
                "X-Title": "MARTS-ModelRouter",
            },
        )
        log.info("[ModelRouter] Initialised — base_url=%s", self._BASE_URL)

    # ── Public API ────────────────────────────────────────────────────────────

    def chat(
        self,
        prompt: str,
        *,
        task_type: TaskType | None = None,
        system: str = "You are a helpful AI assistant for a marketing agency.",
        temperature: float = 0.3,
    ) -> RouterResult:
        """
        Send a prompt and return the best model's response.
        If task_type is None the type is inferred from prompt content.

        Returns a RouterResult with .content, .model_used, and .fallback_count.
        Raises RuntimeError only if every fallback model also fails.
        """
        resolved_type: TaskType = task_type or self._classify(prompt)
        models = _PRIORITY[resolved_type]

        errors: list[str] = []
        for attempt, model in enumerate(models):
            try:
                log.info(
                    "[ModelRouter] attempt=%d task=%s model=%s",
                    attempt, resolved_type, model,
                )
                content = self._call(model, system, prompt, temperature)
                log.info("[ModelRouter] ✅ Success — model=%s fallbacks=%d", model, attempt)
                return RouterResult(
                    content=content,
                    model_used=model,
                    task_type=resolved_type,
                    fallback_count=attempt,
                    errors=errors,
                )
            except (APIError, APITimeoutError, RateLimitError) as exc:
                msg = f"{model}: {type(exc).__name__}: {exc}"
                errors.append(msg)
                log.warning("[ModelRouter] ❌ %s — falling back", msg)

        raise RuntimeError(
            f"All models failed for task_type={resolved_type}.\n" + "\n".join(errors)
        )

    def classify(self, prompt: str) -> TaskType:
        """Public wrapper — returns the detected task type for a prompt."""
        return self._classify(prompt)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _classify(self, prompt: str) -> TaskType:
        """
        Determine task type from prompt heuristics.

        Priority (highest → lowest):
          1. Length >= 4000 chars                          → long_context
          2. Log-like content (5+ ERROR/WARNING/INFO hits) → long_context
          3. Line-dense content (>= 15 lines, >= 300 chars)→ long_context
          4. Keyword scoring                               → winner type
          5. No clear winner                               → fast_reply
        """
        length = len(prompt)
        line_count = prompt.count("\n")

        # Signal 1 — raw length
        if length >= _LONG_CONTEXT_THRESHOLD:
            log.debug("[ModelRouter] classify=long_context (length=%d)", length)
            return "long_context"

        # Signal 2 — looks like a log file (repeated severity markers)
        log_hits = len(re.findall(r"\b(ERROR|WARNING|WARN|INFO|DEBUG|CRITICAL|FATAL)\b", prompt))
        if log_hits >= 5:
            log.debug("[ModelRouter] classify=long_context (log_hits=%d)", log_hits)
            return "long_context"

        # Signal 3 — many lines of non-prose content
        if line_count >= 15 and length >= 300:
            log.debug("[ModelRouter] classify=long_context (lines=%d length=%d)", line_count, length)
            return "long_context"

        lower = prompt.lower()
        scores: dict[TaskType, int] = {t: 0 for t in _KEYWORDS}
        for task_type, kws in _KEYWORDS.items():
            for kw in kws:
                if kw in lower:
                    scores[task_type] += 1

        best: TaskType = max(scores, key=lambda t: scores[t])
        if scores[best] == 0:
            best = "fast_reply"

        log.debug("[ModelRouter] classify=%s scores=%s", best, scores)
        return best

    def _call(
        self,
        model: str,
        system: str,
        prompt: str,
        temperature: float,
    ) -> str:
        response = self._client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""


# ── CLI smoke-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="ModelRouter smoke-test")
    parser.add_argument("--key", help="OpenRouter API key (or set OPENROUTER_API_KEY)")
    args = parser.parse_args()

    router = ModelRouter(api_key=args.key)

    tests: list[tuple[str, TaskType | None]] = [
        ("Write a Python function that parses ISO 8601 timestamps.", "high_intelligence"),
        ("Check if the server is healthy.", "fast_reply"),
        ("Summarize the following log file:\n" + "ERROR timeout\n" * 200, None),  # auto → long_context
    ]

    for prompt, ttype in tests:
        label = ttype or "auto"
        print(f"\n{'─'*55}")
        print(f"Task: {label.upper()} | Prompt: {prompt[:60]}...")
        try:
            result = router.chat(prompt, task_type=ttype)
            print(f"✅ Model used  : {result.model_used}")
            print(f"   Task type   : {result.task_type}")
            print(f"   Fallbacks   : {result.fallback_count}")
            print(f"   Response    : {result.content[:120]}...")
        except RuntimeError as e:
            print(f"❌ All models failed: {e}")
