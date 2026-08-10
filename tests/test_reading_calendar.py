import datetime as dt

from app import reading_calendar as rc
from app.models import Book, TBREntryDetail

TODAY = dt.date(2026, 7, 29)


def _entry(id_, status, started_at=None, finished_at=None, page_count=None, title="Book"):
    book = Book(id=id_, title=title, author=None, isbn=None, cover_url=None, page_count=page_count)
    return TBREntryDetail(
        id=id_, status=status, added_at="2026-01-01", book=book,
        started_at=started_at, finished_at=finished_at,
    )


# --- month_spans ---


def test_month_spans_excludes_wanted_entries():
    entries = [_entry(1, "wanted")]
    assert rc.month_spans(entries, 2026, 7, today=TODAY) == []


def test_month_spans_excludes_entry_without_started_at():
    entries = [_entry(1, "reading", started_at=None)]
    assert rc.month_spans(entries, 2026, 7, today=TODAY) == []


def test_month_spans_excludes_span_entirely_before_month():
    entries = [_entry(1, "finished", started_at="2026-06-01", finished_at="2026-06-05T00:00:00Z")]
    assert rc.month_spans(entries, 2026, 7, today=TODAY) == []


def test_month_spans_excludes_span_entirely_after_month():
    entries = [_entry(1, "finished", started_at="2026-08-01", finished_at="2026-08-05T00:00:00Z")]
    assert rc.month_spans(entries, 2026, 7, today=TODAY) == []


def test_month_spans_includes_partial_overlap():
    # Started in June, finished in July — should still show up in July.
    entries = [_entry(1, "finished", started_at="2026-06-25", finished_at="2026-07-02T00:00:00Z")]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    assert len(spans) == 1
    assert spans[0].start == dt.date(2026, 6, 25)
    assert spans[0].end == dt.date(2026, 7, 2)


def test_month_spans_reading_entry_ends_at_today():
    entries = [_entry(1, "reading", started_at="2026-07-20")]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    assert spans[0].end == TODAY


def test_month_spans_sorted_by_book_id():
    entries = [
        _entry(5, "reading", started_at="2026-07-01"),
        _entry(2, "reading", started_at="2026-07-01"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    assert [s.entry.book.id for s in spans] == [2, 5]


# --- days_active / best_streak ---


def test_days_active_unions_overlapping_spans():
    entries = [
        _entry(1, "finished", started_at="2026-07-01", finished_at="2026-07-03T00:00:00Z"),
        _entry(2, "finished", started_at="2026-07-02", finished_at="2026-07-05T00:00:00Z"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    days = rc.days_active(spans, 2026, 7)
    assert days == {dt.date(2026, 7, d) for d in range(1, 6)}


def test_days_active_clips_to_month_boundaries():
    entries = [_entry(1, "finished", started_at="2026-06-28", finished_at="2026-07-02T00:00:00Z")]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    days = rc.days_active(spans, 2026, 7)
    assert days == {dt.date(2026, 7, 1), dt.date(2026, 7, 2)}  # June 28-30 excluded


def test_best_streak_consecutive_days():
    days = {dt.date(2026, 7, d) for d in (1, 2, 3, 5, 6)}
    assert rc.best_streak(days) == 3  # 1,2,3


def test_best_streak_empty():
    assert rc.best_streak(set()) == 0


def test_streak_does_not_carry_across_month_boundary():
    # A span from Jun 30 to Jul 2 — July's streak must not count Jun 30.
    entries = [_entry(1, "finished", started_at="2026-06-30", finished_at="2026-07-02T00:00:00Z")]
    july_spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    july_days = rc.days_active(july_spans, 2026, 7)
    assert rc.best_streak(july_days) == 2  # Jul 1, Jul 2 only


# --- estimated_pages ---


def test_estimated_pages_distributes_evenly_across_inclusive_days():
    # 100 pages over a 10-day inclusive span (Jul 1-10) = 10 pages/day; all 10 days in July.
    entries = [_entry(1, "finished", started_at="2026-07-01", finished_at="2026-07-10T00:00:00Z", page_count=100)]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    assert rc.estimated_pages(spans, 2026, 7) == 100


def test_estimated_pages_attributes_only_overlapping_days():
    # 100 pages over Jun 26 - Jul 5 (10 inclusive days, 10 pages/day) — only Jul 1-5 (5 days) in July.
    entries = [_entry(1, "finished", started_at="2026-06-26", finished_at="2026-07-05T00:00:00Z", page_count=100)]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    assert rc.estimated_pages(spans, 2026, 7) == 50


def test_estimated_pages_skips_missing_page_count():
    entries = [_entry(1, "finished", started_at="2026-07-01", finished_at="2026-07-05T00:00:00Z", page_count=None)]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    assert rc.estimated_pages(spans, 2026, 7) is None


def test_estimated_pages_none_when_no_spans():
    assert rc.estimated_pages([], 2026, 7) is None


# --- calendar_grid ---


def test_calendar_grid_includes_leading_and_trailing_days():
    grid = rc.calendar_grid(2026, 7, [], today=TODAY)
    first_cell = grid[0][0]
    last_cell = grid[-1][-1]
    # July 1, 2026 is a Wednesday — grid starts on the preceding Sunday (June 28).
    assert first_cell.date == dt.date(2026, 6, 28)
    assert first_cell.in_month is False
    assert last_cell.in_month is False or last_cell.date.month in (7, 8)
    # Every row has exactly 7 cells.
    assert all(len(row) == 7 for row in grid)


def test_calendar_grid_marks_today_and_future():
    grid = rc.calendar_grid(2026, 7, [], today=TODAY)
    flat = [c for row in grid for c in row]
    today_cell = next(c for c in flat if c.date == TODAY)
    future_cell = next(c for c in flat if c.date == TODAY + dt.timedelta(days=1))
    past_cell = next(c for c in flat if c.date == TODAY - dt.timedelta(days=1))
    assert today_cell.is_today is True
    assert today_cell.is_future is False
    assert future_cell.is_future is True
    assert past_cell.is_future is False


def test_calendar_grid_places_active_spans_on_covered_days():
    entries = [_entry(1, "reading", started_at="2026-07-10")]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = [c for row in grid for c in row]
    day_10 = next(c for c in flat if c.date == dt.date(2026, 7, 10))
    day_9 = next(c for c in flat if c.date == dt.date(2026, 7, 9))
    assert len(day_10.active_spans) == 1
    assert [s.entry.id for s in day_10.cover_spans] == [1]
    assert day_9.active_spans == []


def test_calendar_grid_stacks_multiple_concurrent_spans():
    entries = [
        _entry(1, "reading", started_at="2026-07-01"),
        _entry(2, "reading", started_at="2026-07-01"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = [c for row in grid for c in row]
    day_1 = next(c for c in flat if c.date == dt.date(2026, 7, 1))
    assert len(day_1.active_spans) == 2
    assert [s.entry.id for s in day_1.cover_spans] == [1, 2]  # lower book id sorts first


def test_calendar_grid_prioritizes_finished_book_over_started_on_same_day():
    # Book 1 starts on Jul 10, the same day book 2 (higher priority despite a higher id) finishes
    # — the finished book should rank ahead in the stack, not just whichever sorts first by id.
    entries = [
        _entry(2, "finished", started_at="2026-07-01", finished_at="2026-07-10T00:00:00Z"),
        _entry(1, "reading", started_at="2026-07-10"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = [c for row in grid for c in row]
    day_10 = next(c for c in flat if c.date == dt.date(2026, 7, 10))
    assert len(day_10.active_spans) == 2
    # finished today ranks ahead of started today, even though book 1 sorts first by id — but
    # both are still present, unlike the old primary_span model which dropped one entirely.
    assert [s.entry.id for s in day_10.cover_spans] == [2, 1]


def test_calendar_grid_same_day_read_ranks_above_a_same_day_finish_and_start():
    # Reported bug: book 2 finishes, novella 3 starts-and-finishes, and book 1 starts — all on
    # the same date. The old one-winner-plus-one-rescue model dropped the novella entirely once
    # a third span joined the collision; cover_spans must keep all three, novella first, then the
    # regular finish (book 2) ahead of the plain start (book 1).
    entries = [
        _entry(2, "finished", started_at="2026-07-01", finished_at="2026-07-10T00:00:00Z"),
        _entry(3, "finished", started_at="2026-07-10", finished_at="2026-07-10T00:00:00Z"),
        _entry(1, "reading", started_at="2026-07-10"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = [c for row in grid for c in row]
    day_10 = next(c for c in flat if c.date == dt.date(2026, 7, 10))
    assert [s.entry.id for s in day_10.cover_spans] == [3, 2, 1]


def test_calendar_grid_cover_spans_uncapped_for_overflow():
    # 4+ same-day milestones must all still appear — capping the fan to a legible count is a
    # template/CSS concern (see _calendar_section.html), the data layer never drops a milestone.
    entries = [
        _entry(1, "reading", started_at="2026-07-10"),
        _entry(2, "reading", started_at="2026-07-10"),
        _entry(3, "reading", started_at="2026-07-10"),
        _entry(4, "reading", started_at="2026-07-10"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = [c for row in grid for c in row]
    day_10 = next(c for c in flat if c.date == dt.date(2026, 7, 10))
    assert len(day_10.cover_spans) == 4


# --- cover_spans: short-fanned-start declutter ---


def test_calendar_grid_declutters_a_fanned_short_start_that_finishes_tomorrow():
    # Book 2 is a 2-day read: starts Jul 10, finishes Jul 11. Book 1 finishes Jul 10 (a longer,
    # unrelated read) and outranks book 2's start that same day, so book 2 would only ever be
    # fanned, never the winner, on Jul 10 — and since it finishes the very next day, its start-day
    # card is decluttered away entirely rather than shown fanned for one day only to vanish.
    entries = [
        _entry(1, "finished", started_at="2026-07-01", finished_at="2026-07-10T00:00:00Z"),
        _entry(2, "finished", started_at="2026-07-10", finished_at="2026-07-11T00:00:00Z"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = [c for row in grid for c in row]
    day_10 = next(c for c in flat if c.date == dt.date(2026, 7, 10))
    day_11 = next(c for c in flat if c.date == dt.date(2026, 7, 11))
    # Book 2 doesn't appear at all on its start day...
    assert [s.entry.id for s in day_10.cover_spans] == [1]
    # ...but still gets its own card on its actual finish day.
    assert [s.entry.id for s in day_11.cover_spans] == [2]


def test_calendar_grid_does_not_declutter_the_winner_even_if_short():
    # A 2-day read with nothing competing for the start day is the winner by default (the only
    # span present) — the declutter rule only ever drops a *fanned* (non-winner) short start, so
    # it must still render.
    entries = [_entry(1, "finished", started_at="2026-07-10", finished_at="2026-07-11T00:00:00Z")]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = [c for row in grid for c in row]
    day_10 = next(c for c in flat if c.date == dt.date(2026, 7, 10))
    assert [s.entry.id for s in day_10.cover_spans] == [1]


def test_calendar_grid_does_not_declutter_a_fanned_start_that_finishes_later_than_tomorrow():
    # Book 2 spans 4 days (Jul 10-13) — long enough for the connecting bar to read as one
    # continuous book, so a fanned start on Jul 10 is *not* decluttered, unlike the 2-day case.
    entries = [
        _entry(1, "finished", started_at="2026-07-01", finished_at="2026-07-10T00:00:00Z"),
        _entry(2, "finished", started_at="2026-07-10", finished_at="2026-07-13T00:00:00Z"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = [c for row in grid for c in row]
    day_10 = next(c for c in flat if c.date == dt.date(2026, 7, 10))
    assert [s.entry.id for s in day_10.cover_spans] == [1, 2]


# --- lane assignment / bar_spans (stable vertical bar slot) ---


def test_month_spans_reuses_a_freed_lane_for_a_later_span():
    # Book 2 (Jul 1-5) and book 3 (Jul 1-2) overlap, so they take lanes 0 and 1. Book 4 starts
    # Jul 3, after book 3 has ended — it should reuse lane 1, not open a new lane 2.
    entries = [
        _entry(2, "finished", started_at="2026-07-01", finished_at="2026-07-05T00:00:00Z"),
        _entry(3, "finished", started_at="2026-07-01", finished_at="2026-07-02T00:00:00Z"),
        _entry(4, "finished", started_at="2026-07-03", finished_at="2026-07-04T00:00:00Z"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    lanes = {s.entry.id: s.lane for s in spans}
    assert lanes[2] == 0
    assert lanes[3] == 1
    assert lanes[4] == 1


def test_month_spans_gives_concurrently_overlapping_spans_different_lanes():
    # Three books all active Jul 1-2 must land in three distinct lanes.
    entries = [
        _entry(1, "finished", started_at="2026-07-01", finished_at="2026-07-02T00:00:00Z"),
        _entry(2, "finished", started_at="2026-07-01", finished_at="2026-07-02T00:00:00Z"),
        _entry(3, "finished", started_at="2026-07-01", finished_at="2026-07-02T00:00:00Z"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    lanes = {s.entry.id: s.lane for s in spans}
    assert len(set(lanes.values())) == 3


def test_bar_spans_keeps_a_continuing_span_in_a_stable_lane_across_a_mid_span_collision():
    # Reported bug: book 2 runs Jul 1-5; book 1 is an unrelated same-day read on Jul 3 only. Under
    # the old book-id-ordered slice, book 2 sat in slot 0 on Jul 1-2/4-5 but got bumped to slot 1
    # on Jul 3 (book 1 sorts first by id) — its bridging bar segments ended up at two different
    # heights either side of that jump, rendering as a doubled/broken line instead of one
    # continuous one. With lane assignment, book 2 (the earlier-starting span) always owns lane 0.
    entries = [
        _entry(2, "finished", started_at="2026-07-01", finished_at="2026-07-05T00:00:00Z"),
        _entry(1, "finished", started_at="2026-07-03", finished_at="2026-07-03T00:00:00Z"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = {c.date: c for row in grid for c in row}

    book_2_lanes = {
        day: next(s.lane for s in flat[dt.date(2026, 7, day)].bar_spans if s.entry.id == 2)
        for day in range(1, 6)
    }
    assert len(set(book_2_lanes.values())) == 1  # same lane every single day it's active

    day_3 = flat[dt.date(2026, 7, 3)]
    lanes_on_day_3 = {s.entry.id: s.lane for s in day_3.bar_spans}
    assert lanes_on_day_3[1] != lanes_on_day_3[2]  # still distinct lanes when both are active


def test_bar_spans_caps_at_three_lowest_lanes():
    # 4 concurrently overlapping spans — only the lowest 3 lanes should render as bars.
    entries = [
        _entry(i, "finished", started_at="2026-07-01", finished_at="2026-07-05T00:00:00Z")
        for i in range(1, 5)
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = [c for row in grid for c in row]
    day_1 = next(c for c in flat if c.date == dt.date(2026, 7, 1))
    assert len(day_1.bar_spans) == 3
    assert sorted(s.lane for s in day_1.bar_spans) == [0, 1, 2]


def test_bar_spans_pads_a_gap_so_a_higher_lane_never_shifts_row():
    # Reported bug (real-world case): book 1 (Jul 1-5) forces book 2 (Jul 5-14, the long one) into
    # lane 1, since they share Jul 5. Book 3 (Jul 14-16) then reuses lane 0 on Jul 14, the day
    # book 2 ends. On Jul 6-13, book 2 is the *only* active span — without padding, it would
    # render at index 0 (compacting up) on those lone days and index 1 (its real lane) on Jul 5
    # and Jul 14, visually jumping row right at both ends of its own run. Index 1 must hold book 2
    # on every single day it's active, with index 0 a None placeholder whenever nothing else
    # shares that day.
    entries = [
        _entry(1, "finished", started_at="2026-07-01", finished_at="2026-07-05T00:00:00Z"),
        _entry(2, "finished", started_at="2026-07-05", finished_at="2026-07-14T00:00:00Z"),
        _entry(3, "finished", started_at="2026-07-14", finished_at="2026-07-16T00:00:00Z"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    lanes = {s.entry.id: s.lane for s in spans}
    assert lanes[2] == 1  # forced off lane 0 by book 1 still being active on its own start day

    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = {c.date: c for row in grid for c in row}

    for day in range(5, 15):  # book 2's entire active range, Jul 5-14 inclusive
        bar_spans = flat[dt.date(2026, 7, day)].bar_spans
        assert len(bar_spans) == 2
        assert bar_spans[1] is not None and bar_spans[1].entry.id == 2

    for day in range(6, 14):  # the days book 2 is alone — index 0 must be an explicit gap
        assert flat[dt.date(2026, 7, day)].bar_spans[0] is None

    # On the days it shares with book 1 or book 3, index 0 holds the real neighbor, not a gap.
    assert flat[dt.date(2026, 7, 5)].bar_spans[0].entry.id == 1
    assert flat[dt.date(2026, 7, 14)].bar_spans[0].entry.id == 3


def test_bar_spans_hides_a_decluttered_span_not_just_its_cover():
    # Reported bug (real-world case): "Red Seas Under Red Skies" finishes Jul 24; "Artificial
    # Condition" starts Jul 24 too and finishes Jul 25 (a 2-day read) — a fanned, non-winner,
    # short start, so its *cover* is decluttered off Jul 24 by cover_spans. Its bar used to still
    # render there anyway, appearing as an unexplained second line with no cover to justify it —
    # reading as a stray duplicate connector rather than a second book. The bar must be hidden
    # too, on exactly the day the cover is, and show normally from its own finish day onward.
    entries = [
        _entry(1, "finished", started_at="2026-07-17", finished_at="2026-07-24T00:00:00Z"),
        _entry(2, "finished", started_at="2026-07-24", finished_at="2026-07-25T00:00:00Z"),
    ]
    spans = rc.month_spans(entries, 2026, 7, today=TODAY)
    grid = rc.calendar_grid(2026, 7, spans, today=TODAY)
    flat = {c.date: c for row in grid for c in row}

    day_24 = flat[dt.date(2026, 7, 24)]
    assert [s.entry.id for s in day_24.cover_spans] == [1]  # book 2's cover already decluttered
    assert [s.entry.id for s in day_24.bar_spans if s is not None] == [1]  # bar hidden too

    day_25 = flat[dt.date(2026, 7, 25)]
    assert [s.entry.id for s in day_25.cover_spans] == [2]
    assert [s.entry.id for s in day_25.bar_spans if s is not None] == [2]
