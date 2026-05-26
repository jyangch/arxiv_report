"""Tests for the arXiv fetch window classifier and cache-poisoning guard."""

import datetime
from types import SimpleNamespace

import pytest
import pytz

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


class TestSyncWindow:
    """The window must equal the arXiv listing actually dated each weekday.

    arXiv announces Sunday-Thursday at 20:00 ET; the weekend's submissions
    (Fri 14:00 -> Mon 14:00) are bundled into the listing dated *Tuesday*, not
    Monday. The reach-back therefore belongs on Tuesday.
    """

    def _w(self, y: int, m: int, d: int) -> tuple[tuple, tuple]:
        s, e = fetcher.get_arxiv_sync_window(_et(y, m, d))
        return (s.month, s.day, s.hour), (e.month, e.day, e.hour)

    def test_monday_targets_thu_to_fri(self) -> None:
        # 2026-05-18 is a Monday; its listing is Thu 14:00 -> Fri 14:00.
        assert self._w(2026, 5, 18) == ((5, 14, 14), (5, 15, 14))

    def test_tuesday_targets_fri_to_mon(self) -> None:
        # 2026-05-19 is a Tuesday; its listing is the weekend bundle Fri->Mon.
        assert self._w(2026, 5, 19) == ((5, 15, 14), (5, 18, 14))

    def test_wednesday_targets_mon_to_tue(self) -> None:
        assert self._w(2026, 5, 20) == ((5, 18, 14), (5, 19, 14))

    def test_thursday_targets_tue_to_wed(self) -> None:
        assert self._w(2026, 5, 21) == ((5, 19, 14), (5, 20, 14))

    def test_friday_targets_wed_to_thu(self) -> None:
        assert self._w(2026, 5, 22) == ((5, 20, 14), (5, 21, 14))

    def test_weekend_is_empty(self) -> None:
        for day in (23, 24):  # Saturday, Sunday
            s, e = fetcher.get_arxiv_sync_window(_et(2026, 5, day))
            assert s >= e


class TestParseAbsSubmitted:
    def test_parses_v1_utc(self) -> None:
        html = (
            '<div class="submission-history">'
            '<b>[v1]</b> <span>Thu, 21 May 2026 18:00:32 UTC</span> (12 KB)'
            '</div>'
        )
        assert fetcher._parse_abs_submitted(html) == pytz.UTC.localize(
            datetime.datetime(2026, 5, 21, 18, 0, 32)
        )

    def test_returns_none_when_absent(self) -> None:
        assert fetcher._parse_abs_submitted('<html>no history</html>') is None


class TestRssWindowFilter:
    """RSS carries only the latest announcement; it must be filtered to the
    requested window so a stale (not-yet-refreshed) feed yields [], not
    yesterday's papers."""

    def _entry(self, aid: str, title: str) -> dict:
        # feedparser entries support both ``entry['k']`` and ``entry.k``.
        class _FeedDict(dict):
            __getattr__ = dict.__getitem__

        return _FeedDict(
            title=title,
            link=f'http://arxiv.org/abs/{aid}',
            arxiv_announce_type='new',
            summary='s',
            tags=[{'term': 'astro-ph.HE'}],
            authors=[{'name': 'A'}],
            links=[],
        )

    def _patch_feed(self, monkeypatch: pytest.MonkeyPatch, entries: list) -> None:
        fake = SimpleNamespace(bozo=False, entries=entries, feed={})
        monkeypatch.setattr(fetcher.feedparser, 'parse', lambda url: fake)

    def test_drops_papers_outside_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_feed(
            monkeypatch,
            [self._entry('2605.99001', 'In window'), self._entry('2605.99002', 'Too old')],
        )
        dates = {
            '2605.99001': _et(2026, 5, 24, 10),  # inside Fri->Mon window
            '2605.99002': _et(2026, 5, 21, 10),  # Thursday, before the window
        }
        monkeypatch.setattr(
            fetcher, '_fetch_abs_metadata', lambda aid, timeout=10.0: ('', '', '', dates[aid])
        )

        papers = fetcher._fetch_via_rss(_et(2026, 5, 22, 14), _et(2026, 5, 25, 14))

        assert [p['title'] for p in papers] == ['In window']
        assert all('_submitted_dt' not in p for p in papers)

    def test_stale_feed_yields_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_feed(monkeypatch, [self._entry('2605.99002', 'Too old')])
        monkeypatch.setattr(
            fetcher,
            '_fetch_abs_metadata',
            lambda aid, timeout=10.0: ('', '', '', _et(2026, 5, 21, 10)),
        )

        papers = fetcher._fetch_via_rss(_et(2026, 5, 22, 14), _et(2026, 5, 25, 14))

        assert papers == []

    def test_keeps_paper_when_date_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Fail open: an un-scrapeable abs page must not silently drop a paper.
        self._patch_feed(monkeypatch, [self._entry('2605.99003', 'Unknown date')])
        monkeypatch.setattr(
            fetcher, '_fetch_abs_metadata', lambda aid, timeout=10.0: ('', '', '', None)
        )

        papers = fetcher._fetch_via_rss(_et(2026, 5, 22, 14), _et(2026, 5, 25, 14))

        assert [p['title'] for p in papers] == ['Unknown date']
