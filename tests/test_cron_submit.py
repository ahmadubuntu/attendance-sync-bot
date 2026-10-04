"""Tests for the cron submit loop in src/attendance_sync/cron_submit.py.

The previous suite only grepped scripts/cron-catchup.sh for substrings, which is why a
literal '$PROJECT/src' inside a quoted heredoc survived several "passing" test runs. These
tests exercise the real logic: approval-code extraction, one child per payload, per-run
failure accounting, and the guarantee that a plan with nothing to do submits nothing.
"""

import json

import pytest

from attendance_sync import cron_submit


class FakeResult:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, stdout='', stderr='', returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


DRY_OK = (
    'planned payload 1405/06/28 08:30-18:00\n'
    '{"approval_code": "abc123", "payload_count": 1}\n'
    'hint: re-run with --confirm --approve abc123\n'
)


def payload(day='1405/06/28', start='08:30', end='18:00'):
    return {'day': day, 'start_time': start, 'end_time': end}


def record_call(monkeypatch, dry_stdout=DRY_OK, submit_code=0):
    """Record every child invocation and return scripted results."""
    calls = []
    results = {}

    def fake_run(args, timeout=None):
        calls.append(list(args))
        if '--confirm' in args:
            return results.get('submit', FakeResult('{"ok": true}', returncode=submit_code))
        return results.get('dry', FakeResult(dry_stdout))

    monkeypatch.setattr(cron_submit, '_run', fake_run)
    return calls


# ---------------------------------------------------------------------------
# approval code extraction
# ---------------------------------------------------------------------------

def test_approval_code_is_read_from_the_summary_line():
    assert cron_submit.extract_approval_code(DRY_OK) == 'abc123'


def test_hint_line_alone_yields_no_code():
    """The last stdout line is a hint, not JSON; it must not be parsed as a summary."""
    out = 'planned 1 payload\nhint: re-run with --confirm --approve xyz\n'
    assert cron_submit.extract_approval_code(out) is None


def test_empty_and_malformed_output_yield_no_code():
    assert cron_submit.extract_approval_code('') is None
    assert cron_submit.extract_approval_code(None) is None
    assert cron_submit.extract_approval_code('{not json approval_code') is None


def test_json_without_the_key_yields_no_code():
    assert cron_submit.extract_approval_code('{"payload_count": 1}') is None


# ---------------------------------------------------------------------------
# one child interpreter per payload, dry run then confirm
# ---------------------------------------------------------------------------

def test_each_payload_gets_its_own_dry_run_and_submit(tmp_path, monkeypatch):
    calls = record_call(monkeypatch)
    plan = {'payloads': [payload(), payload(day='1405/06/29', start='09:00', end='17:00')]}
    single = tmp_path / 'single.json'

    report = cron_submit.submit_plan(plan, single)

    assert len(report.ok) == 2
    assert not report.failed
    # 2 payloads x (dry run + confirmed submit)
    assert len(calls) == 4
    for call in calls:
        assert call[0] == 'kasra-submit'
        assert '--plan' in call
        # Each child must receive the single-payload file, never the full plan.
        assert str(call[call.index('--plan') + 1]) == str(single)


def test_confirmed_submit_passes_the_code_from_its_own_dry_run(tmp_path, monkeypatch):
    calls = record_call(monkeypatch)
    cron_submit.submit_plan({'payloads': [payload()]}, tmp_path / 'single.json')

    confirmed = [c for c in calls if '--confirm' in c]
    assert len(confirmed) == 1
    assert confirmed[0][confirmed[0].index('--approve') + 1] == 'abc123'


def test_submit_timeout_is_generous_enough_for_a_kasra_save(tmp_path, monkeypatch):
    calls = record_call(monkeypatch)
    cron_submit.submit_plan({'payloads': [payload()]}, tmp_path / 'single.json')
    confirmed = [c for c in calls if '--confirm' in c][0]
    assert confirmed[confirmed.index('--timeout-ms') + 1] == str(cron_submit.SUBMIT_TIMEOUT_MS)


def test_single_file_holds_exactly_one_payload(tmp_path, monkeypatch):
    record_call(monkeypatch)
    single = tmp_path / 'single.json'
    cron_submit.submit_plan({'payloads': [payload(), payload(day='1405/06/29')]}, single)
    written = json.loads(single.read_text(encoding='utf-8'))
    assert len(written['payloads']) == 1


def test_empty_plan_runs_nothing(tmp_path, monkeypatch):
    calls = record_call(monkeypatch)
    report = cron_submit.submit_plan({'payloads': []}, tmp_path / 'single.json')
    assert calls == []
    assert report.total == 0


def test_missing_payloads_key_is_treated_as_empty(tmp_path, monkeypatch):
    calls = record_call(monkeypatch)
    report = cron_submit.submit_plan({}, tmp_path / 'single.json')
    assert calls == []
    assert report.total == 0


# ---------------------------------------------------------------------------
# failure accounting: one bad payload must not abandon the rest
# ---------------------------------------------------------------------------

def test_dry_run_failure_is_recorded_and_the_next_payload_still_runs(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, timeout=None):
        calls.append(list(args))
        if '--confirm' in args:
            return FakeResult('{"ok": true}')
        # First dry run: no approval code at all. Second: normal.
        if len([c for c in calls if '--confirm' not in c]) == 1:
            return FakeResult('dry run blew up', stderr='boom')
        return FakeResult(DRY_OK)

    monkeypatch.setattr(cron_submit, '_run', fake_run)
    report = cron_submit.submit_plan({'payloads': [payload(), payload(day='1405/06/29')]},
                                     tmp_path / 'single.json')

    assert len(report.failed) == 1
    assert len(report.ok) == 1
    assert 'boom' in report.failed[0].detail or 'blew up' in report.failed[0].detail
    # Exactly one confirmed submit: the payload whose dry run produced no code.
    assert len([c for c in calls if '--confirm' in c]) == 1
    assert report.summary() == 'RESULT ok=1 failed=1'


def test_confirm_failure_is_recorded_with_its_detail(tmp_path, monkeypatch):
    def fake_run(args, timeout=None):
        if '--confirm' in args:
            return FakeResult('', stderr='kasra timeout', returncode=1)
        return FakeResult(DRY_OK)

    monkeypatch.setattr(cron_submit, '_run', fake_run)
    report = cron_submit.submit_plan({'payloads': [payload()]}, tmp_path / 'single.json')

    assert not report.ok
    assert len(report.failed) == 1
    assert report.failed[0].code == 'abc123'
    assert 'timeout' in report.failed[0].detail


def test_summary_line_counts_only_this_run(tmp_path, monkeypatch):
    """The tally must come from this run, so an old failure cannot leak into it."""
    lines = []
    monkeypatch.setattr(cron_submit, '_run',
                        lambda args, timeout=None: FakeResult(DRY_OK if '--confirm' not in args
                                                               else '{"ok": true}'))
    report = cron_submit.submit_plan({'payloads': [payload(), payload(day='1405/06/29')]},
                                     tmp_path / 'single.json', log=lines.append)

    assert report.summary() == 'RESULT ok=2 failed=0'
    assert lines[-1] == 'RESULT ok=2 failed=0'


# ---------------------------------------------------------------------------
# the child command must not depend on shell expansion
# ---------------------------------------------------------------------------

def test_child_command_inlines_the_src_path(monkeypatch):
    """AS_SRC is inlined literally, so no '$PROJECT' can survive into the child."""
    monkeypatch.setenv('AS_SRC', '/tmp/fake-src')
    command = cron_submit._child_command()
    assert '/tmp/fake-src' in command
    assert '$PROJECT' not in command


def test_child_command_falls_back_to_the_package_parent(monkeypatch):
    monkeypatch.delenv('AS_SRC', raising=False)
    assert cron_submit.DEFAULT_SRC in cron_submit._child_command()


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))