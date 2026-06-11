import config


def test_iot_defaults():
    # IOT_ENABLED 는 기본 비활성(브로커 없이도 jarvis 가 떠야 하므로)
    assert config.IOT_ENABLED is False
    assert config.MQTT_PORT == 1883
    assert config.MQTT_HOST == ""
    assert isinstance(config.IOT_FILLER, str) and config.IOT_FILLER
    assert config.IOT_CONFIG_PATH.endswith("iot.yaml")
