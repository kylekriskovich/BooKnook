import datetime as dt

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
