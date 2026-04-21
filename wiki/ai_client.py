"""
wiki/ai_client.py — Dedicated OpenRouter AI client for the Wiki package.

Model assignments (cost-isolated from main ModelRouter / task_tracker):
  entity extraction & archiving : deepseek/deepseek-chat
  wiki queries (@best wiki)     : anthropic/claude-3.5-sonnet
"""

from __future__ import annotations

import logging
import os

from openai import OpenAI, APIError, APITimeoutError, RateLimitError

log = logging.getLogger(__name__)

_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MODEL_EXTRACTOR  = "deepseek/deepseek-chat"
MODEL_WIKI_QUERY = "anthropic/claude-3.5-sonnet"

_CONTENT_SUMMARY_SYSTEM = """\
You are a concise content labeler for a marketing agency message archive.
Given a message that contains a URL or file, produce exactly a 3-word summary
that captures the topic (e.g. "Inquiry for Logo", "PDF Brand Guide",
"Website Redesign Proposal", "Client Invoice Attached").
Respond with ONLY the 3-word phrase — no punctuation, no extra text.
"""


class WikiAIClient:
    """
    Self-contained OpenRouter client for wiki AI tasks.
    Intentionally isolated from the main ModelRouter so wiki costs
    are tracked separately and models are never overridden by fallback logic.

    Parameters
    ----------
    api_key : str | None
        OpenRouter API key. Falls back to OPENROUTER_API_KEY env var.
    timeout : float
        Per-request timeout in seconds.
    """

    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
        key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            raise ValueError(
                "OpenRouter API key required. Pass api_key= or set OPENROUTER_API_KEY."
            )
        self._client = OpenAI(
            api_key=key,
            base_url=_OPENROUTER_BASE,
            timeout=timeout,
            default_headers={
                "HTTP-Referer": "https://marts-agency.com",
                "X-Title": "MARTS-Wiki",
            },
        )
        log.info("[WikiAIClient] Initialised — extractor=%s  query=%s",
                 MODEL_EXTRACTOR, MODEL_WIKI_QUERY)

    # ── Public API ────────────────────────────────────────────────────────────

    def extract_entities(
        self,
        prompt: str,
        system: str,
        temperature: float = 0.1,
    ) -> tuple[str, str]:
        """
        Run entity extraction using deepseek/deepseek-chat.
        Returns (response_content, model_id).
        """
        content = self._call(MODEL_EXTRACTOR, system, prompt, temperature)
        return content, MODEL_EXTRACTOR

    def generate_summary(
        self,
        prompt: str,
        system: str,
        temperature: float = 0.2,
    ) -> str:
        """
        Generate an executive summary using anthropic/claude-3.5-sonnet.
        """
        return self._call(MODEL_WIKI_QUERY, system, prompt, temperature)

    def summarize_content(
        self,
        message: str,
        urls: list[str] | None = None,
    ) -> str:
        """
        Generate a 3-word summary of a message that contains a URL or file.
        Used to populate the Summary column when archiving to Chat_Archive.
        Returns an empty string on failure (safe to ignore).
        """
        parts = [f"Message: {message[:300]}"]
        if urls:
            parts.append(f"URLs/files: {', '.join(urls[:3])}")
        prompt = "\n".join(parts)
        try:
            raw = self._call(MODEL_EXTRACTOR, _CONTENT_SUMMARY_SYSTEM, prompt, temperature=0.3)
            words = raw.strip().split()[:3]
            return " ".join(words)
        except Exception as exc:
            log.warning("[WikiAIClient] summarize_content failed: %s", exc)
            return ""

    # ── Private helper ────────────────────────────────────────────────────────

    def _call(
        self,
        model: str,
        system: str,
        prompt: str,
        temperature: float,
    ) -> str:
        try:
            response = self._client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt},
                ],
            )
            return response.choices[0].message.content or ""
        except (APIError, APITimeoutError, RateLimitError) as exc:
            log.error("[WikiAIClient] API error (model=%s): %s", model, exc)
            raise
