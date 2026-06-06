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
import languages
from realtime_stt import RealtimeSTTAdapter, to_pcm16
import hashlib
import secrets
import time
from datetime import datetime


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
    languages: list = field(default_factory=lambda: ["ko", "en"])   # 룸 언어(정규 코드)
    meeting_id: str = ""     # 6자리 hex, 회의 시작 시 발급
    password: str = ""       # 평문(입력/자동). 표시·해시용, DB 미저장
    started_at: str = ""     # ISO8601, 회의 시작 시각

    @property
    def key(self) -> str:
        # 방 key 는 이름 기반 공용 ROOM_KEY (jarvis 1개). 자막·원격 마이크가 같은 방.
        return config.ROOM_KEY


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_meeting_id() -> str:
    """회의별 로컬 유니크 ID. 생성시간 md5 앞 6자리(소문자 hex)."""
    return hashlib.md5(repr(time.time()).encode()).hexdigest()[:6]


def gen_password() -> str:
    """비번 미입력 시 자동 생성(6자리 hex)."""
    return secrets.token_hex(3)


def hash_password(pw: str) -> str:
    return hashlib.sha256((pw or "").encode()).hexdigest()


# 메타 입력 단계 — title 과 vocabulary 두 단계.
# my_name 은 config.USER_NAME 에서 기본값, partner/my 의 언어는 LLM 이 자동 분기.
_META_STEPS = (
    ("title", "회의 제목을 입력하세요 (Enter=기본)"),
    ("languages", "언어 코드 — 쉼표로 (Enter=기본: ko,en)"),
    ("vocabulary", "워드북 — 쉼표로 구분 (Enter=기본: Jarvis, 이름)"),
    ("password", "비번 (Enter=자동 생성)"),
)


class MeetingSetup:
    """메타 입력 진행 상태. title → languages → vocabulary → password 네 단계 입력 후 done.
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
        elif key == "languages":
            self.meta.languages = languages.normalize(v)   # 빈 입력 → DEFAULT(ko,en)
        elif key == "vocabulary":
            if v:
                self.meta.vocabulary = [w.strip() for w in v.split(",") if w.strip()]
            # 빈 입력이면 기본 vocab 유지
        elif key == "password":
            self.meta.password = v    # 빈 값이면 세션 시작 시 자동 생성
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
        self._transcript: list[dict] = []    # [{ts, source, ko, en}] — 회의 기록
        self._tx_tasks: set = set()           # 진행 중 번역 태스크 (종료 시 대기)

    def add_listener(self, callback) -> None:
        """매 _emit 호출 시 callback(kind, text, lang) 가 함께 불린다 (lang 은 번역 언어, 그 외엔 "").
        callback 은 동기 함수든 코루틴 함수든 OK (fire-and-forget)."""
        self._listeners.append(callback)

    def _setup_translator(self) -> None:
        """DeepSeek 우선, 키 없으면 자비스 본체 LLM 로 폴백.
        시스템 프롬프트는 한 번만 빌드 — 매 호출 동일하게 보내야 캐시 히트."""
        glossary = wordbook.load_glossary_lines(path=wordbook.MEET_PATH)
        ctx = config.MEET_CONTEXT.strip()
        self._tx_system = coach.build_multi_system_prompt(
            languages.names(self.meta.languages), ctx, glossary)

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

    def _finalize_meta(self) -> None:
        """회의 시작 시 메타 확정 — id 발급, 비번 미입력 시 자동 생성, 시작 시각 기록."""
        if not self.meta.meeting_id:
            self.meta.meeting_id = new_meeting_id()
        if not self.meta.password:
            self.meta.password = gen_password()
        self.meta.started_at = now_iso()

    def _record_line(self, source: str) -> dict:
        """확정 원문 1줄을 트랜스크립트에 추가하고 entry 반환(번역은 나중에 채움)."""
        entry = {"ts": now_iso(), "source": source, "src_lang": "", "translations": {}}
        self._transcript.append(entry)
        return entry

    def record(self) -> dict:
        """종료 시 저장할 회의 기록. (stop() 후 호출)"""
        return {
            "id": self.meta.meeting_id,
            "password_hash": hash_password(self.meta.password),
            "title": self.meta.title or "회의",
            "started_at": self.meta.started_at,
            "ended_at": now_iso(),
            "languages": list(self.meta.languages),
            "transcript": list(self._transcript),
        }

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
        self._finalize_meta()
        self._transcript = []
        self._loop = asyncio.get_running_loop()
        self._final_q = asyncio.Queue()

        # STT 백엔드: 설정 Gladia(+키) 우선, 실패/미설정 시 RealtimeSTT 폴백
        if settings.get("stt_backend") == "gladia" and config.GLADIA_API_KEY:
            from gladia_stt import GladiaSTT
            try:
                langs = languages.gladia_codes(self.meta.languages)
                self._stt = GladiaSTT(
                    config.GLADIA_API_KEY, model=config.MEET_GLADIA_MODEL, languages=langs,
                    on_partial=self._stt_partial, on_final=self._stt_final, on_log=self.log,
                    vocabulary=self.meta.vocabulary,
                )
                await self._stt.start()
                self.log(f"🎤 회의 STT: Gladia ({config.MEET_GLADIA_MODEL}, {','.join(langs)})")
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
        self.log(f"🎤 회의 시작: {self.meta.title or '회의'} (ID {self.meta.meeting_id})")
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
        # 진행 중 번역 완료 대기 — record() 가 마지막 줄 번역까지 담도록(짧은 타임아웃)
        if self._tx_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tx_tasks, return_exceptions=True), timeout=5.0)
            except asyncio.TimeoutError:
                pass
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

    def _emit(self, kind: str, text: str, lang: str = "") -> None:
        """회의 이벤트를 콘솔 + listener 들로 fan-out. translation 은 lang 동반."""
        flags = {"ko": "🇰🇷", "en": "🇺🇸", "ja": "🇯🇵", "zh": "🇨🇳"}
        prefix = {"source": "🧑", "info": "🎤", "gap": ""}.get(kind, "")
        if kind == "translation":
            prefix = flags.get(lang, "🌐")
        if kind == "gap":
            self.log("")
        elif kind == "partial":
            pass
        elif prefix:
            self.log(f"{prefix} {text}")
        else:
            self.log(text)
        for cb in self._listeners:
            try:
                result = cb(kind, text, lang)
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
            entry = self._record_line(text)
            t = asyncio.create_task(self._translate_bg(text, entry))
            self._tx_tasks.add(t)
            t.add_done_callback(self._tx_tasks.discard)

    async def _translate_bg(self, text: str, entry: dict | None = None):
        """룸의 나머지 모든 언어로 번역(단일 호출). 각 언어를 translation 이벤트로 emit."""
        extra = self.llm.extra if self._tx_client is self.llm.client else {}
        try:
            out = await coach.translate_multi(
                self._tx_client, self._tx_model, text, self._tx_system, extra=extra,
            )
        except Exception as ex:
            self.log(f"[meet] translate error: {ex}")
            return
        for lang, t in out.items():
            if entry is not None:
                entry["translations"][lang] = t
            self._emit("translation", t, lang)
