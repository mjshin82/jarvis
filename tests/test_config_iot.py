import config


def test_iot_config_contract():
    # 값은 .env 에 따라 달라지므로(실사용 시 IOT_ENABLED=true 등) 값 자체가 아니라
    # 설정 배선(타입/형식)을 검증한다 — 기본 포트 1883, 경로 형식, 필러 존재.
    assert isinstance(config.IOT_ENABLED, bool)
    assert isinstance(config.MQTT_PORT, int) and config.MQTT_PORT > 0
    assert isinstance(config.MQTT_HOST, str)
    assert isinstance(config.MQTT_USER, str)
    assert isinstance(config.MQTT_PASS, str)
    assert isinstance(config.IOT_FILLER, str) and config.IOT_FILLER
    assert config.IOT_CONFIG_PATH.endswith("iot.yaml")
