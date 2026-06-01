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

TOOLS = [{
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
}]


def _split_sentences(text: str):
    if not text:
        return []
    parts = re.split(r"(?<=[.!?。…？！\n])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


class LLM:
    def __init__(self):
        self.backend = config.LLM_BACKEND
        self.client = None
        self.model = None

        if self.backend == "mock":
            print(f"[llm] ⚠️  MOCK 모드: '{config.MOCK_MESSAGE}' 로만 응답합니다.")
        elif self.backend == "remote":
            self.client = AsyncOpenAI(
                api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL,
            )
            self.model = config.DEEPSEEK_MODEL
            print(f"[llm] REMOTE(DeepSeek) 모드: {self.model}")
        elif self.backend == "local":
            self.client = AsyncOpenAI(api_key="ollama", base_url=config.OLLAMA_BASE_URL)
            self.model = config.LOCAL_MODEL
            print(f"[llm] LOCAL(Ollama) 모드: {self.model} @ {config.OLLAMA_BASE_URL}")
        else:
            raise ValueError(f"알 수 없는 LLM_BACKEND: {self.backend!r} (mock|remote|local)")

        self.search = config.SEARCH_ENABLED and self.client is not None
        # 현재 날짜 주입 + 검색 가능 여부 안내
        sys_prompt = config.SYSTEM_PROMPT + f"\n오늘 날짜는 {datetime.now():%Y년 %m월 %d일}이다."
        if self.search:
            sys_prompt += "\n최신·실시간 정보가 필요하면 web_search 도구로 검색해 답한다."
            print("[llm] 웹 검색 도구 활성화 (Serper/구글)")
        self.history = [{"role": "system", "content": sys_prompt}]

    async def _stream_answer(self, messages):
        """messages 로 스트리밍 호출하며 문장 단위 yield. 끝나면 history 에 assistant 기록."""
        full, buf = "", ""
        try:
            stream = await self.client.chat.completions.create(
                model=self.model, messages=messages, stream=True,
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

        if self.backend == "mock":
            self.history.append({"role": "assistant", "content": config.MOCK_MESSAGE})
            yield config.MOCK_MESSAGE
            return

        # 검색 비활성 → 곧바로 스트리밍
        if not self.search:
            async for s in self._stream_answer(self.history):
                yield s
            return

        # 1차: 도구 호출 감지 (비스트리밍)
        first = await self.client.chat.completions.create(
            model=self.model, messages=self.history, tools=TOOLS,
        )
        msg = first.choices[0].message

        if not msg.tool_calls:
            # 도구 불필요 → 받은 답을 문장 단위로
            content = (msg.content or "").strip()
            self.history.append({"role": "assistant", "content": content or "(무응답)"})
            for s in _split_sentences(content):
                yield s
            return

        # 도구 호출 → 검색 실행 후 결과 기반 답변
        self.history.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })
        yield config.SEARCH_FILLER   # 검색 동안 먼저 읽어줄 멘트
        for tc in msg.tool_calls:
            if tc.function.name == "web_search":
                try:
                    q = json.loads(tc.function.arguments).get("query", "")
                except Exception:
                    q = ""
                result = await web_search(q)
            else:
                result = "지원하지 않는 도구입니다."
            self.history.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        async for s in self._stream_answer(self.history):
            yield s
