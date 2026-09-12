import pytest


def test_config_preview_only_and_safe_repr():
    from attendance_sync.config import load
    config = load({'GOFT_URL':'https://chat.example/team', 'GOFT_TOKEN':'secret', 'GOFT_CHANNEL_ID':'a'*26})
    assert config.url == 'https://chat.example/team'
    assert 'secret' not in repr(config)


@pytest.mark.parametrize('update', [{}, {'GOFT_URL':'http://chat.example'}, {'GOFT_URL':'https://u:p@chat.example'}, {'GOFT_URL':'https://chat.example/api/v4'}, {'GOFT_CHANNEL_ID':'../../x'}, {'GOFT_TOKEN':'bad\nvalue'}])
def test_config_rejects_invalid(update):
    from attendance_sync.config import load
    env = {'GOFT_URL':'https://chat.example','GOFT_TOKEN':'secret','GOFT_CHANNEL_ID':'a'*26} if update else {}
    env.update(update)
    with pytest.raises(ValueError):
        load(env)
