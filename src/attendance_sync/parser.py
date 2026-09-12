"""Conservative extraction; source lines are never normalized in evidence."""
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo
import jdatetime
from .normalize import normalize

TEHRAN = ZoneInfo('Asia/Tehran')
RULE_VERSION = 'preview-1'
WEEKDAYS = {'شنبه': 5, 'یکشنبه': 6, 'دوشنبه': 0, 'سهشنبه': 1, 'چهارشنبه': 2, 'پنجشنبه': 3, 'جمعه': 4}
WEEKDAY = re.compile(r'یک ?شنبه|دو ?شنبه|سه ?شنبه|چهار ?شنبه|پنج ?شنبه|چهارنشبه|شنبه|جمعه')
DATE = re.compile(r'(?<!\d)(?:1[34]\d{6}|1[34]\d{2}[/.-]\d{1,2}[/.-]\d{1,2})(?!\d)')
MARKER = re.compile(r'(?<!\w)(ورود|خروج)(?!\w)')
CLOCK = re.compile(r'(?<![\d:])(?:\d{1,2}:\d{2}|\d{4})(?![\d:])')


def explicit_date(token):
    token = normalize(token)
    if re.fullmatch(r'\d{8}', token):
        parts = (token[:4], token[4:6], token[6:])
    elif re.fullmatch(r'\d{4}[/.-]\d{1,2}[/.-]\d{1,2}', token):
        parts = re.split(r'[/.-]', token)
    else:
        raise ValueError('Invalid Jalali date')
    return jdatetime.date(*map(int, parts)).togregorian()


def clock(token):
    parts = token.split(':') if ':' in token else (token[:2], token[2:])
    hour, minute = map(int, parts)
    if hour > 23 or minute > 59:
        return None
    return f'{hour:02d}:{minute:02d}'


def parse_post(post):
    posted = datetime.fromtimestamp(post['create_at']/1000, timezone.utc)
    local = posted.astimezone(TEHRAN).date()
    active_lines = []
    fenced = False
    for raw_line in post['message'].splitlines():
        line = normalize(raw_line)
        if line.startswith(('```', '~~~')):
            fenced = not fenced
            continue
        if not fenced and not line.startswith('>') and '`' not in line:
            active_lines.append(raw_line)
    active_text = '\n'.join(active_lines)
    raw_context = [line for line in active_lines if DATE.search(normalize(line)) or WEEKDAY.search(normalize(line))]
    text = normalize(active_text)
    dates = DATE.findall(text)
    source_dates = [m.group() for m in re.finditer(r'(?<!\d)(?:\d{8}|\d{4}[/.-]\d{1,2}[/.-]\d{1,2})(?!\d)', active_text) if DATE.fullmatch(normalize(m.group()))]
    raw_date = source_dates[0] if source_dates else None
    weekdays = WEEKDAY.findall(text)
    source_weekdays = re.findall(r'یک[ \t\u200c]*شنبه|يك[ \t\u200c]*شنبه|دو[ \t\u200c]*شنبه|سه[ \t\u200c]*شنبه|چهار[ \t\u200c]*شنبه|پنج[ \t\u200c]*شنبه|چهارنشبه|شنبه|جمعه', active_text)
    raw_weekday = source_weekdays[0] if source_weekdays else None
    wd = WEEKDAYS.get(normalize(raw_weekday or '').replace(' ', ''))
    reasons = []
    if len({w.replace(' ', '') for w in weekdays}) > 1:
        reasons.append('multiple_weekdays')
    day, basis, suggestion = None, None, None
    if dates:
        try:
            day = explicit_date(dates[0])
            basis = 'explicit_jalali'
            if wd is not None and day.weekday() != wd:
                reasons.append('weekday_date_conflict')
                suggestion = local - timedelta(days=(local.weekday()-wd)%7)
        except ValueError:
            reasons.append('invalid_date')
        if len(set(dates)) > 1:
            reasons.append('multiple_dates')
    elif wd is not None:
        day = local - timedelta(days=(local.weekday()-wd)%7)
        basis = 'weekday_inferred'
    else:
        day, basis = local, 'post_date_assumed'
        reasons.append('unknown_weekday' if raw_weekday else 'post_date_assumed')
    candidate_days = {day.isoformat()} if day else set()
    if suggestion:
        candidate_days.add(suggestion.isoformat())
    for token in dates:
        try:
            candidate_days.add(explicit_date(token).isoformat())
        except ValueError:
            pass
    if reasons:
        for weekday in weekdays:
            candidate_wd = WEEKDAYS.get(weekday.replace(' ', ''))
            if candidate_wd is not None:
                candidate_days.add((local - timedelta(days=(local.weekday()-candidate_wd)%7)).isoformat())
    events = []
    fenced = False
    for line_index, raw_line in enumerate(post['message'].splitlines()):
        line = normalize(raw_line)
        if line.startswith(('```', '~~~')):
            fenced = not fenced
            continue
        if fenced:
            continue
        markers = list(MARKER.finditer(line))
        line_event_start = len(events)
        for index, marker in enumerate(markers):
            prefix = line[:marker.start()] if index == 0 else ''
            prefix = WEEKDAY.sub('', DATE.sub('', prefix)).strip(' >`،,:;-')
            if prefix:
                break
            end = markers[index+1].start() if index+1 < len(markers) else len(line)
            part = WEEKDAY.sub('', DATE.sub('', line[marker.end():end])).lstrip(' `،,:;-')
            match = CLOCK.match(part)
            if match is None and part.strip() and not re.search(r'(?:برای|جهت)\s+ناهار', part):
                del events[line_event_start:]
                break
            if index+1 < len(markers) and match and part[match.end():].strip(' `،,:;-') not in ('', 'و'):
                del events[line_event_start:]
                break
            value = clock(match.group()) if match else None
            issues = list(reasons)
            if line.startswith('>') or '`' in line:
                issues.append('quoted_attendance')
            if re.search(r'نبود|نیست|اصلاح|اشتباه|نکردم', line):
                issues.append('negation_or_correction')
            if re.search(r'(?:برای|جهت)\s+ناهار', part):
                issues.append('break_not_attendance')
            if value is None:
                issues.append('invalid_time' if match else 'missing_time')
            events.append(dict(event_id=f"{post['id']}:{len(events)}", post_id=post['id'],
                source_version=post.get('edit_at', 0), line_index=line_index, event_index=len(events),
                kind='in' if marker.group() == 'ورود' else 'out', raw_text=raw_line,
                pairing_eligible=not any(r in issues for r in ('quoted_attendance', 'break_not_attendance')),
                raw_date=raw_date, raw_weekday=raw_weekday, raw_context=list(raw_context),
                time=value, date=day.isoformat() if day else None,
                suggested_date=suggestion.isoformat() if suggestion else None, candidate_dates=sorted(candidate_days),
                posted_at=posted.isoformat(), date_basis=basis,
                explicit_overtime=bool(re.search(r'اضافه ?کار|خارج از موظفی|overtime', line, re.I)),
                status='review' if issues else 'ready', reasons=issues, rule_version=RULE_VERSION))
    ranges = []
    work_pattern = r'کار|ایجاد|توسعه|بررسی|جلسه|رفع|تست|پیاده|کالکشن|work|develop|fix|deploy'
    work_context = [raw for raw in active_lines if re.search(work_pattern, normalize(raw), re.I)
                    and not re.search(r'ناهار|قطع.*برق|خاموش|نسخه|version|log\b|نبود|نیست|نکردم', normalize(raw), re.I)]
    fenced = False
    range_pattern = re.compile(r'(?<![\d:])(\d{1,2}:\d{2}|\d{4})\s*(?:[-–—]|تا)\s*(\d{1,2}:\d{2}|\d{4})(?![\d:])')
    for line_index, raw_line in enumerate(post['message'].splitlines()):
        line = normalize(raw_line)
        if line.startswith(('```', '~~~')):
            fenced = not fenced
            continue
        if fenced or line.startswith('>') or '`' in line or MARKER.search(line):
            continue
        if re.search(r'ناهار|قطع.*برق|خاموش|نسخه|version|log\b', line, re.I):
            continue
        for match in range_pattern.finditer(line):
            start, end = clock(match[1]), clock(match[2])
            issues = list(reasons)
            if re.search(r'نبود|نیست|اصلاح|اشتباه|نکردم', line):
                issues.append('negation_or_correction')
            if not work_context:
                issues.append('activity_context_unconfirmed')
            if start is None or end is None:
                issues.append('invalid_time')
            range_day, range_basis = day, basis
            warnings = []
            if basis == 'post_date_assumed' and work_context and start and end and start != end:
                completed = datetime.fromisoformat(local.isoformat()+'T'+end).replace(tzinfo=TEHRAN)
                morning_overnight = start > end and posted.astimezone(TEHRAN).hour < 12
                if completed <= posted and (start < end or morning_overnight):
                    issues = [r for r in issues if r != 'post_date_assumed']
                    range_basis = 'posted_clock_inferred'
                    if morning_overnight:
                        range_day = local - timedelta(days=1)
                        range_basis = 'posted_clock_overnight_inferred'
                    warnings.append('inferred_date_from_completed_range')
            ranges.append(dict(range_id=f"{post['id']}:range:{len(ranges)}", post_id=post['id'],
                source_version=post.get('edit_at', 0), raw_text=raw_line,
                source_span={'line_index': line_index, 'start': 0, 'end': len(raw_line)},
                raw_date=raw_date, raw_weekday=raw_weekday, raw_context=list(raw_context),
                date=range_day.isoformat() if range_day else None, suggested_date=suggestion.isoformat() if suggestion else None,
                date_basis=range_basis, candidate_dates=[range_day.isoformat()] if warnings else sorted(candidate_days), start_time=start, end_time=end, posted_at=posted.isoformat(),
                warnings=warnings, work_context=work_context,
                explicit_overtime=bool(re.search(r'اضافه ?کار|خارج از موظفی|overtime', line, re.I)),
                status='review' if issues else 'ready', reasons=issues, rule='activity_range', rule_version=RULE_VERSION))
    return events, ranges
