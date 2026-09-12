def test_normalizes_without_losing_lines():
    from attendance_sync.normalize import normalize
    assert normalize('ورود ۰۷:۵۵\nسه‌شنبه ي ك') == 'ورود 07:55\nسه شنبه ی ک'
