"""Verify local artifact invariants without printing private source text."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import stat

for stem in ('review', 'rolling-review'):
    root = Path('artifacts')
    source = root/(stem+'.json')
    if not source.exists():
        raise SystemExit('Missing artifact '+str(source)+': run the preview command for both windows first')
    report = json.loads(source.read_text())
    assert report['coverage_complete'] is True
    assert report['own_count']+report['other_count'] == report['fetched_count']
    assert report['candidate_count'] == len(report['events'])
    assert report['ready_count']+report['review_count']+report['open_day_count'] == report['candidate_count']
    plurals = {'date_discrepancy': 'date_discrepancies'}
    for key in ('range','interval','segment','context_range','context_event','context_interval','allocation_blocker',
                'withheld_span','context_withheld_span','deferred_span','context_deferred_span','date_discrepancy'):
        assert report[key+'_count'] == len(report[plurals.get(key, key+'s')])
    labels = json.loads((root/(stem+'-labels.json')).read_text())['labels']
    assert len(labels) == report['own_count']
    assert len({l['post_id'] for l in labels}) == len(labels)
    assert all(l['confirmed_label'] is None for l in labels)
    evidence = report['events'] + report['context_events'] + report['ranges'] + report['context_ranges']
    source_ids = {row.get('event_id') or row['range_id'] for row in evidence}
    assert len(source_ids) == len(evidence)
    all_intervals = report['intervals'] + report['context_intervals']
    assert all(set(i['source_ids']) <= source_ids for i in all_intervals)
    interval_by_id = {i['interval_id']: i for i in all_intervals}
    for blocker in report['allocation_blockers']:
        parent = interval_by_id[blocker['interval_id']]
        assert parent['status'] == 'review' and blocker['reasons']
        assert blocker['local_date'] in parent['accounting_days']
        assert set(blocker['source_ids']) <= source_ids
    for span in report['withheld_spans'] + report['context_withheld_spans']:
        assert span['parent_interval_id'] in interval_by_id
        a, b = map(datetime.fromisoformat, (span['start_at'], span['end_at']))
        assert int((b-a).total_seconds())//60 == span['duration_minutes'] > 0
        assert span['local_date'] == a.date().isoformat()
        assert span['reasons'] and set(span['blocker_source_ids']) <= source_ids
    for span in report['deferred_spans'] + report['context_deferred_spans']:
        assert span['parent_interval_id'] in interval_by_id
        a, b = map(datetime.fromisoformat, (span['start_at'], span['end_at']))
        assert int((b-a).total_seconds())//60 == span['duration_minutes'] > 0
        assert span['local_date'] == a.date().isoformat() == report['current_day']
        assert span['submission_eligible'] is False and span['reasons'] == ['current_day_not_finished']
    for interval in report['intervals']:
        parts = [s for s in report['segments'] if s['parent_interval_id'] == interval['interval_id']]
        withheld = [s for s in report['withheld_spans'] if s['parent_interval_id'] == interval['interval_id']]
        deferred = [s for s in report['deferred_spans'] if s['parent_interval_id'] == interval['interval_id']]
        assert interval['allocated_minutes'] == sum(s['duration_minutes'] for s in parts)
        if interval['expected_minutes'] is None:
            assert not parts and not withheld and not deferred and interval['withheld_minutes'] is None
            continue
        a, b = map(datetime.fromisoformat, (interval['start_at'], interval['end_at']))
        assert interval['expected_minutes'] == int((b-a).total_seconds())//60
        assert interval['withheld_minutes'] == sum(s['duration_minutes'] for s in withheld)
        assert interval['deferred_minutes'] == sum(s['duration_minutes'] for s in deferred)
        assert interval['expected_minutes'] == interval['allocated_minutes'] + interval['withheld_minutes'] + interval['deferred_minutes']
        cursor = a
        for part in sorted(parts + withheld + deferred, key=lambda p: p['start_at']):
            assert datetime.fromisoformat(part['start_at']) == cursor
            cursor = datetime.fromisoformat(part['end_at'])
        assert cursor == b
    event_ids = {e['event_id'] for e in report['events'] + report['context_events']}
    assert all(e[key] in event_ids for e in evidence for key in ('paired_entry_id', 'paired_exit_id') if key in e)
    assert report['attendance_message_count'] == len({e['post_id'] for e in report['events']})
    assert report['ready_count'] == sum(e['status'] == 'ready' for e in report['events'])
    ids = {i['interval_id'] for i in report['intervals']}
    assert len(ids) == len(report['intervals'])
    cutoff = datetime.fromisoformat(report['window']['to'])
    assert all(datetime.fromisoformat(s['end_at']) <= cutoff for s in report['segments'])
    page = (root/(stem+'.html')).read_text()
    assert '<h2>Daily summary</h2>' in page and '<summary>context_ranges' in page
    assert 'default-src &#x27;none&#x27;' in page or "default-src 'none'" in page
    assert all(s['parent_interval_id'] in ids for s in report['segments'])
    for segment in report['segments']:
        a,b = map(datetime.fromisoformat,(segment['start_at'],segment['end_at']))
        assert int((b-a).total_seconds())//60 == segment['duration_minutes']
    for suffix in ('.json','.html','-labels.json'):
        assert stat.S_IMODE((root/(stem+suffix)).stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    print(stem, 'verified', {k:v for k,v in report.items() if k.endswith('_count')})
    print('Review reasons:',dict(Counter(reason for row in report['events']+report['ranges']+report['intervals'] for reason in row['reasons'] if row['status']=='review')))
