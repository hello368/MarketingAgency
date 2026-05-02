# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs.**
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.

## 2. Simplicity First
**Minimum code that solves the problem. Nothing speculative.**
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.

## 3. Surgical Changes
**Touch only what you must. Clean up only your own mess.**
- Don't "improve" adjacent code, comments, or formatting.
- Match existing style, even if you'd do it differently.
- Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution
**Define success criteria. Loop until verified.**
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- For multi-step tasks, state a brief plan with verify checks.

---

# MarketingAgency — Project Context

## Overview
Marketing automation system connecting Meta Ads, GHL, Google Sheets.

## Structure
- `main.py` — Entry point
- `model_router.py` — AI model routing
- `gsheet_handler.py` — Google Sheets operations
- `tracker/` — Campaign tracking & reporting
- `scripts/` — GHL-Meta bridge scripts
- `data/` — Data files
- `wiki/` — Documentation

## Key Scripts
| File | Purpose |
|------|---------|
| `main.py` | Main automation entry |
| `model_router.py` | Route to different AI models |
| `scripts/ghl_meta_bridge.py` | GHL ↔ Meta Ads sync |
| `scripts/marketing_bridge.py` | Marketing bridge |

## Commands
```bash
source .venv/bin/activate
python main.py
```

## GitHub
- Public repo: `hello368/MarketingAgency`
- Branch: main
