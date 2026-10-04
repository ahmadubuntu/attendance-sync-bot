"""What the current pipeline makes of the real posts.

Not an assertion file: this is the diagnostic that showed where pairing goes wrong. Run it
with `.venv/bin/python tests/inspect_pairing.py` and read the table.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from fixtures_real_posts import real_posts  # noqa: E402

from attendance_sync.pairing import pair  # noqa: E402
from attendance_sync.parser import parse_post  # noqa: E402


def main():
    posts = real_posts()
    events = []
    for post in posts:
        for group in parse_post(post):
            for event in group:
                events.append(event)

    print('parsed events:', len(events))
    print()
    print(f"{'kind':5} {'time':6} {'date':12} {'basis':16} {'text date':12} post")
    print('-' * 78)
    for event in events:
        print(f"{event['kind']:5} {event['time'] or '':6} "
              f"{str(event.get('date')):12} {str(event.get('date_basis')):16} "
              f"{str(event.get('text_date')):12} {event['post_id'][:10]}")

    print()
    print('after pairing:')
    print('-' * 78)
    for event in pair(events):
        print(f"{event['kind']:5} {str(event.get('time')):6} date={str(event.get('date')):12} "
              f"state={event.get('pairing_state')} flags={event.get('flags')}")


if __name__ == '__main__':
    main()