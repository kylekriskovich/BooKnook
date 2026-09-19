from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app.dates import instant_to_local_date, longest_consecutive_run, parse_instant, today_local
from app.models import PhysicalReadingSession


def session_date(session: dict, zone: Optional[ZoneInfo] = None) -> Optional[date]:
    return instant_to_local_date(session.get("startTime"), zone)


def physical_session_to_grimmory_shape(
    session: PhysicalReadingSession, physical_page_count: Optional[int]
) -> dict:
    """Converts a manually-logged physical session into a Grimmory-shaped dict so it flows
    through this module unmodified (Decisions 7/9). Percentages use the *physical* edition's own
    page count (not books.page_count, which can differ) and are computed live here, not stored."""
    start = parse_instant(session.start_time)
    end = parse_instant(session.end_time)
    duration_seconds = round((end - start).total_seconds()) if start and end else None
    start_progress = end_progress = progress_delta = None
    if physical_page_count:
        start_progress = session.start_page / physical_page_count * 100
        end_progress = session.end_page / physical_page_count * 100
        progress_delta = end_progress - start_progress
    return {
        "startTime": session.start_time,
        "endTime": session.end_time,
        "durationSeconds": duration_seconds,
        "startProgress": start_progress,
        "endProgress": end_progress,
        "progressDelta": progress_delta,
        # Raw page delta, known regardless of physical_page_count - lets build_book_tiles convert
        # to pages using this edition's own count instead of guessing via the ebook's page_count.
        "pageDelta": session.end_page - session.start_page,
    }


def _has_meaningful_progress(session: dict) -> bool:
    # Grimmory never sets progressDelta on audiobook sessions - use durationSeconds instead so
    # real listening time still counts.
    if (session.get("progressDelta") or 0) > 0:
        return True
    if (session.get("pageDelta") or 0) > 0:
        return True
    return session.get("bookType") == "AUDIOBOOK" and (session.get("durationSeconds") or 0) > 0


def session_page_delta(session: dict, fallback_page_count: Optional[int]) -> Optional[float]:
    """A session's own known page delta (physical sessions) if present, else an estimate from its
    percentage progressDelta against the ebook's page_count (Grimmory sessions have no raw pages)."""
    if session.get("pageDelta") is not None:
        return session["pageDelta"]
    delta = session.get("progressDelta")
    if delta and fallback_page_count:
        return delta / 100 * fallback_page_count
    return None


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


def unified_latest_progress(candidates: list[Optional[float]]) -> Optional[float]:
    """Highest of several linked editions' own latest tracked progress - "how far into this book
    am I, across any medium" (DESIGN-multi-edition-refactor.md Decision 5). A high-water mark
    rather than "whichever edition was used most recently", so resuming a medium that hasn't
    caught up yet doesn't read as regression. None if none of the candidates have a value."""
    known = [c for c in candidates if c is not None]
    return max(known) if known else None


def first_meaningful_session_date(sessions: list[dict], zone: Optional[ZoneInfo] = None) -> Optional[date]:
    dated = [
        (started_at, instant_to_local_date(rec.get("startTime"), zone))
        for rec in sessions
        if _has_meaningful_progress(rec)
        and (started_at := parse_instant(rec.get("startTime"))) is not None
    ]
    if not dated:
        return None
    return min(dated, key=lambda t: t[0])[1]


def get_reading_dates(sessions: list[dict], zone: Optional[ZoneInfo] = None) -> list[date]:
    dates = {
        current_date
        for rec in sessions
        if _has_meaningful_progress(rec) and (current_date := session_date(rec, zone)) is not None
    }
    return sorted(dates)


def _entry_duration_days(entry, zone: Optional[ZoneInfo] = None) -> Optional[int]:
    if entry.status != "finished" or not entry.started_at or not entry.finished_at:
        return None
    started = instant_to_local_date(entry.started_at, zone)
    finished = instant_to_local_date(entry.finished_at, zone)
    if started is None or finished is None:
        return None
    days = (finished - started).days + 1
    return days if days >= 1 else None


def _days_to_complete_tile(entry, zone: Optional[ZoneInfo] = None) -> Optional[dict]:
    days = _entry_duration_days(entry, zone)
    if days is None:
        return None
    return {"label": "Days to Complete", "value": f"{days}d"}


def _pages_per_day_fallback_tile(
    entry, today: Optional[date] = None, zone: Optional[ZoneInfo] = None
) -> Optional[dict]:
    today = today or today_local(zone)
    page_count = entry.book.page_count
    if not page_count or not entry.started_at:
        return None
    started = instant_to_local_date(entry.started_at, zone)
    if started is None:
        return None
    end = instant_to_local_date(entry.finished_at, zone) if entry.finished_at else today
    if end is None:
        end = today
    days_elapsed = max((end - started).days + 1, 1)  # inclusive day count, floor for bad data
    pages_per_day = round(page_count / days_elapsed)
    if pages_per_day <= 0:
        return None
    return {"label": "Pages per day", "value": str(pages_per_day)}


def build_book_tiles(
    entry,
    sessions: list[dict],
    today: Optional[date] = None,
    is_audiobook: Optional[bool] = None,
    zone: Optional[ZoneInfo] = None,
) -> list[dict]:
    """Session-dependent tiles for one book (GET /book/{entry_id}) - only meaningful given a
    single book's own reading-session log, not aggregatable across many books. `today` defaults to
    the deployment's local timezone for callers with no client-local date to pass (see
    app/main.py:_resolve_client_today). `is_audiobook` picks "Listening" vs "Reading" labels and
    must be passed explicitly - an entry's book is always the ebook, so `sessions` may belong to
    either it or a paired audiobook."""
    today = today or today_local(zone)
    if is_audiobook is None:
        is_audiobook = entry.book.format == "AUDIOBOOK"
    tiles: list[dict] = []
    page_count = entry.book.page_count
    reading_dates = get_reading_dates(sessions, zone)

    if reading_dates:
        days_label = "Listening Days" if is_audiobook else "Reading Days"
        tiles.append({"label": days_label, "value": str(len(reading_dates))})

        streak = longest_consecutive_run(reading_dates)
        if streak > 1:
            tiles.append({"label": "Best Streak", "value": f"{streak} days"})

        total_seconds = sum(s.get("durationSeconds") or 0 for s in sessions)
        if total_seconds:
            time_label = "Time Spent Listening" if is_audiobook else "Time Spent Reading"
            tiles.append({"label": time_label, "value": format_duration(total_seconds)})

        deltas = [s.get("progressDelta") for s in sessions if s.get("progressDelta")]
        if deltas:
            best_session = max(sessions, key=lambda s: s.get("progressDelta") or 0)
            best_delta = best_session.get("progressDelta") or 0
            best_date = session_date(best_session, zone)
            # Each session's own page delta when known (physical editions), falling back to an
            # estimate via the ebook's page_count only for sessions with no raw pages of their own.
            page_deltas = [
                pd
                for s in sessions
                if s.get("progressDelta") and (pd := session_page_delta(s, page_count)) is not None
            ]
            if page_deltas:
                avg_pages = round(sum(page_deltas) / len(page_deltas))
                if avg_pages > 0:
                    tiles.append({"label": "Pages per session", "value": str(avg_pages)})
                best_page_delta = session_page_delta(best_session, page_count)
                best_pages = round(best_page_delta) if best_page_delta else 0
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

        if entry.status == "reading" and len(reading_dates) >= 2:
            span_days = (reading_dates[-1] - reading_dates[0]).days + 1
            if deltas:
                pace_per_day = sum(deltas) / span_days
                end_progresses = [
                    s.get("endProgress") for s in sessions if s.get("endProgress") is not None
                ]
                latest_progress = max(end_progresses) if end_progresses else 0.0
            elif is_audiobook and entry.audiobook_progress_percent is not None:
                # No session-level pace for audiobooks - fall back to the overall percentage,
                # averaged over listening days observed so far.
                pace_per_day = entry.audiobook_progress_percent / span_days
                latest_progress = entry.audiobook_progress_percent
            else:
                pace_per_day = 0.0
                latest_progress = 0.0
            remaining = max(0.0, 100.0 - latest_progress)
            if pace_per_day > 0 and remaining > 0:
                days_remaining = remaining / pace_per_day
                estimated = today + timedelta(days=round(days_remaining))
                tiles.append(
                    {"label": "Estimated Completion", "value": estimated.strftime("%b %-d, %Y")}
                )
    elif not is_audiobook:
        # "Pages per day" doesn't fit as a no-listening-data-yet stand-in on a Listening tab.
        fallback = _pages_per_day_fallback_tile(entry, today, zone)
        if fallback:
            tiles.append(fallback)

    # Book-level (started_at/finished_at), not medium-specific - included only once, on the
    # primary/Reading tile list, rather than duplicated onto the Listening tab too.
    if not is_audiobook:
        days_to_complete = _days_to_complete_tile(entry, zone)
        if days_to_complete:
            tiles.append(days_to_complete)

    return tiles


# Recency window for _average_pages_per_day - long enough to smooth over a slow week, short
# enough that a pace change (new job, holiday binge-reading) shows up within a season rather than
# being diluted by a whole reading history.
RECENT_PACE_WINDOW_DAYS = 90


def _average_pages_per_day(entries: list, today: date, zone: Optional[ZoneInfo] = None) -> Optional[float]:
    """User's own recent reading pace: total pages / total days across finished books that were
    themselves finished within the last RECENT_PACE_WINDOW_DAYS (see _entry_duration_days for the
    per-book day count) - keeps the estimate responsive to a changed habit instead of diluted by,
    e.g., a fast read from a year ago. Audiobooks are excluded, since page_count isn't a
    meaningful measure of their reading time. None if there's nothing recent to average."""
    cutoff = today - timedelta(days=RECENT_PACE_WINDOW_DAYS)
    total_pages = 0
    total_days = 0
    for entry in entries:
        if entry.book.format == "AUDIOBOOK" or not entry.book.page_count:
            continue
        finished_date = instant_to_local_date(entry.finished_at, zone)
        if finished_date is None or finished_date < cutoff:
            continue
        days = _entry_duration_days(entry, zone)
        if days is None:
            continue
        total_pages += entry.book.page_count
        total_days += days
    return total_pages / total_days if total_days > 0 else None


def _reading_head_start_days(
    entries: list,
    pace: float,
    today: date,
    progress_by_entry_id: Optional[dict[int, float]] = None,
    zone: Optional[ZoneInfo] = None,
) -> float:
    """Estimated days before the wanted queue can start: sum, across every currently-`reading`
    book, of its own estimated remaining pages (floored at 0) / pace. Remaining pages come from a
    real tracked percent-complete when the caller has one (progress_by_entry_id - see
    app/main.py's cheap, local-only cached-session lookup, no live Grimmory fetch), which needs no
    guessing; otherwise falls back to page_count - pace * days elapsed since started_at. A book
    missing page_count (and, on the fallback path, started_at), or an audiobook, contributes 0
    rather than blocking the whole estimate - the queue then simply starts as if that one book
    weren't in progress."""
    progress_by_entry_id = progress_by_entry_id or {}
    head_start = 0.0
    for entry in entries:
        if entry.status != "reading" or entry.book.format == "AUDIOBOOK" or not entry.book.page_count:
            continue
        percent = progress_by_entry_id.get(entry.id)
        if percent is not None:
            pages_read = entry.book.page_count * percent / 100
        else:
            started = instant_to_local_date(entry.started_at, zone)
            if started is None:
                continue
            days_elapsed = max(0, (today - started).days)
            pages_read = min(entry.book.page_count, pace * days_elapsed)
        remaining_pages = max(0.0, entry.book.page_count - pages_read)
        head_start += remaining_pages / pace
    return head_start


def predicted_wanted_queue_months(
    entries: list,
    today: date,
    progress_by_entry_id: Optional[dict[int, float]] = None,
    zone: Optional[ZoneInfo] = None,
) -> dict[int, str]:
    """Predicted "YYYY-MM" each "wanted"-status entry will be reached, walked in the user's manual
    queue order (sort_order) and projected via cumulative page_count / _average_pages_per_day,
    starting after a head start for whatever's already `reading` (_reading_head_start_days).
    Entries missing a page_count fall back to the average of the other queued books' known counts.
    Empty dict (no predictions) if there's no recent pace data yet to project from."""
    pace = _average_pages_per_day(entries, today, zone)
    if not pace:
        return {}
    wanted = sorted((e for e in entries if e.status == "wanted"), key=lambda e: e.sort_order)
    known_page_counts = [e.book.page_count for e in wanted if e.book.page_count]
    if not known_page_counts:
        return {}
    avg_page_count = round(sum(known_page_counts) / len(known_page_counts))
    months: dict[int, str] = {}
    cumulative_days = _reading_head_start_days(entries, pace, today, progress_by_entry_id, zone)
    for entry in wanted:
        cumulative_days += (entry.book.page_count or avg_page_count) / pace
        reached = today + timedelta(days=round(cumulative_days))
        months[entry.id] = f"{reached.year:04d}-{reached.month:02d}"
    return months


def finish_time_tiles_for_collection(entries: list, zone: Optional[ZoneInfo] = None) -> list[dict]:
    """Avg/Fastest/Slowest finish time across many finished entries - entries missing a
    computable duration (see _entry_duration_days) are skipped, not estimated."""
    durations = []
    for entry in entries:
        days = _entry_duration_days(entry, zone)
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


def _prorated_pages(
    entry, window_start: date, window_end: date, zone: Optional[ZoneInfo] = None
) -> Optional[float]:
    """Fraction of entry.book.page_count attributable to the days of its started_at->finished_at
    span that fall within [window_start, window_end] - same overlap-proration as
    reading_calendar.estimated_pages, so a book finished on the window's first day but started 9
    days earlier only contributes 1/10 of its pages. None if page_count/started_at/finished_at is
    missing or there's no valid overlap."""
    page_count = entry.book.page_count
    if not page_count or not entry.started_at or not entry.finished_at:
        return None
    started = instant_to_local_date(entry.started_at, zone)
    finished = instant_to_local_date(entry.finished_at, zone)
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


def build_collection_tiles(
    entries: list,
    window_start: date,
    window_end: date,
    sessions_by_entry_id: Optional[dict[int, list[dict]]] = None,
    zone: Optional[ZoneInfo] = None,
) -> list[dict]:
    """Aggregate tiles over an arbitrary set of finished entries (e.g. a year for GET /stats, a
    month for GET /calendar). `window_start`/`window_end` prorate "Total pages read" by how much
    of each book's reading span falls inside the window (see _prorated_pages), rather than
    crediting a book's full length to its finish window. "Avg pages read" is a true per-session
    average instead - every session (all-time, not window-filtered - session dates aren't trusted
    enough to filter on) across every entry in `sessions_by_entry_id`, keyed by entry.id; omitted
    (tile skipped) when the caller has no session data to pass. "Avg book length" is the plain
    unprorated average of book.page_count, a distinct figure from "Avg pages read" describing book
    length rather than reading pace. Longest/Shortest also use each book's real, unprorated
    page_count."""
    tiles = [{"label": "Books finished", "value": str(len(entries))}]
    if not entries:
        return tiles

    prorated = [
        (entry, pages)
        for entry in entries
        if (pages := _prorated_pages(entry, window_start, window_end, zone)) is not None
    ]
    if prorated:
        total_pages = sum(pages for _, pages in prorated)
        tiles.append({"label": "Total pages read", "value": f"{round(total_pages):,}"})

    if sessions_by_entry_id:
        page_deltas = [
            pd
            for entry in entries
            for s in sessions_by_entry_id.get(entry.id) or []
            if s.get("progressDelta") and (pd := session_page_delta(s, entry.book.page_count)) is not None
        ]
        if page_deltas:
            avg_pages_per_session = round(sum(page_deltas) / len(page_deltas))
            if avg_pages_per_session > 0:
                tiles.append({"label": "Avg pages read", "value": f"{avg_pages_per_session:,}"})

    with_pages = [e for e in entries if e.book.page_count]
    if with_pages:
        avg_book_length = round(sum(e.book.page_count for e in with_pages) / len(with_pages))
        tiles.append({"label": "Avg book length", "value": f"{avg_book_length:,}"})
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

    tiles += finish_time_tiles_for_collection(entries, zone)
    return tiles


def _session_hours(session: dict) -> float:
    return (session.get("durationSeconds") or 0) / 3600


def reading_session_tiles(
    sessions_with_page_counts: list[tuple[dict, Optional[int]]], zone: Optional[ZoneInfo] = None
) -> list[dict]:
    """Ebook-only session stats, spread across the Stats page's Overview/Averages/Highlights tabs
    by group_stat_tiles (kept separate from physical sessions here, unlike build_collection_tiles's
    "Avg pages read" which merges them - these are meant to read as "what Grimmory itself
    tracked"). `sessions_with_page_counts` pairs each raw Grimmory session with its own book's
    page_count (for session_page_delta's percentage fallback), flattened across every entry - one
    row per session, entry boundaries don't matter here since every stat below is either a
    straight count/sum or grouped by session date."""
    meaningful = [(s, pc) for s, pc in sessions_with_page_counts if _has_meaningful_progress(s)]
    if not meaningful:
        return []
    tiles = [{"label": "Total sessions", "value": str(len(meaningful))}]

    total_seconds = sum((s.get("durationSeconds") or 0) for s, _ in meaningful)
    if total_seconds > 0:
        tiles.append({"label": "Total reading time", "value": format_duration(total_seconds)})

    dated_pages = [
        (day, pages)
        for s, pc in meaningful
        if (day := session_date(s, zone)) is not None and (pages := session_page_delta(s, pc))
    ]
    if dated_pages:
        active_months = {(day.year, day.month) for day, _ in dated_pages}
        total_pages = sum(pages for _, pages in dated_pages)
        avg_per_month = round(total_pages / len(active_months))
        tiles.append({"label": "Avg pages per month", "value": f"{avg_per_month:,}"})

        by_day: dict[date, float] = {}
        for day, pages in dated_pages:
            by_day[day] = by_day.get(day, 0) + pages
        best_day, best_day_pages = max(by_day.items(), key=lambda kv: kv[1])
        tiles.append(
            {"label": "Best day", "value": f"{round(best_day_pages):,} pages", "sub": best_day.isoformat()}
        )

        largest_day, largest_pages = max(dated_pages, key=lambda dp: dp[1])
        tiles.append(
            {
                "label": "Largest session",
                "value": f"{round(largest_pages):,} pages",
                "sub": largest_day.isoformat(),
            }
        )

    timed_pages = [
        pages
        for s, pc in meaningful
        if (s.get("durationSeconds") or 0) > 0 and (pages := session_page_delta(s, pc))
    ]
    total_hours = sum(_session_hours(s) for s, _ in meaningful if (s.get("durationSeconds") or 0) > 0)
    if timed_pages and total_hours > 0:
        tiles.append({"label": "Reading speed", "value": f"{sum(timed_pages) / total_hours:.0f} pages/hr"})

    return tiles


def listening_session_tiles(sessions: list[dict]) -> list[dict]:
    """Audiobook-only session stats, spread across the Stats page's tabs by group_stat_tiles."""
    meaningful = [s for s in sessions if _has_meaningful_progress(s)]
    if not meaningful:
        return []
    total_seconds = sum((s.get("durationSeconds") or 0) for s in meaningful)
    tiles = []
    if total_seconds > 0:
        avg_minutes = round(total_seconds / 60 / len(meaningful))
        tiles.append({"label": "Avg listening session", "value": f"{avg_minutes} min"})
        tiles.append({"label": "Total listening time", "value": format_duration(total_seconds)})
    tiles.append({"label": "Audio session count", "value": str(len(meaningful))})
    return tiles


def physical_session_tiles(sessions: list[dict]) -> list[dict]:
    """Physical-only session stats - every manually-logged session counts, not just ones with a
    "meaningful" page delta, since the user typed each one in directly rather than Grimmory
    recording it automatically."""
    if not sessions:
        return []
    return [{"label": "Physical session count", "value": str(len(sessions))}]


# Fixed label->tab assignment for the Stats page's single Overview/Averages/Highlights tab group -
# order here is the display order within each tab, not source order, so the layout stays stable
# regardless of which underlying build_collection_tiles/*_session_tiles tiles happen to be present
# this run. Every label any of those functions can currently produce must appear exactly once here.
STAT_TILE_GROUPS: dict[str, list[str]] = {
    "overview": [
        "Books finished",
        "Total pages read",
        "Total sessions",
        "Total reading time",
        "Total listening time",
        "Physical session count",
        "Audio session count",
        "Longest book",
        "Shortest book",
    ],
    "averages": [
        "Avg pages read",
        "Avg book length",
        "Avg pages per month",
        "Reading speed",
        "Avg listening session",
        "Avg rating",
        "Avg finish time",
    ],
    "highlights": [
        "Best day",
        "Largest session",
        "Fastest finish",
        "Slowest finish",
    ],
}


def group_stat_tiles(tiles: list[dict]) -> dict[str, list[dict]]:
    """Buckets a flat pool of stat tiles (build_collection_tiles + the *_session_tiles builders,
    concatenated) into the Stats page's Overview/Averages/Highlights tabs per STAT_TILE_GROUPS.
    A tile whose data wasn't available this run (e.g. no rated books, so no "Avg rating") is simply
    absent from `tiles` and skipped here rather than erroring."""
    by_label = {t["label"]: t for t in tiles}
    return {
        group: [by_label[label] for label in labels if label in by_label]
        for group, labels in STAT_TILE_GROUPS.items()
    }


def burndown_points(sessions: list[dict], zone: Optional[ZoneInfo] = None) -> list[tuple[date, int]]:
    dated: list[tuple[datetime, date, float]] = []
    for session in sessions:
        if not _has_meaningful_progress(session):
            continue
        started_at = parse_instant(session.get("startTime"))
        end_progress = session.get("endProgress")
        if started_at is None or end_progress is None:
            continue
        dated.append((started_at, instant_to_local_date(session.get("startTime"), zone), end_progress))
    dated.sort(key=lambda t: t[0])

    by_date: dict[date, float] = {}
    for _, day, end_progress in dated:
        by_date[day] = end_progress  # later entries for the same day overwrite earlier ones

    points = [
        (day, round(100.0 - progress))
        for day, progress in sorted(by_date.items())
    ]
    if points:
        # Synthetic "Day 0" point at 100% remaining, the day before the first logged session, so
        # the line burns down from the actual start rather than starting mid-progress.
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
