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
from search import web_search

# 문장 끝으로 볼 부호 (한국어/영어).
_SENTENCE_END = re.compile(r"[.!?。…？！]\s*$|[\n]")
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
    def __init__(self, backend=None):
        self.backend = backend
        self._llm_backend = config.LLM_BACKEND
        self.client = None
        self.model = None

        if self._llm_backend == "mock":
            print(f"[llm] ⚠️  MOCK 모드: '{config.MOCK_MESSAGE}' 로만 응답합니다.")
        elif self._llm_backend == "remote":
            self.client = AsyncOpenAI(
                api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL,
            )
            self.model = config.DEEPSEEK_MODEL
            print(f"[llm] REMOTE(DeepSeek) 모드: {self.model}")
        elif self._llm_backend == "local":
            self.client = AsyncOpenAI(api_key="ollama", base_url=config.OLLAMA_BASE_URL)
            self.model = config.LOCAL_MODEL
            print(f"[llm] LOCAL(Ollama) 모드: {self.model} @ {config.OLLAMA_BASE_URL}")
        else:
            raise ValueError(f"알 수 없는 LLM_BACKEND: {self._llm_backend!r} (mock|remote|local)")

        # local: 모델을 메모리에 유지(콜드 로드 방지). 매 호출에 keep_alive 전달.
        self.extra = {"keep_alive": config.OLLAMA_KEEP_ALIVE} if self._llm_backend == "local" else {}

        # 사용 가능한 도구 구성 (클라이언트 있을 때만)
        self.tools = []
        base = config.SYSTEM_PROMPT
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
        self.last_tool_names = []   # 직전 respond() 에서 호출된 도구 이름들

    def _refresh_now(self):
        """현재 날짜·시간을 시스템 메시지에 반영(매 턴 갱신). 날짜/시간 질문 대응."""
        now = datetime.now()
        self.history[0]["content"] = (
            self.base_system
            + f"\n지금은 {now:%Y년 %m월 %d일} {_WEEKDAYS[now.weekday()]}요일 "
              f"{now:%H시 %M분}이다. 날짜·시간 질문은 검색하지 말고 이 정보로 답한다."
        )

    async def _run_tool(self, name, args_json):
        try:
            args = json.loads(args_json) if args_json else {}
        except Exception:
            args = {}
        if name == "web_search":
            return await web_search(args.get("query", ""))
        if name == "play_music":
            return await self.backend.play_music(args.get("query", ""))
        if name == "stop_music":
            return await self.backend.stop_music()
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
        """messages 로 스트리밍 호출하며 문장 단위 yield. 끝나면 history 에 assistant 기록."""
        full, buf = "", ""
        try:
            stream = await self.client.chat.completions.create(
                model=self.model, messages=messages, stream=True, extra_body=self.extra,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if not delta:
                    continue
                buf += delta
                full += delta
                if _SENTENCE_END.search(buf):
                    s = buf.strip()
                    buf = ""
                    if s:
                        yield s
            if buf.strip():
                yield buf.strip()
        finally:
            self.history.append({"role": "assistant", "content": full.strip() or "(중단됨)"})

    async def respond(self, user_text: str):
        """async generator: 완성된 문장을 하나씩 yield."""
        self.history.append({"role": "user", "content": user_text})
        self.last_tool_names = []

        if self._llm_backend == "mock":
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
            # 도구 불필요 → 받은 답을 문장 단위로
            content = (msg.content or "").strip()
            self.history.append({"role": "assistant", "content": content or "(무응답)"})
            for s in _split_sentences(content):
                yield s
            return

        # 도구 호출 → 실행 후 결과 기반 답변
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
        names = [tc.function.name for tc in msg.tool_calls]
        self.last_tool_names = names
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
