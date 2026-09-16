"""Local CLI: the read-only Mattermost preview plus the Kasra reconciliation commands.

``preview`` stays GET-only. ``kasra-status``/``kasra-plan`` only read Kasra, and
``kasra-submit`` prints the exact payloads it would create unless ``--confirm`` is given.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import sys
from pathlib import Path
import httpx
from .config import load
from .mattermost import Mattermost, collect, APIError
from .report import build_report, write_reports
from .kasra_browser import KasraBrowser
from .kasra_reconcile import (DEFAULT_DESCRIPTION, build_payloads, daily_report_days,
                              documents_from_table, gregorian_from_jalali, jalali_text, kasra_days,
                              our_days, reconcile, validate_private_path, write_plan)
from .period_report import build_period, write_period_reports

JALALI_HINT = 'Use Jalali YYYY/MM/DD for --start and --end, with start not after end'


def approval_code(content):
    """Short code bound to the exact content a human is approving.

    Any change to the payloads changes the code, so a code read from one dry run can never
    authorise a different write.
    """
    canonical = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]


def require_approval(expected, given):
    """Return the approved value or refuse; never proceed on a stale or missing code."""
    if given == expected:
        return given
    if not given and sys.stdin.isatty():
        typed = input(f'Type the approval code {expected} to proceed: ').strip()
        if typed == expected:
            return typed
        raise ValueError('Approval code did not match; nothing was written')
    raise ValueError(f'Approval required: re-run the dry run and pass --approve {expected}')


def load_corrections(path):
    location = Path(path)
    if not location.exists():
        return None
    return json.loads(location.read_text(encoding='utf-8'))


def build_parser():
    parser = argparse.ArgumentParser(prog='attendance-sync')
    sub = parser.add_subparsers(dest='command', required=True)
    preview = sub.add_parser('preview', help='Read Mattermost and write a private local preview')
    preview.add_argument('--days', type=int)
    preview.add_argument('--from', dest='from_time')
    preview.add_argument('--to', dest='to_time')
    preview.add_argument('--output', default='artifacts/review')
    preview.add_argument('--corrections', default='var/corrections.json')
    preview.add_argument('--as-of', dest='as_of')
    preview.add_argument('--debug', action='store_true')
    _add_kasra_inputs(sub.add_parser('kasra-status', help='Read-only per-day reconciliation table'))
    plan = sub.add_parser('kasra-plan', help='Write the private reconciliation plan')
    _add_kasra_inputs(plan)
    plan.add_argument('--output', default='artifacts/kasra-plan.json')
    submit = sub.add_parser('kasra-submit', help='Print (or with --confirm send) the plan payloads')
    submit.add_argument('--plan', default='artifacts/kasra-plan.json')
    submit.add_argument('--confirm', action='store_true')
    submit.add_argument('--approve', help='Approval code printed by the dry run; required before any write')
    submit.add_argument('--include-review', dest='include_review', action='store_true')
    submit.add_argument('--created-docs', dest='created_docs', default='artifacts/kasra-created.json')
    submit.add_argument('--delete-doc-id', dest='delete_doc_id')
    submit.add_argument('--state', default='var/kasra-recon/session-state.json')
    submit.add_argument('--debug', action='store_true')
    period = sub.add_parser('period-report', help='Write the private calendar and summary for a Jalali range')
    period.add_argument('--start')
    period.add_argument('--end')
    period.add_argument('--output', default='artifacts/period-report')
    period.add_argument('--review', default='artifacts/review.json')
    period.add_argument('--snapshot')
    period.add_argument('--corrections', default='var/corrections.json')
    period.add_argument('--state', default='var/kasra-recon/session-state.json')
    period.add_argument('--person-id', dest='person_id')
    period.add_argument('--as-of', dest='as_of')
    period.add_argument('--no-kasra', dest='no_kasra', action='store_true')
    period.add_argument('--debug', action='store_true')
    return parser


def _add_kasra_inputs(parser):
    parser.add_argument('--review', default='artifacts/review.json')
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--snapshot')
    parser.add_argument('--save-snapshot', dest='save_snapshot')
    parser.add_argument('--person-id', dest='person_id')
    parser.add_argument('--description', default=DEFAULT_DESCRIPTION)
    parser.add_argument('--state', default='var/kasra-recon/session-state.json')
    parser.add_argument('--debug', action='store_true')


def main(argv=None, *, env=None, transport=None, kasra=None):
    args = build_parser().parse_args(argv)
    if args.command == 'preview':
        return run_preview(args, env, transport)
    if args.command == 'period-report':
        return run_period_report(args, env, transport, kasra)
    return run_kasra(args, env, kasra)


def run_preview(args, env, transport):
    stage = 'configuration'
    try:
        config = load(os.environ if env is None else env)
        as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
        if as_of.utcoffset() is None:
            raise ValueError('--as-of requires a timezone-aware timestamp')
        corrections = load_corrections(args.corrections)
        if args.from_time or args.to_time:
            if not args.from_time or not args.to_time or args.days is not None:
                raise ValueError('Use both --from and --to, without --days')
            start, end = datetime.fromisoformat(args.from_time), datetime.fromisoformat(args.to_time)
            if start.utcoffset() is None or end.utcoffset() is None or start >= end:
                raise ValueError('Window requires ordered timezone-aware timestamps')
        else:
            days = args.days if args.days is not None else 14
            if days <= 0 or days > 366:
                raise ValueError('--days must be between 1 and 366')
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=days)
        if end - start > timedelta(days=366):
            raise ValueError('Window cannot exceed 366 days')
        stage = 'mattermost'
        with httpx.Client(transport=transport, trust_env=False) as client:
            api = Mattermost(client, config.url, config.token)
            own = api.get('/users/me')
            channel = api.get('/channels/' + config.channel_id)
            if not isinstance(own.get('id'), str) or not own['id'] or channel.get('id') != config.channel_id:
                raise APIError('Mattermost identity/channel schema mismatch')

            def fetch(cursor):
                params = {'per_page': 200}
                if cursor:
                    params['before'] = cursor
                return api.get('/channels/' + config.channel_id + '/posts', params)
            context_start = start - timedelta(days=14)
            posts = collect(fetch, int(context_start.timestamp() * 1000), int(end.timestamp() * 1000))
            if any(not isinstance(p.get('message'), str) or not isinstance(p.get('user_id'), str) for p in posts):
                raise APIError('Mattermost post schema mismatch')
        stage = 'report'
        report = build_report(posts, own['id'], start, end, corrections=corrections, as_of=as_of)
        report['window']['context_from'] = context_start.isoformat()
        report['corrections_path'] = str(args.corrections)
        report['context_policy'] = '14-day bounded lookback; unresolved boundaries remain review'
        paths = write_reports(report, args.output)
        counts = {k: v for k, v in report.items() if k.endswith('_count')}
        print(json.dumps({'coverage_complete': True, **counts, 'reports': [str(p) for p in paths]}))
        return 0
    except Exception as error:
        if isinstance(error, APIError):
            message = str(error)
        elif stage == 'configuration' and isinstance(error, ValueError):
            message = 'Invalid configuration or window; check environment names and CLI arguments'
        else:
            message = 'Preview failed; no complete report produced'
        print(message, file=sys.stderr)
        if args.debug:
            print(f'Debug: stage={stage}; error_type={type(error).__name__}; coverage_complete=false; details redacted', file=sys.stderr)
        return 2


def load_review_report(path, start, end):
    """Reuse an existing preview when it covers exactly this window; otherwise return None.

    A stale or mismatched preview must never be silently reused, so the check is on the window
    bounds: only an exact match short-circuits the fetch. A malformed file is ignored rather than
    trusted.
    """
    location = Path(path)
    if not location.exists():
        return None
    try:
        report = json.loads(location.read_text(encoding='utf-8'))
    except ValueError:
        return None
    if not isinstance(report.get('intervals'), list) or not isinstance(report.get('events'), list):
        return None
    window = report.get('window') or {}
    if (str(window.get('from', ''))[:10] != gregorian_from_jalali(start)
            or str(window.get('to', ''))[:10] != gregorian_from_jalali(end)):
        return None
    return report


def run_period_report(args, env, transport, kasra):
    """Fetch the window covering the range, build the report, write the private artifacts.

    The range is read exactly like ``kasra-status``: same Jalali parsing, same hint text. The
    Mattermost window is opened two weeks early so an early entry can pair with a later exit, but
    only the requested range is rendered.
    """
    stage = 'configuration'
    client = None
    try:
        start, end = resolve_range(args, {'from': args.start, 'to': args.end})
        start_gregorian, end_gregorian = gregorian_from_jalali(start), gregorian_from_jalali(end)
        as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
        if as_of.utcoffset() is None:
            raise ValueError('--as-of requires a timezone-aware timestamp')
        corrections = load_corrections(args.corrections)
        stage = 'mattermost'
        config = load(os.environ if env is None else env)
        window_start = datetime.fromisoformat(start_gregorian + 'T00:00:00+03:30').astimezone(timezone.utc)
        window_end = datetime.fromisoformat(end_gregorian + 'T23:59:59+03:30').astimezone(timezone.utc)
        review_report = load_review_report(args.review, start, end)
        if review_report is not None:
            # A preview of exactly this window already exists: reuse it instead of refetching, so the
            # period report can never disagree with the review the user is looking at.
            report = review_report
        else:
            with httpx.Client(transport=transport, trust_env=False) as http:
                api = Mattermost(http, config.url, config.token)
                own = api.get('/users/me')
                channel = api.get('/channels/' + config.channel_id)
                if not isinstance(own.get('id'), str) or not own['id'] or channel.get('id') != config.channel_id:
                    raise APIError('Mattermost identity/channel schema mismatch')

                def fetch(cursor):
                    params = {'per_page': 200}
                    if cursor:
                        params['before'] = cursor
                    return api.get('/channels/' + config.channel_id + '/posts', params)
                context_start = window_start - timedelta(days=14)
                posts = collect(fetch, int(context_start.timestamp() * 1000), int(window_end.timestamp() * 1000))
            stage = 'report'
            report = build_report(posts, own['id'], window_start, window_end, corrections=corrections, as_of=as_of)
        stage = 'kasra'
        day_map = None
        if not args.no_kasra:
            if args.snapshot:
                snapshot = json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
                daily, documents = snapshot['daily'], snapshot['documents']
            else:
                client = open_client(args, env, kasra)
                daily = client.read_daily_report(start, end)
                documents = client.read_documents(start, end)
            ours = [day for day in our_days(report['segments'])
                    if start_gregorian <= day['local_date'] <= end_gregorian]
            table = {day: row for day, row in daily_report_days(_columns(daily), _rows(daily)).items()
                     if start <= day <= end}
            found = [row for row in documents_from_table(_columns(documents), _rows(documents))
                     if row.get('day') and start <= row['day'] <= end]
            day_map = kasra_days(table, found)
        stage = 'render'
        period = build_period(report, start=start, end=end, kasra=day_map)
        paths = write_period_reports(period, args.output)
        print(json.dumps({'range': {'start': start, 'end': end}, 'kasra_check': period['kasra_check'],
                          'days': period['totals']['days'], 'incomplete_days': period['incomplete_days'],
                          'reports': [str(path) for path in paths]}))
        return 0
    except Exception as error:
        if isinstance(error, APIError):
            message = str(error)
        elif stage in ('configuration', 'render') and isinstance(error, ValueError):
            message = 'Invalid configuration or range for period-report'
        else:
            message = 'Period report failed; no complete artifacts were produced'
        print(message, file=sys.stderr)
        if getattr(args, 'debug', False):
            print(f'Debug: stage={stage}; error_type={type(error).__name__}; details redacted', file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()


def run_kasra(args, env, kasra):
    if args.command == 'kasra-submit':
        return run_submit(args, env, kasra)
    stage = 'configuration'
    client = None
    try:
        segments, window = load_review(args.review)
        start, end = resolve_range(args, window)
        stage = 'kasra'
        if args.snapshot:
            snapshot = json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
            daily, documents = snapshot['daily'], snapshot['documents']
            person_id = args.person_id or snapshot.get('person_id')
        else:
            client = open_client(args, env, kasra)
            daily = client.read_daily_report(start, end)
            documents = client.read_documents(start, end)
            person_id = args.person_id or read_person_id(client)
            if args.save_snapshot:
                write_plan({'range': {'start': start, 'end': end}, 'person_id': person_id,
                            'daily': daily, 'documents': documents}, args.save_snapshot)
        stage = 'reconcile'
        ours = [day for day in our_days(segments)
                if gregorian_from_jalali(start) <= day['local_date'] <= gregorian_from_jalali(end)]
        report = {day: row for day, row in daily_report_days(_columns(daily), _rows(daily)).items()
                  if start <= day <= end}
        found = [row for row in documents_from_table(_columns(documents), _rows(documents))
                 if row.get('day') and start <= row['day'] <= end]
        kasra = kasra_days(report, found)
        result = reconcile(ours, kasra)
        result['range'] = {'start': start, 'end': end}
        if args.command == 'kasra-status':
            print(render_status(result))
            print(json.dumps({'range': result['range'], 'unregistered': result['unregistered'],
                              **result['counts']}))
            return 0
        stage = 'plan'
        plan = {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
                'range': result['range'], 'description': args.description, 'person_id': person_id,
                'unregistered': result['unregistered'], 'counts': result['counts'],
                'days': result['days'],
                'payloads': build_payloads(result, person_id=person_id, description=args.description)
                if person_id else []}
        path = write_plan(plan, args.output)
        print(json.dumps({'plan': str(path), 'payloads': len(plan['payloads']), 'person_id_known': bool(person_id),
                          'unregistered': result['unregistered'], **result['counts']}))
        return 0
    except Exception as error:
        if stage == 'configuration' and isinstance(error, ValueError):
            message = 'Invalid configuration or inputs for the Kasra command'
        else:
            message = 'Kasra command failed; nothing was written unless --confirm was given'
        print(message, file=sys.stderr)
        if getattr(args, 'debug', False):
            print(f'Debug: stage={stage}; error_type={type(error).__name__}; details redacted', file=sys.stderr)
        return 2
    finally:
        if client is not None:
            client.close()


def run_submit(args, env, kasra):
    stage = 'configuration'
    try:
        plan_path = Path(args.plan)
        if not plan_path.exists():
            raise ValueError('Missing plan file')
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        if args.delete_doc_id:
            expected = approval_code({'delete_doc_id': str(args.delete_doc_id)})
            if not args.confirm:
                print(json.dumps({'mode': 'dry_run', 'would_delete': str(args.delete_doc_id),
                                  'approval_code': expected}))
                return 0
            require_approval(expected, args.approve)
            client = open_client(args, env, kasra)
            try:
                print(json.dumps(client.delete_document(args.delete_doc_id, confirm=True)))
            finally:
                client.close()
            return 0
        payloads = list(plan.get('payloads') or [])
        # Validate the private output location before anything can be created on the server, so a
        # save can never succeed while its audit record has nowhere to go.
        created_path = validate_private_path(args.created_docs)
        selected = [row for row in payloads if args.include_review or not row.get('requires_review')]
        skipped = [row for row in payloads if row not in selected]
        expected = approval_code(selected)
        for payload in selected:
            print(render_payload(payload))
        for payload in skipped:
            print('# skipped until reviewed: ' + render_payload(payload, compact=True))
        stage = 'write'
        if not args.confirm:
            print(json.dumps({'mode': 'dry_run', 'payloads': len(selected), 'skipped': len(skipped),
                              'written': 0, 'approval_code': expected}))
            print('# to register these exact documents re-run with: --confirm --approve ' + expected,
                  file=sys.stderr)
            return 0
        require_approval(expected, args.approve)
        client = open_client(args, env, kasra)
        try:
            created = []
            for payload in selected:
                client.write_credit_document(payload, confirm=True)
                created.append(record_created(client, payload))
                # Flush after every payload: a document that exists on the server must always be
                # recorded, even when a later payload fails.
                write_plan({'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                            'documents': created}, created_path)
        finally:
            client.close()
        verified = [row for row in created if row['read_back']]
        print(json.dumps({'mode': 'confirmed', 'written': len(verified), 'attempted': len(created),
                          'created': str(created_path)}))
        if len(verified) != len(created):
            print('kasra-submit: some documents could not be read back; treat them as unverified',
                  file=sys.stderr)
            return 3
        return 0
    except Exception as error:
        if stage == 'configuration' and isinstance(error, ValueError):
            message = 'Invalid configuration or plan for kasra-submit'
        else:
            message = 'kasra-submit failed; read back the document list before retrying'
        print(message, file=sys.stderr)
        if getattr(args, 'debug', False):
            print(f'Debug: stage={stage}; error_type={type(error).__name__}; details redacted', file=sys.stderr)
        return 2


def record_created(client, payload):
    """Read the document list back and record the exact document the request created."""
    table = client.read_documents(payload['day'], payload['day'])
    documents = [row for row in documents_from_table(_columns(table), _rows(table))
                 if row.get('credit_type') == payload['credit_type'] and row.get('day') == payload['day']
                 and row.get('start_time') == payload['start_time'] and row.get('end_time') == payload['end_time']
                 and row.get('active')]
    match = max(documents, key=lambda row: int(row['doc_id'])) if documents else None
    return {'document_id': match['doc_id'] if match else None, 'status_id': match['status_id'] if match else None,
            'credit_type': payload['credit_type'], 'day': payload['day'], 'start_time': payload['start_time'],
            'end_time': payload['end_time'], 'minutes': payload['minutes'],
            'description': payload['description'], 'read_back': match is not None}


def load_review(path):
    location = Path(path)
    if not location.exists():
        raise ValueError('Missing review report')
    report = json.loads(location.read_text(encoding='utf-8'))
    if not isinstance(report.get('segments'), list) or not isinstance(report.get('window'), dict):
        raise ValueError('Invalid review report')
    return report['segments'], report['window']


def resolve_range(args, window):
    start = args.start or jalali_text(str(window['from'])[:10])
    end = args.end or jalali_text(str(window['to'])[:10])
    try:
        start_gregorian, end_gregorian = gregorian_from_jalali(start), gregorian_from_jalali(end)
    except ValueError:
        raise ValueError(JALALI_HINT)
    if start_gregorian > end_gregorian:
        raise ValueError(JALALI_HINT)
    return start, end


def open_client(args, env, kasra):
    if kasra is not None:
        kasra.open()
        return kasra
    environment = os.environ if env is None else env
    if not environment.get('KASRA_URL'):
        raise ValueError('Missing KASRA_URL')
    client = KasraBrowser(environment['KASRA_URL'], state_path=args.state,
                          username=environment.get('KASRA_USERNAME'),
                          password=environment.get('KASRA_PASSWORD'))
    client.open()
    return client


def read_person_id(client):
    reader = getattr(client, 'read_person_id', None)
    if reader is None:
        return None
    try:
        return reader()
    except Exception:
        return None


def _columns(table):
    return (table or {}).get('columns') or []


def _rows(table):
    return (table or {}).get('rows') or []


def render_status(result):
    header = ('day         jalali      status            exp_reg exp_ot req_reg req_ot '
              'mis_reg mis_ot pend_reg pend_ot flags')
    lines = [header]
    for day in result['days']:
        lines.append('{:<11} {:<11} {:<17} {:>7} {:>6} {:>7} {:>6} {:>7} {:>6} {:>8} {:>7} {}'.format(
            day['local_date'], day['jalali_date'], day['status'],
            day['expected']['regular_minutes'], day['expected']['overtime_minutes'],
            day['requested']['regular_minutes'], day['requested']['overtime_minutes'],
            day['missing']['regular_minutes'], day['missing']['overtime_minutes'],
            day['pending']['regular_minutes'], day['pending']['overtime_minutes'],
            ','.join(day['flags']) if day['flags'] else '-'))
    return '\n'.join(lines)


def render_payload(payload, compact=False):
    keys = ('credit_type', 'day', 'start_date', 'start_time', 'end_date', 'end_time', 'day_count',
            'minutes', 'person_id', 'requires_review', 'description')
    if compact:
        keys = ('credit_type', 'day', 'start_time', 'end_time', 'minutes')
    return json.dumps({key: payload.get(key) for key in keys}, ensure_ascii=False)
