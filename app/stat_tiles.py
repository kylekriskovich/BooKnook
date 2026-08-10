from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from app.dates import longest_consecutive_run, parse_date, parse_instant, today_utc


def session_date(session: dict) -> Optional[date]:
    return parse_date(session.get("startTime"))


def _has_meaningful_progress(session: dict) -> bool:
    return (session.get("progressDelta") or 0) > 0


def format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(int(total_seconds), 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def latest_progress(sessions: list[dict]) -> Optional[float]:
    dated = [
        (started_at, rec.get("endProgress"))
        for rec in sessions
        if (started_at := parse_instant(rec.get("startTime"))) is not None
        and rec.get("endProgress") is not None
    ]
    if not dated:
        return None
    return max(dated, key=lambda t: t[0])[1]


def first_meaningful_session_date(sessions: list[dict]) -> Optional[date]:
    dated = [
        (started_at, started_at.date())
        for rec in sessions
        if _has_meaningful_progress(rec)
        and (started_at := parse_instant(rec.get("startTime"))) is not None
    ]
    if not dated:
        return None
    return min(dated, key=lambda t: t[0])[1]


def get_reading_dates(sessions: list[dict]) -> list[date]:
    dates = {
        current_date
        for rec in sessions
        if _has_meaningful_progress(rec) and (current_date := session_date(rec)) is not None
    }
    return sorted(dates)


def _entry_duration_days(entry) -> Optional[int]:
    if entry.status != "finished" or not entry.started_at or not entry.finished_at:
        return None
    started = parse_date(entry.started_at)
    finished = parse_date(entry.finished_at)
    if started is None or finished is None:
        return None
    days = (finished - started).days + 1
    return days if days >= 1 else None


def _days_to_complete_tile(entry) -> Optional[dict]:
    days = _entry_duration_days(entry)
    if days is None:
        return None
    return {"label": "Days to Complete", "value": f"{days}d"}


def _pages_per_day_fallback_tile(entry) -> Optional[dict]:
    page_count = entry.book.page_count
    if not page_count or not entry.started_at:
        return None
    started = parse_date(entry.started_at)
    if started is None:
        return None
    end = parse_date(entry.finished_at) if entry.finished_at else today_utc()
    if end is None:
        end = today_utc()
    days_elapsed = max((end - started).days + 1, 1)  # inclusive day count, floor for bad data
    pages_per_day = round(page_count / days_elapsed)
    if pages_per_day <= 0:
        return None
    return {"label": "Pages per day", "value": str(pages_per_day)}


def build_book_tiles(entry, sessions: list[dict]) -> list[dict]:
    """Session-dependent tiles for one book (GET /book/{entry_id}) - only meaningful given a
    single book's own reading-session log, not aggregatable across many books without fetching
    every one's sessions (which the collection tiles below deliberately never do)."""
    tiles: list[dict] = []
    page_count = entry.book.page_count
    reading_dates = get_reading_dates(sessions)

    if reading_dates:
        tiles.append({"label": "Reading Days", "value": str(len(reading_dates))})

        streak = longest_consecutive_run(reading_dates)
        if streak > 1:
            tiles.append({"label": "Best Streak", "value": f"{streak} days"})

        total_seconds = sum(s.get("durationSeconds") or 0 for s in sessions)
        if total_seconds:
            tiles.append({"label": "Time Spent Reading", "value": format_duration(total_seconds)})

        deltas = [s.get("progressDelta") for s in sessions if s.get("progressDelta")]
        if deltas:
            best_session = max(sessions, key=lambda s: s.get("progressDelta") or 0)
            best_delta = best_session.get("progressDelta") or 0
            best_date = session_date(best_session)
            if page_count:
                avg_pages = round((sum(deltas) / len(deltas)) / 100 * page_count)
                if avg_pages > 0:
                    tiles.append({"label": "Pages per session", "value": str(avg_pages)})
                best_pages = round(best_delta / 100 * page_count)
                if best_pages > 0:
                    tiles.append(
                        {
                            "label": "Best Session",
                            "value": f"{best_pages} pages",
                            "sub": best_date.isoformat() if best_date else None,
                        }
                    )
            else:
                best_pct = round(best_delta)
                if best_pct > 0:
                    tiles.append(
                        {
                            "label": "Best Session",
                            "value": f"{best_pct}%",
                            "sub": best_date.isoformat() if best_date else None,
                        }
                    )

        if entry.status == "reading" and len(reading_dates) >= 2 and deltas:
            span_days = (reading_dates[-1] - reading_dates[0]).days + 1
            pace_per_day = sum(deltas) / span_days
            end_progresses = [s.get("endProgress") for s in sessions if s.get("endProgress") is not None]
            latest_progress = max(end_progresses) if end_progresses else 0.0
            remaining = max(0.0, 100.0 - latest_progress)
            if pace_per_day > 0 and remaining > 0:
                days_remaining = remaining / pace_per_day
                estimated = today_utc() + timedelta(days=round(days_remaining))
                tiles.append(
                    {"label": "Estimated Completion", "value": estimated.strftime("%b %-d, %Y")}
                )
    else:
        fallback = _pages_per_day_fallback_tile(entry)
        if fallback:
            tiles.append(fallback)

    days_to_complete = _days_to_complete_tile(entry)
    if days_to_complete:
        tiles.append(days_to_complete)

    return tiles


def finish_time_tiles_for_collection(entries: list) -> list[dict]:
    """Avg/Fastest/Slowest finish time across many finished entries - entries missing a
    computable duration (see _entry_duration_days) are skipped, not estimated."""
    durations = []
    for entry in entries:
        days = _entry_duration_days(entry)
        if days is not None:
            durations.append((days, entry.book.title))
    if not durations:
        return []
    avg_days = round(sum(d for d, _ in durations) / len(durations))
    fastest = min(durations, key=lambda t: t[0])
    slowest = max(durations, key=lambda t: t[0])
    return [
        {"label": "Avg finish time", "value": f"{avg_days}d"},
        {"label": "Fastest finish", "value": f"{fastest[0]}d", "sub": fastest[1]},
        {"label": "Slowest finish", "value": f"{slowest[0]}d", "sub": slowest[1]},
    ]


def _prorated_pages(entry, window_start: date, window_end: date) -> Optional[float]:
    """Fraction of entry.book.page_count attributable to the days of its started_at->finished_at
    span that actually fall within [window_start, window_end] - same overlap-proration principle
    as reading_calendar.estimated_pages, applied here to the exact finished-in-window entries
    build_collection_tiles already selects. A book started 9 days before the window and finished
    on the window's first day only contributes 1/10 of its pages, not the full count - previously
    every book finished inside the window counted in full regardless of how much of its reading
    happened outside it. None if page_count/started_at/finished_at is missing, or the span/overlap
    can't be computed (bad-data guards, same posture as _entry_duration_days)."""
    page_count = entry.book.page_count
    if not page_count or not entry.started_at or not entry.finished_at:
        return None
    started = parse_date(entry.started_at)
    finished = parse_date(entry.finished_at)
    if started is None or finished is None:
        return None
    span_days = (finished - started).days + 1
    if span_days < 1:
        return None
    overlap_start = max(started, window_start)
    overlap_end = min(finished, window_end)
    if overlap_start > overlap_end:
        return None
    overlap_days = (overlap_end - overlap_start).days + 1
    return page_count * overlap_days / span_days


def build_collection_tiles(entries: list, window_start: date, window_end: date) -> list[dict]:
    """Session-independent aggregate tiles over an arbitrary set of finished entries - e.g. all
    books finished in a year (GET /stats) or a month (GET /calendar) or any other
    timeframe/selection a caller filters down to; this function doesn't know or care how the set
    was chosen, which is what lets a month/goal-window timeframe reuse it with no new tile logic.
    `window_start`/`window_end` bound that same timeframe (e.g. Jan 1-Dec 31 for a year, the
    month's first/last day for a month) - needed to prorate Total/Avg pages by how much of each
    book's reading span actually falls inside the window (see _prorated_pages), rather than
    crediting a book's full length to whichever window it happened to finish in. Each tile
    independently skips entries missing the field it needs (page_count/rating/started_at) rather
    than estimating. "Avg pages read" and "Avg book length" are the same figure by construction
    (both = total prorated pages / entries with a computable proration) - kept as separate tiles
    since they're separate asks with different framing, not a bug. Longest/Shortest book still
    show each book's real, unprorated page_count (and title) - prorating an individual book's
    displayed length would read as a data error, not a feature, so only the sum/average prorate."""
    tiles = [{"label": "Books finished", "value": str(len(entries))}]
    if not entries:
        return tiles

    prorated = [
        (entry, pages)
        for entry in entries
        if (pages := _prorated_pages(entry, window_start, window_end)) is not None
    ]
    if prorated:
        total_pages = sum(pages for _, pages in prorated)
        avg_pages = round(total_pages / len(prorated))
        tiles += [
            {"label": "Total pages read", "value": f"{round(total_pages):,}"},
            {"label": "Avg pages read", "value": f"{avg_pages:,}"},
            {"label": "Avg book length", "value": f"{avg_pages:,}"},
        ]

    with_pages = [e for e in entries if e.book.page_count]
    if with_pages:
        longest = max(with_pages, key=lambda e: e.book.page_count)
        shortest = min(with_pages, key=lambda e: e.book.page_count)
        tiles += [
            {"label": "Longest book", "value": f"{longest.book.page_count:,}", "sub": longest.book.title},
            {"label": "Shortest book", "value": f"{shortest.book.page_count:,}", "sub": shortest.book.title},
        ]

    with_rating = [e for e in entries if e.rating]
    if with_rating:
        avg_rating = sum(e.rating for e in with_rating) / len(with_rating)
        tiles.append({"label": "Avg rating", "value": f"{avg_rating:.1f}"})

    tiles += finish_time_tiles_for_collection(entries)
    return tiles


def burndown_points(sessions: list[dict]) -> list[tuple[date, int]]:
    dated: list[tuple[datetime, date, float]] = []
    for session in sessions:
        if not _has_meaningful_progress(session):
            continue
        started_at = parse_instant(session.get("startTime"))
        end_progress = session.get("endProgress")
        if started_at is None or end_progress is None:
            continue
        dated.append((started_at, started_at.date(), end_progress))
    dated.sort(key=lambda t: t[0])

    by_date: dict[date, float] = {}
    for _, day, end_progress in dated:
        by_date[day] = end_progress  # later entries for the same day overwrite earlier ones

    points = [
        (day, round(100.0 - progress))
        for day, progress in sorted(by_date.items())
    ]
    if points:
        # Synthetic "Day 0" point at 100% remaining, the day before the first logged session.
        # Without it the first plotted point already reflects whatever was left after *that*
        # day's own session (e.g. 85% remaining after an evening spent reading), which reads as
        # progress appearing out of nowhere rather than the line burning down from the actual
        # start of the book.
        first_day = points[0][0]
        points.insert(0, (first_day - timedelta(days=1), 100))
    return points


def burndown_svg_points(points: list[tuple[date, int]], width: int = 300, height: int = 100) -> str:
    """Maps burndown_points onto an SVG viewBox of the given size - x is proportional to each
    point's actual elapsed days since the first point, not its index in the list, so a gap
    between reading sessions (points only exist for days with a logged session) shows up as a
    gap on the chart instead of being smoothed away by even spacing."""
    if not points:
        return ""
    if len(points) == 1:
        _, remaining = points[0]
        y = height * (1 - remaining / 100)
        return f"0,{y:.1f} {width},{y:.1f}"
    start_day = points[0][0]
    total_days = (points[-1][0] - start_day).days
    coords = []
    for day, remaining in points:
        x = width * (day - start_day).days / total_days
        y = height * (1 - remaining / 100)
        coords.append(f"{x:.1f},{y:.1f}")
    return " ".join(coords)


def burndown_day_span(points: list[tuple[date, int]]) -> int:
    """Total elapsed days the burndown chart's x-axis covers, for its "Day 0" / "Day N" labels."""
    if len(points) < 2:
        return 0
    return (points[-1][0] - points[0][0]).days
