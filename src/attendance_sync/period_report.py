"""The permanent period report: one range in, a calendar and a summary out.

The user asked for a script they can run whenever they like — "I tell it the range and it
reports" — so this module is deliberately split in two:

* :func:`build_period` is pure: it reads the read-only report produced by ``build_report``,
  the optional Kasra reconciliation rows, and the requested Jalali range, and returns one
  plain record per day of that range;
* :func:`render_html` and :func:`render_markdown` turn those records into private artifacts.

Two rules matter and are enforced here rather than in the renderer:

* only days of the requested range become cells, even though the pipeline fetched two weeks of
  context so that an early entry can pair with a later exit;
* a day holding a single clock, or an unresolved event, is reported as *incomplete* with its
  source ids, so a blank cell is explained instead of silently empty.
"""
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path

import jdatetime

from .allocation import quota_minutes
from .kasra_reconcile import (CREDIT_TYPE_OVERTIME, CREDIT_TYPE_REGULAR, STATUS_APPROVED,
                              STATUS_PENDING, gregorian_from_jalali, jalali_text, label_from_minutes)
from .parser import TEHRAN

# Day statuses, most specific first. ``holiday`` is a rest day that Kasra names; ``rest`` is a
# rest day by the confirmed weekly policy (Thursday and Friday) or by a rest/absence marker.
DAY_STATUSES = ('absence', 'holiday', 'rest', 'pending_approval', 'both', 'overtime', 'regular',
                'open_day', 'incomplete', 'no_work')
WEEK_COLUMNS = ('Saturday', 'Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
STATUS_LABELS = {'regular': 'regular work', 'overtime': 'overtime', 'both': 'regular + overtime',
                 'absence': 'absence', 'rest': 'rest', 'holiday': 'holiday',
                 'pending_approval': 'pending approval', 'incomplete': 'incomplete data',
                 'open_day': 'current day, still open', 'no_work': 'no work reported'}
STATUS_CLASSES = {'regular': 'regular', 'overtime': 'overtime', 'both': 'both', 'absence': 'absence',
                  'rest': 'rest', 'holiday': 'rest', 'pending_approval': 'pending',
                  'incomplete': 'incomplete', 'open_day': 'open', 'no_work': 'none'}
FONT_STACK = "system-ui,-apple-system,'Segoe UI',sans-serif"


def requested_days(start, end):
    """Every Gregorian day of an inclusive Jalali range, in order."""
    first, last = date.fromisoformat(gregorian_from_jalali(start)), date.fromisoformat(gregorian_from_jalali(end))
    if first > last:
        raise ValueError('Range end must not precede its start')
    return [(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]


def week_grid(days):
    """Lay the requested days out Saturday-first, padding the first and last week with blanks.

    The pad keeps the grid honest: a month rarely starts on a Saturday, and the empty leading
    cells make that visible instead of shifting every later day into the wrong column.
    """
    if not days:
        return []
    first = date.fromisoformat(days[0])
    offset = (first.weekday() + 2) % 7          # Saturday -> 0, Sunday -> 1, ... Friday -> 6
    cells = [None] * offset + list(days)
    cells += [None] * (-len(cells) % 7)
    return [cells[index:index + 7] for index in range(0, len(cells), 7)]


def _minutes_between(start_at, end_at):
    if not start_at or not end_at:
        return 0
    return int((datetime.fromisoformat(end_at) - datetime.fromisoformat(start_at)).total_seconds()) // 60


def _day_minutes(report, local_date):
    """Computed regular and overtime minutes for one local day, from the allocated segments."""
    regular = sum(segment['duration_minutes'] for segment in report['segments']
                  if segment['local_date'] == local_date and segment['category'] == 'regular_remote')
    overtime = sum(segment['duration_minutes'] for segment in report['segments']
                   if segment['local_date'] == local_date and segment['category'] == 'overtime_remote')
    return regular, overtime


def _clocks(report, local_date):
    """The first entry clock and the last exit clock recorded for one local day."""
    entry = min((event for event in report['events']
                 if event['kind'] == 'in' and event['date'] == local_date and event['time']), key=lambda e: e['time'],
                default=None, )
    exit_event = max((event for event in report['events']
                      if event['kind'] == 'out' and event['date'] == local_date and event['time']), key=lambda e: e['time'],
                     default=None)
    return (entry['time'] if entry else None), (exit_event['time'] if exit_event else None)


def _unresolved(report, local_date):
    """Events of one day whose pairing is not settled, with their ids, evidence and reasons.

    An unpaired exit keeps ``date`` empty by design — its day is undetermined — so the day it is
    attributed to comes from the interval's ``accounting_days``, which is where the report already
    records the candidate days.
    """
    days_by_event = {}
    for interval in report.get('intervals', []):
        for source_id in interval['source_ids']:
            days_by_event.setdefault(source_id, set()).update(interval['accounting_days'])
    unresolved = []
    for event in report['events']:
        if not event.get('pairing_eligible', True):
            continue
        attributed = days_by_event.get(event['event_id'], set())
        if event['date'] != local_date and local_date not in attributed:
            continue
        if event.get('pairing_state') in ('incomplete_day', 'review') or event['status'] == 'review':
            unresolved.append({'event_id': event['event_id'], 'kind': event['kind'],
                               'time': event.get('time'), 'reasons': list(event['reasons']),
                               'raw_text': event.get('raw_text')})
    return unresolved


def _evidence(report, local_date):
    """The untouched source lines of one day; they are displayed escaped, never interpreted."""
    return [item['raw_text'] for item in report['events'] + report.get('ranges', [])
            if item.get('date') == local_date and item.get('raw_text')]


def _kasra_day(kasra, jalali):
    entry = (kasra or {}).get(jalali) or {}
    documents = [document for document in (entry.get('documents') or []) if document.get('active')]
    def total(credit_type, status_id):
        return sum(document['minutes'] for document in documents
                   if document.get('credit_type') == credit_type and document.get('status_id') == status_id
                   and document.get('minutes'))
    registered_regular, registered_overtime = total(CREDIT_TYPE_REGULAR, STATUS_APPROVED), total(CREDIT_TYPE_OVERTIME, STATUS_APPROVED)
    pending_regular, pending_overtime = total(CREDIT_TYPE_REGULAR, STATUS_PENDING), total(CREDIT_TYPE_OVERTIME, STATUS_PENDING)
    return {'marker': entry.get('day_type'), 'registered_regular_minutes': registered_regular,
            'registered_overtime_minutes': registered_overtime,
            'pending_regular_minutes': pending_regular, 'pending_overtime_minutes': pending_overtime,
            'documents': [document['doc_id'] for document in documents]}


def _status(local_date, regular, overtime, kasra_row, unresolved, open_day):
    """One status per day, chosen so the most actionable fact wins.

    A Kasra shortfall marker never outranks work we can actually evidence: a day that the
    system flags as an absence while the chat shows a full working day is a mismatch to
    review, not a day off.
    """
    marker = (kasra_row['marker'] or '').strip()
    absence_marker = 'غيبت' in marker or 'غیبت' in marker
    if open_day:
        return 'open_day'
    if kasra_row['pending_regular_minutes'] or kasra_row['pending_overtime_minutes']:
        return 'pending_approval'
    if regular and overtime:
        return 'both'
    if overtime:
        return 'overtime'
    if regular:
        return 'regular'
    if unresolved:
        return 'incomplete'
    if absence_marker:
        return 'absence'
    if marker:
        return 'holiday' if quota_minutes(date.fromisoformat(local_date)) else 'rest'
    return 'rest' if quota_minutes(date.fromisoformat(local_date)) == 0 else 'no_work'


def build_period(report, *, start, end, kasra=None, kasra_check='performed'):
    """One record per day of the requested Jalali range, plus period totals.

    ``report`` is the read-only report from :func:`attendance_sync.report.build_report`; nothing
    here reads Mattermost or Kasra. ``kasra`` is the reconciliation mapping returned by
    :func:`attendance_sync.kasra_reconcile.kasra_days`, or ``None`` under ``--no-kasra``.
    """
    today = report.get('current_day')
    days = []
    for local_date in requested_days(start, end):
        jalali = jalali_text(local_date)
        regular, overtime = _day_minutes(report, local_date)
        entry, exit_clock = _clocks(report, local_date)
        unresolved = _unresolved(report, local_date)
        kasra_row = _kasra_day(kasra, jalali)
        open_day = any(event['kind'] == 'in' and event['date'] == local_date and event['status'] == 'open_day'
                       for event in report['events'])
        status = _status(local_date, regular, overtime, kasra_row, unresolved, open_day)
        incomplete = status == 'incomplete' and not (regular or overtime)
        days.append({'local_date': local_date, 'jalali_date': jalali,
                     'weekday': jdatetime.date.fromgregorian(date=date.fromisoformat(local_date)).strftime('%A'),
                     'entry': entry, 'exit': exit_clock,
                     'regular_minutes': regular, 'overtime_minutes': overtime,
                     'registered_regular_minutes': kasra_row['registered_regular_minutes'] if kasra is not None else None,
                     'registered_overtime_minutes': kasra_row['registered_overtime_minutes'] if kasra is not None else None,
                     'pending_regular_minutes': kasra_row['pending_regular_minutes'],
                     'pending_overtime_minutes': kasra_row['pending_overtime_minutes'],
                     'kasra_day_type': kasra_row['marker'], 'kasra_documents': kasra_row['documents'],
                     'status': status, 'incomplete': incomplete,
                     'unresolved': unresolved, 'evidence': _evidence(report, local_date),
                     'source_ids': sorted({event['event_id'] for event in unresolved})})
    work = ('regular', 'overtime', 'both')
    totals = {'regular_minutes': sum(row['regular_minutes'] for row in days),
              'overtime_minutes': sum(row['overtime_minutes'] for row in days),
              'registered_regular_minutes': sum(row['registered_regular_minutes'] or 0 for row in days),
              'registered_overtime_minutes': sum(row['registered_overtime_minutes'] or 0 for row in days),
              'work_days': sum(row['status'] in work for row in days),
              'absence_days': sum(row['status'] == 'absence' for row in days),
              'rest_days': sum(row['status'] in ('rest', 'holiday') for row in days),
              'holiday_days': sum(row['status'] == 'holiday' for row in days),
              'pending_days': sum(row['status'] == 'pending_approval' for row in days),
              'incomplete_days': sum(row['incomplete'] for row in days),
              'days': len(days)}
    return {'schema_version': 1, 'scope': 'period', 'start': start, 'end': end,
            'gregorian': {'from': days[0]['local_date'], 'to': days[-1]['local_date']} if days else None,
            'timezone': 'Asia/Tehran', 'today': today, 'kasra_check': kasra_check if kasra is not None else 'not_performed',
            'week_columns': list(WEEK_COLUMNS), 'totals': totals, 'days': days,
            'incomplete_days': [row['jalali_date'] for row in days if row['incomplete']],
            'review_event_ids': sorted({event['event_id'] for row in days for event in row['unresolved']})}


def _cell(day):
    """One calendar cell; every value crosses the escape boundary exactly once."""
    if day is None:
        return '<td class="day empty"></td>'
    facts = [('Entry', day['entry']), ('Exit', day['exit']),
             ('Regular', label_from_minutes(day['regular_minutes'])),
             ('Overtime', label_from_minutes(day['overtime_minutes'])),
             ('Registered', '—' if day['registered_regular_minutes'] is None else
              f"{label_from_minutes(day['registered_regular_minutes'])} / {label_from_minutes(day['registered_overtime_minutes'])}")]
    body = ''.join('<dt>' + escape(name) + '</dt><dd>' + escape(value or '—') + '</dd>'
                   for name, value in facts)
    note = STATUS_LABELS[day['status']]
    if day['incomplete']:
        note += ' — no counterpart clock for ' + ', '.join(event['event_id'] for event in day['unresolved'])
    evidence = ''
    if day['evidence']:
        evidence = ('<pre class="evidence" dir="auto">'
                    + escape('\n'.join(day['evidence'])) + '</pre>')
    return ('<td class="day ' + STATUS_CLASSES[day['status']] + '"'
            + f' data-local_date="{escape(day["local_date"])}" data-jalali_date="{escape(day["jalali_date"])}"'
            + f' data-status="{escape(day["status"])}" data-regular="{day["regular_minutes"]}"'
            + f' data-overtime="{day["overtime_minutes"]}"'
            + f' data-registered="{escape("" if day["registered_regular_minutes"] is None else str(day["registered_regular_minutes"]))}"'
            + f' data-incomplete="{"yes" if day["incomplete"] else "no"}"'
            + f' data-sources="{escape(",".join(day["source_ids"]))}">'
            + f'<span class="gregorian">{escape(day["local_date"])}</span>'
            + f'<span class="jalali">{escape(day["jalali_date"])}</span>'
            + '<dl>' + body + '</dl>' + evidence
            + f'<span class="status">{escape(note)}</span></td>')


def render_html(period):
    """The week calendar: no external asset, and a policy that forbids loading one anyway."""
    head = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>Attendance period report</title><style>'
            f'body{{font:14px {FONT_STACK};margin:2rem;background:#fff;color:#111}}'
            'table.calendar{border-collapse:collapse;width:100%;table-layout:fixed}'
            'table.calendar th{border:1px solid #bbb;padding:.4rem;background:#f2f2f2}'
            'td.day{border:1px solid #bbb;padding:.5rem;vertical-align:top;height:7rem}'
            'td.day.empty{background:#fafafa}'
            'td.day.regular{background:#e8f3ff}td.day.overtime{background:#fff3d6}'
            'td.day.both{background:#e6f7e6}td.day.absence{background:#ffe0e0}'
            'td.day.rest{background:#f2f2f2}td.day.pending{background:#ede4ff}'
            'td.day.incomplete{background:#fff0f3}td.day.open{background:#e0f7fa}'
            'td.day.none{background:#fff}'
            'span.gregorian{display:block;font-weight:700}span.jalali{display:block;color:#555}'
            'dl{margin:.4rem 0;display:grid;grid-template-columns:auto 1fr;gap:0 .4rem}'
            'dt{color:#555}dd{margin:0}span.status{display:block;margin-top:.3rem;font-style:italic}'
            'pre.evidence{margin:.3rem 0 0;font-size:11px;white-space:pre-wrap;color:#444}'
            '</style></head>')
    content = [head, '<body><h1>Attendance period report</h1>',
               '<p dir="auto">' + escape(f'{period["start"]} to {period["end"]} '
                   f'({period["gregorian"]["from"]} to {period["gregorian"]["to"]}), Asia/Tehran. '
                   'Computed minutes come from our own extracted intervals; registered minutes are what Kasra '
                   'already holds. A day marked incomplete is explained in the note under the grid.') + '</p>',
               '<h2>Calendar</h2><table class="calendar"><tr>'
               + ''.join(f'<th>{escape(column)}</th>' for column in period['week_columns']) + '</tr>']
    for week in week_grid([row['local_date'] for row in period['days']]):
        by_date = {row['local_date']: row for row in period['days']}
        content.append('<tr>' + ''.join(_cell(by_date.get(day)) for day in week) + '</tr>')
    content.append('</table>')
    totals = period['totals']
    content.append('<h2>Totals</h2><table class="totals">'
                   + ''.join(f'<tr><th>{escape(name)}</th><td>{escape(str(value))}</td></tr>'
                             for name, value in (('Computed regular (minutes)', totals['regular_minutes']),
                                                 ('Computed overtime (minutes)', totals['overtime_minutes']),
                                                 ('Registered regular (minutes)', totals['registered_regular_minutes']),
                                                 ('Registered overtime (minutes)', totals['registered_overtime_minutes']),
                                                 ('Work days', totals['work_days']), ('Absence days', totals['absence_days']),
                                                 ('Rest/holiday days', totals['rest_days']),
                                                 ('Pending approval days', totals['pending_days']),
                                                 ('Incomplete days', totals['incomplete_days'])))
                   + '</table>')
    incomplete = [row for row in period['days'] if row['incomplete']]
    content.append('<h2>Incomplete days</h2>')
    if not incomplete:
        content.append('<p>None.</p>')
    else:
        content.append('<ul dir="auto">')
        for row in incomplete:
            explained = '; '.join(f"{event['event_id']} ({event['kind']} {event['time'] or 'no clock'}): "
                                  + ', '.join(event['reasons']) for event in row['unresolved'])
            content.append('<li>' + escape(f'{row["local_date"]} / {row["jalali_date"]}: {explained}') + '</li>')
        content.append('</ul>')
    return ''.join(content) + '</body></html>'


def render_markdown(period):
    """The summary the user reads: period totals, the day table, and why a day looks empty."""
    totals = period['totals']
    lines = ['# Attendance period ' + f'{period["start"]} — {period["end"]}', '',
             f'Gregorian range: {period["gregorian"]["from"]} to {period["gregorian"]["to"]} (Asia/Tehran).',
             f'Kasra check: {period["kasra_check"]}.', '',
             '## Period summary', '',
             f'- Computed regular minutes: {totals["regular_minutes"]}',
             f'- Computed overtime minutes: {totals["overtime_minutes"]}',
             f'- Registered regular minutes (Kasra): {totals["registered_regular_minutes"]}',
             f'- Registered overtime minutes (Kasra): {totals["registered_overtime_minutes"]}',
             f'- Work days: {totals["work_days"]}',
             f'- Absence days: {totals["absence_days"]}',
             f'- Rest or holiday days: {totals["rest_days"]}',
             f'- Pending approval days: {totals["pending_days"]}',
             f'- Incomplete days: {totals["incomplete_days"]} of {totals["days"]}', '',
             '## Days', '',
             '| Day | Jalali | Entry | Exit | Computed regular | Computed overtime | Registered regular '
             '| Registered overtime | Status |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for row in period['days']:
        registered = ('—' if row['registered_regular_minutes'] is None
                      else f'{label_from_minutes(row["registered_regular_minutes"])} / '
                           f'{label_from_minutes(row["registered_overtime_minutes"])}')
        cells = [row['local_date'], row['jalali_date'], row['entry'] or '—', row['exit'] or '—',
                 label_from_minutes(row['regular_minutes']), label_from_minutes(row['overtime_minutes']),
                 registered.split(' / ')[0], registered.split(' / ')[1] if ' / ' in registered else registered,
                 STATUS_LABELS[row['status']]]
        lines.append('| ' + ' | '.join(cell.replace('|', '\\|') for cell in cells) + ' |')
    lines += ['', '## Incomplete days', '']
    incomplete = [row for row in period['days'] if row['incomplete']]
    if not incomplete:
        lines.append('None: every day of the range has a determined pairing or no clock at all.')
    else:
        lines.append('These days look empty because the data itself is incomplete, not because no work '
                     'was recorded:')
        lines.append('')
        for row in incomplete:
            for event in row['unresolved']:
                lines.append(f'- {row["local_date"]} / {row["jalali_date"]}: event `{event["event_id"]}` '
                             f'({event["kind"]}, clock {event["time"] or "missing"}) is unresolved: '
                             + ', '.join(event['reasons']) + '.')
    if period['review_event_ids']:
        lines += ['', 'Source ids of the unresolved events: ' + ', '.join(f'`{i}`' for i in period['review_event_ids']) + '.']
    return '\n'.join(lines) + '\n'


def write_period_reports(period, output):
    """Write the calendar and the summary as private files in a private directory.

    Reuses the atomic owner-only writing of the preview reports rather than repeating it.
    """
    from .report import validate_private_output, write_private_text
    output = validate_private_output(output)
    parent = output.parent
    html_path, markdown_path = Path(str(output) + '.html'), Path(str(output) + '.md')
    write_private_text(str(html_path), render_html(period), prefix='.period-')
    write_private_text(str(markdown_path), render_markdown(period), prefix='.period-')
    return [html_path, markdown_path]
