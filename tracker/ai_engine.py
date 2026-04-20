"""
MARTS — AI Engine singleton.
Wraps ModelRouter so any module can call ai_engine.chat() without
owning the initialisation lifecycle.

Usage:
    import ai_engine
    ai_engine.init()                          # once, at startup
    text = ai_engine.chat("...", task_type="fast_reply")
    # returns str on success, None if AI is unavailable or errors
"""
import os
import logging
from model_router import ModelRouter, TaskType

log = logging.getLogger(__name__)

_router: ModelRouter | None = None


def init(api_key: str | None = None) -> bool:
    global _router
    key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        log.warning("[AI] OPENROUTER_API_KEY not set — AI features disabled")
        return False
    try:
        _router = ModelRouter(api_key=key)
        log.info("[AI] ✅ ModelRouter initialised — smart routing ACTIVE")
        log.info("[AI]    high_intelligence → anthropic/claude-sonnet-4.6")
        log.info("[AI]    long_context      → google/gemini-2.5-pro")
        log.info("[AI]    fast_reply        → deepseek/deepseek-chat")
        return True
    except Exception as exc:
        log.error("[AI] ❌ Init failed: %s", exc)
        return False


def chat(prompt: str, task_type: TaskType | None = None, **kw) -> str | None:
    """
    Route a prompt to the best model for the task type.
    Returns the response text, or None if AI is unavailable or all models fail.
    Never raises.
    """
    if _router is None:
        return None
    try:
        result = _router.chat(prompt, task_type=task_type, **kw)
        log.info(
            "[AI] ✅ %s → %s (fallbacks=%d)",
            task_type or "auto", result.model_used, result.fallback_count,
        )
        return result.content
    except Exception as exc:
        log.warning("[AI] chat() failed: %s", exc)
        return None


def is_ready() -> bool:
    return _router is not None
