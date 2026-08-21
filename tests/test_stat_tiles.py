import datetime as dt

from app import stat_tiles
from app.models import Book, TBREntryDetail


def _entry(
    status="reading",
    started_at=None,
    finished_at=None,
    page_count=None,
    format=None,
    audiobook_progress_percent=None,
):
    book = Book(
        id=1, title="Dune", author="Frank Herbert", isbn="111", cover_url=None,
        page_count=page_count, format=format,
    )
    return TBREntryDetail(
        id=1, status=status, added_at="2026-01-01", book=book,
        started_at=started_at, finished_at=finished_at,
        audiobook_progress_percent=audiobook_progress_percent,
    )


def _session(day, start_progress, end_progress, duration_seconds=1800, hour="10"):
    """start_progress/end_progress are 0-100 percentages of the book — confirmed against real
    Grimmory session data 2026-07-29 (progressDelta values sum to match the final endProgress on
    a 0-100 scale, not 0.0-1.0)."""
    delta = round(end_progress - start_progress, 4)
    return {
        "startTime": f"{day}T{hour}:00:00Z",
        "endTime": f"{day}T{hour}:30:00Z",
        "durationSeconds": duration_seconds,
        "startProgress": start_progress,
        "endProgress": end_progress,
        "progressDelta": delta,
    }


def _audiobook_session(day, duration_seconds=1800, hour="10"):
    """Shaped like a real Grimmory AUDIOBOOK reading-session payload: bookType is set, but
    startProgress/endProgress/progressDelta are always null - Grimmory's own audiobook player
    never populates them (confirmed against its frontend source, 2026-08-21), unlike a normal
    ebook session's _session() above."""
    return {
        "bookType": "AUDIOBOOK",
        "startTime": f"{day}T{hour}:00:00Z",
        "endTime": f"{day}T{hour}:30:00Z",
        "durationSeconds": duration_seconds,
        "startProgress": None,
        "endProgress": None,
        "progressDelta": None,
    }


# --- build_book_tiles: no session data ---


def test_no_sessions_no_data_yields_no_tiles():
    entry = _entry(status="reading")
    assert stat_tiles.build_book_tiles(entry, []) == []


def test_no_sessions_finished_yields_days_to_complete():
    entry = _entry(status="finished", started_at="2026-01-01", finished_at="2026-01-11T00:00:00Z")
    tiles = stat_tiles.build_book_tiles(entry, [])
    # Inclusive day count: Jan 1 through Jan 11 is 11 calendar days, not a 10-day difference.
    assert {"label": "Days to Complete", "value": "11d"} in tiles


def test_days_to_complete_same_day_is_1_not_0():
    # Regression test: started and finished on the same calendar day must show "1d", not "0d" —
    # the bug the user caught reading Animal Farm cover to cover in a single day (2026-07-29).
    entry = _entry(status="finished", started_at="2026-01-01", finished_at="2026-01-01T18:00:00Z")
    tiles = stat_tiles.build_book_tiles(entry, [])
    assert {"label": "Days to Complete", "value": "1d"} in tiles


def test_no_sessions_finished_with_finished_before_started_is_guarded():
    entry = _entry(status="finished", started_at="2026-01-11", finished_at="2026-01-01T00:00:00Z")
    tiles = stat_tiles.build_book_tiles(entry, [])
    assert not any(t["label"] == "Days to Complete" for t in tiles)


def test_no_sessions_reading_with_page_count_falls_back_to_pages_per_day():
    entry = _entry(status="reading", started_at="2026-01-01", page_count=300)
    tiles = stat_tiles.build_book_tiles(entry, [])
    labels = {t["label"] for t in tiles}
    assert "Pages per day" in labels
    assert "Pages per session" not in labels


def test_no_sessions_no_page_count_no_fallback_tile():
    entry = _entry(status="reading", started_at="2026-01-01", page_count=None)
    tiles = stat_tiles.build_book_tiles(entry, [])
    assert tiles == []


# --- build_book_tiles: with session data ---


def test_sessions_yield_reading_days_and_time_spent():
    entry = _entry(status="reading")
    sessions = [
        _session("2026-01-01", 0, 10),
        _session("2026-01-03", 10, 20),
    ]
    tiles = stat_tiles.build_book_tiles(entry, sessions)
    by_label = {t["label"]: t["value"] for t in tiles}
    assert by_label["Reading Days"] == "2"
    assert by_label["Time Spent Reading"] == "1h"
    # Non-consecutive days (Jan 1, Jan 3) — no streak tile.
    assert "Best Streak" not in by_label


def test_reading_days_excludes_zero_delta_sessions():
    # Regression test: a zero-progress "opened the book, didn't read" session must not count as a
    # reading day — must agree with first_meaningful_session_date's started_at guess, which
    # already excluded these (confirmed 2026-07-29: not filtering these out consistently produced
    # a "Reading Days" span wider than "Days to Complete" implied, on the same page).
    entry = _entry(status="reading")
    sessions = [
        _session("2025-12-31", 0, 0),  # zero delta, day before any real reading
        _session("2026-01-01", 0, 10),
        _session("2026-01-02", 10, 10),  # zero delta, between two real reading days
        _session("2026-01-03", 10, 20),
    ]
    tiles = stat_tiles.build_book_tiles(entry, sessions)
    by_label = {t["label"]: t["value"] for t in tiles}
    assert by_label["Reading Days"] == "2"  # only Jan 1 and Jan 3
    assert "Best Streak" not in by_label  # Jan 1 and Jan 3 aren't consecutive


def test_consecutive_days_yield_best_streak():
    entry = _entry(status="reading")
    sessions = [
        _session("2026-01-01", 0, 10),
        _session("2026-01-02", 10, 20),
        _session("2026-01-03", 20, 30),
    ]
    tiles = stat_tiles.build_book_tiles(entry, sessions)
    by_label = {t["label"]: t["value"] for t in tiles}
    assert by_label["Best Streak"] == "3 days"


# --- audiobook sessions: progressDelta/endProgress always null, durationSeconds is the only
# reliable activity signal Grimmory provides for these (see _audiobook_session above) ---


def test_audiobook_sessions_with_no_progress_still_count_as_activity():
    entry = _entry(status="reading", format="AUDIOBOOK")
    sessions = [
        _audiobook_session("2026-01-01", duration_seconds=1800),
        _audiobook_session("2026-01-02", duration_seconds=1800),
    ]
    tiles = stat_tiles.build_book_tiles(entry, sessions)
    by_label = {t["label"]: t["value"] for t in tiles}
    assert by_label["Listening Days"] == "2"
    assert by_label["Best Streak"] == "2 days"
    assert by_label["Time Spent Listening"] == "1h"


def test_audiobook_tiles_use_generic_labels_when_book_format_unknown():
    # entry.book.format (synced from Grimmory's primaryFile.bookType) is the source of truth for
    # which labels to use, not a session's own bookType - a book that hasn't been format-synced
    # yet must not show mislabeled "Listening" tiles just because its sessions happen to be
    # AUDIOBOOK-typed.
    entry = _entry(status="reading", format=None)
    sessions = [_audiobook_session("2026-01-01", duration_seconds=1800)]
    tiles = stat_tiles.build_book_tiles(entry, sessions)
    by_label = {t["label"]: t["value"] for t in tiles}
    assert by_label["Reading Days"] == "1"
    assert by_label["Time Spent Reading"] == "30m"


def test_audiobook_session_with_zero_duration_is_not_meaningful():
    entry = _entry(status="reading")
    sessions = [_audiobook_session("2026-01-01", duration_seconds=0)]
    tiles = stat_tiles.build_book_tiles(entry, sessions)
    assert tiles == []


def test_non_audiobook_session_with_null_progress_still_excluded():
    # A null-progress session for a non-audiobook type must not get swept in by the AUDIOBOOK
    # carve-out - _has_meaningful_progress's duration fallback is keyed specifically on
    # bookType == "AUDIOBOOK", not "no progress data at all".
    entry = _entry(status="reading")
    sessions = [
        {
            "startTime": "2026-01-01T10:00:00Z",
            "endTime": "2026-01-01T10:30:00Z",
            "durationSeconds": 1800,
            "startProgress": None,
            "endProgress": None,
            "progressDelta": None,
        }
    ]
    tiles = stat_tiles.build_book_tiles(entry, sessions)
    assert tiles == []


def test_burndown_still_empty_for_audiobook_sessions():
    # burndown_points needs a real endProgress time series, which Grimmory never provides for
    # audiobooks - the duration-based activity fix must not fabricate one.
    sessions = [_audiobook_session("2026-01-01"), _audiobook_session("2026-01-02")]
    assert stat_tiles.burndown_points(sessions) == []


def test_estimated_completion_falls_back_to_audiobook_progress_percent():
    # Session-level progressDelta/endProgress are always null for audiobooks, so the pace/latest-
    # progress used to have no way to compute Estimated Completion here at all even though the
    # exact number it needs (tbr_entries.audiobook_progress_percent) already exists on the entry.
    entry = _entry(status="reading", format="AUDIOBOOK", audiobook_progress_percent=20.0)
    sessions = [
        _audiobook_session("2026-01-01"),
        _audiobook_session("2026-01-02"),
    ]
    tiles = stat_tiles.build_book_tiles(entry, sessions, today=dt.date(2026, 1, 2))
    by_label = {t["label"]: t["value"] for t in tiles}
    # pace = 20% / 2 listening days = 10%/day, remaining = 80% -> 8 days from today (Jan 2) -> Jan 10.
    assert by_label["Estimated Completion"] == "Jan 10, 2026"


def test_estimated_completion_omitted_without_audiobook_progress_percent():
    # No fallback value stored yet (not synced, or a non-audiobook with no session deltas either)
    # - must not fabricate a pace out of nothing.
    entry = _entry(status="reading", format="AUDIOBOOK", audiobook_progress_percent=None)
    sessions = [_audiobook_session("2026-01-01"), _audiobook_session("2026-01-02")]
    tiles = stat_tiles.build_book_tiles(entry, sessions, today=dt.date(2026, 1, 2))
    assert not any(t["label"] == "Estimated Completion" for t in tiles)


def test_pages_per_session_and_best_session_use_page_count():
    entry = _entry(status="reading", page_count=300)
    sessions = [
        _session("2026-01-01", 0, 10),  # 10% of 300 = 30 pages
        _session("2026-01-02", 10, 40),  # 30% of 300 = 90 pages — best
    ]
    tiles = stat_tiles.build_book_tiles(entry, sessions)
    by_label = {t["label"]: t for t in tiles}
    assert by_label["Pages per session"]["value"] == "60"
    assert by_label["Best Session"]["value"] == "90 pages"
    assert by_label["Best Session"]["sub"] == "2026-01-02"


def test_best_session_falls_back_to_percent_without_page_count():
    entry = _entry(status="reading", page_count=None)
    sessions = [_session("2026-01-01", 0, 25)]
    tiles = stat_tiles.build_book_tiles(entry, sessions)
    by_label = {t["label"]: t for t in tiles}
    assert by_label["Best Session"]["value"] == "25%"


def test_estimated_completion_requires_two_reading_days():
    entry = _entry(status="reading")
    tiles = stat_tiles.build_book_tiles(entry, [_session("2026-01-01", 0, 10)])
    assert not any(t["label"] == "Estimated Completion" for t in tiles)


def test_estimated_completion_present_with_pace_and_remaining_progress():
    entry = _entry(status="reading")
    sessions = [
        _session("2026-01-01", 0, 10),
        _session("2026-01-02", 10, 20),
    ]
    tiles = stat_tiles.build_book_tiles(entry, sessions)
    assert any(t["label"] == "Estimated Completion" for t in tiles)


def test_estimated_completion_uses_client_supplied_today_not_server_utc(monkeypatch):
    # Regression test: the server's UTC "today" can lag a viewer's actual local day by up to many
    # hours (see app/main.py:_resolve_client_today) - the estimate must be computed from the
    # caller's own `today`, not whatever today_utc() says.
    monkeypatch.setattr(stat_tiles, "today_utc", lambda: dt.date(2020, 1, 1))
    entry = _entry(status="reading")
    sessions = [
        _session("2026-01-01", 0, 10),
        _session("2026-01-02", 10, 20),
    ]
    tiles = stat_tiles.build_book_tiles(entry, sessions, today=dt.date(2026, 1, 3))
    by_label = {t["label"]: t["value"] for t in tiles}
    # pace = 10%/day, remaining = 80% -> 8 days from the client's today (Jan 3), not from
    # today_utc()'s mocked 2020 date.
    assert by_label["Estimated Completion"] == "Jan 11, 2026"


def test_estimated_completion_omitted_when_finished_status():
    entry = _entry(status="finished")
    sessions = [
        _session("2026-01-01", 0, 10),
        _session("2026-01-02", 10, 100),
    ]
    tiles = stat_tiles.build_book_tiles(entry, sessions)
    assert not any(t["label"] == "Estimated Completion" for t in tiles)


def test_pages_per_day_fallback_uses_client_supplied_today_not_server_utc(monkeypatch):
    monkeypatch.setattr(stat_tiles, "today_utc", lambda: dt.date(2020, 1, 1))
    entry = _entry(status="reading", started_at="2026-01-01", page_count=300)
    tiles = stat_tiles.build_book_tiles(entry, [], today=dt.date(2026, 1, 10))
    by_label = {t["label"]: t["value"] for t in tiles}
    # 10 elapsed days (Jan 1 - Jan 10 inclusive) -> 300 / 10 = 30 pages/day, computed from the
    # client's today, not today_utc()'s mocked 2020 date.
    assert by_label["Pages per day"] == "30"


def test_sessions_present_skip_pages_per_day_fallback():
    # Fallback is only for the no-sessions-at-all case — must not appear alongside real sessions.
    entry = _entry(status="reading", started_at="2026-01-01", page_count=300)
    tiles = stat_tiles.build_book_tiles(entry, [_session("2026-01-01", 0, 10)])
    assert not any(t["label"] == "Pages per day" for t in tiles)


# --- latest_progress ---


def test_latest_progress_empty_sessions():
    assert stat_tiles.latest_progress([]) is None


def test_latest_progress_returns_most_recent_sessions_end_progress():
    sessions = [
        _session("2026-01-03", 20, 30, hour="10"),  # chronologically latest
        _session("2026-01-01", 0, 10, hour="10"),
        _session("2026-01-02", 10, 20, hour="10"),
    ]
    assert stat_tiles.latest_progress(sessions) == 30


def test_latest_progress_uses_time_not_list_order():
    # Same day, different hours — must pick the later one by actual timestamp, not list position.
    sessions = [
        _session("2026-01-01", 10, 50, hour="20"),  # listed first but happens later
        _session("2026-01-01", 0, 10, hour="08"),
    ]
    assert stat_tiles.latest_progress(sessions) == 50


def test_latest_progress_ignores_sessions_missing_end_progress():
    sessions = [
        {"startTime": "2026-01-02T10:00:00Z", "endTime": "2026-01-02T10:30:00Z",
         "durationSeconds": 1800, "startProgress": 10, "endProgress": None, "progressDelta": None},
        _session("2026-01-01", 0, 10),
    ]
    assert stat_tiles.latest_progress(sessions) == 10


# --- first_meaningful_session_date ---


def test_first_meaningful_session_date_empty_sessions():
    assert stat_tiles.first_meaningful_session_date([]) is None


def test_first_meaningful_session_date_all_zero_delta():
    sessions = [_session("2026-01-01", 10, 10), _session("2026-01-02", 10, 10)]
    assert stat_tiles.first_meaningful_session_date(sessions) is None


def test_first_meaningful_session_date_ignores_zero_delta_sessions_before_and_after():
    import datetime as dt

    sessions = [
        _session("2026-01-01", 0, 0),  # zero-delta app-open, before real reading
        _session("2026-01-03", 0, 5),  # first real reading
        _session("2026-01-02", 0, 0),  # zero-delta, out of order, still before Jan 3
        _session("2026-01-04", 5, 10),  # later real reading
    ]
    assert stat_tiles.first_meaningful_session_date(sessions) == dt.date(2026, 1, 3)


def test_first_meaningful_session_date_counts_audiobook_sessions():
    import datetime as dt

    sessions = [_audiobook_session("2026-01-01", duration_seconds=0), _audiobook_session("2026-01-03")]
    assert stat_tiles.first_meaningful_session_date(sessions) == dt.date(2026, 1, 3)


# --- burndown_points ---


def test_burndown_points_empty_for_no_sessions():
    assert stat_tiles.burndown_points([]) == []


def test_burndown_points_sorted_and_deduplicated_by_day():
    sessions = [
        _session("2026-01-02", 10, 20, hour="09"),
        _session("2026-01-01", 0, 10, hour="10"),
        _session("2026-01-01", 10, 15, hour="20"),  # later same-day session wins
    ]
    points = stat_tiles.burndown_points(sessions)
    # A synthetic 100%-remaining point is prepended the day before the first real session — see
    # test_burndown_points_prepends_100_percent_day_before_first_session for that behavior itself.
    assert [d.isoformat() for d, _ in points] == ["2025-12-31", "2026-01-01", "2026-01-02"]
    assert points[1][1] == 85  # 100 - 15 = 85% remaining
    assert points[2][1] == 80  # 100 - 20 = 80% remaining


def test_burndown_points_excludes_zero_delta_sessions():
    sessions = [
        _session("2026-01-01", 0, 10),
        _session("2026-01-02", 10, 10),  # zero delta — must not appear as its own point
        _session("2026-01-03", 10, 20),
    ]
    points = stat_tiles.burndown_points(sessions)
    assert [d.isoformat() for d, _ in points] == ["2025-12-31", "2026-01-01", "2026-01-03"]


def test_burndown_points_prepends_100_percent_day_before_first_session():
    # The chart should always burn down from 0% read (100% remaining) rather than starting at
    # whatever was already left after the first day's own session — otherwise progress reads as
    # having appeared out of nowhere instead of being visibly made on day one.
    sessions = [_session("2026-01-05", 0, 15)]
    points = stat_tiles.burndown_points(sessions)
    assert [d.isoformat() for d, _ in points] == ["2026-01-04", "2026-01-05"]
    assert points[0][1] == 100
    assert points[1][1] == 85  # 100 - 15 = 85% remaining after the first session


# --- burndown_svg_points ---


def test_burndown_svg_points_empty():
    assert stat_tiles.burndown_svg_points([]) == ""


def test_burndown_svg_points_single_point_spans_full_width():
    import datetime as dt

    points = [(dt.date(2026, 1, 1), 50)]
    svg = stat_tiles.burndown_svg_points(points, width=300, height=100)
    assert svg == "0,50.0 300,50.0"


def test_burndown_svg_points_multiple_points_span_width_evenly_when_days_are_consecutive():
    points = [(dt.date(2026, 1, 1), 100), (dt.date(2026, 1, 2), 50), (dt.date(2026, 1, 3), 0)]
    svg = stat_tiles.burndown_svg_points(points, width=300, height=100)
    coords = svg.split(" ")
    assert coords[0] == "0.0,0.0"  # 100% remaining -> top-left
    assert coords[1] == "150.0,50.0"  # midpoint
    assert coords[2] == "300.0,100.0"  # 0% remaining -> bottom-right


def test_burndown_svg_points_x_is_proportional_to_elapsed_days_not_index():
    # A gap between reading sessions (points only exist for days with a session) must show up as
    # a gap on the chart, not be smoothed away by evenly spacing by point index.
    points = [(dt.date(2026, 1, 1), 100), (dt.date(2026, 1, 2), 80), (dt.date(2026, 1, 10), 0)]
    svg = stat_tiles.burndown_svg_points(points, width=900, height=100)
    coords = svg.split(" ")
    assert coords[0] == "0.0,0.0"
    assert coords[1] == "100.0,20.0"  # 1 of 9 elapsed days -> 1/9 of the width, not 1/2
    assert coords[2] == "900.0,100.0"


# --- burndown_day_span ---


def test_burndown_day_span_counts_elapsed_days():
    points = [(dt.date(2026, 1, 1), 100), (dt.date(2026, 1, 10), 0)]
    assert stat_tiles.burndown_day_span(points) == 9


def test_burndown_day_span_zero_for_fewer_than_two_points():
    assert stat_tiles.burndown_day_span([]) == 0
    assert stat_tiles.burndown_day_span([(dt.date(2026, 1, 1), 100)]) == 0


def _finished_entry(entry_id, title, page_count=None, rating=None, started_at=None, finished_at=None):
    book = Book(id=entry_id, title=title, author=None, isbn=None, cover_url=None, page_count=page_count)
    return TBREntryDetail(
        id=entry_id, status="finished", added_at="2026-01-01", book=book,
        started_at=started_at, finished_at=finished_at, rating=rating,
    )


# --- build_collection_tiles ---

_YEAR_WINDOW = (dt.date(2026, 1, 1), dt.date(2026, 12, 31))


def test_build_collection_tiles_empty_entries_shows_zero_finished_only():
    assert stat_tiles.build_collection_tiles([], *_YEAR_WINDOW) == [{"label": "Books finished", "value": "0"}]


def test_build_collection_tiles_books_finished_count():
    entries = [_finished_entry(1, "A"), _finished_entry(2, "B")]
    tiles = stat_tiles.build_collection_tiles(entries, *_YEAR_WINDOW)
    assert {"label": "Books finished", "value": "2"} in tiles


def test_build_collection_tiles_pages_and_longest_shortest():
    # Both spans fall entirely inside the window, so proration doesn't reduce their contribution -
    # this is the "full credit" baseline; see the boundary-crossing test below for partial credit.
    entries = [
        _finished_entry(1, "Short Book", page_count=100, started_at="2026-01-01", finished_at="2026-01-05T00:00:00Z"),
        _finished_entry(2, "Long Book", page_count=300, started_at="2026-01-10", finished_at="2026-01-15T00:00:00Z"),
    ]
    tiles = stat_tiles.build_collection_tiles(entries, *_YEAR_WINDOW)
    by_label = {t["label"]: t for t in tiles}
    assert by_label["Total pages read"]["value"] == "400"
    assert by_label["Avg pages read"]["value"] == "200"
    assert by_label["Longest book"]["value"] == "300"
    assert by_label["Longest book"]["sub"] == "Long Book"
    assert by_label["Shortest book"]["value"] == "100"
    assert by_label["Shortest book"]["sub"] == "Short Book"


def test_build_collection_tiles_prorates_pages_for_span_crossing_window_boundary():
    # A book started 9 days before the window and finished on the window's first day - only 1 of
    # its 10 reading days falls inside the window, so only 1/10 of its pages should count.
    entries = [
        _finished_entry(1, "A", page_count=1000, started_at="2025-12-23", finished_at="2026-01-01T00:00:00Z")
    ]
    tiles = stat_tiles.build_collection_tiles(entries, dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    by_label = {t["label"]: t["value"] for t in tiles}
    assert by_label["Total pages read"] == "100"


def test_build_collection_tiles_longest_shortest_show_real_unprorated_page_count():
    # Longest/Shortest display a specific book's actual length + title - prorating that display
    # would read as a data error ("War and Peace: 100 pages"), so only the sum/average prorate.
    entries = [
        _finished_entry(1, "A", page_count=1000, started_at="2025-12-23", finished_at="2026-01-01T00:00:00Z")
    ]
    tiles = stat_tiles.build_collection_tiles(entries, dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    by_label = {t["label"]: t for t in tiles}
    assert by_label["Longest book"]["value"] == "1,000"


def test_build_collection_tiles_pages_sum_skips_entries_missing_started_at():
    # Can't prorate without a span - excluded from the sum, but Longest/Shortest (unprorated,
    # no span needed) still shows it.
    entries = [_finished_entry(1, "A", page_count=200, finished_at="2026-01-05T00:00:00Z")]
    tiles = stat_tiles.build_collection_tiles(entries, *_YEAR_WINDOW)
    labels = {t["label"] for t in tiles}
    assert "Total pages read" not in labels
    by_label = {t["label"]: t for t in tiles}
    assert by_label["Longest book"]["value"] == "200"


def test_build_collection_tiles_pages_tiles_omitted_when_no_book_has_page_count():
    entries = [_finished_entry(1, "A"), _finished_entry(2, "B")]
    tiles = stat_tiles.build_collection_tiles(entries, *_YEAR_WINDOW)
    labels = {t["label"] for t in tiles}
    assert "Total pages read" not in labels


def test_build_collection_tiles_avg_rating_skips_unrated():
    entries = [_finished_entry(1, "A", rating=4), _finished_entry(2, "B", rating=None)]
    tiles = stat_tiles.build_collection_tiles(entries, *_YEAR_WINDOW)
    by_label = {t["label"]: t["value"] for t in tiles}
    assert by_label["Avg rating"] == "4.0"


def test_build_collection_tiles_finish_time_is_inclusive_day_count():
    # Regression test (moved from test_main.py's now-removed main._stat_tiles): started and
    # finished on the same calendar day must count as 1 day, not 0.
    entries = [_finished_entry(1, "A", started_at="2026-01-01", finished_at="2026-01-01T18:00:00Z")]
    tiles = stat_tiles.build_collection_tiles(entries, *_YEAR_WINDOW)
    by_label = {t["label"]: t["value"] for t in tiles}
    assert by_label["Avg finish time"] == "1d"
    assert by_label["Fastest finish"] == "1d"
    assert by_label["Slowest finish"] == "1d"


def test_build_collection_tiles_finish_time_skips_entries_missing_started_at():
    entries = [_finished_entry(1, "A", finished_at="2026-01-11T00:00:00Z")]
    tiles = stat_tiles.build_collection_tiles(entries, *_YEAR_WINDOW)
    assert not any(t["label"] == "Avg finish time" for t in tiles)


# --- finish_time_tiles_for_collection / _entry_duration_days shared with build_book_tiles ---


def test_finish_time_and_days_to_complete_use_the_same_duration_math():
    # Same underlying computation (_entry_duration_days), just framed differently for a
    # collection (avg/fastest/slowest) vs. a single book (Days to Complete) — this locks that in.
    entry = _finished_entry(1, "A", started_at="2026-01-01", finished_at="2026-01-11T00:00:00Z")
    collection_days = stat_tiles.finish_time_tiles_for_collection([entry])[0]["value"]
    book_days = next(
        t["value"] for t in stat_tiles.build_book_tiles(entry, []) if t["label"] == "Days to Complete"
    )
    assert collection_days == "11d" == book_days
