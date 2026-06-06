import languages


def test_normalize_alias_and_dedup():
    assert languages.normalize(["jp", "ko", "jp"]) == ["ja", "ko"]


def test_normalize_str_input():
    assert languages.normalize("ko, en, ja") == ["ko", "en", "ja"]


def test_normalize_empty_and_invalid_to_default():
    assert languages.normalize([]) == ["ko", "en"]
    assert languages.normalize("") == ["ko", "en"]
    assert languages.normalize(["xx", "zz"]) == ["ko", "en"]


def test_names_and_gladia():
    assert languages.names(["jp"]) == ["Japanese"]
    assert languages.gladia_codes(["jp", "ko"]) == ["ja", "ko"]
