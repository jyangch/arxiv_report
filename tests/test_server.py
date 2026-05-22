"""Tests for the arxiv_report FastAPI server."""

import datetime

from server import _parse_date


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
