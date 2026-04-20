"""
Agency Remote Tracking System — Team Configuration
Edit this file to update team members, roles, and settings.
"""

# ─────────────────────────────────────────
# TEAM MEMBERS
# Keys must match exactly the Display Name in Google Chat.
# ─────────────────────────────────────────
TEAM_MEMBERS: dict[str, dict] = {
    "Michael": {"group": "Top Leader"},
    "Kaye":    {"group": "Management & Ops"},
    "Anna":    {"group": "Management & Ops"},
    "Ivan":    {"group": "Tech & Dev"},
    "Izzy":    {"group": "Tech & Dev"},
    "Kevin":   {"group": "Tech & Dev"},
    "Milo":    {"group": "Tech & Dev"},
    "Tiffany": {"group": "Ads & Growth"},
    "Danni":   {"group": "Ads & Growth"},
    "Silver":  {"group": "Creative"},
    "Jhon":    {"group": "Creative"},
    "Lovely":  {"group": "Sales Support"},
}

# ─────────────────────────────────────────
# TIMEZONE
# The local time used for check-in/check-out windows.
# Philippines Standard Time = Asia/Manila (UTC+8)
# ─────────────────────────────────────────
TIMEZONE = "Asia/Manila"

# ─────────────────────────────────────────
# CHECK-IN / EOD WINDOWS (in local time)
# ─────────────────────────────────────────
CHECKIN_PROMPT_HOUR   = 8
CHECKIN_PROMPT_MINUTE = 55
CHECKIN_WINDOW_START  = (8, 50)   # 08:50 local
CHECKIN_WINDOW_END    = (9, 15)   # 09:15 local — after this = late
LATE_SWEEP_HOUR       = 9
LATE_SWEEP_MINUTE     = 16        # runs 1 min after window closes

EOD_PROMPT_HOUR       = 16
EOD_PROMPT_MINUTE     = 45
EOD_WINDOW_START      = (16, 45)  # must match prompt time — thread key created at 16:45
EOD_WINDOW_END        = (17,  0)

# ─────────────────────────────────────────
# SPACE NAME MATCHING
# Used to identify the Daily Report Channel from space.displayName
# Partial, case-insensitive match.
# ─────────────────────────────────────────
DAILY_REPORT_SPACE_KEYWORD = "daily report"

# ─────────────────────────────────────────
# SLA TIMER
# SLA_SECONDS = 15  → testing mode (15-second countdown)
# SLA_SECONDS = 900 → production mode (15 minutes)
# Change this one value to flip between test and prod.
# ─────────────────────────────────────────
SLA_MINUTES = 15          # kept for legacy references
SLA_SECONDS = 15          # ← TESTING: set to 900 for production

# ─────────────────────────────────────────
# TASK NAG ESCALATION INTERVALS
# Testing : 15s / 30s / 45s
# Production: 900s (15m) / 1800s (30m) / 2700s (45m)
# ─────────────────────────────────────────
NAG_L1_SECONDS = 900   # 15 min
NAG_L2_SECONDS = 1800  # 30 min
NAG_L3_SECONDS = 2700  # 45 min
