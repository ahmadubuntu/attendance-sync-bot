"""Fixture built from real Mattermost posts (2026-09-16 .. 2026-10-01).

The posts below were fetched with the authenticated API and are copied verbatim, including
the original Persian weekday typography and the posting timestamp. They are the evidence
every pairing test in this file is checked against: nothing here is invented.

The scenario that matters is the one the user reported: consecutive days each have one
entry and one exit, but the exit for a day is posted on the *following* morning, sometimes in
the same minute as the next day's entry. Pairing must use the date written in the text, never
the day the message was sent.
"""

import datetime

TEHRAN = datetime.timezone(datetime.timedelta(hours=3, minutes=30))

# (post_id, create_at ISO in Tehran, message) in channel order.
REAL_POSTS = [
    ('m6mtx3knsi', '2026-09-16T07:59:00', 'چهارشنبه 14050625\nورود 0800'),
    ('am1xto63ti', '2026-09-16T07:59:00', 'خروج سه شنبه 1710'),
    ('tk6cjhy7k7', '2026-09-16T23:44:00', 'خروج چهار شنبه 1845'),
    ('qszy68n1qj', '2026-09-19T08:27:00', 'شنبه 1405/06/28\nورود 0825'),
    ('uos4ybtapi', '2026-09-20T08:13:00',
     'خروج شنبه 2300 گرچه دیرتر بود ولی تایم شام و ... رو حذف میکنیم و مابقیشم فشرده در نظر میگیریم'),
    ('khyhysbdei', '2026-09-20T08:13:00', 'یکشنبه 14050629\nورود 0810'),
    ('byk7691sct', '2026-09-21T07:07:00', 'خروج یکشنبه 1710 با قطع برق'),
    ('14h8arhjkp', '2026-09-21T07:11:00', 'دوشنبه 1405/06/30\nورود 0710'),
    ('9h5eh9ne4i', '2026-09-22T00:52:00', 'خروج 2200 '),
    ('krxsqozms3', '2026-09-22T08:31:00', 'سه شنبه 14050631\nورود 0830'),
    ('ytux9roe3p', '2026-09-23T08:26:00', 'چهارشنبه 14050701\nورود 0825'),
    ('m3dmn63mqt', '2026-09-23T08:26:00', 'خروج سه شنبه 2000'),
    ('c6kb1qempf', '2026-09-23T08:26:00',
     'بعد از اینکه 1840 رفتم بیرون . دوباره 2300 تا 0130 روی همین سنتری مشفول بودم که ساعت خروج رو یکپارچه میزنم.'),
    ('n1rgcodt5p', '2026-09-26T08:58:00', 'شنبه 14050704\nورود 0845'),
    ('cisy7h1q7t', '2026-09-26T08:57:00', 'خروج چهارشنبه 1830'),
    ('g1pkdwq6yi', '2026-09-27T07:13:00', 'خروج شنبه 2000'),
    ('fymz4w5k3j', '2026-09-27T07:14:00', 'یکشنبه 14050705\nورود 0700'),
    ('73fw4osdu3', '2026-09-28T09:07:00', 'خروج یکشنبه 1705'),
    ('8ywpwbp3r7', '2026-09-28T09:07:00', 'دوشنبه 14050706\nورود 0900'),
    ('ngd6g9c7f3', '2026-09-29T07:31:00', 'خروج دوشنبه 2200'),
    ('awe6uu8ynb', '2026-09-29T09:21:00', 'سه شنبه 14050707 \nورود ساعت 0900'),
    ('cqbk88cngi', '2026-09-29T16:56:00', 'لپتاپ با بی برقی خاموش شد، فعلا خروج میزنم'),
    ('fqd59fjy7b', '2026-09-30T08:44:00', 'خروج سه شنبه 2359'),
    ('3c3xzxtr67', '2026-09-30T08:44:00', 'چهارشنبه 14050708\nورود 0845'),
    ('h19mx9epzj', '2026-10-01T13:56:00', 'خروج چهارشنبه 1930'),
]


def real_posts():
    """Return the fixture as the post dicts the app's collector produces."""
    posts = []
    for post_id, iso, message in REAL_POSTS:
        moment = datetime.datetime.fromisoformat(iso).replace(tzinfo=TEHRAN)
        posts.append({
            'id': post_id,
            'message': message,
            'create_at': int(moment.timestamp() * 1000),
            'user_id': 'real',
        })
    return posts