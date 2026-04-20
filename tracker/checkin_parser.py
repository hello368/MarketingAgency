"""
Check-in & EOD parser.
Extracts 3 daily goals from free-form text, and URLs from EOD posts.
"""
import re
from typing import Optional


# ─────────────────────────────────────────
# GOAL EXTRACTION
# Handles numbered lists (1. 2. 3.), bullet points, and plain lines.
# ─────────────────────────────────────────

# Patterns that indicate the start of a new goal item
_GOAL_SPLITTERS = re.compile(
    r"""
    (?:^|\n)\s*          # start of string or newline + optional spaces
    (?:
        \d+[\.\)\-]\s*   # 1. or 1) or 1-
      | [•\-\*]\s*       # bullet: • - *
      | (?:Goal|GOAL)\s*\d*\s*[:\-]?\s*  # "Goal 1:", "GOAL:"
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

def parse_checkin(text: str) -> list[str]:
    """
    Extract up to 3 goals from a check-in message.
    Returns a list of 0–3 strings.
    """
    text = text.strip()

    # Remove common preambles ("Good morning!", "Here are my goals:", etc.)
    text = re.sub(
        r"(?i)^(good\s+morning[!,.\s]*|here\s+(are\s+)?my\s+(3\s+)?goals[:\s]*|daily\s+(check[\-\s]in|goals)[:\s]*)",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    # Try to split by numbered/bulleted items
    parts = _GOAL_SPLITTERS.split(text)
    goals = [p.strip() for p in parts if p and p.strip() and len(p.strip()) > 3]

    if len(goals) >= 3:
        return goals[:3]

    # Fallback: split by newlines
    lines = [ln.strip() for ln in text.split("\n") if ln.strip() and len(ln.strip()) > 3]
    if lines:
        return lines[:3]

    # Last resort: return the whole text as goal 1
    return [text] if text else []


# ─────────────────────────────────────────
# URL EXTRACTION (EOD result links)
# ─────────────────────────────────────────

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")

def parse_eod_links(text: str) -> list[str]:
    """Extract all URLs from an EOD submission message."""
    return _URL_PATTERN.findall(text)


# ─────────────────────────────────────────
# MENTION EXTRACTION
# Google Chat sends annotations for @mentions. This is a fallback
# text parser for cases where the annotation is not present.
# ─────────────────────────────────────────

def extract_mentions_from_text(text: str, team_names: list[str]) -> list[str]:
    """
    Find team member names mentioned with @ in message text.
    Returns list of matched team member names.
    """
    found = []
    at_mentions = re.findall(r"@(\w+)", text)
    for mention in at_mentions:
        for name in team_names:
            if mention.lower() == name.lower():
                found.append(name)
    return list(set(found))
