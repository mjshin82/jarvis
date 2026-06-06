# streaming_stt.py
"""일반 대화용 스트리밍 STT — RealtimeSTTAdapter 에 무음 플러시를 켠 얇은 래퍼.

main.py 가 import 하는 공개 이름. recorder 래핑/플러시 로직은 realtime_stt 에 있다.
"""
from realtime_stt import RealtimeSTTAdapter


class StreamingRecognizer(RealtimeSTTAdapter):
    def __init__(self, *, on_partial, on_final, model="small", realtime_model="tiny",
                 language="ko", on_log=print, recorder_factory=None):
        super().__init__(
            on_partial=on_partial, on_final=on_final, model=model,
            realtime_model=realtime_model, language=language, on_log=on_log,
            recorder_factory=recorder_factory, silence_flush=True,
        )
