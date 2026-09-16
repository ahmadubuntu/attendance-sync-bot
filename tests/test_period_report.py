"""The permanent period report: pure logic, synthetic data, no browser and no network.

The user asked for one permanent script: state a Jalali range and get a calendar plus a
summary. These tests pin the parts that must not drift — the day cells, the statuses, the
totals, the escaping, and the promise that a day whose data is incomplete is explained.
"""
import json
import stat
from datetime import datetime
from pathlib import Path

import pytest

from attendance_sync.kasra_reconcile import CREDIT_TYPE_OVERTIME, CREDIT_TYPE_REGULAR, STATUS_APPROVED, STATUS_PENDING
from test_parser import post

# 1405/06/21 is Saturday 2026-09-12; 1405/06/27 is Friday 2026-09-18.
SATURDAY, WEDNESDAY, THURSDAY, FRIDAY = '2026-09-12', '2026-09-16', '2026-09-17', '2026-09-18'


def report_for(rows, **kwargs):
    from attendance_sync.report import build_report
    from datetime import datetime
    return build_report(rows, 'self', datetime.fromisoformat('2026-09-12T00:00:00+03:30'),
                        datetime.fromisoformat('2026-09-18T23:59:00+03:30'), **kwargs)


def rows_for(*specs):
    return [post(text, stamp, ident) for text, stamp, ident in specs]


def document(doc_id, jalali, credit_type, start, end, status_id=STATUS_APPROVED):
    minutes = (int(end[:2]) * 60 + int(end[3:])) - (int(start[:2]) * 60 + int(start[3:]))
    return {'doc_id': doc_id, 'credit_type': credit_type, 'status_id': status_id, 'day': jalali,
            'start_time': start, 'end_time': end, 'minutes': minutes,
            'active': status_id in (STATUS_APPROVED, STATUS_PENDING), 'doc_type_id': 1}


def kasra_side(*documents, day_type=None, jalali='1405/06/21', regular=0, overtime=0):
    return {jalali: {'jalali_date': jalali, 'day_type': day_type, 'report_regular_minutes': regular,
                     'report_overtime_minutes': overtime, 'documents': list(documents)}}


def calendar_days(page):
    """Read the week grid back as one record per rendered day cell."""
    import re
    from html import unescape
    grid = page.split('class="calendar"')[1].split('</table>')[0]
    days = []
    for opening in re.findall(r'<td class="day[^"]*"[^>]*>', grid):
        if 'class="day empty"' in opening:
            continue
        days.append({key: unescape(value) for key, value in
                     re.findall(r'data-([a-z_]+)="([^"]*)"', opening)})
    return days


def test_range_validation_matches_the_kasra_status_style():
    from attendance_sync.cli import main
    for start, end in (('1405/6/1', '1405/06/21'), ('1405/06/21', '1405/06/01'), ('14/06/01', '1405/06/21')):
        assert main(['period-report', '--start', start, '--end', end, '--output', 'artifacts/out']) == 2


def test_a_full_pair_day_shows_both_clocks_and_regular_minutes():
    report = report_for(rows_for(('شنبه 14050621 ورود 0810 خروج 1710', '2026-09-12T17:10:00+03:30', 'both'),))
    from attendance_sync.period_report import build_period
    period = build_period(report, start='1405/06/21', end='1405/06/27')
    day = next(row for row in period['days'] if row['local_date'] == SATURDAY)
    assert day['entry'] == '08:10' and day['exit'] == '17:10'
    assert day['regular_minutes'] == 540
    assert day['status'] == 'regular'
    assert day['incomplete'] is False


def test_a_day_with_only_one_clock_is_incomplete_and_explained():
    report = report_for(rows_for(('خروج شنبه 1700', '2026-09-12T17:00:00+03:30', 'lonely'),))
    from attendance_sync.period_report import build_period, render_markdown
    period = build_period(report, start='1405/06/21', end='1405/06/27')
    day = next(row for row in period['days'] if row['local_date'] == SATURDAY)
    assert day['incomplete'] is True
    assert day['regular_minutes'] == 0 and day['overtime_minutes'] == 0
    assert day['source_ids']
    note = render_markdown(period)
    assert '1405/06/21' in note and 'incomplete' in note


def test_a_current_day_open_entry_is_not_reported_as_incomplete():
    from datetime import datetime
    from attendance_sync.report import build_report
    report = build_report(rows_for(('شنبه ورود 0810', '2026-09-12T08:10:00+03:30', 'open'),), 'self',
                          datetime.fromisoformat('2026-09-12T00:00:00+03:30'),
                          datetime.fromisoformat('2026-09-18T23:59:00+03:30'),
                          as_of=datetime.fromisoformat('2026-09-12T10:00:00+03:30'))
    from attendance_sync.period_report import build_period
    day = next(row for row in build_period(report, start='1405/06/21', end='1405/06/27')['days']
               if row['local_date'] == SATURDAY)
    assert day['incomplete'] is False
    assert day['status'] == 'open_day'


def test_absence_and_rest_days_carry_their_status():
    from attendance_sync.period_report import build_period
    period = build_period(report_for([]), start='1405/06/21', end='1405/06/27',
                          kasra={'1405/06/23': {'jalali_date': '1405/06/23', 'day_type': 'غيبت', 'documents': []},
                                 '1405/06/26': {'jalali_date': '1405/06/26', 'day_type': 'استراحت', 'documents': []}})
    by_day = {row['jalali_date']: row for row in period['days']}
    assert by_day['1405/06/23']['status'] == 'absence'
    assert by_day['1405/06/26']['status'] == 'rest'


def test_a_holiday_and_a_thursday_are_both_rest_by_policy():
    from attendance_sync.period_report import build_period
    period = build_period(report_for([]), start='1405/06/21', end='1405/06/27',
                          kasra={'1405/06/24': {'jalali_date': '1405/06/24', 'day_type': 'عید سعید', 'documents': []}})
    by_day = {row['jalali_date']: row for row in period['days']}
    assert by_day['1405/06/24']['status'] == 'holiday'
    assert by_day['1405/06/26']['status'] == 'rest'


def test_overtime_and_both_days_are_distinguished():
    from attendance_sync.period_report import build_period
    report = report_for(rows_for(
        ('شنبه 14050621 ورود 0810 خروج 1210', '2026-09-12T12:10:00+03:30', 'short'),
        ('یکشنبه 14050622 ورود 0810 خروج 1210', '2026-09-13T12:10:00+03:30', 'short2'),
        ('یکشنبه 14050622 اضافه کاری 1300-1700', '2026-09-13T17:05:00+03:30', 'extra')))
    by_day = {row['local_date']: row for row in build_period(report, start='1405/06/21', end='1405/06/27')['days']}
    assert by_day[SATURDAY]['status'] == 'regular'
    assert by_day['2026-09-13']['regular_minutes'] > 0
    assert by_day['2026-09-13']['overtime_minutes'] > 0
    assert by_day['2026-09-13']['status'] == 'both'


def test_a_pending_document_marks_the_day_pending_approval():
    from attendance_sync.period_report import build_period
    kasra = kasra_side(document('1', '1405/06/21', CREDIT_TYPE_REGULAR, '08:20', '17:20', status_id=STATUS_PENDING))
    period = build_period(report_for([]), start='1405/06/21', end='1405/06/27', kasra=kasra)
    day = next(row for row in period['days'] if row['local_date'] == SATURDAY)
    assert day['status'] == 'pending_approval'
    assert day['registered_regular_minutes'] == 0
    assert day['pending_regular_minutes'] == 540


def test_the_registered_minutes_kasra_already_holds_are_shown():
    from attendance_sync.period_report import build_period
    kasra = kasra_side(document('1', '1405/06/21', CREDIT_TYPE_REGULAR, '09:00', '10:00'),
                       document('2', '1405/06/21', CREDIT_TYPE_OVERTIME, '17:00', '18:30'))
    day = next(row for row in build_period(report_for([]), start='1405/06/21', end='1405/06/27',
                                           kasra=kasra)['days'] if row['local_date'] == SATURDAY)
    assert day['registered_regular_minutes'] == 60
    assert day['registered_overtime_minutes'] == 90


def test_summary_totals_equal_the_sum_of_the_day_rows():
    from attendance_sync.period_report import build_period
    report = report_for(rows_for(
        ('شنبه 14050621 ورود 0810 خروج 1710', '2026-09-12T17:10:00+03:30', 'a'),
        ('یکشنبه 14050622 ورود 0810 خروج 1210', '2026-09-13T12:10:00+03:30', 'b'),
        ('دوشنبه 14050623 اضافه کاری 1300-1700', '2026-09-14T17:05:00+03:30', 'c')))
    period = build_period(report, start='1405/06/21', end='1405/06/27')
    days = period['days']
    assert period['totals']['regular_minutes'] == sum(row['regular_minutes'] for row in days)
    assert period['totals']['overtime_minutes'] == sum(row['overtime_minutes'] for row in days)
    assert period['totals']['work_days'] == sum(row['status'] in ('regular', 'overtime', 'both') for row in days)
    assert period['totals']['incomplete_days'] == sum(row['incomplete'] for row in days)


def test_the_grid_covers_saturday_to_friday_for_exactly_the_requested_range():
    from attendance_sync.period_report import build_period, render_html
    period = build_period(report_for([]), start='1405/06/21', end='1405/06/27')
    assert len(period['days']) == 7
    assert [row['local_date'] for row in period['days']] == [
        '2026-09-12', '2026-09-13', '2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18']
    page = render_html(period)
    days = calendar_days(page)
    assert [day['local_date'] for day in days] == [row['local_date'] for row in period['days']]
    assert days[0]['jalali_date'] == '1405/06/21' and days[-1]['jalali_date'] == '1405/06/27'


def test_the_html_escapes_a_message_that_carries_markup():
    from attendance_sync.period_report import build_period, render_html
    report = report_for(rows_for(('شنبه 14050621 ورود 0810 خروج 1710 <b>bold</b>',
                                  '2026-09-12T17:10:00+03:30', 'markup'),))
    page = render_html(build_period(report, start='1405/06/21', end='1405/06/27'))
    assert '<b>bold</b>' not in page
    assert '&lt;b&gt;bold&lt;/b&gt;' in page
    assert "default-src 'none'" in page


def test_the_renderer_never_reaches_for_an_external_asset():
    from attendance_sync.period_report import build_period, render_html
    page = render_html(build_period(report_for([]), start='1405/06/21', end='1405/06/27'))
    assert 'http://' not in page and 'https://' not in page
    assert '<link' not in page and '<img' not in page and '<script' not in page


def test_the_markdown_lists_the_day_columns_and_the_incomplete_note():
    from attendance_sync.period_report import build_period, render_markdown
    kasra = kasra_side(document('1', '1405/06/21', CREDIT_TYPE_REGULAR, '08:20', '17:20'),
                       regular=540, overtime=0)
    report = report_for(rows_for(('شنبه 14050621 ورود 0810 خروج 1710', '2026-09-12T17:10:00+03:30', 'a'),
                                 ('خروج یکشنبه 1700', '2026-09-13T17:00:00+03:30', 'lonely')))
    text = render_markdown(build_period(report, start='1405/06/21', end='1405/06/27', kasra=kasra))
    assert '| Day |' in text or '| Date |' in text
    for column in ('Entry', 'Exit', 'Computed regular', 'Computed overtime', 'Registered regular',
                   'Registered overtime', 'Status'):
        assert column in text
    assert '2026-09-13' in text
    assert 'incomplete' in text.lower()


def test_the_report_renders_only_the_requested_range_even_with_context_events():
    from attendance_sync.period_report import build_period
    # The later range pairs with an earlier entry; the earlier day must not appear as a cell.
    report = report_for(rows_for(('شنبه 14050621 ورود 2300', '2026-09-12T23:00:00+03:30', 'night'),
                                 ('خروج 0400', '2026-09-13T04:00:00+03:30', 'morning')))
    period = build_period(report, start='1405/06/23', end='1405/06/27')
    assert [row['jalali_date'] for row in period['days']] == [
        '1405/06/23', '1405/06/24', '1405/06/25', '1405/06/26', '1405/06/27']


def test_no_kasra_still_produces_the_same_shape_without_registered_minutes():
    from attendance_sync.period_report import build_period
    report = report_for(rows_for(('شنبه 14050621 ورود 0810 خروج 1710', '2026-09-12T17:10:00+03:30', 'a')))
    period = build_period(report, start='1405/06/21', end='1405/06/27', kasra=None, kasra_check='not_performed')
    day = next(row for row in period['days'] if row['local_date'] == SATURDAY)
    assert day['regular_minutes'] == 540
    assert day['registered_regular_minutes'] is None
    assert period['kasra_check'] == 'not_performed'


def test_write_period_reports_creates_private_files(tmp_path):
    from attendance_sync.period_report import build_period, write_period_reports
    report = report_for(rows_for(('شنبه 14050621 ورود 0810 خروج 1710', '2026-09-12T17:10:00+03:30', 'a')))
    paths = write_period_reports(build_period(report, start='1405/06/21', end='1405/06/27'),
                                 tmp_path / 'private' / 'period-report')
    assert [path.name for path in paths] == ['period-report.html', 'period-report.md']
    for path in paths:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths[0].parent.stat().st_mode) == 0o700
    assert '2026-09-12' in paths[0].read_text(encoding='utf-8')
    assert '2026-09-12' in paths[1].read_text(encoding='utf-8')


def test_write_period_reports_rejects_a_shared_directory(tmp_path, monkeypatch):
    from attendance_sync.period_report import build_period, write_period_reports
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        write_period_reports(build_period(report_for([]), start='1405/06/21', end='1405/06/27'),
                             'period-report')


ENV = {'GOFT_URL': 'https://chat.example', 'GOFT_TOKEN': 'secret', 'GOFT_CHANNEL_ID': 'a' * 26}
DAILY_COLUMNS = ['رديف', 'تاريخ', 'روز', 'ترددها', 'دوركاري', 'دوركاري خارج از موظفي', 'كسر حضور']
DOC_COLUMNS = ['', 'فیلترStatusID', 'فیلترDocID', 'فیلترDocTypeID', 'DocDescr', 'DocTitle', 'RSDate', 'REDate']


def mattermost_transport(rows):
    import httpx
    def handler(request):
        assert request.method == 'GET' and request.url.host == 'chat.example'
        if request.url.path.endswith('/users/me'):
            return httpx.Response(200, json={'id': 'self'})
        if request.url.path.endswith('/posts'):
            listed = sorted(rows, key=lambda row: row['create_at'], reverse=True)
            data = ({'order': [], 'posts': {}} if request.url.params.get('before')
                    else {'order': [row['id'] for row in listed], 'posts': {row['id']: row for row in listed}})
            return httpx.Response(200, json=data)
        return httpx.Response(200, json={'id': 'a' * 26})
    return httpx.MockTransport(handler)


class FakeKasra:
    """Duck-typed stand-in that records calls and never touches a browser."""

    def __init__(self, daily=None, documents=None):
        self.daily = daily if daily is not None else {'columns': DAILY_COLUMNS, 'rows': [
            ['1', '1405/06/21', 'شنبه', 'دوركاري خارج از موظفي', '', '03:31', '09:00']]}
        self.documents = documents if documents is not None else {'columns': DOC_COLUMNS, 'rows': [
            ['', '203', '900001', '1', '', 'مجوز دوركاري ساعتي از تاریخ 1405/06/21 تا تاریخ 1405/06/21 '
                                           'از 08:20 تا 17:20', '1405/06/21', '1405/06/21']]}
        self.calls = []

    def open(self):
        self.calls.append('open')

    def close(self):
        self.calls.append('close')

    def read_daily_report(self, start, end):
        self.calls.append('read_daily_report')
        return self.daily

    def read_documents(self, start, end):
        self.calls.append('read_documents')
        return self.documents


def run_period(tmp_path, rows, extra=(), kasra=None, env=ENV):
    from attendance_sync.cli import main
    output = tmp_path / 'private' / 'period-report'
    code = main(['period-report', '--start', '1405/06/21', '--end', '1405/06/27',
                 '--output', str(output), '--corrections', str(tmp_path / 'absent-corrections.json')] + list(extra),
                env=env, transport=mattermost_transport(rows), kasra=kasra)
    return code, Path(output), output


def test_period_report_cli_writes_both_private_artifacts(tmp_path):
    from pathlib import Path
    rows = rows_for(('شنبه 14050621 ورود 0830 خروج 1730', '2026-09-12T17:30:00+03:30', 'a'))
    code, page, output = run_period(tmp_path, rows, kasra=FakeKasra())
    assert code == 0
    html, markdown = Path(str(output) + '.html'), Path(str(output) + '.md')
    assert html.exists() and markdown.exists()
    assert stat.S_IMODE(html.stat().st_mode) == 0o600
    assert stat.S_IMODE(markdown.stat().st_mode) == 0o600
    assert stat.S_IMODE(html.parent.stat().st_mode) == 0o700
    body = html.read_text(encoding='utf-8')
    assert '2026-09-12' in body and '1405/06/21' in body and '2026-09-18' in body
    assert '1405/06/21' in markdown.read_text(encoding='utf-8')


def test_period_report_cli_reads_a_snapshot_without_a_browser(tmp_path):
    from pathlib import Path
    snapshot = tmp_path / 'snapshot.json'
    client = FakeKasra()
    snapshot.write_text(json.dumps({'daily': client.daily, 'documents': client.documents}), encoding='utf-8')

    class Exploding:
        def __getattr__(self, name):
            raise AssertionError('snapshot mode must not touch Kasra')

    rows = rows_for(('شنبه 14050621 ورود 0830 خروج 1730', '2026-09-12T17:30:00+03:30', 'a'))
    code, page, output = run_period(tmp_path, rows, ['--snapshot', str(snapshot)], kasra=Exploding())
    assert code == 0
    assert '2026-09-12' in Path(str(output) + '.html').read_text(encoding='utf-8')


def test_period_report_cli_no_kasra_omits_the_registered_minutes(tmp_path):
    from pathlib import Path
    rows = rows_for(('شنبه 14050621 ورود 0830 خروج 1730', '2026-09-12T17:30:00+03:30', 'a'))
    code, page, output = run_period(tmp_path, rows, ['--no-kasra'])
    assert code == 0
    html = Path(str(output) + '.html').read_text(encoding='utf-8')
    assert 'data-registered=""' in html
    assert 'not_performed' in Path(str(output) + '.md').read_text(encoding='utf-8')


def test_period_report_cli_rejects_a_malformed_range(tmp_path, capsys):
    from attendance_sync.cli import main
    for start, end in (('1405/6/1', '1405/06/27'), ('1405/06/27', '1405/06/21'), ('14/06/21', '1405/06/27')):
        assert main(['period-report', '--start', start, '--end', end,
                     '--output', str(tmp_path / 'private' / 'out')], env=ENV) == 2
    assert 'Invalid' in capsys.readouterr().err


def test_period_report_cli_does_not_leak_credentials_on_failure(tmp_path, capsys):
    import httpx
    from attendance_sync.cli import main

    def handler(request):
        raise httpx.ConnectError('secret password body', request=request)

    assert main(['period-report', '--start', '1405/06/21', '--end', '1405/06/27',
                 '--output', str(tmp_path / 'private' / 'out')],
                env=ENV, transport=httpx.MockTransport(handler)) == 2
    text = capsys.readouterr().err
    assert 'secret' not in text and 'password' not in text


def write_review(path, rows, start='2026-09-12T00:00:00+03:30', end='2026-09-18T23:59:59+03:30'):
    """A preview report of an exact window, as the preview subcommand would leave behind."""
    from attendance_sync.report import build_report
    report = build_report(rows, 'self', datetime.fromisoformat(start), datetime.fromisoformat(end))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report), encoding='utf-8')
    return path


def test_period_report_reuses_a_review_of_the_exact_window(tmp_path):
    """No transport: the review file alone must satisfy the command when the window matches."""
    from attendance_sync.cli import main
    review = write_review(tmp_path / 'private' / 'review.json',
                          rows_for(('شنبه 14050621 ورود 0830 خروج 1730', '2026-09-12T17:30:00+03:30', 'a')))
    output = tmp_path / 'private' / 'period-report'
    assert main(['period-report', '--start', '1405/06/21', '--end', '1405/06/27',
                 '--output', str(output), '--review', str(review), '--no-kasra'], env=ENV) == 0
    assert '2026-09-12' in (tmp_path / 'private' / 'period-report.html').read_text(encoding='utf-8')


def test_period_report_ignores_a_review_of_another_window(tmp_path):
    from attendance_sync.cli import main
    review = write_review(tmp_path / 'private' / 'review.json',
                          rows_for(('شنبه 14050621 ورود 0830 خروج 1730', '2026-09-12T17:30:00+03:30', 'a')),
                          start='2026-09-05T00:00:00+03:30', end='2026-09-06T23:59:59+03:30')
    rows = rows_for(('شنبه 14050621 ورود 0810 خروج 1710', '2026-09-12T17:10:00+03:30', 'a'))
    output = tmp_path / 'private' / 'period-report'
    assert main(['period-report', '--start', '1405/06/21', '--end', '1405/06/27',
                 '--output', str(output), '--review', str(review), '--no-kasra'],
                env=ENV, transport=mattermost_transport(rows)) == 0
    body = (tmp_path / 'private' / 'period-report.html').read_text(encoding='utf-8')
    assert '08:10' in body and '17:10' in body


def test_period_report_ignores_a_malformed_review_file(tmp_path):
    from attendance_sync.cli import main
    review = tmp_path / 'private' / 'review.json'
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text('{not json', encoding='utf-8')
    rows = rows_for(('شنبه 14050621 ورود 0830 خروج 1730', '2026-09-12T17:30:00+03:30', 'a'))
    assert main(['period-report', '--start', '1405/06/21', '--end', '1405/06/27',
                 '--output', str(tmp_path / 'private' / 'out'), '--review', str(review), '--no-kasra'],
                env=ENV, transport=mattermost_transport(rows)) == 0
