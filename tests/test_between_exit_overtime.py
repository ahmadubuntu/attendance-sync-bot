"""Pin the between-exit-and-next-entry rule the user confirmed.

Work recorded between an exit and the next day's entry is overtime: the user returns after
leaving and keeps working. The pipeline already marks such a span return_after_exit and
allocates it as overtime; these tests pin that behaviour so it cannot regress, including the
exact real shape from Wednesday 1405/06/25 (exit 18:45, evening work 19:30-22:00, next entry
Saturday 08:25).
"""
from datetime import datetime

from attendance_sync.report import build_report


def post(message, created, post_id):
    stamp = datetime.fromisoformat(created)
    return {'id': post_id, 'user_id': 'self', 'create_at': int(stamp.timestamp() * 1000),
            'edit_at': 0, 'message': message}


def test_work_between_an_exit_and_the_next_entry_is_overtime():
    rows = [post('چهارشنبه 14050625\nورود 0800', '2026-09-16T07:59:00+03:30', 'a'),
            post('خروج چهار شنبه 1845', '2026-09-16T18:50:00+03:30', 'b'),
            post('رفع مشکل سرویس\n1930-2200', '2026-09-16T22:05:00+03:30', 'c'),
            post('شنبه 14050628\nورود 0825', '2026-09-19T08:25:00+03:30', 'd')]
    report = build_report(rows, 'self', datetime.fromisoformat('2026-09-10T00:00:00+03:30'),
                          datetime.fromisoformat('2026-09-20T23:59:00+03:30'))
    evening = [item for item in report['intervals'] if item['origin'] == 'activity_range']
    assert evening, report['intervals']
    assert evening[0]['explicit_overtime'] is True
    assert 'return_after_exit' in evening[0]['reasons']
    segments = [s for s in report['segments'] if s['local_date'] == '2026-09-16'
                and s['start_at'][11:16] == '19:30']
    assert segments and segments[0]['category'] == 'overtime_remote'


def test_the_regular_day_itself_is_not_overtime():
    rows = [post('چهارشنبه 14050625\nورود 0800', '2026-09-16T07:59:00+03:30', 'a'),
            post('خروج چهار شنبه 1845', '2026-09-16T18:50:00+03:30', 'b')]
    report = build_report(rows, 'self', datetime.fromisoformat('2026-09-10T00:00:00+03:30'),
                          datetime.fromisoformat('2026-09-20T23:59:00+03:30'))
    day = [s for s in report['segments'] if s['local_date'] == '2026-09-16']
    assert any(s['category'] == 'regular_remote' for s in day)
    assert all(s['category'] != 'overtime_remote' or s['start_at'][11:16] >= '16:00' for s in day)
