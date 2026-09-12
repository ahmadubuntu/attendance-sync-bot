"""Explain date discrepancies using chronological evidence, never silently fix them."""
from datetime import datetime, timedelta, date
from .parser import TEHRAN, WEEKDAYS
from .normalize import normalize


def annotate_date_context(events, ranges):
    entries = sorted((e for e in events if e['kind'] == 'in' and e.get('pairing_eligible', True)),
                     key=lambda e: e['posted_at'])
    for item in events + ranges:
        earlier = [e for e in entries if e['posted_at'] < item['posted_at'] and e['post_id'] != item['post_id']]
        later = [e for e in entries if e['posted_at'] > item['posted_at'] and e['post_id'] != item['post_id']]
        previous, following = (earlier[-1] if earlier else None), (later[0] if later else None)
        def evidence(entry):
            return {k: entry[k] for k in ('event_id', 'date', 'posted_at', 'status')} if entry else None
        posted = datetime.fromisoformat(item['posted_at']).astimezone(TEHRAN)
        context = dict(previous_entry=evidence(previous), next_entry=evidence(following),
                       posted_local_date=posted.date().isoformat(), posted_at=item['posted_at'],
                       raw_date=item['raw_date'], raw_weekday=item['raw_weekday'],
                       kasra_check='not_performed', comparison_status='pending_kasra_comparison')
        conflict = 'weekday_date_conflict' in item['reasons'] or 'correction' in item or 'stale_date_correction' in item['reasons']
        backwards = (item.get('kind') == 'in' and item['raw_date'] and previous and previous['status'] == 'ready'
                     and item['date'] and previous['date'] and item['date'] < previous['date'])
        if backwards:
            conflict = True
            item['reasons'].append('date_chronology_conflict')
            item['status'] = 'review'
        if conflict:
            wd = WEEKDAYS.get(normalize(item['raw_weekday'] or '').replace(' ', ''))
            suggestion = posted.date() - timedelta(days=(posted.weekday()-wd)%7) if wd is not None else None
            context['suggested_date'] = suggestion.isoformat() if suggestion else None
            context['neighbor_support'] = bool(suggestion and (not previous or not previous['date'] or date.fromisoformat(previous['date']) <= suggestion)
                                              and (not following or not following['date'] or suggestion <= date.fromisoformat(following['date'])))
            context['resolution'] = 'user_confirmed' if 'correction' in item else 'review_required'
            item['date_discrepancy'] = context
        if item.get('date_basis', '').startswith('posted_clock'):
            item['date_evidence'] = context
