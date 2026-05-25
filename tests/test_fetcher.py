"""Tests for the arXiv fetch window classifier and cache-poisoning guard."""

import datetime

import pytest

from core import fetcher


def _et(y: int, m: int, d: int, hour: int = 12) -> datetime.datetime:
    """Build an Eastern-timezone-aware datetime for the given date."""
    return fetcher.ARXIV_TZ.localize(datetime.datetime(y, m, d, hour))


class TestDescribeEmptyWindow:
    def test_weekend_named(self) -> None:
        # 2026-05-23 is a Saturday.
        msg = fetcher.describe_empty_window(_et(2026, 5, 23))
        assert 'weekend' in msg.lower()

    def test_holiday_named(self) -> None:
        # 2026-01-19 is Martin Luther King Jr. Day (an arXiv holiday).
        msg = fetcher.describe_empty_window(_et(2026, 1, 19))
        assert 'Martin Luther King' in msg

    def test_weekday_miss_suggests_retry(self) -> None:
        # 2026-05-25 is a Monday and NOT an arXiv holiday (Memorial Day is
        # not observed), so an empty result is unexpected -> suggest a retry.
        msg = fetcher.describe_empty_window(_et(2026, 5, 25))
        assert 'unexpected' in msg.lower()
        assert 'again' in msg.lower()


class TestArxivHolidayName:
    """The holidays library is filtered to arXiv's actually-observed set."""

    def test_observed_holiday_returned(self) -> None:
        assert fetcher._arxiv_holiday_name(datetime.date(2026, 1, 19)) == (
            'Martin Luther King Jr. Day'
        )

    def test_observed_shift_returned(self) -> None:
        # July 4 2026 is a Saturday; arXiv observes the Friday shift.
        assert fetcher._arxiv_holiday_name(datetime.date(2026, 7, 3)) is not None

    def test_us_holidays_arxiv_ignores_are_filtered(self) -> None:
        # These are in holidays.US() but arXiv announces on them anyway.
        assert fetcher._arxiv_holiday_name(datetime.date(2026, 5, 25)) is None  # Memorial Day
        assert fetcher._arxiv_holiday_name(datetime.date(2026, 2, 16)) is None  # Presidents Day
        assert fetcher._arxiv_holiday_name(datetime.date(2026, 11, 11)) is None  # Veterans Day

    def test_non_holiday_returns_none(self) -> None:
        assert fetcher._arxiv_holiday_name(datetime.date(2026, 5, 20)) is None


class TestEmptyResultNotCached:
    """A degraded fetch returning [] must not poison the per-window cache."""

    def _patch_common(self, monkeypatch: pytest.MonkeyPatch) -> list:
        saved: list = []
        monkeypatch.setattr(fetcher, '_is_in_cooldown', lambda: False)
        monkeypatch.setattr(fetcher, '_load_cache', lambda *a: None)
        monkeypatch.setattr(
            fetcher, '_save_cache', lambda *a: saved.append(a[2])
        )
        return saved

    def test_empty_not_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saved = self._patch_common(monkeypatch)
        monkeypatch.setattr(fetcher, '_fetch_via_api', lambda s, e: [])

        # Tuesday -> non-weekend window (start < end), so it reaches the fetch.
        papers = fetcher.fetch_arxiv_papers(as_of=_et(2026, 5, 19))

        assert papers == []
        assert saved == []  # nothing persisted

    def test_nonempty_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saved = self._patch_common(monkeypatch)
        one = [{'url': 'http://arxiv.org/abs/2605.00001'}]
        monkeypatch.setattr(fetcher, '_fetch_via_api', lambda s, e: list(one))

        papers = fetcher.fetch_arxiv_papers(as_of=_et(2026, 5, 19))

        assert papers == one
        assert len(saved) == 1  # persisted exactly once
