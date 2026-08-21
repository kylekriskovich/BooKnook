from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from app.dates import longest_consecutive_run, parse_date, today_utc
from app.models import TBREntryDetail


@dataclass
class BookSpan:
    entry: TBREntryDetail
    start: date
    end: date
    # Stable vertical bar slot for the month, assigned by _assign_lanes.
    lane: int = 0

    @property
    def days_inclusive(self) -> int:
        return (self.end - self.start).days + 1


# Function Name: _book_span
# Description: Builds a BookSpan for an entry's active date range, or None if it can't be placed.
# Parameters:
# - entry (TBREntryDetail): the TBR entry to convert.
# - today (date): current date, used as the end date for entries still being read.
# Returns: BookSpan covering the entry's active date range, or None if it can't be placed.
def _book_span(entry: TBREntryDetail, today: date) -> Optional[BookSpan]:
    if entry.status not in ("reading", "finished") or not entry.started_at:
        return None
    start = parse_date(entry.started_at)
    if start is None:
        return None
    if entry.status == "finished":
        end = parse_date(entry.finished_at) if entry.finished_at else start
        if end is None:
            end = start
    else:
        end = today
    if end < start:
        end = start
    return BookSpan(entry=entry, start=start, end=end)

# Function Name: _all_spans
# Description: Converts every placeable entry into a BookSpan.
# Parameters:
# - entries (list[TBREntryDetail]): TBR entries to convert.
# - today (Optional[date]): current date; defaults to today_utc() if not given.
# Returns: List of BookSpans for entries that could be placed on a calendar.
def _all_spans(entries: list[TBREntryDetail], today: Optional[date] = None) -> list[BookSpan]:
    today = today or today_utc()
    return [span for entry in entries if (span := _book_span(entry, today)) is not None]

# Function Name: _assign_lanes
# Description: Assigns each span a stable vertical bar lane via greedy interval scheduling
#   ("minimum platforms"): reuse the lowest-numbered lane whose occupant has ended, else open a
#   new one. Keeps a span in the same lane for its whole duration so its bar doesn't jump vertical
#   position mid-run. Mutates spans in place.
# Parameters:
# - spans (list[BookSpan]): spans to assign lanes to.
# Returns: None.
def _assign_lanes(spans: list[BookSpan]) -> None:
    lane_ends: list[date] = []
    for span in sorted(spans, key=lambda s: (s.start, s.entry.book.id)):
        for lane, occupied_until in enumerate(lane_ends):
            if occupied_until < span.start:
                lane_ends[lane] = span.end
                span.lane = lane
                break
        else:
            span.lane = len(lane_ends)
            lane_ends.append(span.end)

# Function Name: month_spans
# Description: Returns spans overlapping the given month, sorted by book id for stable placement.
# Parameters:
# - entries (list[TBREntryDetail]): TBR entries to consider.
# - year (int): calendar year.
# - month (int): calendar month (1-12).
# - today (Optional[date]): current date; defaults to today_utc() if not given.
# Returns: Spans whose range overlaps the month, sorted by book id.
def month_spans(
    entries: list[TBREntryDetail], year: int, month: int, today: Optional[date] = None
) -> list[BookSpan]:
    month_start = date(year, month, 1)
    month_end = _last_day_of_month(year, month)
    spans = [
        span
        for span in _all_spans(entries, today)
        if span.start <= month_end and span.end >= month_start
    ]
    _assign_lanes(spans)
    return sorted(spans, key=lambda span: span.entry.book.id)

# Function Name: _last_day_of_month
# Description: Returns the last calendar date of the given month.
# Parameters:
# - year (int): calendar year.
# - month (int): calendar month (1-12).
# Returns: Date of the last day of the month.
def _last_day_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])

# Function Name: days_active
# Description: Computes the union of days within the month covered by any span.
# Parameters:
# - spans (list[BookSpan]): spans to union over.
# - year (int): calendar year.
# - month (int): calendar month (1-12).
# Returns: Set of dates within the month covered by at least one span.
def days_active(spans: list[BookSpan], year: int, month: int) -> set[date]:
    month_start = date(year, month, 1)
    month_end = _last_day_of_month(year, month)
    days: set[date] = set()
    for span in spans:
        day = max(span.start, month_start)
        end = min(span.end, month_end)
        while day <= end:
            days.add(day)
            day += timedelta(days=1)
    return days

# Function Name: best_streak
# Description: Finds the longest run of consecutive dates in a set of days.
# Parameters:
# - days (set[date]): dates to scan, e.g. one month's worth from days_active.
# Returns: Length of the longest consecutive run of dates.
def best_streak(days: set[date]) -> int:
    return longest_consecutive_run(days)

# Function Name: estimated_pages
# Description: Estimates the month's pages read by distributing each book's pages across its active days.
# Parameters:
# - spans (list[BookSpan]): spans to estimate over.
# - year (int): calendar year.
# - month (int): calendar month (1-12).
# Returns: Rounded estimated page count, or None if no span had a known page_count.
def estimated_pages(spans: list[BookSpan], year: int, month: int) -> Optional[int]:
    month_start = date(year, month, 1)
    month_end = _last_day_of_month(year, month)
    total = 0.0
    counted = False
    for span in spans:
        page_count = span.entry.book.page_count
        if not page_count:
            continue
        overlap_start = max(span.start, month_start)
        overlap_end = min(span.end, month_end)
        if overlap_start > overlap_end:
            continue
        overlap_days = (overlap_end - overlap_start).days + 1
        total += page_count / span.days_inclusive * overlap_days
        counted = True
    return round(total) if counted else None


@dataclass
class DayCell:
    date: date
    in_month: bool
    is_today: bool
    is_future: bool
    active_spans: list[BookSpan] = field(default_factory=list)

    # Function Name: _milestone_order
    # Description: Spans with a milestone (start or end) on this date, priority order - index 0 is
    #   the "winner" (see cover_spans). Shared with _decluttered_spans so both agree on the winner.
    # Returns: Milestone BookSpans, highest display priority first.
    def _milestone_order(self) -> list[BookSpan]:
        milestones = [
            span
            for span in self.active_spans
            if span.start == self.date or span.end == self.date
        ]

        def sort_key(span: BookSpan) -> tuple[int, int]:
            if span.start == span.end:
                tier = 0  # same-day read
            elif span.end == self.date:
                tier = 1  # finished today
            else:
                tier = 2  # started today only
            return (tier, span.entry.book.id)

        return sorted(milestones, key=sort_key)

    # Function Name: _decluttered_spans
    # Description: Non-winner milestone spans hidden entirely (cover and bar) today: one that
    #   starts today and finishes tomorrow - too short a gap for the bar to read as one continuous
    #   book rather than two stray one-off covers.
    # Returns: Non-winner milestone spans to hide entirely today.
    @property
    def _decluttered_spans(self) -> list[BookSpan]:
        ordered = self._milestone_order()
        if not ordered:
            return []
        _winner, rest = ordered[0], ordered[1:]
        return [
            span
            for span in rest
            if span.start == self.date and span.end - span.start == timedelta(days=1)
        ]

    # Function Name: cover_spans
    # Description: Spans with a milestone on this date, display order - index 0 is the "winner"
    #   (rendered centered/prominent), rest fan out behind it. Same-day start+finish ranks
    #   highest, then a finish outranks a mere start, ties by book id. Uncapped here (the template
    #   caps at 3) except for _decluttered_spans.
    # Returns: BookSpans with a milestone on this date, highest display priority first.
    @property
    def cover_spans(self) -> list[BookSpan]:
        ordered = self._milestone_order()
        if not ordered:
            return ordered
        winner, rest = ordered[0], ordered[1:]
        declutter = self._decluttered_spans
        rest = [span for span in rest if span not in declutter]
        return [winner, *rest]

    # Function Name: bar_spans
    # Description: active_spans minus _decluttered_spans, one slot per lane up to the display cap
    #   of 3 (index == lane). None-trimmed at both ends - only an interior gap (a lower lane empty
    #   while a higher one is occupied) becomes None, so a lane doesn't visually shift position
    #   depending on which lanes above/below it happen to be occupied that day.
    # Returns: Up to 3 slots, index == lane, real BookSpans or None for an occupied-above gap.
    @property
    def bar_spans(self) -> list[Optional[BookSpan]]:
        declutter = self._decluttered_spans
        visible = [span for span in self.active_spans if span not in declutter]
        by_lane = {span.lane: span for span in visible if span.lane < 3}
        if not by_lane:
            return []
        highest_lane = max(by_lane)
        return [by_lane.get(lane) for lane in range(highest_lane + 1)]

# Function Name: calendar_grid
# Description: Builds a Sun-Sat month grid of DayCells, including adjacent-month days to fill each row.
# Parameters:
# - year (int): calendar year.
# - month (int): calendar month (1-12).
# - spans (list[BookSpan]): spans to place on the grid.
# - today (Optional[date]): current date; defaults to today_utc() if not given.
# Returns: List of week rows, each a list of 7 DayCells.
def calendar_grid(
    year: int, month: int, spans: list[BookSpan], today: Optional[date] = None
) -> list[list[DayCell]]:
    today = today or today_utc()
    cal = calendar.Calendar(firstweekday=6)  # Sunday first
    all_dates = list(cal.itermonthdates(year, month))

    cells = []
    for day in all_dates:
        active = [span for span in spans if span.start <= day <= span.end]
        cells.append(
            DayCell(
                date=day,
                in_month=(day.month == month),
                is_today=(day == today),
                is_future=(day > today),
                active_spans=active,
            )
        )

    return [cells[i : i + 7] for i in range(0, len(cells), 7)]
