from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

# Shared by main.py, stat_tiles.py, and reading_calendar.py - all parse the same
# "...Z"-suffixed-Instant-or-bare-ISO-date" strings Grimmory/this app produce.

# Function Name: today_utc
# Description: Returns the current date in UTC.
# Parameters: None
# Returns: Current date in UTC (date)
def today_utc() -> date:
    # Explicit UTC, not the OS timezone - every date derived from Grimmory data is UTC (see parse_instant).
    return datetime.now(timezone.utc).date()

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

# Function Name: longest_consecutive_run
# Description: Length of the longest run of calendar-consecutive dates in a collection.
# Parameters:
# - days: Dates to scan — order and duplicates don't matter, sorted/deduplicated internally.
# Returns: Length of the longest consecutive run (int), 0 if `days` is empty.
def longest_consecutive_run(days) -> int:
    # Shared by stat_tiles.build_book_tiles' "Best Streak" tile (per-book reading days) and
    # reading_calendar.best_streak (a calendar month's active days) - same algorithm, different
    # day sets, previously implemented twice.
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
