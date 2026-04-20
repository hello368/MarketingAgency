"""
MARTS — Shared mutable singletons.

Set by main.py at startup; read by scheduler.py and any other module
that needs the GSheetHandler without importing main (which causes circular imports).

Usage:
    import state
    if state.gsheet:
        state.gsheet.update_status(...)
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gsheet_handler import GSheetHandler

gsheet: "GSheetHandler | None" = None
gsheet_ok: bool = False
