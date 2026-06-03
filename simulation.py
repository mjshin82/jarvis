"""모드 매니저 — 평상시 + 번역 모드(/trans) 상태 보관.

회의 모드(/meet) 는 별도 MeetingSession 이 관리하지만, STT 언어 분기 같은
'평상시가 아닌 상태'는 여기 MODE 에 모아둔다. (audio_io.py, stt.py 에서
모드별 동작을 결정할 때 참조)

번역 모드 (/trans):
  발화를 LLM 으로 한국어로 옮긴다. /stop 까지 무한 듣기. 호출어 비활성.
"""
import config


class _Mode:
    """전역 모드 상태. 번역 모드 켜짐/꺼짐을 추적."""

    def __init__(self):
        self.translate: bool = False
        self.translate_src_lang: str | None = None   # None = 자동 감지

    # --- 번역 모드 ---
    def is_translate(self) -> bool:
        return self.translate

    def start_translate(self, src_lang: str | None = None) -> None:
        self.translate = True
        self.translate_src_lang = src_lang

    def end_translate(self) -> None:
        self.translate = False
        self.translate_src_lang = None

    # --- 다른 모듈이 매 호출마다 참조 ---
    def stt_initial_prompt(self) -> str | None:
        """현재 상태에 특화된 STT 컨디셔닝. 평상시는 wordbook 이 기본."""
        return None

    def stt_lang(self) -> str | None:
        """STT 언어. 번역 모드는 None(자동 감지) 가능."""
        if self.translate:
            return self.translate_src_lang
        return config.WHISPER_LANG

    def tts_lang(self) -> str:
        return config.SUPERTONIC_LANG

    def tts_voice(self) -> str:
        return config.SUPERTONIC_VOICE


MODE = _Mode()
