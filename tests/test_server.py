"""Tests for the arxiv_report FastAPI server."""

import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from server import _list_recent_dates, _parse_date


class TestParseDate:
    def test_valid_iso_date(self) -> None:
        assert _parse_date('2026-05-22') == datetime.date(2026, 5, 22)

    def test_invalid_format(self) -> None:
        assert _parse_date('foo') is None

    def test_invalid_separator(self) -> None:
        assert _parse_date('2026/05/22') is None

    def test_extra_components(self) -> None:
        # fromisoformat will accept time suffixes; reject those.
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


class TestRawRoute:
    def test_serves_existing_report(self, client: TestClient, reports_dir: Path) -> None:
        (reports_dir / 'arXiv_astro_ph_HE_daily_report_2026-05-22.html').write_text(
            '<html><body>hello</body></html>'
        )
        r = client.get('/r/2026-05-22/raw')
        assert r.status_code == 200
        assert 'hello' in r.text
        assert r.headers['content-type'].startswith('text/html')

    def test_missing_file_returns_404(self, client: TestClient) -> None:
        r = client.get('/r/2099-01-01/raw')
        assert r.status_code == 404

    def test_invalid_date_returns_400(self, client: TestClient) -> None:
        r = client.get('/r/foo/raw')
        assert r.status_code == 400


class TestDateRoute:
    def test_existing_report_renders_iframe(self, client: TestClient, reports_dir: Path) -> None:
        (reports_dir / 'arXiv_astro_ph_HE_daily_report_2026-05-22.html').write_text('x')
        r = client.get('/r/2026-05-22')
        assert r.status_code == 200
        # iframe wired to the raw endpoint for this date
        assert 'src="/r/2026-05-22/raw"' in r.text
        assert 'id="sidebar"' in r.text  # base template was rendered

    def test_missing_report_renders_placeholder(self, client: TestClient) -> None:
        r = client.get('/r/2099-01-01')
        assert r.status_code == 200
        assert 'No report for 2099-01-01 yet' in r.text
        # No iframe -- placeholder only.
        assert 'src="/r/2099-01-01/raw"' not in r.text

    def test_invalid_date_returns_400(self, client: TestClient) -> None:
        r = client.get('/r/foo')
        assert r.status_code == 400


class TestRoot:
    def test_no_reports_renders_placeholder(self, client: TestClient) -> None:
        r = client.get('/')
        assert r.status_code == 200
        assert 'No report' in r.text

    def test_with_reports_redirects_to_latest(self, client: TestClient, reports_dir: Path) -> None:
        for d in ('2026-05-13', '2026-05-19', '2026-05-15'):
            (reports_dir / f'arXiv_astro_ph_HE_daily_report_{d}.html').write_text('x')
        r = client.get('/', follow_redirects=False)
        assert r.status_code == 307
        assert r.headers['location'] == '/r/2026-05-19'
