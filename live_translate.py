"""회의 모드 — RealtimeSTT 로 실시간 자막을 받고, 발화 단위로 양방향 번역.

평상시 STT(faster-whisper, sd 기반)와 별개. /meet 진입 시에만 활성화하고
종료 시 깔끔히 shutdown. 메모리·CPU 부담이 큰 라이브러리라 회의 동안만 띄운다.

양방향 자동 분기:
  - 한국어 발화 → 영어로 번역 (내가 한 말을 상대에게)
  - 그 외 발화  → 한국어로 번역 (상대가 한 말을 나에게)

흐름:
  시작 → RealtimeSTT 시작 (워드북 initial_prompt 주입)
       → 부분 결과는 status 영역에 흘림
       → 발화 끝나면 🧑 원문 + 🌐 번역 한 쌍 출력 (방향 자동 결정)
  종료 → RealtimeSTT shutdown
"""
import asyncio
import sys
from dataclasses import dataclass, field

import numpy as np
from openai import AsyncOpenAI

import config
import coach
import wordbook
import settings
from realtime_stt import RealtimeSTTAdapter, to_pcm16


# --- 회의 메타 입력 흐름 ---

@dataclass
class MeetingMeta:
    """회의 시작 전 사용자에게 받는 메타 정보."""
    partner_name: str = ""
    partner_lang: str = ""
    my_name: str = ""
    my_lang: str = ""
    title: str = ""
    vocabulary: list = field(default_factory=list)   # STT 보강 단어

    @property
    def key(self) -> str:
        # 방 key 는 이름 기반 공용 ROOM_KEY (jarvis 1개). 자막·원격 마이크가 같은 방.
        return config.ROOM_KEY


# 메타 입력 단계 — title 과 vocabulary 두 단계.
# my_name 은 config.USER_NAME 에서 기본값, partner/my 의 언어는 LLM 이 자동 분기.
_META_STEPS = (
    ("title", "회의 제목을 입력하세요 (Enter=기본)"),
    ("vocabulary", "워드북 — 쉼표로 구분 (Enter=기본: Jarvis, 이름)"),
)


class MeetingSetup:
    """메타 입력 진행 상태. title → vocabulary 두 단계 입력 후 done.
    my_name 은 config.USER_NAME 으로 자동 채움."""

    def __init__(self, default_my_name: str = "Concode"):
        self._default_vocab = ["Jarvis", default_my_name]
        self.meta = MeetingMeta(my_name=default_my_name, title="회의",
                                vocabulary=list(self._default_vocab))
        self.step_index = 0

    @property
    def done(self) -> bool:
        return self.step_index >= len(_META_STEPS)

    @property
    def prompt(self) -> str:
        return _META_STEPS[self.step_index][1] if not self.done else ""

    def submit(self, value: str) -> None:
        """현재 단계 답 저장 후 다음 단계로. title/vocabulary 명시 처리(빈 입력→기본)."""
        key, _ = _META_STEPS[self.step_index]
        v = value.strip()
        if key == "title":
            self.meta.title = v or "회의"
        elif key == "vocabulary":
            if v:
                self.meta.vocabulary = [w.strip() for w in v.split(",") if w.strip()]
            # 빈 입력이면 기본 vocab 유지
        self.step_index += 1



class MeetingSession:
    """RealtimeSTT 한 인스턴스를 들고 다닌다. 메인 이벤트 루프에서 콜백을
    안전하게 다루기 위해 asyncio.Queue 로 final 텍스트를 넘긴다.

    번역기는 별도 결정:
      - MEET_REMOTE_ENABLED 이고 DEEPSEEK_API_KEY 가 있으면 DeepSeek 직행.
      - 그렇지 않으면 자비스 본체 LLM(local) 으로 폴백.
    어느 쪽이든 시스템 프롬프트는 진입 시 한 번만 빌드해서 매 호출 동일하게
    보낸다 — DeepSeek 자동 prompt caching 활용해 비용 절감."""

    def __init__(self, *, log, set_status, llm,
                 meta: MeetingMeta | None = None,
                 model: str = "small", realtime_model: str = "tiny",
                 language: str = "",
                 ):
        self.log = log
        self.set_status = set_status
        self.llm = llm                       # 자비스 본체 LLM (폴백용)
        self.meta = meta or MeetingMeta()
        self.model = model
        self.realtime_model = realtime_model
        self.language = language
        self._rt = None         # RealtimeSTT 폴백(어댑터)
        self._stt = None        # 스트리밍 STT 백엔드(Gladia)
        self._loop = None
        self._final_q: asyncio.Queue[str | None] | None = None
        self._consumer_task: asyncio.Task | None = None
        self._partial_last = ""
        # 외부 listener (예: 웹 중계). _emit 의 fan-out 대상.
        self._listeners: list = []
        # 회의 진행 동안 보관할 외부 리소스 (예: RelayClient) — stop() 에서 close
        self._relay = None
        # 번역기 (start() 에서 결정)
        self._tx_client = None
        self._tx_model = ""
        self._tx_system = ""
        self._tx_label = ""   # 화면 안내용

    def add_listener(self, callback) -> None:
        """매 _emit 호출 시 callback(kind, text) 가 함께 불린다.
        callback 은 동기 함수든 코루틴 함수든 OK (fire-and-forget)."""
        self._listeners.append(callback)

    def _setup_translator(self) -> None:
        """DeepSeek 우선, 키 없으면 자비스 본체 LLM 로 폴백.
        시스템 프롬프트는 한 번만 빌드 — 매 호출 동일하게 보내야 캐시 히트."""
        glossary = wordbook.load_glossary_lines(path=wordbook.MEET_PATH)
        # 메타가 있으면 컨텍스트에 양측 정보를 명시 → 번역 방향성/존중 톤 안정성↑
        ctx_lines = [config.MEET_CONTEXT.strip()]
        m = self.meta
        if m.partner_name or m.my_name:
            parts = []
            if m.partner_name:
                p = m.partner_name + (f" (speaks {m.partner_lang})" if m.partner_lang else "")
                parts.append(f"Counterpart: {p}")
            if m.my_name:
                me = m.my_name + (f" (speaks {m.my_lang})" if m.my_lang else "")
                parts.append(f"User: {me}")
            ctx_lines.append(" / ".join(parts))
        self._tx_system = coach._build_meet_system_prompt("\n".join(ctx_lines), glossary)

        use_remote = (settings.get("llm_backend") == "deepseek"
                      and config.DEEPSEEK_API_KEY
                      and config.DEEPSEEK_API_KEY != "sk-your-key-here")
        if use_remote:
            self._tx_client = AsyncOpenAI(
                api_key=config.DEEPSEEK_API_KEY,
                base_url=config.DEEPSEEK_BASE_URL,
            )
            self._tx_model = config.MEET_REMOTE_MODEL
            self._tx_label = f"DeepSeek ({self._tx_model})"
        else:
            # 폴백: 자비스 본체 LLM
            self._tx_client = self.llm.client
            self._tx_model = self.llm.model
            self._tx_label = f"local ({self._tx_model})"

    def feed_block(self, block) -> None:
        """MicRouter tap 이 매 블록 호출 — float32 → int16 PCM 으로 활성 STT 백엔드 주입."""
        if self._stt is None and self._rt is None:
            return
        pcm16 = to_pcm16(block)
        if self._stt is not None:
            self._stt.feed_pcm(pcm16)
        else:
            self._rt.feed_pcm16(pcm16)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._final_q = asyncio.Queue()

        # STT 백엔드: 설정 Gladia(+키) 우선, 실패/미설정 시 RealtimeSTT 폴백
        if settings.get("stt_backend") == "gladia" and config.GLADIA_API_KEY:
            from gladia_stt import GladiaSTT
            try:
                langs = [s.strip() for s in config.MEET_GLADIA_LANGUAGES.split(",") if s.strip()]
                self._stt = GladiaSTT(
                    config.GLADIA_API_KEY, model=config.MEET_GLADIA_MODEL, languages=langs,
                    on_partial=self._stt_partial, on_final=self._stt_final, on_log=self.log,
                    vocabulary=self.meta.vocabulary,
                )
                await self._stt.start()
                self.log(f"🎤 회의 STT: Gladia ({config.MEET_GLADIA_MODEL}, {config.MEET_GLADIA_LANGUAGES})")
            except Exception as e:
                self._stt = None
                self.log(f"Gladia 연결 실패 — 로컬 STT 폴백: {e}")

        if self._stt is None:
            wb_prompt = wordbook.load_initial_prompt(path=wordbook.MEET_PATH) or ""
            vocab_str = ", ".join(self.meta.vocabulary)
            prompt = ", ".join(p for p in (wb_prompt, vocab_str) if p) or None
            self._rt = RealtimeSTTAdapter(
                on_partial=self._stt_partial, on_final=self._stt_final,
                model=self.model, realtime_model=self.realtime_model, language=self.language,
                initial_prompt=prompt, on_log=self.log,
            )
            await self._rt.start()

        # 번역기 + final 소비자는 두 백엔드 공통
        self._setup_translator()
        self._consumer_task = asyncio.create_task(self._consume_finals())
        self.log(f"🎤 회의 모드 시작 (번역: {self._tx_label}). 끝내려면 /stop.")

    async def stop(self) -> None:
        if self._rt is not None:
            try:
                await self._rt.close()
            except Exception:
                pass
            self._rt = None
        if self._stt is not None:
            try:
                await self._stt.close()
            except Exception:
                pass
            self._stt = None
        if self._final_q is not None:
            await self._final_q.put(None)
        if self._consumer_task and not self._consumer_task.done():
            try:
                await asyncio.wait_for(self._consumer_task, timeout=2.0)
            except Exception:
                self._consumer_task.cancel()
        self._consumer_task = None
        # 외부 listener 리소스(예: RelayClient) 정리 — end 송신 + 소켓 close
        if self._relay is not None:
            try:
                await self._relay.close()
            except Exception:
                pass
            self._relay = None
        self.log("🎤 회의 모드 종료.")

    # --- 내부 ---

    def _stt_partial(self, text: str) -> None:
        """스트리밍 STT interim — 메인 루프에서 직접 호출(threadsafe 불요)."""
        text = (text or "").strip()
        if not text or text == self._partial_last:
            return
        self._partial_last = text
        self._emit("partial", text)
        self.set_status(f"📝 {text[:80]}")

    def _stt_final(self, text: str) -> None:
        """스트리밍 STT 발화 종료 — 기존 final 큐로(→ source + 번역)."""
        if self._final_q is not None:
            self._final_q.put_nowait((text or "").strip())

    def _emit(self, kind: str, text: str) -> None:
        """회의 이벤트를 콘솔 + 등록된 listener 들로 fan-out.
        kind: 'source' | 'translation_ko' | 'translation_en' | 'info' | 'gap' | 'partial'"""
        # 1) 콘솔 출력 (기존)
        prefix = {
            "source": "🧑",
            "translation_ko": "🌐",
            "translation_en": "🇺🇸",
            "info": "🎤",
            "gap": "",
        }.get(kind, "")
        if kind == "gap":
            self.log("")
        elif kind == "partial":
            pass    # 콘솔엔 partial 안 찍음 (status 영역이 따로 표시)
        elif prefix:
            self.log(f"{prefix} {text}")
        else:
            self.log(text)
        # 2) listener fan-out (fire-and-forget — listener 실패가 회의 막지 않게)
        for cb in self._listeners:
            try:
                result = cb(kind, text)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as e:
                self.log(f"[meet] listener error: {e}")

    async def _consume_finals(self):
        """확정 발화 처리 — 출력 + 양방향 번역. 메인 이벤트 루프에서 안전하게.
        가독성을 위해 두 번째 발화부터는 앞에 빈 줄 한 줄 추가."""
        first = True
        while True:
            item = await self._final_q.get()
            if item is None:
                return
            text = item.strip()
            if not text:
                continue
            text = wordbook.apply_aliases(text, path=wordbook.MEET_PATH)
            try:
                self.set_status(None)
            except Exception:
                pass
            self._partial_last = ""
            if not first:
                self._emit("gap", "")
            first = False
            self._emit("source", text)
            asyncio.create_task(self._translate_bg(text))

    async def _translate_bg(self, text: str):
        """양방향 번역. 시스템 프롬프트에 워드북/맥락/few-shot 이 다 들어있고
        매 호출마다 *정확히 동일* 하게 보내므로 DeepSeek prompt caching 히트."""
        # local 폴백이면 ollama keep_alive 가 필요, remote 는 빈 dict
        extra = self.llm.extra if self._tx_client is self.llm.client else {}
        try:
            out = await coach.translate_meeting(
                self._tx_client, self._tx_model, text, self._tx_system, extra=extra,
            )
        except Exception as ex:
            self.log(f"[meet] translate error: {ex}")
            return
        if out:
            kind = "translation_en" if coach.is_korean(text) else "translation_ko"
            self._emit(kind, out)
