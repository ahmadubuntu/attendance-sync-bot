"""Private local evidence reports; unrelated messages are never copied."""
from datetime import datetime, date, timezone, timedelta
from html import escape
import json
import os
from pathlib import Path
import tempfile
import jdatetime
from .parser import parse_post, TEHRAN
from .pairing import pair
from .intervals import build_intervals, split_midnights
from .allocation import allocate, allocation_blockers
from .corrections import apply_corrections, validate_corrections
from .date_context import annotate_date_context


def build_report(posts, own_id, start, end, *, corrections=None, as_of=None):
    validate_corrections(corrections)
    if as_of is not None and as_of.utcoffset() is None:
        raise ValueError('As-of must be timezone-aware')
    current_day = as_of.astimezone(TEHRAN).date().isoformat() if as_of else None
    window_posts = [p for p in posts if start <= datetime.fromtimestamp(p['create_at']/1000, timezone.utc) <= end]
    window_ids = {p['id'] for p in window_posts}
    events, ranges, ignored, labels = [], [], [], []
    for post in posts:
        reason = 'other_author' if post['user_id'] != own_id else 'deleted_post' if post.get('delete_at') else 'system_post' if post.get('type') else None
        parsed_events, parsed_ranges = ([],[]) if reason else parse_post(post)
        apply_corrections(parsed_events + parsed_ranges, post, corrections)
        events.extend(parsed_events)
        ranges.extend(parsed_ranges)
        if post['id'] in window_ids:
            label = 'attendance' if parsed_events else 'activity_range' if parsed_ranges else reason or 'no_attendance_or_work_range'
            if not parsed_events and not parsed_ranges:
                ignored.append({'post_id':post['id'], 'reason':label})
            if post['user_id'] == own_id:
                labels.append({'post_id':post['id'], 'source_version':post.get('edit_at',0), 'proposed_label':label, 'confirmed_label':None})
    paired = pair(events)
    annotate_date_context(paired, ranges)
    for event in paired:
        event['submission_eligible'] = False
        if (event['kind'] == 'in' and event['date'] == current_day
                and event['reasons'] == ['unmatched_entry']):
            event.update(status='open_day', reasons=[], information=['current_day_unfinished'])
    intervals = build_intervals(paired, ranges)
    source_by_id = {e['event_id']: e for e in paired} | {r['range_id']: r for r in ranges}
    for interval in intervals:
        if interval['origin'] == 'unmatched_event' and source_by_id[interval['source_ids'][0]]['status'] == 'open_day':
            interval.update(status='open_day', reasons=[], information=['current_day_unfinished'], submission_eligible=False)
        for source_id in interval['source_ids']:
            source = source_by_id[source_id]
            keys = ('start_at', 'end_at') if 'range_id' in source else ('start_at',) if source['kind'] == 'in' else ('end_at',)
            latest = datetime.fromisoformat(source['posted_at']) + timedelta(minutes=1)
            if any(interval[key] and datetime.fromisoformat(interval[key]) > latest for key in keys):
                interval['status'] = 'review'
                if 'future_work_after_post' not in interval['reasons']:
                    interval['reasons'].append('future_work_after_post')
        if any(interval[key] and datetime.fromisoformat(interval[key]) > end for key in ('start_at', 'end_at')):
            interval['status'] = 'review'
            interval['reasons'].append('future_work_after_cutoff')
    all_withheld = []
    all_deferred = []
    all_segments = allocate(intervals, all_withheld, current_day=current_day, deferred_spans=all_deferred)
    relevant_ids = {e['event_id'] for e in paired if e['post_id'] in window_ids} | {r['range_id'] for r in ranges if r['post_id'] in window_ids}
    all_intervals = intervals
    intervals = [i for i in all_intervals if relevant_ids.intersection(i['source_ids'])]
    selected_days = {day for i in intervals for day in i['accounting_days']}
    context_intervals = [i for i in all_intervals if i not in intervals and selected_days.intersection(i['accounting_days'])]
    blockers = [b for b in allocation_blockers(all_intervals) if b['local_date'] in selected_days]
    for interval in intervals:
        interval['crosses_source_window'] = any(source not in relevant_ids for source in interval['source_ids'])
    visible = [e for e in paired if e['post_id'] in window_ids]
    needed = {source for i in intervals + context_intervals for source in i['source_ids']}
    context = [e for e in paired if e['post_id'] not in window_ids and e['event_id'] in needed]
    context_ranges = [r for r in ranges if r['post_id'] not in window_ids and r['range_id'] in needed]
    ranges = [r for r in ranges if r['post_id'] in window_ids]
    selected_intervals = {i['interval_id'] for i in intervals}
    segments = [s for s in all_segments if s['parent_interval_id'] in selected_intervals]
    withheld = [s for s in all_withheld if s['parent_interval_id'] in selected_intervals]
    context_ids = {i['interval_id'] for i in context_intervals}
    context_withheld = [s for s in all_withheld if s['parent_interval_id'] in context_ids]
    deferred = [s for s in all_deferred if s['parent_interval_id'] in selected_intervals]
    context_deferred = [s for s in all_deferred if s['parent_interval_id'] in context_ids]
    collected = {}
    for item in visible + context + ranges + context_ranges:
        evidence = item.get('date_discrepancy')
        if evidence:
            collected.setdefault((evidence['posted_at'], evidence['raw_date']), evidence)
    date_discrepancies = list(collected.values())
    for item in visible + context + ranges + context_ranges + segments:
        day = item.get('date') or item.get('local_date')
        item['jalali_date'] = jdatetime.date.fromgregorian(date=date.fromisoformat(day)).strftime('%Y/%m/%d') if day else None
    own = sum(p['user_id'] == own_id for p in window_posts)
    report = dict(schema_version=2, kasra_check='not_performed', as_of=as_of.isoformat() if as_of else None,
        current_day=current_day, current_day_policy='defer_current_day' if as_of else 'historical_no_current_day',
        corrections_applied=sum(1 for item in paired + ranges if 'correction' in item),
        window={'from':start.isoformat(),'to':end.isoformat(),'timezone':'Asia/Tehran'},
        coverage_complete=True, fetched_count=len(window_posts), context_fetched_count=len(posts)-len(window_posts),
        own_count=own, other_count=len(window_posts)-own, attendance_message_count=len({e['post_id'] for e in visible}),
        candidate_count=len(visible), ready_count=sum(e['status']=='ready' for e in visible),
        open_day_count=sum(e['status']=='open_day' for e in visible),
        review_count=sum(e['status']=='review' for e in visible), range_count=len(ranges),
        interval_count=len(intervals), segment_count=len(segments), events=visible, context_events=context,
        context_intervals=context_intervals, context_interval_count=len(context_intervals), context_event_count=len(context),
        ranges=ranges, context_ranges=context_ranges, context_range_count=len(context_ranges), intervals=intervals, segments=segments,
        allocation_blockers=blockers, allocation_blocker_count=len(blockers),
        withheld_spans=withheld, withheld_span_count=len(withheld),
        deferred_spans=deferred, deferred_span_count=len(deferred),
        context_deferred_spans=context_deferred, context_deferred_span_count=len(context_deferred),
        date_discrepancies=date_discrepancies, date_discrepancy_count=len(date_discrepancies),
        context_withheld_spans=context_withheld, context_withheld_span_count=len(context_withheld), ignored=ignored, labels=labels)
    assert report['ready_count']+report['review_count']+report['open_day_count'] == report['candidate_count']
    return report


def render_html(report):
    content = ['<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">',
        '<title>Attendance preview</title><style>body{font:14px system-ui;margin:2rem}table{border-collapse:collapse;display:block;overflow:auto}td,th{border:1px solid #aaa;padding:.4rem;white-space:pre-wrap}th{background:#eee}</style>',
        '<h1>Attendance preview — read only</h1><p>Ready is not approval. Review intervals are not allocated. Inferred dates and context are identified explicitly. Gaps are not work. No Kasra access.</p>']
    days = {}
    for item in report['intervals']:
        if item['start_at'] and item['end_at'] and item['end_at'] > item['start_at']:
            for a, b in split_midnights(datetime.fromisoformat(item['start_at']), datetime.fromisoformat(item['end_at'])):
                day = a.date().isoformat()
                row = days.setdefault(day, {'times': [], 'regular': 0, 'overtime': 0, 'reasons': set()})
                stop = '24:00' if b.date() != a.date() else b.strftime('%H:%M')
                row['times'].append(a.strftime('%H:%M') + '–' + stop)
                if item['status'] == 'review':
                    row['reasons'].update(item['reasons'])
    for item in report['events'] + report['ranges']:
        day = item.get('date') or 'Unresolved date'
        row = days.setdefault(day, {'times': [], 'regular': 0, 'overtime': 0, 'reasons': set()})
        if item['status'] == 'review':
            row['reasons'].update(item['reasons'])
    for segment in report['segments']:
        row = days.setdefault(segment['local_date'], {'times': [], 'regular': 0, 'overtime': 0, 'reasons': set()})
        row['regular' if segment['category'] == 'regular_remote' else 'overtime'] += segment['duration_minutes']
    for span in report['withheld_spans']:
        row = days.setdefault(span['local_date'], {'times': [], 'regular': 0, 'overtime': 0, 'reasons': set()})
        row['withheld'] = row.get('withheld', 0) + span['duration_minutes']
        row['reasons'].update(span['reasons'])
    for span in report['deferred_spans']:
        row = days.setdefault(span['local_date'], {'times': [], 'regular': 0, 'overtime': 0, 'reasons': set()})
        row['deferred'] = row.get('deferred', 0) + span['duration_minutes']
        row['reasons'].update(span['reasons'])
    for blocker in report['allocation_blockers']:
        row = days.setdefault(blocker['local_date'], {'times': [], 'regular': 0, 'overtime': 0, 'reasons': set()})
        row['reasons'].update(blocker['reasons'])
    note = 'Minutes below are allocated selected work only; withheld and deferred minutes are separate. Separate spans preserve gaps.'
    if report['current_day']:
        note += (' The current day ' + str(report['current_day']) + ' is not submitted on the current day; its minutes are deferred,'
                 ' because the day is still open. No registration is proposed for it.')
    content.append('<h2>Daily summary</h2><p>' + escape(note) + '</p><table><tr><th>Date / Jalali</th><th>Work spans</th><th>Regular min</th><th>Overtime min</th><th>Total min</th><th>Withheld min</th><th>Deferred min</th><th>Review reasons</th></tr>')
    for day, row in sorted(days.items()):
        jalali = jdatetime.date.fromgregorian(date=date.fromisoformat(day)).strftime('%Y/%m/%d') if day != 'Unresolved date' else ''
        values = [day + ' / ' + jalali, ', '.join(row['times']) or 'Unresolved', row['regular'], row['overtime'], row['regular'] + row['overtime'], row.get('withheld', 0), row.get('deferred', 0), ', '.join(sorted(row['reasons'])) or 'None']
        content.append('<tr>' + ''.join('<td>' + escape(str(value)) + '</td>' for value in values) + '</tr>')
    regular = sum(row['regular'] for row in days.values())
    overtime = sum(row['overtime'] for row in days.values())
    withheld = sum(row.get('withheld', 0) for row in days.values())
    deferred = sum(row.get('deferred', 0) for row in days.values())
    content.append(f'<tr><th>Total</th><td></td><td>{regular}</td><td>{overtime}</td><td>{regular + overtime}</td><td>{withheld}</td><td>{deferred}</td><td></td></tr></table>')
    summary = {k:v for k,v in report.items() if not isinstance(v,list)}
    content.append('<details><summary>Report metadata</summary><pre>'+escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre></details>')
    for section in ('date_discrepancies','events','context_events','ranges','context_ranges','intervals','context_intervals','segments','allocation_blockers','withheld_spans','context_withheld_spans','deferred_spans','context_deferred_spans','ignored','labels'):
        rows = report[section]
        content.append('<details><summary>'+escape(section)+f' ({len(rows)})</summary>')
        if not rows:
            content.append('<p>None</p></details>')
            continue
        columns = list(dict.fromkeys(key for row in rows for key in row))
        content.append('<table><tr>'+''.join('<th>'+escape(c)+'</th>' for c in columns)+'</tr>')
        for row in sorted(rows,key=lambda r: r.get('status')!='review'):
            content.append('<tr>'+''.join('<td dir="auto">'+escape(str(row.get(c,'')))+'</td>' for c in columns)+'</tr>')
        content.append('</table></details>')
    return ''.join(content)+'</html>'


def write_reports(report, output):
    output = Path(output)
    parent = output.parent
    if parent == Path('.') or parent.is_symlink():
        raise ValueError('Use a dedicated private output directory')
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent.chmod(0o700)
    paths = [Path(str(output)+'.json'), Path(str(output)+'.html'), Path(str(output)+'-labels.json')]
    texts = [json.dumps(report, ensure_ascii=False, indent=2), render_html(report),
             json.dumps({'status':'unconfirmed_system_proposals','labels':report['labels']},ensure_ascii=False,indent=2)]
    for path, text in zip(paths,texts):
        fd, temporary = tempfile.mkstemp(prefix='.preview-', dir=parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                os.fchmod(stream.fileno(),0o600)
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary,path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return paths
