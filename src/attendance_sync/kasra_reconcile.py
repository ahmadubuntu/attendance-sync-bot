"""Pure reconciliation between extracted work intervals and Kasra documents.

No browser, no network, no business rule hidden in the adapter: this module answers
"which minutes are not requested in Kasra yet" from three plain inputs:

* our own extracted segments (the ``segments`` list of ``artifacts/review.json``),
* the daily work report rows (registered minutes per column),
* the document inquiry rows (documents of credit type ``14085``/``60054``).

Kasra is a request system: a saved document waits for an approver. A day is therefore
``pending_approval`` while a document exists with an active status, and it is only
reported as missing when no document of the needed credit type covers that day.
Minutes are always exact integers; nothing is ever rounded to a coarser boundary.
"""
import json
import os
from datetime import date, datetime, time
from pathlib import Path
import re
import tempfile

import jdatetime

DEFAULT_DESCRIPTION = ('این مجوز توسط ربات AttendanceSync ثبت شده است و ممکن است با واقعیت مغایرت داشته باشد؛ '
                       'در صورت نیاز کاربر باید آن را اصلاح کند.')

# Credit types confirmed on the live system (docs/kasra-contract.md, section 6).
CREDIT_TYPE_REGULAR = 14085
CREDIT_TYPE_OVERTIME = 60054
CREDIT_TYPES = {'regular_remote': CREDIT_TYPE_REGULAR, 'overtime_remote': CREDIT_TYPE_OVERTIME}
CREDIT_TYPE_TITLES = {CREDIT_TYPE_REGULAR: 'دوركاري ساعتي', CREDIT_TYPE_OVERTIME: 'دوركاري خارج از موظفي'}
CATEGORIES = ('regular_remote', 'overtime_remote')
SHORT = {'regular_remote': 'regular', 'overtime_remote': 'overtime'}

# Document statuses observed on the live system; 201 waits for an approver.
STATUS_PENDING = 201
STATUS_APPROVED = 203
ACTIVE_STATUS_IDS = (STATUS_PENDING, STATUS_APPROVED)
INACTIVE_STATUS_IDS = (204, 205, 209)

DAY_STATUSES = ('ok', 'no_work', 'no_document', 'missing_regular', 'missing_overtime',
                'partial_regular', 'partial_overtime', 'pending_approval')
CATEGORY_STATES = ('not_applicable', 'ok', 'pending', 'missing', 'partial', 'unknown_extent')

JALALI_MONTHS = (None, 'فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
                 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند')

TIME_LABEL = re.compile(r'^(\d{1,2}):(\d{2})$')
JALALI_DATE = re.compile(r'^(\d{4})/(\d{2})/(\d{2})$')
TITLE_TIMES = re.compile(r'(?<![\d:/])(\d{1,2}:\d{2})(?![\d:])')


# --- small pure helpers -----------------------------------------------------

def minutes_from_label(label):
    """Parse a ``HH:MM`` duration label into exact minutes (``''`` means zero)."""
    text = (label or '').strip()
    if not text:
        return 0
    match = TIME_LABEL.match(text)
    if not match or int(match.group(2)) > 59:
        raise ValueError('Invalid duration label')
    return int(match.group(1)) * 60 + int(match.group(2))


def label_from_minutes(value):
    """Render exact minutes as ``HH:MM`` without any rounding."""
    value = int(value)
    if value < 0:
        raise ValueError('Negative duration')
    return f'{value // 60:02d}:{value % 60:02d}'


def jalali_text(day):
    """Convert an ISO Gregorian date string to a Jalali ``YYYY/MM/DD`` string."""
    return jdatetime.date.fromgregorian(date=date.fromisoformat(day)).strftime('%Y/%m/%d')


def gregorian_from_jalali(text):
    """Convert a Jalali ``YYYY/MM/DD`` string to an ISO Gregorian date string."""
    match = JALALI_DATE.match((text or '').strip())
    if not match:
        raise ValueError('Invalid Jalali date')
    return jdatetime.date(*(int(part) for part in match.groups())).togregorian().isoformat()


def jalali_month_label(day):
    """Jalali month and year label used by the work-period combo (``شهریور 1405``)."""
    jalali = jdatetime.date.fromgregorian(date=date.fromisoformat(day))
    return f'{JALALI_MONTHS[jalali.month]} {jalali.year}'


def persian_digits(text):
    """Render digits for the Kasra form inputs, which expect Persian numerals."""
    return str(text).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))


def _cell(row, index):
    if index is None or index >= len(row):
        return ''
    value = row[index]
    return value.strip() if isinstance(value, str) else ('' if value is None else str(value))


def _ascii_key(label):
    return ''.join(character for character in (label or '') if ord(character) < 128).strip()


def _column_index(columns, name):
    for position, label in enumerate(columns or ()):
        if _ascii_key(label) == name or (label or '').strip() == name:
            return position
    return None


def _title_times(title):
    """Extract ``(start, end)`` clock labels from a document title, if present."""
    found = TITLE_TIMES.findall(title or '')
    if len(found) < 2:
        return (None, None)
    return (found[-2], found[-1])


# --- our own extracted work -------------------------------------------------

def _segment_minutes(segment):
    if segment.get('duration_minutes') is not None:
        return int(segment['duration_minutes'])
    start = datetime.fromisoformat(segment['start_at'])
    end = datetime.fromisoformat(segment['end_at'])
    return int((end - start).total_seconds()) // 60


def _merge_runs(segments):
    """Merge segments of one category that touch each other; keep real gaps apart."""
    runs = []
    for segment in segments:
        minutes = _segment_minutes(segment)
        if minutes <= 0:
            continue
        if runs and runs[-1]['end_at'] == segment['start_at']:
            runs[-1]['end_at'] = segment['end_at']
            runs[-1]['minutes'] += minutes
            runs[-1]['segment_ids'].append(segment.get('segment_id'))
        else:
            runs.append({'start_at': segment['start_at'], 'end_at': segment['end_at'], 'minutes': minutes,
                         'segment_ids': [segment.get('segment_id')]})
    return runs


def our_days(segments):
    """Group our extracted segments into per-day expected regular/overtime minutes."""
    grouped = {}
    for segment in segments or ():
        if not segment.get('submission_eligible') or not segment.get('local_date'):
            continue
        if segment.get('category') not in CATEGORIES:
            continue
        day = grouped.setdefault(segment['local_date'], {
            'local_date': segment['local_date'], 'jalali_date': jalali_text(segment['local_date']),
            'runs': {category: [] for category in CATEGORIES}})
        day['runs'][segment['category']].append(segment)
    days = []
    for _, day in sorted(grouped.items()):
        for category in CATEGORIES:
            day['runs'][category] = _merge_runs(sorted(day['runs'][category], key=lambda row: row['start_at']))
        for category in CATEGORIES:
            day[SHORT[category] + '_minutes'] = sum(run['minutes'] for run in day['runs'][category])
        days.append(day)
    return days


# --- the Kasra side --------------------------------------------------------

def daily_report_days(columns, rows):
    """Map daily-report columns/rows to registered minutes per Jalali day."""
    day_index = _column_index(columns, 'تاريخ')
    regular_index = _column_index(columns, 'دوركاري')
    overtime_index = _column_index(columns, 'دوركاري خارج از موظفي')
    type_index = _column_index(columns, 'ترددها')
    if day_index is None or (regular_index is None and overtime_index is None):
        raise ValueError('Daily report columns not recognised')
    days = {}
    for row in rows or ():
        day = _cell(row, day_index)
        if not JALALI_DATE.match(day):
            continue
        markers = []
        day_type = _cell(row, type_index)
        if 'منتظر' in day_type:
            markers.append('overtime_remote' if 'خارج' in day_type else 'regular_remote')
        days[day] = {'jalali_date': day, 'day_type': day_type,
                     'report_regular_minutes': minutes_from_label(_cell(row, regular_index)),
                     'report_overtime_minutes': minutes_from_label(_cell(row, overtime_index)),
                     'pending_markers': markers}
    return days


def documents_from_table(columns, rows):
    """Map document-inquiry rows to normalised document records."""
    index = {name: _column_index(columns, name)
             for name in ('DocID', 'StatusID', 'DocTypeID', 'RSDate', 'REDate', 'DocTitle')}
    documents = []
    for row in rows or ():
        doc_id = _cell(row, index['DocID'])
        if not re.fullmatch(r'\d+', doc_id):
            continue
        title = _cell(row, index['DocTitle'])
        if 'خارج از موظفي' in title:
            credit_type = CREDIT_TYPE_OVERTIME
        elif 'دوركاري ساعتي' in title:
            credit_type = CREDIT_TYPE_REGULAR
        else:
            credit_type = None
        status = _cell(row, index['StatusID'])
        status_id = int(status) if status.isdigit() else None
        start_time, end_time = _title_times(title) if credit_type else (None, None)
        minutes = None
        if start_time and end_time:
            minutes = minutes_from_label(end_time) - minutes_from_label(start_time)
            if minutes <= 0:
                minutes += 24 * 60
        documents.append({'doc_id': doc_id, 'credit_type': credit_type, 'doc_type_id': _cell(row, index['DocTypeID']),
                          'status_id': status_id, 'day': _cell(row, index['RSDate']),
                          'end_day': _cell(row, index['REDate']), 'doc_title': title,
                          'start_time': start_time, 'end_time': end_time, 'minutes': minutes,
                          'active': status_id in ACTIVE_STATUS_IDS})
    return documents


def kasra_days(report_days, documents):
    """Merge report days and documents into the mapping consumed by reconcile."""
    days = {}
    for day, row in (report_days or {}).items():
        days[day] = {'jalali_date': day, 'day_type': row.get('day_type'),
                     'report_regular_minutes': row.get('report_regular_minutes', 0),
                     'report_overtime_minutes': row.get('report_overtime_minutes', 0),
                     'pending_markers': list(row.get('pending_markers') or []), 'documents': []}
    for document in documents or ():
        day = document.get('day')
        if not day:
            continue
        entry = days.setdefault(day, {'jalali_date': day, 'day_type': None, 'report_regular_minutes': 0,
                                      'report_overtime_minutes': 0, 'pending_markers': [], 'documents': []})
        entry['documents'].append(document)
    return days


# --- reconciliation --------------------------------------------------------

def _category_state(expected, active, unknown_extent, pending_marker):
    requested = sum(document['minutes'] for document in active if document.get('minutes'))
    has_pending = any(document.get('status_id') == STATUS_PENDING for document in active)
    if expected == 0:
        state = 'not_applicable'
    elif not active and pending_marker:
        state = 'unknown_extent'
    elif requested == 0 and unknown_extent:
        state = 'unknown_extent'
    elif requested >= expected:
        state = 'pending' if has_pending else 'ok'
    elif requested == 0:
        state = 'missing'
    else:
        state = 'partial'
    missing = 0
    if state == 'missing':
        missing = expected
    elif state == 'partial':
        missing = expected - requested
    return {'state': state, 'expected_minutes': expected, 'requested_minutes': requested,
            'registered_minutes': sum(document['minutes'] for document in active
                                     if document.get('minutes') and document.get('status_id') == STATUS_APPROVED),
            'pending_minutes': sum(document['minutes'] for document in active
                                   if document.get('minutes') and document.get('status_id') == STATUS_PENDING),
            'missing_minutes': missing, 'doc_ids': [document['doc_id'] for document in active],
            'unknown_extent': bool(unknown_extent and state == 'unknown_extent')}


def reconcile(our, kasra):
    """Classify every day of our data and report the exact minutes still missing."""
    kasra = kasra or {}
    days = []
    for day in our or ():
        jalali = day['jalali_date']
        entry = kasra.get(jalali, {})
        documents = list(entry.get('documents') or [])
        categories = {}
        flags = []
        for category in CATEGORIES:
            expected = day[SHORT[category] + '_minutes']
            active = [document for document in documents
                      if document.get('active') and document.get('credit_type') == CREDIT_TYPES[category]]
            unknown_extent = any(document.get('minutes') is None for document in active)
            categories[category] = _category_state(expected, active, unknown_extent,
                                                   SHORT[category] in (entry.get('pending_markers') or []))
            state = categories[category]['state']
            if state == 'missing':
                flags.append('missing_' + SHORT[category])
            elif state == 'partial':
                flags.append('partial_' + SHORT[category])
            elif state in ('pending', 'unknown_extent'):
                flags.append('manual_review' if state == 'unknown_extent' else 'pending_approval')
        record = {'local_date': day['local_date'], 'jalali_date': jalali, 'runs': day['runs'],
                  'documents': documents, 'report': {'regular_minutes': entry.get('report_regular_minutes', 0),
                                                     'overtime_minutes': entry.get('report_overtime_minutes', 0)}}
        record['status'] = _day_status(categories)
        record['flags'] = sorted(set(flags))
        record['manual_review'] = any(row['state'] == 'unknown_extent' for row in categories.values())
        record['actionable'] = record['status'] in ('no_document', 'missing_regular', 'missing_overtime',
                                                    'partial_regular', 'partial_overtime')
        for key in ('expected', 'requested', 'registered', 'pending', 'missing'):
            record[key] = {SHORT[category] + '_minutes': categories[category][key + '_minutes']
                           for category in CATEGORIES}
        record['missing_total_minutes'] = sum(categories[category]['missing_minutes'] for category in CATEGORIES)
        days.append(record)
    days += _kasra_only_days(our or (), kasra)
    days.sort(key=lambda row: row['local_date'])
    return {'days': days, 'unregistered': [row['jalali_date'] for row in days if row['missing_total_minutes'] > 0],
            'counts': _counts(days)}


def _kasra_only_days(our, kasra):
    known = {day['jalali_date'] for day in our}
    extra = []
    for jalali, entry in sorted(kasra.items()):
        documents = [document for document in (entry.get('documents') or []) if document.get('active')]
        if jalali in known or not documents:
            continue
        extra.append({'local_date': gregorian_from_jalali(jalali), 'jalali_date': jalali,
                      'runs': {category: [] for category in CATEGORIES}, 'documents': documents,
                      'report': {'regular_minutes': entry.get('report_regular_minutes', 0),
                                 'overtime_minutes': entry.get('report_overtime_minutes', 0)},
                      'status': 'no_work', 'flags': ['registered_without_expected_work'],
                      'manual_review': False, 'actionable': False,
                      'expected': {SHORT[category] + '_minutes': 0 for category in CATEGORIES},
                      'requested': {SHORT[category] + '_minutes': 0 for category in CATEGORIES},
                      'registered': {SHORT[category] + '_minutes': 0 for category in CATEGORIES},
                      'pending': {SHORT[category] + '_minutes': 0 for category in CATEGORIES},
                      'missing': {SHORT[category] + '_minutes': 0 for category in CATEGORIES},
                      'missing_total_minutes': 0})
    return extra


def _day_status(categories):
    states = {category: categories[category]['state'] for category in CATEGORIES}
    if all(state == 'not_applicable' for state in states.values()):
        return 'no_work'
    if all(state == 'missing' for state in states.values()):
        return 'no_document'
    if states['regular_remote'] == 'missing':
        return 'missing_regular'
    if states['overtime_remote'] == 'missing':
        return 'missing_overtime'
    if states['overtime_remote'] == 'partial':
        return 'partial_overtime'
    if states['regular_remote'] == 'partial':
        return 'partial_regular'
    if any(state in ('pending', 'unknown_extent') for state in states.values()):
        return 'pending_approval'
    return 'ok'


def _counts(days):
    return {'days': len(days),
            'ok_days': sum(row['status'] == 'ok' for row in days),
            'pending_days': sum(row['status'] == 'pending_approval' for row in days),
            'actionable_days': sum(row['actionable'] for row in days),
            'no_work_days': sum(row['status'] == 'no_work' for row in days),
            'manual_review_days': sum(row['manual_review'] for row in days),
            'expected_total_minutes': sum(sum(row['expected'].values()) for row in days),
            'requested_total_minutes': sum(sum(row['requested'].values()) for row in days),
            'registered_total_minutes': sum(sum(row['registered'].values()) for row in days),
            'pending_total_minutes': sum(sum(row['pending'].values()) for row in days),
            'missing_regular_minutes': sum(row['missing']['regular_minutes'] for row in days),
            'missing_overtime_minutes': sum(row['missing']['overtime_minutes'] for row in days),
            'missing_total_minutes': sum(row['missing_total_minutes'] for row in days)}


def subtract_intervals(runs, covered):
    """Return the parts of ``runs`` that no ``covered`` interval covers, exactly."""
    result = []
    for run in runs:
        pieces = [(datetime.fromisoformat(run['start_at']), datetime.fromisoformat(run['end_at']))]
        for start, end in sorted(covered or ()):
            covered_start, covered_end = datetime.fromisoformat(start), datetime.fromisoformat(end)
            remaining = []
            for piece_start, piece_end in pieces:
                if covered_end <= piece_start or covered_start >= piece_end:
                    remaining.append((piece_start, piece_end))
                    continue
                if piece_start < covered_start:
                    remaining.append((piece_start, covered_start))
                if covered_end < piece_end:
                    remaining.append((covered_end, piece_end))
            pieces = remaining
        for piece_start, piece_end in pieces:
            minutes = int((piece_end - piece_start).total_seconds()) // 60
            if minutes > 0:
                result.append({'start_at': piece_start.isoformat(), 'end_at': piece_end.isoformat(),
                               'minutes': minutes})
    return result


def _covered_intervals(jalali_day, documents, category, zone):
    covered = []
    for document in documents:
        if not document.get('active') or document.get('credit_type') != CREDIT_TYPES[category]:
            continue
        if not document.get('start_time') or not document.get('end_time'):
            continue
        doc_day = document.get('day') or jalali_day
        if doc_day != jalali_day:
            continue
        start_clock = time.fromisoformat(document['start_time'])
        end_clock = time.fromisoformat(document['end_time'])
        start_day = gregorian_from_jalali(doc_day)
        end_day = gregorian_from_jalali(document.get('end_day') or doc_day)
        covered.append((datetime.combine(date.fromisoformat(start_day), start_clock, tzinfo=zone).isoformat(),
                        datetime.combine(date.fromisoformat(end_day), end_clock, tzinfo=zone).isoformat()))
    return covered


def build_payloads(result, *, person_id, description=DEFAULT_DESCRIPTION):
    """Build one credit document per unregistered run; never rounds minutes."""
    payloads = []
    for day in result.get('days') or ():
        for category in CATEGORIES:
            missing = day['missing'][SHORT[category] + '_minutes']
            if missing <= 0:
                continue
            runs = day['runs'][category]
            if not runs:
                continue
            zone = datetime.fromisoformat(runs[0]['start_at']).tzinfo
            covered = _covered_intervals(day['jalali_date'], day['documents'], category, zone)
            missing_runs = subtract_intervals(runs, covered)
            if sum(run['minutes'] for run in missing_runs) != missing:
                continue  # the readable coverage does not explain the missing minutes: do not guess
            for run in missing_runs:
                start = datetime.fromisoformat(run['start_at'])
                end = datetime.fromisoformat(run['end_at'])
                warnings = []
                if start.date() != end.date() and end.time() == time.min:
                    warnings.append('ends_at_midnight')
                payloads.append({
                    'credit_type': CREDIT_TYPES[category], 'credit_type_title': CREDIT_TYPE_TITLES[CREDIT_TYPES[category]],
                    'category': category, 'day': day['jalali_date'], 'local_date': day['local_date'],
                    'start_date': persian_digits(jalali_text(start.date().isoformat())),
                    'start_time': start.strftime('%H:%M'),
                    'end_date': persian_digits(jalali_text(end.date().isoformat())),
                    'end_time': end.strftime('%H:%M'),
                    'day_count': '1', 'minutes': run['minutes'], 'person_id': person_id,
                    'description': description, 'source_run': {'start_at': run['start_at'], 'end_at': run['end_at']},
                    'warnings': warnings, 'requires_review': bool(warnings)})
    return payloads


def validate_private_path(output):
    """Reject an output path that is not inside a dedicated private directory."""
    output = Path(output)
    parent = output.parent
    if parent == Path('.') or parent.is_symlink():
        raise ValueError(f'Use a dedicated private output directory for {output.name}')
    return output


def write_plan(plan, output):
    """Write the private reconciliation plan with owner-only permissions."""
    output = validate_private_path(output)
    parent = output.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix='.kasra-plan-', dir=parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(plan, ensure_ascii=False, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return output
