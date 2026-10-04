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
# Wording that puts `ورود`/`خروج` and a four-digit number into a technical sense rather than
# an attendance one. Used to disqualify the line before a marker is read as a clock.
TECHNICAL_PROSE = re.compile(
    r'نسخه|ورود کاربر|خروج کاربر|لاگین|لاگ ?اوت|login|logout|sign ?in|sign ?out'
    r'|version|release|deploy|commit|branch|api|endpoint|database|server'
    r'|باگ|bug|خطای سیستم|اختلال|مشکل ورود|مشکل خروج', re.I)
# Filler the user writes between the marker and the clock: `ورود ساعت 0900`,
# `ورود حدوداکنون 0700`. These carry no clock of their own, so they are stripped before the
# clock is read rather than being allowed to hide it.
CLOCK_FILLER = re.compile(r'ساعت\s*|حدوداکنون|حدود\s*|تقریبا\s*|حدودی\s*')


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
        except ValueError:
            reasons.append('invalid_date')
        if len(set(dates)) > 1:
            reasons.append('multiple_dates')
    elif wd is not None:
        # A weekday with no date beside it names a weekday, not a day. Which day that was
        # comes from the channel's own written dates, resolved later by weekday_resolve --
        # never from the posting instant. `create_at` is a UTC moment: read on a New York
        # clock the very same note looks like the previous day, so deriving the day from it
        # makes the result depend on where the bot happens to run.
        day = None
        basis = 'weekday_only'
    else:
        day, basis = None, 'undated'
        reasons.append('unknown_weekday' if raw_weekday else 'no_date_in_text')
    candidate_days = {day.isoformat()} if day else set()
    if suggestion:
        candidate_days.add(suggestion.isoformat())
    for token in dates:
        try:
            candidate_days.add(explicit_date(token).isoformat())
        except ValueError:
            pass
    # A weekday on its own adds no candidate day here. Which day of that weekday was meant is
    # decided in `weekday_resolve`, against the dates the channel states in writing -- not
    # against the posting instant. Guessing "the Tuesday of the week this note was sent in"
    # would put a day in the candidate set that depends on where the bot runs, and a run on a
    # New York clock would offer the Friday before as a candidate for a Tuesday.
    # `weekday_resolve` fills `candidate_dates` where the text genuinely leaves a choice.
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
        # Prose that merely contains the word. `بررسی مشکل ورود کاربران در نسخه 1700` is a
        # bug report: `ورود` is part of "user login" and `1700` is a version number. Read as
        # attendance it becomes a real entry at 17:00, which then pairs with a later `خروج`
        # and files a shift nobody worked. Technical wording disqualifies the whole line, the
        # same way it already does for work ranges.
        if markers and TECHNICAL_PROSE.search(line):
            markers = []
        line_event_start = len(events)
        for index, marker in enumerate(markers):
            prefix = line[:marker.start()] if index == 0 else ''
            # Words before the marker are fine: `لپتاپ خاموش شد، فعلا خروج میزنم` is a real
            # exit. Only a date or a weekday in front of the marker means the marker belongs
            # to the line above (`سه شنبه 14050707 / خروج 1705`), not to this line.
            stray = WEEKDAY.sub('', DATE.sub('', prefix)).strip(' >`،,:;-')
            if stray and (WEEKDAY.search(prefix) or DATE.search(prefix)):
                break
            end = markers[index+1].start() if index+1 < len(markers) else len(line)
            part = WEEKDAY.sub('', DATE.sub('', line[marker.end():end])).lstrip(' `،,:;-')
            # Filler words carry no clock, so they are removed before the clock is read:
            # otherwise `ورود ساعت 0900` reads as "no clock" and the whole note is dropped.
            part = CLOCK_FILLER.sub('', part).lstrip(' `،,:;-')
            match = CLOCK.search(part) if part else None
            # Prose after the marker is normal: `خروج میزنم` states an exit whose clock the
            # user never gave. That note is kept and flagged for review, not dropped -- and a
            # clockless note is never turned into a guessed one.
            prose = CLOCK_FILLER.sub('', CLOCK.sub('', part)).strip(' `،,:;-' 'و')
            is_prose = bool(re.search(r'میزنم|میزنیم|می‌زنم|کردم|شد', part))
            if match is None and prose and not is_prose \
                    and not re.search(r'(?:برای|جهت)\s+ناهار', part):
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
            # A range whose day the text never stated is left undated. Guessing it from the
            # posting instant (a shift that finished before the note was sent, read as "this
            # must have been yesterday") is how a night shift gets filed under the wrong day,
            # and it changes answer with the machine's timezone.
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
