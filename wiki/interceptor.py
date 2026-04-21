"""
Wiki Interceptor — Entity extraction pipeline for incoming messages.

Scans messages for business entities (Client Name, Budget, Service Fee,
Project Name, Assignee) via a lightweight LLM call, then sets status to
'Verified' or 'Pending' based on confidence and ambiguity signals.

Uses WikiModelRouter — deepseek/deepseek-chat for extraction, claude-3.5-sonnet
for @best reports (cost-isolated from the main task_tracker ModelRouter).

Usage
-----
    from wiki.interceptor import WikiInterceptor

    interceptor = WikiInterceptor()          # reads OPENROUTER_API_KEY from env

    result = interceptor.intercept("Budget for Luna is 100 bucks")
    print(result.status)          # "Verified"
    print(result.budget.value)    # "100"

    result2 = interceptor.intercept("Maybe the fee is around 1500?")
    print(result2.status)         # "Pending"
    print(interceptor.verification_question(result2))
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Literal

from openai import OpenAI, APIError, APITimeoutError, RateLimitError

from tracker.config import MODEL_SMART

log = logging.getLogger(__name__)


# ── WikiModelRouter ───────────────────────────────────────────────────────────

@dataclass
class _WikiRouterResult:
    content: str
    model_used: str


class WikiModelRouter:
    """
    Dedicated OpenRouter client for all Wiki module LLM calls.

    Completely independent of the main ModelRouter — has its own OpenAI client
    with ``X-Title: MARTS-Brain-Wiki`` for OpenRouter dashboard tracking.

    worker_chat()  — deepseek/deepseek-chat       background entity extraction
    analyst_chat() — anthropic/claude-3.5-sonnet  @best wiki command reports
    """

    _BASE_URL      = "https://openrouter.ai/api/v1"
    _WORKER_MODEL  = "deepseek/deepseek-chat"
    _ANALYST_MODEL = MODEL_SMART

    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
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
                "HTTP-Referer": "https://marts-agency.com",
                "X-Title": "MARTS-Brain-Wiki",
            },
        )
        log.info(
            "[WikiModelRouter] Initialised — worker=%s analyst=%s",
            self._WORKER_MODEL, self._ANALYST_MODEL,
        )

    def worker_chat(
        self, prompt: str, *, system: str, temperature: float = 0.1
    ) -> _WikiRouterResult:
        """Background entity extraction — deepseek/deepseek-chat."""
        return self._call(self._WORKER_MODEL, system, prompt, temperature)

    def analyst_chat(
        self, prompt: str, *, system: str, temperature: float = 0.2
    ) -> _WikiRouterResult:
        """@best wiki command reports — anthropic/claude-3.5-sonnet."""
        return self._call(self._ANALYST_MODEL, system, prompt, temperature)

    def summarize_content(self, text: str, urls: list[str] | None = None) -> str:
        """
        Generate a 3-word label for URL/file messages (Chat Archive Summary column).
        Returns empty string on failure — safe to ignore.
        """
        parts = [f"Message: {text[:300]}"]
        if urls:
            parts.append(f"URLs/files: {', '.join(urls[:3])}")
        system = (
            "Label this message/attachment with exactly a 3-word phrase "
            "(e.g. 'PDF Brand Guide', 'Client Invoice Attached'). "
            "Respond with ONLY the 3-word phrase — no punctuation, no extra text."
        )
        try:
            raw = self.worker_chat("\n".join(parts), system=system, temperature=0.3)
            return " ".join(raw.content.strip().split()[:3])
        except Exception as exc:
            log.warning("[WikiModelRouter] summarize_content failed: %s", exc)
            return ""

    def _call(
        self, model: str, system: str, prompt: str, temperature: float
    ) -> _WikiRouterResult:
        try:
            response = self._client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt},
                ],
            )
            content = response.choices[0].message.content or ""
            log.info("[WikiModelRouter] ✅ model=%s chars=%d", model, len(content))
            return _WikiRouterResult(content=content, model_used=model)
        except (APIError, APITimeoutError, RateLimitError) as exc:
            log.error("[WikiModelRouter] ❌ model=%s error=%s", model, exc)
            raise


# ── Known team members (used as assignee hints) ───────────────────────────────
_TEAM_MEMBERS = [
    "Michael", "Kaye", "Anna", "Ivan", "Izzy",
    "Kevin", "Milo", "Tiffany", "Danni", "Silver", "Jhon", "Lovely",
]

# ── Ambiguity signals — any match forces status to Pending ───────────────────
_AMBIGUITY_RE = re.compile(
    r"\b(maybe|perhaps|possibly|probably|i think|i believe|not sure|"
    r"around|approximately|roughly|unsure|might be|could be|"
    r"sort of|kind of|or so|i guess|not certain|unclear)\b",
    re.IGNORECASE,
)

# ── Confidence threshold — fields below this trigger Pending ──────────────────
_CONFIDENCE_THRESHOLD = 0.65

# ── System prompt for the extraction LLM call (concise to minimise input tokens)
_EXTRACTION_SYSTEM = """\
Extract business entities from agency messages. Respond ONLY with valid JSON, no markdown.
Fields: client_name (business/person), budget (dollar spend), service_fee (retainer/monthly fee), project_name (campaign/deliverable), assignee (team member first name).
Normalise money: "1.5k"→"1500", "100 bucks"→"100".
{"client_name":{"value":<str|null>,"raw":<str|null>,"confidence":<0-1>},"budget":{"value":<str|null>,"raw":<str|null>,"confidence":<0-1>},"service_fee":{"value":<str|null>,"raw":<str|null>,"confidence":<0-1>},"project_name":{"value":<str|null>,"raw":<str|null>,"confidence":<0-1>},"assignee":{"value":<str|null>,"raw":<str|null>,"confidence":<0-1>},"overall_confidence":<0-1>}
"""


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ExtractedField:
    value: str | None
    raw: str | None
    confidence: float

    @classmethod
    def empty(cls) -> "ExtractedField":
        return cls(value=None, raw=None, confidence=0.0)

    @classmethod
    def from_dict(cls, d: dict) -> "ExtractedField":
        return cls(
            value=d.get("value") or None,
            raw=d.get("raw") or None,
            confidence=float(d.get("confidence", 0.0)),
        )

    def has_value(self) -> bool:
        return self.value is not None and str(self.value).strip() != ""


@dataclass
class WikiExtraction:
    raw_message: str
    client_name: ExtractedField
    budget: ExtractedField
    service_fee: ExtractedField
    project_name: ExtractedField
    assignee: ExtractedField
    overall_confidence: float
    status: Literal["Verified", "Pending"]
    pending_reason: str | None
    ambiguity_words: list[str]
    model_used: str

    def extracted_fields(self) -> dict[str, ExtractedField]:
        return {
            "client_name": self.client_name,
            "budget": self.budget,
            "service_fee": self.service_fee,
            "project_name": self.project_name,
            "assignee": self.assignee,
        }

    def has_any(self) -> bool:
        return any(f.has_value() for f in self.extracted_fields().values())

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "pending_reason": self.pending_reason,
            "overall_confidence": self.overall_confidence,
            "ambiguity_words": self.ambiguity_words,
            "model_used": self.model_used,
            "entities": {
                name: {"value": f.value, "raw": f.raw, "confidence": f.confidence}
                for name, f in self.extracted_fields().items()
            },
        }


# ── Main interceptor ──────────────────────────────────────────────────────────

class WikiInterceptor:
    """
    Intercepts incoming messages and extracts business entities.

    Uses WikiModelRouter.worker_chat() (deepseek/deepseek-chat) — fully
    isolated from the main ModelRouter so wiki extraction costs are tracked
    under MARTS-Brain-Wiki in the OpenRouter dashboard.

    Parameters
    ----------
    api_key : str | None
        OpenRouter API key. Falls back to OPENROUTER_API_KEY env var.
    confidence_threshold : float
        Fields below this confidence trigger Pending status.
    """

    def __init__(
        self,
        api_key: str | None = None,
        confidence_threshold: float = _CONFIDENCE_THRESHOLD,
    ):
        self._router = WikiModelRouter(api_key=api_key)
        self._threshold = confidence_threshold

    # ── Public API ────────────────────────────────────────────────────────────

    def intercept(self, message: str) -> WikiExtraction:
        """
        Extract entities from `message` and return a WikiExtraction.

        Status is 'Pending' when:
          - The message contains hedging/ambiguity language, OR
          - Any extracted field has confidence below the threshold, OR
          - Overall LLM confidence is below the threshold.
        """
        ambiguous, ambiguity_words = self._detect_ambiguity(message)

        try:
            raw_json, model_used = self._call_llm(message)
            data = self._parse_json(raw_json)
        except Exception as exc:
            log.warning("[WikiInterceptor] LLM call failed: %s", exc)
            return self._fallback_extraction(message, str(exc))

        return self._build(data, message, model_used, ambiguous, ambiguity_words)

    def verification_question(self, extraction: WikiExtraction) -> str | None:
        """
        Generate a clarification question for a Pending extraction.
        Returns None if extraction is already Verified or has no extractable fields.
        """
        if extraction.status == "Verified":
            return None

        unclear = [
            name
            for name, f in extraction.extracted_fields().items()
            if f.has_value() and f.confidence < self._threshold
        ]
        missing = [
            name
            for name, f in extraction.extracted_fields().items()
            if not f.has_value()
        ]

        parts: list[str] = []

        if extraction.ambiguity_words:
            parts.append(
                f"I noticed some uncertainty in your message "
                f"({', '.join(extraction.ambiguity_words)}). "
                "Can you confirm the following details?"
            )

        if unclear:
            readable = [n.replace("_", " ") for n in unclear]
            parts.append(f"I'm not fully confident about: {', '.join(readable)}.")

        if not parts and not missing:
            return None

        question = " ".join(parts) if parts else "Could you clarify a few things?"

        prompts: list[str] = []
        for name, f in extraction.extracted_fields().items():
            label = name.replace("_", " ").title()
            if name in unclear:
                prompts.append(f"  • {label}: you mentioned '{f.raw}' — is this correct?")
            elif name in missing and name in ("client_name", "budget", "service_fee"):
                prompts.append(f"  • {label}: not detected — please provide if applicable.")

        if prompts:
            question += "\n" + "\n".join(prompts)

        return question.strip()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _detect_ambiguity(self, message: str) -> tuple[bool, list[str]]:
        matches = _AMBIGUITY_RE.findall(message)
        unique = list(dict.fromkeys(m.lower() for m in matches))
        return bool(unique), unique

    def _call_llm(self, message: str) -> tuple[str, str]:
        team_hint = ", ".join(_TEAM_MEMBERS)
        prompt = f"Team: {team_hint}\nMessage: {message}"
        result = self._router.worker_chat(
            prompt, system=_EXTRACTION_SYSTEM, temperature=0.1,
        )
        return result.content, result.model_used

    def _parse_json(self, raw: str) -> dict:
        # Strip markdown code fences if present
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        # Extract the outermost JSON object in case the model added surrounding text
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object found in LLM response: {raw[:200]}")
        return json.loads(match.group())

    def _build(
        self,
        data: dict,
        message: str,
        model_used: str,
        ambiguous: bool,
        ambiguity_words: list[str],
    ) -> WikiExtraction:
        fields = {
            "client_name":  ExtractedField.from_dict(data.get("client_name") or {}),
            "budget":        ExtractedField.from_dict(data.get("budget") or {}),
            "service_fee":   ExtractedField.from_dict(data.get("service_fee") or {}),
            "project_name":  ExtractedField.from_dict(data.get("project_name") or {}),
            "assignee":      ExtractedField.from_dict(data.get("assignee") or {}),
        }
        overall = float(data.get("overall_confidence", 0.0))

        low_confidence_fields = [
            name for name, f in fields.items()
            if f.has_value() and f.confidence < self._threshold
        ]

        pending_reasons: list[str] = []
        if ambiguous:
            pending_reasons.append(
                f"ambiguity markers detected: {', '.join(ambiguity_words)}"
            )
        if low_confidence_fields:
            pending_reasons.append(
                f"low-confidence fields: {', '.join(low_confidence_fields)}"
            )
        if overall < self._threshold and overall > 0.0:
            pending_reasons.append(f"overall confidence {overall:.2f} below threshold")

        status: Literal["Verified", "Pending"] = (
            "Pending" if pending_reasons else "Verified"
        )

        log.info(
            "[WikiInterceptor] status=%s model=%s confidence=%.2f ambiguous=%s",
            status, model_used, overall, ambiguous,
        )

        return WikiExtraction(
            raw_message=message,
            client_name=fields["client_name"],
            budget=fields["budget"],
            service_fee=fields["service_fee"],
            project_name=fields["project_name"],
            assignee=fields["assignee"],
            overall_confidence=overall,
            status=status,
            pending_reason="; ".join(pending_reasons) if pending_reasons else None,
            ambiguity_words=ambiguity_words,
            model_used=model_used,
        )

    def _fallback_extraction(self, message: str, error: str) -> WikiExtraction:
        empty = ExtractedField.empty()
        return WikiExtraction(
            raw_message=message,
            client_name=empty,
            budget=empty,
            service_fee=empty,
            project_name=empty,
            assignee=empty,
            overall_confidence=0.0,
            status="Pending",
            pending_reason=f"LLM extraction failed: {error}",
            ambiguity_words=[],
            model_used="none",
        )


# ── CLI smoke-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="WikiInterceptor smoke-test")
    parser.add_argument("--key", help="OpenRouter API key (or set OPENROUTER_API_KEY)")
    parser.add_argument("--message", help="Single message to test")
    args = parser.parse_args()

    interceptor = WikiInterceptor(api_key=args.key)

    test_cases = [
        "Budget for Luna is 100 bucks",
        "Monthly fee set to 1509",
        "Assign the GlowSpa Q2 campaign to Tiffany, budget around $3,000",
        "Maybe the service fee is roughly 500? I think Ivan handles it",
        "@best Kaye SkinBar NYC website redesign — client budget $2,500",
        "Just a regular message with no business data",
    ]

    messages = [args.message] if args.message else test_cases

    for msg in messages:
        print(f"\n{'─' * 60}")
        print(f"Input : {msg}")
        result = interceptor.intercept(msg)
        print(f"Status: {result.status}  (confidence={result.overall_confidence:.2f})")
        if result.pending_reason:
            print(f"Reason: {result.pending_reason}")
        for name, f in result.extracted_fields().items():
            if f.has_value():
                label = name.replace("_", " ").title()
                print(f"  {label:<15} = {f.value!r}  (raw={f.raw!r}, conf={f.confidence:.2f})")
        if result.status == "Pending":
            q = interceptor.verification_question(result)
            if q:
                print(f"\nVerification:\n{q}")
