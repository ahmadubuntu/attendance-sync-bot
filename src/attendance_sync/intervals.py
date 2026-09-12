"""Resolved half-open intervals and derived midnight boundaries."""
from datetime import datetime, time, timedelta
from .parser import TEHRAN


def split_midnights(start, end):
    if start.utcoffset() is None or end.utcoffset() is None or end <= start:
        raise ValueError('Positive timezone-aware interval required')
    start, end = start.astimezone(TEHRAN), end.astimezone(TEHRAN)
    parts = []
    while start < end:
        boundary = datetime.combine(start.date()+timedelta(days=1), time.min, TEHRAN)
        stop = min(end, boundary)
        parts.append((start, stop))
        start = stop
    return parts


def resolve(ident, sources, day, start_clock, end_clock, origin, overtime, reasons, status):
    item = dict(interval_id=ident, source_ids=sources, start_at=None, end_at=None,
                origin=origin, explicit_overtime=overtime, status=status, reasons=list(reasons), end_day_offset=0,
                accounting_days=[day] if day else [])
    if day and start_clock and end_clock:
        start = datetime.fromisoformat(day+'T'+start_clock).replace(tzinfo=TEHRAN)
        end = datetime.fromisoformat(day+'T'+end_clock).replace(tzinfo=TEHRAN)
        if end < start:
            end += timedelta(days=1)
            item['end_day_offset'] = 1
            item['reasons'].append('overnight_rollover')
        item.update(start_at=start.isoformat(), end_at=end.isoformat())
        if end > start:
            item['accounting_days'] = [a.date().isoformat() for a, b in split_midnights(start, end)]
        if end == start:
            item['status'] = 'review'
            item['reasons'].append('ambiguous_duration')
    else:
        item['status'] = 'review'
        item['reasons'].append('unresolved_interval')
    return item


def build_intervals(events, ranges):
    by_id = {e['event_id']: e for e in events}
    items = []
    closed_days = set()
    for exit_event in events:
        if 'paired_entry_id' not in exit_event:
            continue
        entry = by_id[exit_event['paired_entry_id']]
        overtime = entry['date'] in closed_days or entry.get('explicit_overtime',False) or exit_event.get('explicit_overtime',False)
        reasons = list(dict.fromkeys(entry['reasons'] + exit_event['reasons']))
        if overtime:
            reasons.append('return_after_exit')
        item = resolve('pair:'+entry['event_id'], [entry['event_id'], exit_event['event_id']],
                       entry['date'], entry['time'], exit_event['time'], 'paired_events', overtime,
                       reasons, 'review' if 'review' in (entry['status'], exit_event['status']) else 'ready')
        items.append(item)
        if item['end_at']:
            closed_days.add(entry['date'])
    items += [resolve(r['range_id'], [r['range_id']], r['date'], r['start_time'], r['end_time'],
                    'activity_range', r['explicit_overtime'], r['reasons'], r['status']) for r in ranges]
    items += [resolve('unmatched:'+e['event_id'], [e['event_id']], e['date'] or e.get('source_date'),
                      None, None, 'unmatched_event', e['explicit_overtime'], e['reasons'], 'review')
              for e in events if e.get('pairing_eligible', True)
              and 'paired_entry_id' not in e and 'paired_exit_id' not in e]
    source_by_id = by_id | {r['range_id']: r for r in ranges}
    for item in items:
        if item['status'] == 'review':
            days = set(item['accounting_days'])
            for source_id in item['source_ids']:
                source = source_by_id[source_id]
                if source['status'] == 'review':
                    candidates = source.get('candidate_dates', [])
                    days.update(candidates)
                    if item['end_day_offset']:
                        days.update((datetime.fromisoformat(day)+timedelta(days=1)).date().isoformat() for day in candidates)
            item['accounting_days'] = sorted(days)
    unique = []
    for item in items:
        exact = next((p for p in unique if item['start_at'] and (p['start_at'], p['end_at']) == (item['start_at'], item['end_at'])), None)
        if exact:
            exact['source_ids'].extend(item['source_ids'])
            exact['accounting_days'] = sorted(set(exact['accounting_days'] + item['accounting_days']))
            exact['explicit_overtime'] |= item['explicit_overtime']
            exact['reasons'] = list(dict.fromkeys(exact['reasons'] + item['reasons'] + ['duplicate_representation']))
            if item['status'] == 'review':
                exact['status'] = 'review'
        else:
            unique.append(item)
    for i, item in enumerate(unique):
        for other in unique[i+1:]:
            if item['start_at'] and other['start_at'] and item['start_at'] < other['end_at'] and other['start_at'] < item['end_at']:
                for overlap in (item, other):
                    overlap['status'] = 'review'
                    if 'overlapping_intervals' not in overlap['reasons']:
                        overlap['reasons'].append('overlapping_intervals')
    return unique
