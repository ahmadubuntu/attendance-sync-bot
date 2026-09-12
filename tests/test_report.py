from datetime import datetime, timezone
import json
import stat
from test_parser import post


def test_report_pipeline_context_privacy_counts_and_permissions(tmp_path):
    from attendance_sync.report import build_report, write_reports
    rows = [post('شنبه ورود 0800','2026-09-05T05:00:00+00:00','context'),
            post('خروج شنبه 1700','2026-09-05T14:00:00+00:00','exit'),
            post('unrelated private text','2026-09-05T15:00:00+00:00','negative'),
            post('شنبه کار 1800-1900 <script>alert(1)</script>','2026-09-05T16:00:00+00:00','range')]
    rows.append(dict(post('خروج 2000','2026-09-05T17:00:00+00:00','other'),user_id='other'))
    start,end = datetime(2026,9,5,10,tzinfo=timezone.utc),datetime(2026,9,6,tzinfo=timezone.utc)
    report = build_report(rows,'self',start,end)
    assert report['fetched_count'] == 4 and report['own_count'] == 3 and report['other_count'] == 1
    assert report['candidate_count'] == 1 and report['range_count'] == 1
    assert report['events'][0]['paired_entry_id'] == 'context:0'
    assert report['interval_count'] == 2
    assert 'unrelated private text' not in json.dumps(report)
    paths = write_reports(report,tmp_path/'private'/'review')
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(paths[0].parent.stat().st_mode) == 0o700
    page = paths[1].read_text()
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert '1405/' in page and '2026-09-05' in page
    assert len(json.loads(paths[2].read_text())['labels']) == 3
