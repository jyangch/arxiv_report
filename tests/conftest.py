"""Shared pytest fixtures for arxiv_report server tests."""

from pathlib import Path

from fastapi.testclient import TestClient
import pytest


@pytest.fixture
def reports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``REPORTS_DIR`` to a per-test tmp directory.

    Patches both the source-of-truth constant in ``arxiv_report.render``
    and the import-time copy in ``server``. Returns the tmp path so
    tests can pre-populate fake report files.
    """
    d = tmp_path / 'reports'
    d.mkdir()
    monkeypatch.setattr('arxiv_report.render.REPORTS_DIR', str(d))
    monkeypatch.setattr('server.REPORTS_DIR', str(d))
    return d


@pytest.fixture
def client(reports_dir: Path) -> TestClient:
    """A TestClient bound to a clean tmp reports directory.

    Importing inside the fixture ensures the ``reports_dir`` monkeypatch
    has already swapped the constants before ``app`` resolves them.
    """
    from server import app
    return TestClient(app)
