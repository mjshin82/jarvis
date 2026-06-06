"""LLM 응답 — 스트리밍 + 문장 청킹 + 웹 검색 도구 호출.

토큰을 스트리밍으로 받아 문장 종결부호가 보이면 즉시 yield → 첫 문장부터 TTS.
대화 맥락은 self.history 에 누적(멀티턴).

백엔드(config.LLM_BACKEND): mock | remote(DeepSeek) | local(Ollama)
검색: config.SEARCH_ENABLED 면 web_search 도구를 제공 → 모델이 필요시 호출하면
      실제 검색 결과를 다시 넣어 최종 답변을 생성한다(2단계).
"""
import json
import re
from datetime import datetime

from openai import AsyncOpenAI

import config
import settings
import wordbook
from search import web_search
from music import play_music, stop_music
from simulation import MODE
import music_intent

# 문장 끝으로 볼 부호 (한국어/영어).
_SENTENCE_END = re.compile(r"[.!?。…？！]\s*$|[\n]")
# Qwen3 등의 추론 블록. /no_think 로도 안 막힐 때 후처리로 제거.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

_TOOL_WEB_SEARCH = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "인터넷에서 최신·실시간 정보를 검색한다. 모르는 사실, 최근 사건, "
                       "특정 대상(게임/인물/제품 등) 조회가 필요할 때 사용.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "검색어"}},
            "required": ["query"],
        },
    },
}

_TOOL_PLAY_MUSIC = {
    "type": "function",
    "function": {
        "name": "play_music",
        "description": "유튜브에서 노래/음악/영상을 찾아 브라우저로 재생한다. "
                       "사용자가 '○○ 틀어줘/들려줘/재생해줘' 처럼 음악·영상 재생을 요청할 때 사용.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "곡/아티스트/영상 검색어"}},
            "required": ["query"],
        },
    },
}

_TOOL_STOP_MUSIC = {
    "type": "function",
    "function": {
        "name": "stop_music",
        "description": "재생 중인 음악/영상을 멈춘다(브라우저의 유튜브 탭을 닫음). "
                       "'꺼줘/멈춰/그만/정지/스톱' 등 재생 중지 요청 시 사용.",
        "parameters": {"type": "object", "properties": {}},
    },
}

def _split_sentences(text: str):
    if not text:
        return []
    parts = re.split(r"(?<=[.!?。…？！\n])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


class LLM:
    def __init__(self):
        self._mock = (config.LLM_BACKEND == "mock")
        self.backend = "mock"
        self.client = None
        self.model = None
        self.extra = {}
        if self._mock:
            print(f"[llm] ⚠️  MOCK 모드: '{config.MOCK_MESSAGE}' 로만 응답합니다.")
        else:
            self.set_backend(settings.get("conversation_llm_backend"))

        # 사용 가능한 도구 구성 (클라이언트 있을 때만)
        self.tools = []
        base = config.SYSTEM_PROMPT + wordbook.load_system_hint()
        if self.client is not None:
            if config.SEARCH_ENABLED:
                self.tools.append(_TOOL_WEB_SEARCH)
                base += "\n최신·실시간 정보가 필요하면 web_search 도구로 검색해 답한다."
            if config.MUSIC_ENABLED:
                self.tools.append(_TOOL_PLAY_MUSIC)
                self.tools.append(_TOOL_STOP_MUSIC)
                base += "\n음악/영상 재생은 play_music, 중지는 stop_music 도구를 사용한다."
        self.use_tools = bool(self.tools)
        if self.use_tools:
            names = ", ".join(t["function"]["name"] for t in self.tools)
            print(f"[llm] 도구 활성화: {names}")
        # 날짜·시간은 매 응답마다 갱신(_refresh_now)하므로 base 만 보관
        self.base_system = base
        self.history = [{"role": "system", "content": base}]

    def set_backend(self, backend: str) -> None:
        """대화 LLM 백엔드 전환(deepseek=remote / local=ollama). mock 이면 무시.
        전제 미충족(예: deepseek 키 없음) 시 사용 가능한 쪽으로 폴백."""
        if self._mock:
            return
        want = "remote" if backend == "deepseek" else "local"
        has_remote = bool(config.DEEPSEEK_API_KEY and config.DEEPSEEK_API_KEY != "sk-your-key-here")
        if want == "remote" and not has_remote:
            want = "local"
        if want == "remote":
            self.client = AsyncOpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL)
            self.model = config.DEEPSEEK_MODEL
            self.extra = {}
            self.backend = "remote"
        else:
            self.client = AsyncOpenAI(api_key="ollama", base_url=config.OLLAMA_BASE_URL)
            self.model = config.LOCAL_MODEL
            self.extra = {"keep_alive": config.OLLAMA_KEEP_ALIVE}
            self.backend = "local"
        print(f"[llm] 대화 LLM 백엔드: {self.backend} ({self.model})")

    def _refresh_now(self):
        """현재 날짜·시간을 시스템 메시지에 반영(매 턴 갱신). 날짜/시간 질문 대응."""
        now = datetime.now()
        self.history[0]["content"] = (
            self.base_system
            + f"\n지금은 {now:%Y년 %m월 %d일} {_WEEKDAYS[now.weekday()]}요일 "
              f"{now:%H시 %M분}이다. 날짜·시간 질문은 검색하지 말고 이 정보로 답한다."
        )

    async def _fast_path(self, intent: tuple, user_text: str):
        """LLM 우회: 의도가 명백한 음악 명령을 곧장 실행. LLM 호출 0회.
        history 에는 user/assistant 쌍을 남겨 후속 대화 흐름이 자연스럽게."""
        name, params = intent
        self.history.append({"role": "user", "content": user_text})
        if name == "music_stop":
            yield config.STOP_FILLER
            result = await stop_music()
            self.history.append({"role": "assistant", "content": config.STOP_FILLER})
            # 결과 멘트는 tool 결과를 그대로 짧게 알려주는 게 자연스러움
            if result and result != "음악을 껐습니다(탭 0개).":
                yield result
        elif name == "music_play":
            yield config.MUSIC_FILLER
            result = await play_music(params.get("query", ""))
            self.history.append({"role": "assistant", "content": config.MUSIC_FILLER})
            if result:
                yield result

    async def _run_tool(self, name, args_json):
        try:
            args = json.loads(args_json) if args_json else {}
        except Exception:
            args = {}
        if name == "web_search":
            return await web_search(args.get("query", ""))
        if name == "play_music":
            return await play_music(args.get("query", ""))
        if name == "stop_music":
            return await stop_music()
        return "지원하지 않는 도구입니다."

    async def warmup(self):
        """시작 시 모델을 메모리에 미리 적재 → 첫 실제 응답 지연 제거."""
        if self.client is None:
            return
        try:
            await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "안녕"}],
                max_tokens=1,
                extra_body=self.extra,
            )
            print("[llm] 모델 예열 완료")
        except Exception as e:
            print(f"[llm] 예열 생략: {e}")

    async def _stream_answer(self, messages):
        """messages 로 스트리밍 호출하며 문장 단위 yield. 끝나면 history 에 assistant 기록.
        Qwen3 등이 흘리는 <think>...</think> 블록은 통째로 버퍼에 모았다가 닫힐 때 제거."""
        full, buf = "", ""
        in_think = False           # <think> 가 열려있는 중인지
        try:
            stream = await self.client.chat.completions.create(
                model=self.model, messages=messages, stream=True, extra_body=self.extra,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if not delta:
                    continue
                buf += delta
                # think 블록 처리: 열림/닫힘 사이의 토큰은 yield 하지 않는다
                while True:
                    if in_think:
                        end = buf.lower().find("</think>")
                        if end < 0:
                            buf = ""           # 닫힐 때까지 통째로 폐기
                            break
                        buf = buf[end + len("</think>"):]
                        in_think = False
                    else:
                        start = buf.lower().find("<think>")
                        if start < 0:
                            break
                        # think 이전 텍스트는 살리고 그 뒤부터는 폐기 모드로
                        kept = buf[:start]
                        buf = buf[start + len("<think>"):]
                        full += kept
                        in_think = True
                        # kept 안에 문장 종결이 있으면 즉시 yield
                        if kept and _SENTENCE_END.search(kept):
                            s = kept.strip()
                            if s:
                                yield s
                            kept = ""
                if in_think:
                    continue
                # 일반 경로: 문장 종결 보이면 잘라서 yield
                if _SENTENCE_END.search(buf):
                    s = buf.strip()
                    full += buf
                    buf = ""
                    if s:
                        yield s
            if buf.strip():
                full += buf
                yield buf.strip()
        finally:
            cleaned = _THINK_BLOCK.sub("", full).strip()
            self.history.append({"role": "assistant", "content": cleaned or "(중단됨)"})

    async def respond(self, user_text: str):
        """async generator: 완성된 문장을 하나씩 yield."""
        # Fast-path: 명백한 음악 명령은 LLM 호출 없이 곧장 도구 실행
        if self.use_tools and config.MUSIC_ENABLED:
            intent = music_intent.classify(user_text)
            if intent:
                async for s in self._fast_path(intent, user_text):
                    yield s
                return

        self.history.append({"role": "user", "content": user_text})

        if self.backend == "mock":
            self.history.append({"role": "assistant", "content": config.MOCK_MESSAGE})
            yield config.MOCK_MESSAGE
            return

        self._refresh_now()   # 현재 날짜·시간을 시스템 프롬프트에 반영

        # 도구 비활성 → 곧바로 스트리밍
        if not self.use_tools:
            async for s in self._stream_answer(self.history):
                yield s
            return

        # 1차: 도구 호출 감지 (비스트리밍)
        first = await self.client.chat.completions.create(
            model=self.model, messages=self.history, tools=self.tools, extra_body=self.extra,
        )
        msg = first.choices[0].message

        if not msg.tool_calls:
            content = _THINK_BLOCK.sub("", msg.content or "").strip()
            self.history.append({"role": "assistant", "content": content or "(무응답)"})
            for s in _split_sentences(content):
                yield s
            return

        names = [tc.function.name for tc in msg.tool_calls]

        # 일반 도구 호출 → 실행 후 결과 기반 답변
        self.history.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })
        # 도구 종류에 맞는 멘트를 먼저 읽어 침묵을 메움
        if "play_music" in names:
            yield config.MUSIC_FILLER
        elif "stop_music" in names:
            yield config.STOP_FILLER
        elif "web_search" in names:
            yield config.SEARCH_FILLER
        for tc in msg.tool_calls:
            result = await self._run_tool(tc.function.name, tc.function.arguments)
            self.history.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        async for s in self._stream_answer(self.history):
            yield s
