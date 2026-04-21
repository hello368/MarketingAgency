"""
MARTS V2 — Modular LLM Client
OpenRouter (OpenAI-compatible) API. Requires OPENROUTER_API_KEY.
"""
from __future__ import annotations

import json
import logging
import os

from openai import OpenAI

from tracker.config import MODEL_FAST, MODEL_SMART

log = logging.getLogger(__name__)

_OR_BASE = "https://openrouter.ai/api/v1"


def _get_client() -> OpenAI | None:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        log.warning("[LLM] OPENROUTER_API_KEY not set — LLM features disabled")
        return None
    return OpenAI(api_key=key, base_url=_OR_BASE)


def _call(
    prompt: str,
    system: str = "",
    model: str = MODEL_FAST,
    max_tokens: int = 512,
) -> str:
    """Shared OpenRouter call. Returns empty string on failure."""
    client = _get_client()
    if not client:
        return ""
    try:
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=model,
            messages=msgs,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        log.warning("[LLM] call failed — model=%s err=%s", model, exc)
        return ""


# ── Public API ────────────────────────────────────────────────────────────────

def summarize_update(content: str) -> tuple[str, str]:
    """
    Analyze update content and return (5-word summary, 1-2 sentence context).
    Falls back to rule-based extraction if LLM fails.
    """
    system = "You are a concise business analyst. Respond ONLY with valid JSON. No markdown."
    prompt = f"""Analyze this work update. Return exactly this JSON:
{{"summary": "<5 words max capturing the key point>", "context": "<1-2 sentences of detailed context>"}}

Update: {content}"""

    raw = _call(prompt, system=system, model=MODEL_FAST, max_tokens=256)
    try:
        data = json.loads(raw)
        summary = str(data.get("summary", "")).strip() or " ".join(content.split()[:5])
        context = str(data.get("context", "")).strip() or content[:150]
        return summary, context
    except Exception:
        words = content.split()
        return " ".join(words[:5]), content[:150]


def answer_rag(question: str, rows: list[dict]) -> str:
    """
    Generate a RAG answer using Update_Tracker rows as context.
    Returns a guidance message if no rows are found.
    """
    if not rows:
        return (
            "No updates found for this space.\n"
            "Log one first with `@best update [content]`."
        )

    ctx_lines = []
    for r in rows[:40]:
        ts      = r.get("timestamp", "")
        summary = r.get("summary", "")
        context = r.get("context", "")
        content = r.get("original_content", "")
        ctx_lines.append(f"[{ts}] ⚡{summary} | {content} | 🔍{context}")

    ctx_text = "\n".join(ctx_lines)
    system   = (
        "You are a helpful assistant answering questions based on team work updates. "
        "Be specific, cite relevant records, and always respond in English."
    )
    prompt = f"""Based on the following team update records, answer the question.

=== Team Update Records ===
{ctx_text}

=== Question ===
{question}

Provide a clear, detailed answer. Cite specific records where relevant."""

    result = _call(prompt, system=system, model=MODEL_SMART, max_tokens=800)
    return result or "Sorry, failed to generate an answer."


def generate_brief(updates: list[dict], tasks: list[dict]) -> str:
    """Generate a daily briefing from today's/yesterday's Update_Tracker + Task_Tracker data."""
    if not updates and not tasks:
        return (
            "No data to brief yet.\n"
            "Log updates first with `@best update [content]`."
        )

    u_lines = "\n".join(
        f"• [{r.get('space_name', '')}] {r.get('summary', '')} — {r.get('context', '')}"
        for r in updates[:20]
    ) or "No updates"

    t_lines = "\n".join(
        f"• [{r.get('priority', 'Normal')}] {r.get('assignee', '')} — {r.get('content', '')} ({r.get('status', '')})"
        for r in tasks[:20]
    ) or "No active tasks"

    system = "You are a concise team manager. Generate clear daily briefings in English."
    prompt = f"""Based on today's and yesterday's team activity, write a concise daily briefing.

=== Recent Updates ===
{u_lines}

=== Task Status ===
{t_lines}

Write in the following format, under 200 words:
📋 *Daily Briefing*

*📥 Key Updates*
• (2-3 key points)

*✅ Task Status*
• (2-3 progress items)"""

    client = _get_client()
    if not client:
        return "⚠️ LLM unavailable (OPENROUTER_API_KEY not set)."
    try:
        msgs: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ]
        resp = client.chat.completions.create(
            model=MODEL_SMART,
            messages=msgs,
            max_tokens=600,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        log.warning("[LLM] generate_brief failed — model=%s %s: %s", MODEL_SMART, type(exc).__name__, exc)
        return f"⚠️ Failed to generate briefing ({type(exc).__name__})."
