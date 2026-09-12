from collections import defaultdict
from datetime import datetime, timedelta
from .intervals import split_midnights

POLICY_VERSION = 'sat-tue-540-wed-480-thu-fri-0-v1'


def quota_minutes(day):
    return {0:540, 1:540, 2:480, 3:0, 4:0, 5:540, 6:540}[day.weekday()]


def allocation_blockers(intervals):
    return [dict(local_date=day, interval_id=item['interval_id'],
                 source_ids=list(item['source_ids']), reasons=list(item['reasons']))
            for item in intervals if item['status'] == 'review'
            for day in item['accounting_days']]


def allocate(intervals, withheld_spans=None):
    used = defaultdict(int)
    segments = []
    withheld_spans = [] if withheld_spans is None else withheld_spans
    blockers = allocation_blockers(intervals)
    blocked_days = {b['local_date'] for b in blockers}
    for interval in sorted(intervals, key=lambda i: i['start_at'] or ''):
        interval.update(expected_minutes=None, allocated_minutes=0, withheld_minutes=None)
        if not interval['start_at'] or not interval['end_at'] or interval['end_at'] <= interval['start_at']:
            continue
        interval.update(expected_minutes=0, withheld_minutes=0)
        for start, end in split_midnights(datetime.fromisoformat(interval['start_at']), datetime.fromisoformat(interval['end_at'])):
            day = start.date()
            duration = int((end-start).total_seconds())//60
            interval['expected_minutes'] += duration
            if interval['status'] == 'review' or day.isoformat() in blocked_days:
                reasons = list(interval['reasons']) if interval['status'] == 'review' else ['daily_quota_blocked_by_review']
                interval['withheld_minutes'] += duration
                interval['allocation_reasons'] = list(dict.fromkeys(interval.get('allocation_reasons', []) + reasons))
                withheld_spans.append(dict(parent_interval_id=interval['interval_id'], local_date=day.isoformat(),
                    start_at=start.isoformat(), end_at=end.isoformat(), duration_minutes=duration,
                    reasons=reasons, blocker_source_ids=sorted({source for b in blockers
                        if b['local_date'] == day.isoformat() for source in b['source_ids']})))
                continue
            interval['allocated_minutes'] += duration
            regular = 0 if interval['explicit_overtime'] else min(duration, max(0, quota_minutes(day)-used[day]))
            used[day] += regular
            for category, minutes in [('regular_remote',regular),('overtime_remote',duration-regular)]:
                if not minutes:
                    continue
                stop = start+timedelta(minutes=minutes)
                segments.append(dict(segment_id=f"{interval['interval_id']}:segment:{len(segments)}",
                    parent_interval_id=interval['interval_id'], local_date=day.isoformat(),
                    start_at=start.isoformat(), end_at=stop.isoformat(), duration_minutes=minutes,
                    category=category, classification_basis='explicit_overtime' if interval['explicit_overtime'] else 'daily_quota',
                    policy_version=POLICY_VERSION))
                start = stop
    return segments
