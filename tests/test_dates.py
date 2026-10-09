import datetime as dt
from zoneinfo import ZoneInfo

from app import dates


def test_today_utc_returns_a_date_not_datetime():
    result = dates.today_utc()
    assert isinstance(result, dt.date)
    assert not isinstance(result, dt.datetime)
    assert result == dt.datetime.now(dt.timezone.utc).date()


def test_parse_instant_none_for_empty():
    assert dates.parse_instant(None) is None
    assert dates.parse_instant("") is None


def test_parse_instant_handles_z_suffix():
    parsed = dates.parse_instant("2026-01-01T12:00:00Z")
    assert parsed == dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)


def test_parse_instant_handles_offset_suffix():
    parsed = dates.parse_instant("2026-01-01T12:00:00+00:00")
    assert parsed == dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)


def test_parse_instant_handles_bare_date():
    parsed = dates.parse_instant("2026-01-01")
    assert parsed == dt.datetime(2026, 1, 1)


def test_parse_instant_none_for_garbage():
    assert dates.parse_instant("not-a-date") is None


def test_parse_date_extracts_date_from_instant():
    assert dates.parse_date("2026-01-01T12:00:00Z") == dt.date(2026, 1, 1)


def test_parse_date_none_for_empty():
    assert dates.parse_date(None) is None


# --- instant_to_local_date ---


def test_instant_to_local_date_regression_7am_utc8_boundary():
    # The exact bug: 7am UTC+8 is 23:00 the *previous* day in UTC. Naively truncating to a UTC
    # date (the old parse_date behavior) reports the wrong calendar day - see app/library_check.py
    # _apply_status's started_at fallback, the root cause this fixes.
    instant = "2026-09-16T23:00:00Z"
    utc_plus_8 = ZoneInfo("Etc/GMT-8")  # POSIX Etc/GMT signs are inverted: GMT-8 means UTC+8
    assert dates.parse_date(instant) == dt.date(2026, 9, 16)  # old, wrong
    assert dates.instant_to_local_date(instant, utc_plus_8) == dt.date(2026, 9, 17)  # fixed


def test_instant_to_local_date_uses_default_zone_when_unspecified():
    instant = "2026-09-16T23:00:00Z"
    assert dates.instant_to_local_date(instant) == dt.date(2026, 9, 16)  # DEFAULT_ZONE is UTC


def test_instant_to_local_date_passes_bare_manual_date_through_untouched():
    # A manually-entered "YYYY-MM-DD" (no time-of-day) carries nothing to reinterpret - shifting
    # it by a timezone would be wrong, so a naive parse just returns the date as typed.
    utc_plus_8 = ZoneInfo("Etc/GMT-8")
    assert dates.instant_to_local_date("2026-09-17", utc_plus_8) == dt.date(2026, 9, 17)


def test_instant_to_local_date_none_for_empty():
    assert dates.instant_to_local_date(None) is None


def test_instant_to_local_date_none_for_garbage():
    assert dates.instant_to_local_date("not-a-date") is None


# --- today_local ---


def test_today_local_defaults_to_default_zone():
    assert dates.today_local() == dt.datetime.now(dates.DEFAULT_ZONE).date()


def test_today_local_respects_explicit_zone():
    utc_minus_12 = ZoneInfo("Etc/GMT+12")
    assert dates.today_local(utc_minus_12) == dt.datetime.now(utc_minus_12).date()
