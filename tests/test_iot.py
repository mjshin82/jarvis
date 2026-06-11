import textwrap
import pytest
import iot


SAMPLE = textwrap.dedent("""
    appliances:
      aircon:
        aliases: ["에어컨", "에어콘"]
        commands:
          power:    { topic: "ir/aircon/power",       payload: "ON" }
          set_temp: { topic: "ir/aircon/temperature", payload: "{value}" }
      tv:
        aliases: ["티비", "TV"]
        commands:
          power:  { topic: "ir/tv/power", payload: "TOGGLE" }
""")


@pytest.fixture
def loaded(tmp_path):
    p = tmp_path / "iot.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    iot.load_config(str(p))
    return iot


def test_load_lists_appliances(loaded):
    assert set(loaded.list_appliances()) == {"aircon", "tv"}


def test_resolve_alias(loaded):
    assert loaded.resolve("에어컨") == "aircon"
    assert loaded.resolve("TV") == "tv"          # 대소문자/공백 무시
    assert loaded.resolve(" 티비 ") == "tv"
    assert loaded.resolve("aircon") == "aircon"  # 키 자체도 허용
    assert loaded.resolve("냉장고") is None


def test_commands_for(loaded):
    assert set(loaded.commands_for("aircon")) == {"power", "set_temp"}
    assert loaded.commands_for("없음") == []


def test_resolve_topic_payload_static(loaded):
    topic, payload = loaded.resolve_command("aircon", "power", None)
    assert topic == "ir/aircon/power"
    assert payload == "ON"


def test_resolve_topic_payload_templated(loaded):
    topic, payload = loaded.resolve_command("에어컨", "set_temp", 26)
    assert topic == "ir/aircon/temperature"
    assert payload == "26"


def test_resolve_command_unknown_returns_none(loaded):
    assert loaded.resolve_command("aircon", "없는명령", None) is None
    assert loaded.resolve_command("없는가전", "power", None) is None


def test_missing_file_is_empty(tmp_path):
    iot.load_config(str(tmp_path / "nope.yaml"))
    assert iot.list_appliances() == []
