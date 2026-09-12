"""Stage one: GET-only preview, no external mutation commands."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import sys
from pathlib import Path
import httpx
from .config import load
from .mattermost import Mattermost, collect, APIError
from .report import build_report, write_reports


def load_corrections(path):
    location = Path(path)
    if not location.exists():
        return None
    return json.loads(location.read_text(encoding='utf-8'))


def main(argv=None, *, env=None, transport=None):
    parser = argparse.ArgumentParser(prog='attendance-sync')
    sub = parser.add_subparsers(dest='command',required=True)
    preview = sub.add_parser('preview',help='Read Mattermost and write a private local preview')
    preview.add_argument('--days',type=int)
    preview.add_argument('--from',dest='from_time')
    preview.add_argument('--to',dest='to_time')
    preview.add_argument('--output',default='artifacts/review')
    preview.add_argument('--corrections',default='var/corrections.json')
    preview.add_argument('--as-of',dest='as_of')
    preview.add_argument('--debug',action='store_true')
    args = parser.parse_args(argv)
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
            start,end = datetime.fromisoformat(args.from_time),datetime.fromisoformat(args.to_time)
            if start.utcoffset() is None or end.utcoffset() is None or start >= end:
                raise ValueError('Window requires ordered timezone-aware timestamps')
        else:
            days = args.days if args.days is not None else 14
            if days <= 0 or days > 366:
                raise ValueError('--days must be between 1 and 366')
            end = datetime.now(timezone.utc)
            start = end-timedelta(days=days)
        if end-start > timedelta(days=366):
            raise ValueError('Window cannot exceed 366 days')
        stage = 'mattermost'
        with httpx.Client(transport=transport,trust_env=False) as client:
            api = Mattermost(client,config.url,config.token)
            own = api.get('/users/me')
            channel = api.get('/channels/'+config.channel_id)
            if not isinstance(own.get('id'),str) or not own['id'] or channel.get('id') != config.channel_id:
                raise APIError('Mattermost identity/channel schema mismatch')
            def fetch(cursor):
                params = {'per_page':200}
                if cursor:
                    params['before'] = cursor
                return api.get('/channels/'+config.channel_id+'/posts',params)
            context_start = start-timedelta(days=14)
            posts = collect(fetch,int(context_start.timestamp()*1000),int(end.timestamp()*1000))
            if any(not isinstance(p.get('message'),str) or not isinstance(p.get('user_id'),str) for p in posts):
                raise APIError('Mattermost post schema mismatch')
        stage = 'report'
        report = build_report(posts,own['id'],start,end,corrections=corrections,as_of=as_of)
        report['window']['context_from'] = context_start.isoformat()
        report['corrections_path'] = str(args.corrections)
        report['context_policy'] = '14-day bounded lookback; unresolved boundaries remain review'
        paths = write_reports(report,args.output)
        counts = {k:v for k,v in report.items() if k.endswith('_count')}
        print(json.dumps({'coverage_complete':True,**counts,'reports':[str(p) for p in paths]}))
        return 0
    except Exception as error:
        if isinstance(error,APIError):
            message = str(error)
        elif stage == 'configuration' and isinstance(error,ValueError):
            message = 'Invalid configuration or window; check environment names and CLI arguments'
        else:
            message = 'Preview failed; no complete report produced'
        print(message,file=sys.stderr)
        if args.debug:
            print(f'Debug: stage={stage}; error_type={type(error).__name__}; coverage_complete=false; details redacted',file=sys.stderr)
        return 2
