"""Independent correctness regressions using synthetic attendance evidence."""
from datetime import datetime
import pytest
from test_parser import post
from attendance_sync.report import build_report, render_html


def report_for(rows, start='2026-09-12T00:00:00+03:30', end='2026-09-13T23:59:00+03:30'):
    return build_report(rows, 'self', datetime.fromisoformat(start), datetime.fromisoformat(end))


@pytest.mark.parametrize('text', [
    'شنبه بررسی مشکل ورود کاربران و خروج 1700',
    'شنبه ورود کاربران و خروج 1700',
    'شنبه ورود 0900 بررسی مشکل کاربران و خروج 1700',
])
def test_multimarker_prose_cannot_close_genuine_entry(text):
    report = report_for([
        post('شنبه ورود 0800', '2026-09-12T08:00:00+03:30', 'a'),
        post(text, '2026-09-12T17:00:00+03:30', 'b'),
    ])
    assert report['segments'] == []
    assert not any(e.get('paired_entry_id') for e in report['events'])


@pytest.mark.parametrize('text', ['شنبه ورود 0800 خروج', 'شنبه ورود 0800'])
@pytest.mark.parametrize('context', [False, True])
def test_unresolved_attendance_blocks_known_day_with_provenance(text, context):
    from copy import deepcopy
    rows = [post(text, '2026-09-12T12:00:00+03:30', 'a'),
            post('شنبه کار 1300-2300', '2026-09-12T23:00:00+03:30', 'b')]
    before = deepcopy(rows)
    report = report_for(rows, start='2026-09-12T13:00:00+03:30' if context else '2026-09-12T00:00:00+03:30')
    assert report['segments'] == []
    blocker = next(b for b in report['allocation_blockers'] if 'a:0' in b['source_ids'])
    assert blocker['local_date'] == '2026-09-12'
    assert blocker['reasons']
    evidence = report['context_events'] if context else report['events']
    assert next(e for e in evidence if e['event_id'] == 'a:0')['raw_text'] == text
    unresolved = next(i for i in report['intervals'] + report['context_intervals'] if 'a:0' in i['source_ids'])
    assert unresolved['accounting_days'] == ['2026-09-12']
    assert unresolved['start_at'] is None and unresolved['end_at'] is None
    assert rows == before


@pytest.mark.parametrize('text,days', [
    ('یکشنبه 14050621 ورود 0800', ['2026-09-12', '2026-09-13']),
    ('شنبه یکشنبه ورود 0800', ['2026-09-12', '2026-09-13']),
    ('14050621 14050622 کار 0800-0900', ['2026-09-12', '2026-09-13']),
])
def test_ambiguous_dates_block_all_candidate_days(text, days):
    report = report_for([
        post(text, '2026-09-13T12:00:00+03:30', 'a'),
        post('یکشنبه کار 1300-2300', '2026-09-13T23:00:00+03:30', 'b'),
    ], start='2026-09-13T13:00:00+03:30')
    assert report['segments'] == []
    item = report['context_intervals'][0]
    assert item['accounting_days'] == days
    assert report['allocation_blockers'][0]['local_date'] == '2026-09-13'


@pytest.mark.parametrize('text', ['> شنبه ورود 0800', 'شنبه خروج برای ناهار 1200', '`شنبه ورود 0800`'])
def test_ineligible_review_evidence_cannot_block_day(text):
    report = report_for([
        post(text, '2026-09-12T12:00:00+03:30', 'a'),
        post('شنبه کار 1300-2300', '2026-09-12T23:00:00+03:30', 'b'),
    ])
    assert sum(s['duration_minutes'] for s in report['segments']) == 600
    assert report['allocation_blockers'] == []


@pytest.mark.parametrize('overnight,expected,allocated,withheld', [(True, 300, 240, 60), (False, 600, 0, 600)])
def test_withheld_spans_conserve_duration_and_show_daily_review(overnight, expected, allocated, withheld):
    from copy import deepcopy
    import re
    rows = [post('شنبه کار 0800-0900 نبود', '2026-09-12T10:00:00+03:30', 'a'),
            post('شنبه اضافه کاری 2300-0400' if overnight else 'شنبه کار 1300-2300',
                 '2026-09-13T04:00:00+03:30' if overnight else '2026-09-12T23:00:00+03:30', 'b')]
    before = deepcopy(rows)
    report = report_for(rows, start='2026-09-12T13:00:00+03:30')
    item = report['intervals'][0]
    assert item.get('expected_minutes') == expected
    assert item['allocated_minutes'] == allocated
    assert item['withheld_minutes'] == withheld
    assert item['expected_minutes'] == item['allocated_minutes'] + item['withheld_minutes']
    span = report['withheld_spans'][0]
    assert span['local_date'] == '2026-09-12'
    assert span['duration_minutes'] == withheld
    assert span['parent_interval_id'] == item['interval_id']
    assert span['reasons'] == ['daily_quota_blocked_by_review']
    assert span['blocker_source_ids'] == ['a:range:0']
    assert report['context_ranges'][0]['raw_text'] == rows[0]['message']
    assert item['start_at'].endswith('23:00:00+03:30' if overnight else '13:00:00+03:30')
    compact = render_html(report).split('<h2>Daily summary</h2>')[1].split('<details>')[0]
    daily = next(row for row in re.findall(r'<tr>.*?</tr>', compact) if '2026-09-12' in row)
    assert 'daily_quota_blocked_by_review' in daily and '<td>None</td>' not in daily
    assert f'<td>{withheld}</td>' in daily and 'Withheld min' in compact
    if overnight:
        next_day = next(row for row in re.findall(r'<tr>.*?</tr>', compact) if '2026-09-13' in row)
        assert '<td>240</td>' in next_day
        assert 'daily_quota_blocked_by_review' not in next_day
    assert rows == before


@pytest.mark.parametrize('tamper', [False, True])
def test_artifact_verifier_accounts_for_withheld_duration(tmp_path, tamper):
    import json
    import subprocess
    import sys
    from pathlib import Path
    from attendance_sync.report import write_reports
    report = report_for([
        post('شنبه کار 0800-0900 نبود', '2026-09-12T10:00:00+03:30', 'a'),
        post('شنبه اضافه کاری 2300-0400', '2026-09-13T04:00:00+03:30', 'b'),
    ], start='2026-09-12T13:00:00+03:30')
    for stem in ('review', 'rolling-review'):
        write_reports(report, tmp_path / 'artifacts' / stem)
    if tamper:
        path = tmp_path / 'artifacts/review.json'
        report['withheld_spans'][0]['duration_minutes'] -= 1
        path.write_text(json.dumps(report))
    result = subprocess.run([sys.executable, str(Path('scripts/verify_artifacts.py').resolve())], cwd=tmp_path, capture_output=True, text=True)
    assert (result.returncode != 0) == tamper, result.stdout + result.stderr


def test_raw_context_retains_original_weekday_typography():
    from attendance_sync.parser import parse_post
    event = parse_post(post('دو\u200cشنبه ۱۴۰۵۰۶۲۳\nورود 0800', '2026-09-14T08:00:00+03:30'))[0][0]
    assert event['raw_weekday'] == 'دو\u200cشنبه'
    assert event['raw_context'] == ['دو\u200cشنبه ۱۴۰۵۰۶۲۳']
    assert event['date'] == '2026-09-14'


def test_context_allocation_evidence_and_review_reason_remain_visible():
    report = report_for([
        post('شنبه کار 0800-1200 نبود', '2026-09-12T12:00:00+03:30', 'a'),
        post('شنبه کار 1300-1900', '2026-09-12T19:00:00+03:30', 'b'),
    ], start='2026-09-12T13:00:00+03:30')
    assert report.get('context_intervals')
    assert report['context_ranges'][0]['range_id'] == 'a:range:0'
    compact = render_html(report).split('<h2>Daily summary</h2>')[1].split('<details>')[0]
    assert 'daily_quota_blocked_by_review' in compact


def test_compact_daily_summary_preserves_expandable_evidence():
    report = report_for([
        post('شنبه کار 0800-1900', '2026-09-12T19:00:00+03:30', 'a'),
        post('یکشنبه ورود 0800', '2026-09-13T08:00:00+03:30', 'b'),
    ])
    page = render_html(report)
    assert '<h2>Daily summary</h2>' in page
    compact = page.split('<h2>Daily summary</h2>')[1].split('<details>')[0]
    assert '2026-09-12' in compact and '1405/06/21' in compact
    assert '08:00–19:00' in compact
    assert '<td>540</td>' in compact and '<td>120</td>' in compact
    assert 'unmatched_entry' in compact
    assert '<summary>events' in page and '<summary>context_ranges' in page
    assert 'source_version' in page


@pytest.mark.parametrize('text', ['شنبه 14050621 کار 1800-1900', 'شنبه 14050621 ورود 1800 خروج 1900'])
def test_future_work_is_not_allocated_as_of_cutoff(text):
    report = report_for([post(text, '2026-09-12T08:00:00+03:30')], end='2026-09-12T09:00:00+03:30')
    assert report['segments'] == []
    assert report['intervals'][0]['status'] == 'review'
    assert 'future_work_after_cutoff' in report['intervals'][0]['reasons']
    assert report['intervals'][0]['start_at'] == '2026-09-12T18:00:00+03:30'


@pytest.mark.parametrize('text', ['شنبه 14050621 کار 1800-1900', 'شنبه 14050621 ورود 1800 خروج 1900'])
def test_implausibly_future_claim_is_review_even_after_cutoff_passes(text):
    report = report_for([post(text, '2026-09-12T08:00:00+03:30')])
    assert report['segments'] == []
    assert 'future_work_after_post' in report['intervals'][0]['reasons']


def test_claim_ahead_of_post_is_eligible_once_the_as_of_instant_has_passed():
    report = build_report(
        [post('شنبه 14050621 ورود 0830 خروج 1945', '2026-09-12T19:16:00+03:30')],
        'self', datetime.fromisoformat('2026-09-12T00:00:00+03:30'),
        datetime.fromisoformat('2026-09-13T00:00:00+03:30'),
        as_of=datetime.fromisoformat('2026-09-13T00:05:00+03:30'))
    interval = report['intervals'][0]
    assert interval['status'] == 'ready'
    assert 'future_work_after_post' not in interval['reasons']
    assert interval['warnings'] == ['claim_ahead_of_post']
    assert sum(row['duration_minutes'] for row in report['segments']) == 675


def test_claim_ahead_of_the_as_of_instant_stays_review():
    report = build_report(
        [post('شنبه 14050621 ورود 0830 خروج 1945', '2026-09-12T19:16:00+03:30')],
        'self', datetime.fromisoformat('2026-09-12T00:00:00+03:30'),
        datetime.fromisoformat('2026-09-13T00:00:00+03:30'),
        as_of=datetime.fromisoformat('2026-09-12T19:30:00+03:30'))
    assert report['segments'] == []
    assert 'future_work_after_post' in report['intervals'][0]['reasons']


@pytest.mark.parametrize('text,stamp', [
    ('شنبه کار 0800-1700', '2026-09-12T16:59:30+03:30'),
    ('شنبه کار 2300-0400', '2026-09-13T04:00:00+03:30'),
    ('شنبه ورود 2300\nخروج 0400', '2026-09-13T04:00:00+03:30'),
])
def test_post_time_validation_preserves_rounding_and_retrospective_overnight(text, stamp):
    report = report_for([post(text, stamp)])
    assert report['intervals'][0]['status'] == 'ready'
    assert report['segments']


def test_merged_context_range_keeps_resolvable_provenance():
    report = report_for([
        post('شنبه کار 0800-1200', '2026-09-12T12:00:00+03:30', 'a'),
        post('شنبه ورود 0800 خروج 1200', '2026-09-12T14:00:00+03:30', 'b'),
    ], start='2026-09-12T13:00:00+03:30')
    sources = {e['event_id'] for e in report['events'] + report['context_events']}
    sources.update(r['range_id'] for r in report['ranges'] + report.get('context_ranges', []))
    assert all(set(i['source_ids']) <= sources for i in report['intervals'])
    assert report['context_ranges'][0]['range_id'] == 'a:range:0'
    assert report['context_range_count'] == 1
    assert report['ranges'] == []


@pytest.mark.parametrize('middle', ['> خروج 1200', 'خروج برای ناهار 1200', '`خروج 1200`'])
def test_nonattendance_does_not_consume_open_entry(middle):
    report = report_for([
        post('شنبه ورود 0800', '2026-09-12T08:00:00+03:30', 'a'),
        post(middle, '2026-09-12T12:00:00+03:30', 'b'),
        post('خروج 1700', '2026-09-12T17:00:00+03:30', 'c'),
    ])
    exit_event = next(e for e in report['events'] if e['post_id'] == 'c')
    assert exit_event.get('paired_entry_id') == 'a:0'
    assert exit_event['status'] == 'ready'
    assert sum(s['duration_minutes'] for s in report['segments']) == 540


@pytest.mark.parametrize('excluded', ['> شنبه 14050621', '```\nشنبه 14050621\n```'])
def test_quoted_context_cannot_redate_active_attendance(excluded):
    raw = excluded + '\nدو شنبه ورود 0800 خروج 1700'
    report = report_for([post(raw, '2026-09-14T17:00:00+03:30')], end='2026-09-14T18:00:00+03:30')
    assert all(e['date'] == '2026-09-14' for e in report['events'])
    assert all(e['raw_date'] is None for e in report['events'])
    assert all(e['raw_weekday'] == 'دو شنبه' for e in report['events'])
    assert all(e['raw_context'] == ['دو شنبه ورود 0800 خروج 1700'] for e in report['events'])


def test_multiple_active_weekdays_require_review():
    report = report_for([post('شنبه\nدوشنبه ورود 0800 خروج 1700', '2026-09-14T17:00:00+03:30')], end='2026-09-14T18:00:00+03:30')
    assert report['segments'] == []
    assert all('multiple_weekdays' in e['reasons'] for e in report['events'])


@pytest.mark.parametrize('uncertain', [False, True])
def test_context_work_affects_visible_daily_quota(uncertain):
    report = report_for([
        post('شنبه کار 0800-1200' + (' نبود' if uncertain else ''), '2026-09-12T12:00:00+03:30', 'a'),
        post('شنبه کار 1300-1900', '2026-09-12T19:00:00+03:30', 'b'),
    ], start='2026-09-12T13:00:00+03:30')
    actual = [(s['category'], s['duration_minutes']) for s in report['segments']]
    assert actual == ([] if uncertain else [('regular_remote', 300), ('overtime_remote', 60)])
    assert len(report['intervals']) == 1


def test_ambiguous_newer_entries_block_older_pairing():
    report = report_for([
        post('شنبه ورود 0800', '2026-09-12T08:00:00+03:30', 'a'),
        post('شنبه ورود 0900', '2026-09-12T09:00:00+03:30', 'b'),
        post('شنبه ورود 1000', '2026-09-12T09:00:00+03:30', 'c'),
        post('خروج 1700', '2026-09-12T17:00:00+03:30', 'd'),
    ])
    exit_event = next(e for e in report['events'] if e['post_id'] == 'd')
    assert exit_event['status'] == 'review'
    assert not exit_event.get('paired_entry_id')
    assert 'ambiguous_prior_attendance' in exit_event['reasons']
    assert report['segments'] == []


def test_technical_login_prose_cannot_create_work():
    report = report_for([
        post('شنبه\nبررسی مشکل ورود کاربران در نسخه 1700', '2026-09-12T18:00:00+03:30', 'a'),
        post('خروج 1900', '2026-09-12T19:00:00+03:30', 'q'),
    ])
    assert report['segments'] == []
    assert not any(e.get('paired_entry_id') for e in report['events'])


@pytest.mark.parametrize('text', ['خروج چهارنشبه 2030', 'سه شنبه ورود 0800', 'ورود 0800 خروج 1700'])
def test_attendance_grammar_retains_weekday_and_clock_adjacency(text):
    from attendance_sync.parser import parse_post
    events, _ = parse_post(post(text))
    assert events and all(e['time'] for e in events)
