"""Pair in posting order, without rewriting immutable source evidence."""
from copy import deepcopy
from datetime import date
from .parser import WEEKDAYS
from .normalize import normalize


def flag(item, reason):
    if reason not in item['reasons']:
        item['reasons'].append(reason)
    item['status'] = 'review'


def pair(events):
    unique = {}
    for event in events:
        previous = unique.get(event['event_id'])
        if previous is None or event['source_version'] >= previous['source_version']:
            unique[event['event_id']] = event
    result = deepcopy(list(unique.values()))
    result.sort(key=lambda e: (e['posted_at'], e['event_index']))
    eligible = [e for e in result if e.get('pairing_eligible', True)]
    ambiguous = {e['posted_at'] for e in eligible if len({x['post_id'] for x in eligible if x['posted_at'] == e['posted_at']}) > 1}
    opened = []
    for event in result:
        event['source_date'] = event['date']
        if not event.get('pairing_eligible', True):
            continue
        if event['posted_at'] in ambiguous:
            flag(event, 'ambiguous_post_order')
            if not opened or opened[-1] is not None:
                opened.append(None)
            continue
        if event['kind'] == 'in':
            opened.append(event)
            continue
        if not opened:
            event['date'] = None
            flag(event, 'unpaired_exit')
            continue
        if opened[-1] is None:
            event['date'] = None
            flag(event, 'ambiguous_prior_attendance')
            continue
        entry = opened.pop()
        event['paired_entry_id'] = entry['event_id']
        entry['paired_exit_id'] = event['event_id']
        event['date_basis'] = 'paired_entry'
        event['date'] = entry['date']
        event['reasons'] = [r for r in event['reasons'] if r not in ('post_date_assumed', 'unknown_weekday')]
        event['status'] = 'review' if event['reasons'] else 'ready'
        if event['raw_date'] and event['source_date'] != entry['date']:
            event['date'] = event['source_date']
            flag(event, 'explicit_exit_date_conflict')
        if entry['status'] == 'review':
            flag(event, 'paired_entry_review')
        if event['status'] == 'review':
            flag(entry, 'paired_exit_review')
        weekday = WEEKDAYS.get(normalize(event['raw_weekday'] or '').replace(' ', ''))
        if event['raw_weekday'] and (weekday is None or not entry['date'] or weekday != date.fromisoformat(entry['date']).weekday()):
            event['reasons'].append('exit_weekday_overridden_by_pairing')
    for entry in opened:
        if entry is not None:
            flag(entry, 'unmatched_entry')
    return result
