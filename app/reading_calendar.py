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
    # Stable vertical bar slot for the month, assigned by _assign_lanes — see that function's
    # docstring for why this needs to be a fixed per-span property rather than recomputed per day.
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
# Description: Assigns each span a stable vertical bar lane via greedy interval scheduling (by
# start date, tie-broken by book id) — the same "minimum platforms" algorithm used for calendar/
# Gantt-style lane packing: walk spans in chronological order, reusing the lowest-numbered lane
# whose previous occupant has already ended, or opening a new lane if none is free. This
# guarantees two things the per-day bar rendering depends on: any spans overlapping on a given
# day always land in different lanes, and — crucially — a span keeps the *same* lane for its
# entire duration, so its connecting bar renders at one consistent vertical position across every
# day it's active (see DayCell.bar_spans / _calendar_section.html), instead of jumping slot
# whenever some unrelated span enters or leaves that day's mix (the bug this exists to fix: a
# span's bridge segments ending up at two different heights either side of the jump, reading as
# a doubled/broken line rather than one continuous one). Mutates spans in place.
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
    # Description: Every span with a milestone (start or end) on this cell's date, priority order
    # (index 0 is the "winner" — see cover_spans). Shared by cover_spans and _decluttered_spans so
    # both agree on who the winner is without computing the ordering twice independently.
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
    # Description: Milestone spans hidden from *both* cover_spans and bar_spans today: a fanned
    # (non-winner) span whose start is today and which finishes tomorrow. A 1-day-later finish is
    # too short for the connecting bar to read as "one continuous book" rather than two unrelated
    # one-off covers a day apart, so it's decluttered off the start day entirely — cover *and*
    # bar — rather than just the cover. A hidden cover with its bar still showing used to read as
    # a stray, unexplained second line (nothing on screen said *why* a second book's bar had
    # appeared that day). It still gets its own (likely winning) card and bar on its actual finish
    # day. The winner itself is never decluttered by this rule, regardless of its own duration.
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
    # Description: Every span with a milestone (start or end) on this cell's date, ordered for
    # display — index 0 is the "winner" (rendered centered, full prominence; see
    # _calendar_section.html), any further spans fan out behind it by their own type. A book that
    # starts and finishes the same day ranks highest (nothing else can be more specific to this
    # date); next, a span finishing today outranks one merely starting today (finishing is the
    # more notable event of the two); ties break by book id. The template only renders the top 3
    # — this list itself is intentionally left uncapped so no milestone is ever silently dropped
    # by the data layer, except for _decluttered_spans (see there).
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
    # Description: This cell's active_spans — minus anything in _decluttered_spans, so a
    # decluttered book's bar never shows up unexplained on the one day its cover is deliberately
    # hidden — one slot per lane (see _assign_lanes) up to the display cap of 3, index i holding
    # whatever occupies lane i today — or None if lane i is unoccupied today but a *higher* lane
    # isn't (e.g. a long-running book sits alone in lane 1 for days, then a short book joins in
    # lane 0: without a placeholder there, the long book's bar would visually compact up into slot
    # 0 on its lone days and back down to slot 1 whenever lane 0 is briefly occupied — the exact
    # same "jumps slot, bridge segments end up at two different heights" bug _assign_lanes exists
    # to prevent, just still possible with lane 1 was the only occupied lane instead of book-id
    # ordering being the culprit). A leading empty slot never appears alone — bar_spans is
    # None-trimmed at both ends: nothing renders below the highest occupied lane, and lane 0 alone
    # (the overwhelmingly common case) needs no padding at all. Only interior gaps (a lower lane
    # empty while a higher one is occupied) become None.
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
