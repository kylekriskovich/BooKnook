from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# Shared by main.py, stat_tiles.py, and reading_calendar.py - all parse the same
# "...Z"-suffixed-Instant-or-bare-ISO-date" strings Grimmory/this app produce.

# Deployment-wide fallback for converting a Grimmory Instant to a calendar day - mirrors
# Grimmory's own ZoneId.systemDefault() pattern (one zone per deployment, set via the container's
# TZ/here TBR_TIMEZONE env var), not a per-user setting. Every bucketing function below takes an
# optional `zone` override so a real per-user zone can be threaded in later without touching the
# date math itself. Resolved once at import - restart the app after changing TBR_TIMEZONE.
DEFAULT_ZONE = ZoneInfo(os.environ.get("TBR_TIMEZONE", "UTC"))

# Function Name: today_utc
# Description: Returns the current date in UTC.
# Parameters: None
# Returns: Current date in UTC (date)
def today_utc() -> date:
    # Explicit UTC, not the OS timezone - every date derived from Grimmory data is UTC (see parse_instant).
    return datetime.now(timezone.utc).date()

# Function Name: today_local
# Description: Returns the current date in the given (or deployment-default) timezone.
# Parameters:
# - zone (Optional[ZoneInfo]): timezone to use; defaults to DEFAULT_ZONE.
# Returns: Current local date (date)
def today_local(zone: Optional[ZoneInfo] = None) -> date:
    return datetime.now(zone or DEFAULT_ZONE).date()

# Function Name: parse_instant
# Description: Parses an ISO 8601 instant string into a datetime object.
# Parameters:
# - instant_str (Optional[str]): ISO 8601 instant string.
# Returns: Parsed datetime object or None if parsing fails.
def parse_instant(instant_str: Optional[str]) -> Optional[datetime]:
    if not instant_str:
        return None
    try:
        return datetime.fromisoformat(instant_str.replace("Z", "+00:00"))
    except ValueError:
        return None

# Function Name: parse_date
# Description: Parses an ISO 8601 date string into a date object.
# Parameters:
# - date_str (Optional[str]): ISO 8601 date string.
# Returns: Parsed date object or None if parsing fails.
def parse_date(date_str: Optional[str]) -> Optional[date]:
    out_date = parse_instant(date_str)
    return out_date.date() if out_date else None

# Function Name: instant_to_local_date
# Description: Converts a Grimmory Instant (or a bare local date, from manual user entry) to a
#   calendar date - the former is shifted into the given/default timezone first, the latter is
#   returned untouched since it carries no time-of-day to reinterpret.
# Parameters:
# - instant_str (Optional[str]): "...Z"-suffixed Instant, or a bare "YYYY-MM-DD" date.
# - zone (Optional[ZoneInfo]): timezone to bucket a real Instant into; defaults to DEFAULT_ZONE.
# Returns: Local calendar date, or None if parsing fails.
def instant_to_local_date(instant_str: Optional[str], zone: Optional[ZoneInfo] = None) -> Optional[date]:
    parsed = parse_instant(instant_str)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.date()
    return parsed.astimezone(zone or DEFAULT_ZONE).date()

# Function Name: longest_consecutive_run
# Description: Length of the longest run of calendar-consecutive dates in a collection.
# Parameters:
# - days: Dates to scan — order and duplicates don't matter, sorted/deduplicated internally.
# Returns: Length of the longest consecutive run (int), 0 if `days` is empty.
def longest_consecutive_run(days) -> int:
    # Shared by stat_tiles' "Best Streak" tile and reading_calendar.best_streak.
    ordered = sorted(set(days))
    if not ordered:
        return 0
    longest = current = 1
    for prev, curr in zip(ordered, ordered[1:]):
        if (curr - prev).days == 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest
