# tests/test_remote_mic_config.py
import config


def test_room_key_derives_from_user_name():
    # 방 key 는 항상 이름(USER_NAME)에서 정제되어 나온다 — 환경 무관 불변식
    assert config.ROOM_KEY == config._room_key(config.USER_NAME)


def test_remote_mic_key_env_removed():
    # 별도 REMOTE_MIC_KEY 는 폐기되고 ROOM_KEY 로 통일됨
    assert not hasattr(config, "REMOTE_MIC_KEY")


def test_remote_mic_idle_default():
    assert config.REMOTE_MIC_IDLE_S == 2.0


def test_room_key_sanitizes_name():
    assert config._room_key("MJ Shin") == "MJ_Shin"
    assert config._room_key("a/b?c#d") == "abcd"
    assert config._room_key("  ") == "jarvis"   # 빈 이름 폴백
    assert config._room_key("회의방") == "회의방"  # 한글 허용
