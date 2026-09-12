"""User-confirmed local overrides bound to immutable source snapshots."""
from datetime import date
import hashlib
import json
import re


def validate_corrections(config):
    if config is None:
        return
    if not isinstance(config, dict) or config.get('schema_version') != 1 or not isinstance(config.get('date_corrections'), list):
        raise ValueError('Invalid corrections schema')
    seen = set()
    for entry in config['date_corrections']:
        if not isinstance(entry, dict) or set(entry) != {'post_id', 'source_fingerprint', 'raw_date', 'target_date', 'confirmed_by'}:
            raise ValueError('Invalid correction entry')
        if any(not isinstance(v, str) for v in entry.values()) or entry['confirmed_by'] != 'user':
            raise ValueError('Correction requires user confirmation')
        if not entry['post_id'] or entry['post_id'] in seen or not re.fullmatch(r'[0-9a-f]{64}', entry['source_fingerprint']):
            raise ValueError('Invalid or duplicate source identity')
        if not entry['raw_date'] or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', entry['target_date']):
            raise ValueError('Invalid correction dates')
        date.fromisoformat(entry['target_date'])
        seen.add(entry['post_id'])


def source_fingerprint(post):
    fields = {k: post.get(k, 0) for k in ('id', 'create_at', 'edit_at', 'message')}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def apply_corrections(items, post, config):
    fingerprint = source_fingerprint(post)
    for item in items:
        item['source_fingerprint'] = fingerprint
        item['kasra_check'] = 'not_performed'
        for entry in (config or {}).get('date_corrections', []):
            if entry['post_id'] != post['id']:
                continue
            if entry['source_fingerprint'] != fingerprint or entry['raw_date'] != item['raw_date']:
                item['status'] = 'review'
                item['reasons'].append('stale_date_correction')
                continue
            item['original_date'] = item['date']
            item['correction'] = dict(entry)
            item['date'] = date.fromisoformat(entry['target_date']).isoformat()
            item['date_basis'] = 'user_confirmed_correction'
            item['candidate_dates'] = [item['date']]
            item['reasons'] = [r for r in item['reasons'] if r not in ('weekday_date_conflict', 'invalid_date')]
            item['status'] = 'review' if item['reasons'] else 'ready'
