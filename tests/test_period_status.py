"""Regression tests for the period-report status precedence and the as-of default."""
from datetime import datetime
import json

import pytest

from attendance_sync.period_report import build_period


def day_row(local_date, jalali, **extra):
    row = {'local_date': local_date, 'jalali_date': jalali, 'regular_minutes': 0, 'overtime_minutes': 0}
    row.update(extra)
    return row


def kasra_row(marker):
    return {'marker': marker, 'registered_regular_minutes': 0, 'registered_overtime_minutes': 0,
            'pending_regular_minutes': 0, 'pending_overtime_minutes': 0, 'documents': []}


def report_with(events, segments):
    return {'events': events, 'context_events': [], 'segments': segments, 'ranges': [],
            'intervals': [], 'current_day': None}


def test_absence_marker_never_outranks_evidenced_work():
    from attendance_sync.period_report import _status
    status = _status('2026-08-16', 540, 45, kasra_row('غيبت'), [], False)
    assert status in ('both', 'regular', 'overtime'), 'a day we can evidence must not read as absence'
    assert status != 'absence'


def test_absence_marker_still_reported_when_no_work_is_evidenced():
    from attendance_sync.period_report import _status
    assert _status('2026-08-16', 0, 0, kasra_row('غيبت'), [], False) == 'absence'


def test_pending_approval_outranks_work_labels():
    from attendance_sync.period_report import _status
    row = kasra_row('دوركاري ساعتي (منتظر تایید)')
    row['pending_regular_minutes'] = 540
    assert _status('2026-09-08', 540, 160, row, [], False) == 'pending_approval'


def test_open_day_outranks_everything_else():
    from attendance_sync.period_report import _status
    row = kasra_row('غيبت')
    assert _status('2026-09-12', 540, 135, row, [], True) == 'open_day'


def test_period_report_cli_defers_the_current_day_by_default(monkeypatch, tmp_path):
    """The shipped CLI must never treat the live day as a completed historical day."""
    from attendance_sync import cli
    captured = {}
    real_build = cli.build_report

    def spy(posts, own_id, start, end, **kwargs):
        captured['as_of'] = kwargs.get('as_of')
        return real_build(posts, own_id, start, end, **kwargs)

    monkeypatch.setattr(cli, 'build_report', spy)
    payload = {'order': [], 'posts': {}}
    import httpx
    import os
    env = {'GOFT_URL': 'https://chat.example', 'GOFT_TOKEN': 'secret', 'GOFT_CHANNEL_ID': 'a' * 26}

    def handler(request):
        if request.url.path.endswith('/users/me'):
            return httpx.Response(200, json={'id': 'self'})
        if request.url.path.endswith('/posts'):
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={'id': 'a' * 26})

    code = cli.main(['period-report', '--start', '1405/06/20', '--end', '1405/06/21',
                     '--no-kasra', '--output', str(tmp_path / 'private' / 'report')],
                    env=env, transport=httpx.MockTransport(handler))
    assert code == 0
    assert captured['as_of'] is not None, 'period-report must resolve an as-of instant, not leave it empty'
    assert captured['as_of'].utcoffset() is not None
