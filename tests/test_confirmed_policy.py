"""Synthetic source-bound corrections and confirmed work policy regressions."""
from datetime import datetime
import hashlib
import json
from copy import deepcopy
from test_parser import post
from attendance_sync.report import build_report


def report(rows, **kwargs):
    return build_report(rows, 'self', datetime.fromisoformat('2026-09-01T00:00:00+03:30'),
                        datetime.fromisoformat('2026-09-20T23:59:00+03:30'), **kwargs)


def signature(row):
    fields = {k: row.get(k, 0) for k in ('id', 'create_at', 'edit_at', 'message')}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def override(row):
    return {'schema_version': 1, 'date_corrections': [dict(post_id=row['id'], source_fingerprint=signature(row),
            raw_date='14050622', target_date='2026-09-14', confirmed_by='user')]}


def test_confirmed_correction_precedes_pairing_preserves_raw_evidence():
    rows = [post('دوشنبه 14050622\nورود 0810', '2026-09-14T08:10:00+03:30', 'synthetic-in'),
            post('خروج 1710', '2026-09-14T17:10:00+03:30', 'synthetic-out')]
    before = deepcopy(rows)
    result = report(rows, corrections=override(rows[0]))
    entry, exit_event = result['events']
    assert entry['raw_date'] == '14050622'
    assert entry['date'] == exit_event['date'] == '2026-09-14'
    assert entry['correction']['confirmed_by'] == 'user'
    assert entry['source_fingerprint'] == signature(rows[0])
    assert result['intervals'][0]['status'] == 'ready'
    assert result['intervals'][0]['accounting_days'] == ['2026-09-14']
    assert result['kasra_check'] == 'not_performed'
    assert rows == before


def test_stale_override_remains_review_even_when_date_is_no_longer_conflicting():
    row = post('دوشنبه 14050622\nورود 0810 خروج 1710', '2026-09-14T17:10:00+03:30', 'synthetic')
    config = override(row)
    row['message'] = row['message'].replace('14050622', '14050623')
    result = report([row], corrections=config)
    assert result['segments'] == []
    assert 'stale_date_correction' in result['events'][0]['reasons']


def test_invalid_or_unconfirmed_correction_is_rejected():
    import pytest
    row = post('دوشنبه 14050622\nورود 0810', '2026-09-14T08:10:00+03:30')
    config = override(row)
    config['date_corrections'][0]['confirmed_by'] = 'software'
    with pytest.raises(ValueError):
        report([row], corrections=config)


def test_whole_post_work_context_and_completed_clock_anchor_range():
    row = post('رفع مشکل سرویس\n22:10-23:20', '2026-09-05T23:25:00+03:30')
    result = report([row])
    activity = result['ranges'][0]
    assert activity['status'] == 'ready'
    assert activity['date_basis'] == 'posted_clock_inferred'
    assert activity['date'] == '2026-09-05'
    assert 'inferred_date_from_completed_range' in activity['warnings']
    assert activity['work_context'] == ['رفع مشکل سرویس']
    assert sum(s['duration_minutes'] for s in result['segments']) == 70


def test_unanchored_overnight_completed_morning_uses_previous_start_day():
    result = report([post('اضافه کاری 2300-0400', '2026-09-13T04:05:00+03:30')])
    activity = result['ranges'][0]
    assert activity['date'] == '2026-09-12'
    assert activity['date_basis'] == 'posted_clock_overnight_inferred'
    assert activity['status'] == 'ready'
    assert [s['duration_minutes'] for s in result['segments']] == [60, 240]
    assert all(s['category'] == 'overtime_remote' for s in result['segments'])


def test_night_range_after_closed_attendance_is_overtime_despite_unused_quota():
    result = report([post('شنبه ورود 0900 خروج 1200', '2026-09-05T12:00:00+03:30', 'day'),
                     post('رفع مشکل\n22:10-23:20', '2026-09-05T23:25:00+03:30', 'night')])
    night = next(i for i in result['intervals'] if i['origin'] == 'activity_range')
    assert night['explicit_overtime'] is True
    assert 'return_after_exit' in night['reasons']
    assert [(s['category'], s['duration_minutes']) for s in result['segments'] if s['parent_interval_id'] == night['interval_id']] == [('overtime_remote', 70)]


def test_current_open_entry_is_information_not_review_or_quota_blocker():
    result = report([post('شنبه ورود 0810', '2026-09-12T08:10:00+03:30')],
                    as_of=datetime.fromisoformat('2026-09-12T10:00:00+03:30'))
    event = result['events'][0]
    assert event['status'] == 'open_day'
    assert event['submission_eligible'] is False
    assert event['reasons'] == []
    assert result['intervals'][0]['status'] == 'open_day'
    assert result['allocation_blockers'] == []
    assert result['review_count'] == 0 and result['open_day_count'] == 1
    assert result['segments'] == []


def test_current_closed_work_is_deferred_and_overnight_previous_day_stays_allocated():
    result = report([post('جمعه اضافه کاری 2300-0400', '2026-09-12T04:00:00+03:30', 'night'),
                     post('شنبه کار 0800-0900', '2026-09-12T09:00:00+03:30', 'day')],
                    as_of=datetime.fromisoformat('2026-09-12T10:00:00+03:30'))
    assert [(s['local_date'], s['duration_minutes']) for s in result['segments']] == [('2026-09-11', 60)]
    assert [s['duration_minutes'] for s in result['deferred_spans']] == [240, 60]
    assert all(s['submission_eligible'] is False for s in result['deferred_spans'])
    assert result['withheld_spans'] == [] and result['review_count'] == 0
    for item in result['intervals']:
        assert item['expected_minutes'] == item['allocated_minutes'] + item['withheld_minutes'] + item['deferred_minutes']
    assert result['intervals'][1]['submission_eligible'] is False


def test_date_discrepancy_includes_neighbors_posting_clock_and_pending_kasra():
    result = report([
        post('یکشنبه 14050622 ورود 0800 خروج 1700', '2026-09-13T17:00:00+03:30', 'before'),
        post('دوشنبه 14050622 ورود 0800 خروج 1700', '2026-09-14T17:00:00+03:30', 'conflict'),
        post('سه شنبه 14050624 ورود 0800 خروج 1700', '2026-09-15T17:00:00+03:30', 'after')])
    conflict = next(e for e in result['events'] if e['post_id'] == 'conflict')
    assert conflict['date'] == '2026-09-13' and conflict['status'] == 'review'
    evidence = conflict['date_discrepancy']
    assert evidence['suggested_date'] == '2026-09-14'
    assert evidence['previous_entry']['date'] == '2026-09-13'
    assert evidence['next_entry']['date'] == '2026-09-15'
    assert evidence['posted_local_date'] == '2026-09-14'
    assert evidence['kasra_check'] == 'not_performed'
    assert evidence['comparison_status'] == 'pending_kasra_comparison'
    assert result['date_discrepancy_count'] == len(result['date_discrepancies'])
