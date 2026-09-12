"""Bounded GET-only Mattermost transport. No response bodies in errors."""
import time
import httpx


class APIError(ValueError):
    pass


class Mattermost:
    def __init__(self, client, base_url, token, sleep=time.sleep):
        self.client, self.base_url, self._token, self.sleep = client, base_url, token, sleep

    def get(self, path, params=None):
        for attempt in range(3):
            try:
                response = self.client.get(self.base_url+'/api/v4'+path, params=params,
                    headers={'Authorization':'Bearer '+self._token}, follow_redirects=False, timeout=30)
            except httpx.HTTPError:
                raise APIError('Mattermost network request failed') from None
            status = response.status_code
            if status == 429 or status >= 500:
                if attempt < 2:
                    delay = response.headers.get('Retry-After', '1')
                    self.sleep(min(5, max(0, int(delay))) if delay.isdigit() else 1)
                    continue
            if status != 200:
                raise APIError(f'Mattermost GET failed (HTTP {status})')
            try:
                data = response.json()
            except ValueError:
                raise APIError('Mattermost returned invalid JSON') from None
            if not isinstance(data, dict):
                raise APIError('Mattermost returned invalid schema')
            return data


def collect(fetch_page, start_ms, end_ms, max_pages=100):
    found, signatures = {}, set()
    cursor = None
    previous_oldest = None
    for _ in range(max_pages):
        data = fetch_page(cursor)
        try:
            order, posts = data['order'], data['posts']
            if not isinstance(order, list) or not isinstance(posts, dict):
                raise ValueError
            rows = [posts[ident] for ident in order]
            if any(not isinstance(r.get('create_at'), int) or r.get('id') != ident for r, ident in zip(rows, order)):
                raise ValueError
            times = [r['create_at'] for r in rows]
            if times != sorted(times, reverse=True):
                raise ValueError
        except (KeyError, TypeError, AttributeError, ValueError):
            raise APIError('Incomplete coverage: invalid posts schema or order') from None
        signature = tuple(order)
        if rows and signature in signatures:
            raise APIError('Incomplete coverage: repeated page')
        signatures.add(signature)
        if rows and previous_oldest is not None and times[0] > previous_oldest:
            raise APIError('Incomplete coverage: cursor moved forward')
        for row in rows:
            if start_ms <= row['create_at'] <= end_ms:
                old = found.get(row['id'])
                if old is not None and old != row:
                    raise APIError('Incomplete coverage: source changed during scan')
                found[row['id']] = row
        if not rows or min(times) < start_ms:
            return sorted(found.values(), key=lambda r: r['create_at'])
        previous_oldest = times[-1]
        cursor = order[-1]
    raise APIError('Incomplete coverage: page limit reached')
