"""Tests for the arxiv_report FastAPI server."""

import datetime
from pathlib import Path
import uuid

from fastapi.testclient import TestClient
import pytest

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


class TestRecent:
    def test_empty(self, client: TestClient) -> None:
        r = client.get('/recent')
        assert r.status_code == 200
        assert 'No reports yet.' in r.text

    def test_listing_descending(self, client: TestClient, reports_dir: Path) -> None:
        for d in ('2026-05-13', '2026-05-19', '2026-05-15'):
            (reports_dir / f'arXiv_astro_ph_HE_daily_report_{d}.html').write_text('x')
        r = client.get('/recent')
        assert r.status_code == 200
        # First date in body should be the newest.
        i_19 = r.text.index('2026-05-19')
        i_15 = r.text.index('2026-05-15')
        i_13 = r.text.index('2026-05-13')
        assert i_19 < i_15 < i_13

    def test_active_flag(self, client: TestClient, reports_dir: Path) -> None:
        (reports_dir / 'arXiv_astro_ph_HE_daily_report_2026-05-19.html').write_text('x')
        r = client.get('/recent?active=2026-05-19')
        assert 'is-active' in r.text


class TestWorker:
    def test_success_path(self, reports_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import server

        monkeypatch.setattr(server, 'fetch_arxiv_papers', lambda as_of: [{'title': 't'}])
        monkeypatch.setattr(server, 'generate_report', lambda papers: ('<p>body</p>', 'claude'))
        save_called = {}

        def fake_save(papers, report, provider, as_of=None):
            save_called.update(papers=papers, report=report, provider=provider, as_of=as_of)
            return '/fake/path.html'

        monkeypatch.setattr(server, 'save_html', fake_save)
        task_id = 'fixed-id'
        server._tasks[task_id] = {
            'status': 'running',
            'date': '2026-05-22',
            'messages': [],
            'report_path': None,
            'provider': None,
            'error': None,
        }
        server._worker(
            task_id,
            datetime.datetime(2026, 5, 22, 12, 0),
            '2026-05-22',
        )
        t = server._tasks[task_id]
        assert t['status'] == 'done'
        assert t['provider'] == 'claude'
        assert t['report_path'] == '/fake/path.html'
        assert t['error'] is None
        assert any('Fetching' in m for m in t['messages'])
        assert any('Calling LLM' in m for m in t['messages'])
        assert save_called['provider'] == 'claude'

    def test_empty_papers_short_circuits(
        self, reports_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import server

        monkeypatch.setattr(server, 'fetch_arxiv_papers', lambda as_of: [])
        called = {'gen': False, 'save': False}
        monkeypatch.setattr(
            server,
            'generate_report',
            lambda papers: called.update(gen=True) or ('x', 'p'),
        )
        monkeypatch.setattr(
            server,
            'save_html',
            lambda *a, **k: called.update(save=True) or '/p',
        )
        task_id = 'fixed-id-empty'
        server._tasks[task_id] = {
            'status': 'running',
            'date': '2026-05-23',
            'messages': [],
            'report_path': None,
            'provider': None,
            'error': None,
        }
        server._worker(task_id, datetime.datetime(2026, 5, 23, 12, 0), '2026-05-23')
        t = server._tasks[task_id]
        assert t['status'] == 'done'
        assert t['report_path'] is None
        assert not called['gen']
        assert not called['save']
        assert any('No papers' in m for m in t['messages'])

    def test_fetch_error_marks_task_error(
        self, reports_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import server

        def boom(as_of):
            raise RuntimeError('429 Too Many Requests')

        monkeypatch.setattr(server, 'fetch_arxiv_papers', boom)
        task_id = 'fixed-id-fetch'
        server._tasks[task_id] = {
            'status': 'running',
            'date': '2026-05-24',
            'messages': [],
            'report_path': None,
            'provider': None,
            'error': None,
        }
        server._worker(task_id, datetime.datetime(2026, 5, 24, 12, 0), '2026-05-24')
        t = server._tasks[task_id]
        assert t['status'] == 'error'
        # The raw '429' from arXiv is humanized into a friendlier message.
        assert 'rate-limiting' in t['error']

    def test_llm_error_marks_task_error(
        self, reports_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import server

        monkeypatch.setattr(server, 'fetch_arxiv_papers', lambda as_of: [{'title': 't'}])

        def boom(papers):
            raise RuntimeError('All providers failed.')

        monkeypatch.setattr(server, 'generate_report', boom)
        monkeypatch.setattr(server, 'save_html', lambda *a, **k: '/p')
        task_id = 'fixed-id-llm'
        server._tasks[task_id] = {
            'status': 'running',
            'date': '2026-05-25',
            'messages': [],
            'report_path': None,
            'provider': None,
            'error': None,
        }
        server._worker(task_id, datetime.datetime(2026, 5, 25, 12, 0), '2026-05-25')
        t = server._tasks[task_id]
        assert t['status'] == 'error'
        assert 'providers failed' in t['error']


class TestGenerate:
    def test_invalid_date_returns_400(self, client: TestClient) -> None:
        r = client.post('/generate', data={'date': 'foo'})
        assert r.status_code == 400

    def test_starts_task_and_returns_panel(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import threading

        import server

        # Block the worker thread on an event so we can observe the
        # 'running' state in _tasks before it completes.
        started = threading.Event()
        release = threading.Event()

        def fake_worker(task_id, as_of, date_str):
            started.set()
            release.wait(timeout=5)
            server._tasks[task_id]['status'] = 'done'

        monkeypatch.setattr(server, '_worker', fake_worker)
        r = client.post('/generate', data={'date': '2026-05-22'})
        assert r.status_code == 200
        assert 'status-panel' in r.text
        assert 'EventSource' in r.text
        # The task id is embedded in the SSE script.
        assert '/generate/stream/' in r.text
        assert started.wait(timeout=2)
        # Exactly one task registered with status 'running' before we let go.
        running = [t for t in server._tasks.values() if t['status'] == 'running']
        assert len(running) == 1
        release.set()


class TestStream:
    def _seed_task(self, status: str, **extra) -> str:
        import server

        task_id = str(uuid.uuid4())
        server._tasks[task_id] = {
            'status': status,
            'date': '2026-05-22',
            'messages': extra.get('messages', []),
            'report_path': extra.get('report_path'),
            'provider': extra.get('provider'),
            'error': extra.get('error'),
        }
        return task_id

    def test_unknown_task_emits_done_event(self, client: TestClient) -> None:
        with client.stream('GET', '/generate/stream/nope') as r:
            assert r.status_code == 200
            body = ''.join(chunk for chunk in r.iter_text())
        assert 'event: done' in body
        assert 'Task not found' in body

    def test_done_with_report_emits_iframe_fragment(
        self, client: TestClient, reports_dir: Path
    ) -> None:
        (reports_dir / 'arXiv_astro_ph_HE_daily_report_2026-05-22.html').write_text('x')
        task_id = self._seed_task(
            'done',
            messages=['Fetching arXiv papers...', 'Done.'],
            report_path='ignored',
            provider='claude',
        )
        with client.stream('GET', f'/generate/stream/{task_id}') as r:
            body = ''.join(chunk for chunk in r.iter_text())
        assert 'event: done' in body
        assert 'src="/r/2026-05-22/raw"' in body
        # Each message is sent as a separate data line.
        assert 'Fetching arXiv papers' in body

    def test_done_empty_emits_empty_panel(self, client: TestClient) -> None:
        task_id = self._seed_task(
            'done',
            messages=['No papers for this date (weekend / holiday / out of range).'],
            report_path=None,
        )
        with client.stream('GET', f'/generate/stream/{task_id}') as r:
            body = ''.join(chunk for chunk in r.iter_text())
        assert 'event: done' in body
        assert 'No papers' in body
        assert 'src=' not in body  # no iframe

    def test_error_emits_error_panel(self, client: TestClient) -> None:
        task_id = self._seed_task(
            'error',
            messages=['Fetching arXiv papers...', 'Fetch failed: 429'],
            error='429 Too Many Requests',
        )
        with client.stream('GET', f'/generate/stream/{task_id}') as r:
            body = ''.join(chunk for chunk in r.iter_text())
        assert 'event: done' in body
        assert '429' in body
        assert 'Wait 5-15 minutes' in body


class TestHumanizeFetchError:
    def test_429_message_is_compacted(self) -> None:
        import server

        msg = server._humanize_fetch_error(
            RuntimeError(
                'Page request resulted in HTTP 429 '
                '(https://export.arxiv.org/api/query?search_query=cat%3Aastro-ph.he&...)'
            )
        )
        assert 'arXiv API is rate-limiting' in msg
        assert 'http' not in msg.lower()
        assert 'export.arxiv.org' not in msg

    def test_cooldown_message_passthrough(self) -> None:
        import server

        cd_msg = 'arXiv API cooldown active (set after a recent HTTP 429); retry in up to 30 min.'
        assert server._humanize_fetch_error(RuntimeError(cd_msg)) == cd_msg

    def test_unrelated_message_untouched(self) -> None:
        import server

        assert server._humanize_fetch_error(ValueError('some other error')) == 'some other error'


class TestControls:
    def test_no_cooldown_renders_active_form(self, client: TestClient) -> None:
        r = client.get('/controls')
        assert r.status_code == 200
        # Active form: HTMX-bound, not aria-disabled.
        assert 'hx-post="/generate"' in r.text
        assert 'aria-disabled' not in r.text
        assert 'Rate-limited' not in r.text
        assert 'Generate report' in r.text

    def test_cooldown_renders_disabled_state(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import server

        monkeypatch.setattr(server, '_arxiv_cooldown_remaining', lambda: 12 * 60)
        r = client.get('/controls')
        assert r.status_code == 200
        assert 'Rate-limited' in r.text
        assert 'disabled' in r.text
        # No active POST form when disabled.
        assert 'hx-post="/generate"' not in r.text
        assert 'retry in ~12 min' in r.text

    def test_active_query_prefills_date_input(self, client: TestClient) -> None:
        r = client.get('/controls?active=2026-05-20')
        assert 'value="2026-05-20"' in r.text


class TestGenerateCooldownGuard:
    def test_post_during_cooldown_returns_error_panel(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import server

        # Stub the worker so we can detect whether the route actually
        # tried to spawn one (it must NOT during cooldown).
        spawned = {'flag': False}

        def fake_worker(task_id, as_of, date_str):
            spawned['flag'] = True

        monkeypatch.setattr(server, '_arxiv_cooldown_remaining', lambda: 7 * 60)
        monkeypatch.setattr(server, '_worker', fake_worker)

        r = client.post('/generate', data={'date': '2026-05-20'})
        assert r.status_code == 200
        assert 'alert-error' in r.text
        assert 'rate-limiting' in r.text.lower() or 'rate-limited' in r.text.lower()
        assert 'retry in ~7 min' in r.text
        # Critically, no worker thread should have started.
        assert spawned['flag'] is False
