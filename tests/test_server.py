"""Tests for the arxiv_report FastAPI server."""

import datetime
from pathlib import Path

from server import _list_recent_dates, _parse_date


class TestParseDate:
    def test_valid_iso_date(self) -> None:
        assert _parse_date('2026-05-22') == datetime.date(2026, 5, 22)

    def test_invalid_format(self) -> None:
        assert _parse_date('foo') is None

    def test_invalid_separator(self) -> None:
        assert _parse_date('2026/05/22') is None

    def test_extra_components(self) -> None:
        # fromisoformat will accept time suffixes — reject those.
        assert _parse_date('2026-05-22T12:00') is None

    def test_empty(self) -> None:
        assert _parse_date('') is None

    def test_traversal_attempt(self) -> None:
        assert _parse_date('../etc/passwd') is None


class TestListRecentDates:
    def _touch(self, reports_dir: Path, date: str) -> None:
        (reports_dir / f'arXiv_astro_ph_HE_daily_report_{date}.html').write_text('x')

    def test_empty_dir_returns_empty(self, reports_dir: Path) -> None:
        assert _list_recent_dates() == []

    def test_returns_dates_descending(self, reports_dir: Path) -> None:
        for d in ('2026-05-13', '2026-05-19', '2026-05-15'):
            self._touch(reports_dir, d)
        assert _list_recent_dates() == [
            datetime.date(2026, 5, 19),
            datetime.date(2026, 5, 15),
            datetime.date(2026, 5, 13),
        ]

    def test_limit(self, reports_dir: Path) -> None:
        for d in ('2026-05-13', '2026-05-14', '2026-05-15'):
            self._touch(reports_dir, d)
        assert _list_recent_dates(limit=2) == [
            datetime.date(2026, 5, 15),
            datetime.date(2026, 5, 14),
        ]

    def test_ignores_non_matching_filenames(self, reports_dir: Path) -> None:
        self._touch(reports_dir, '2026-05-15')
        (reports_dir / 'README.txt').write_text('x')
        (reports_dir / 'arXiv_astro_ph_HE_daily_report_garbage.html').write_text('x')
        assert _list_recent_dates() == [datetime.date(2026, 5, 15)]
