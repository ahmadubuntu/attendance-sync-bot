from dataclasses import dataclass, field
from urllib.parse import urlsplit
import re


@dataclass(frozen=True)
class Config:
    url: str
    token: str = field(repr=False)
    channel_id: str


def load(env):
    names = ('GOFT_URL','GOFT_TOKEN','GOFT_CHANNEL_ID')
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise ValueError('Missing environment variables: '+', '.join(missing))
    url = env['GOFT_URL'].rstrip('/')
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or any(c.isspace() for c in url):
        raise ValueError('GOFT_URL must be an HTTPS base URL without credentials, query or fragment')
    if parts.path.endswith('/api/v4'):
        raise ValueError('GOFT_URL must not end in /api/v4')
    if not re.fullmatch(r'[a-z0-9]{26}', env['GOFT_CHANNEL_ID']):
        raise ValueError('Invalid GOFT_CHANNEL_ID format')
    if not re.fullmatch(r'[\x21-\x7e]+', env['GOFT_TOKEN']):
        raise ValueError('Invalid GOFT_TOKEN format')
    return Config(url, env['GOFT_TOKEN'], env['GOFT_CHANNEL_ID'])
