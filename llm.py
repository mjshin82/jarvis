"""DeepSeek V4 스트리밍 + 문장 단위 청킹.

토큰을 스트리밍으로 받아 문장 종결부호가 보이면 즉시 yield 한다.
→ LLM 이 답을 끝내기 전에 첫 문장부터 TTS로 흘려보낼 수 있다(체감 지연 ↓).
대화 맥락은 self.history 에 누적한다(알렉사식 멀티턴).

백엔드는 config.LLM_BACKEND 로 선택한다:
  mock   : 실제 호출 없이 고정 메시지(MOCK_MESSAGE)로 응답 — 비용 0
  remote : DeepSeek V4 API
  local  : 로컬 Ollama (OpenAI 호환). base_url/model 만 다르고 스트리밍 경로는 공유.
"""
import re

from openai import AsyncOpenAI

import config

# 문장 끝으로 볼 부호 (한국어/영어). 이게 나오면 한 문장 flush.
_SENTENCE_END = re.compile(r"[.!?。…？！]\s*$|[\n]")


class LLM:
    def __init__(self):
        self.backend = config.LLM_BACKEND
        self.client = None
        self.model = None

        if self.backend == "mock":
            print(f"[llm] ⚠️  MOCK 모드: '{config.MOCK_MESSAGE}' 로만 응답합니다.")
        elif self.backend == "remote":
            self.client = AsyncOpenAI(
                api_key=config.DEEPSEEK_API_KEY,
                base_url=config.DEEPSEEK_BASE_URL,
            )
            self.model = config.DEEPSEEK_MODEL
            print(f"[llm] REMOTE(DeepSeek) 모드: {self.model}")
        elif self.backend == "local":
            # Ollama OpenAI 호환 엔드포인트. api_key 는 형식상 필요(아무 값).
            self.client = AsyncOpenAI(
                api_key="ollama",
                base_url=config.OLLAMA_BASE_URL,
            )
            self.model = config.LOCAL_MODEL
            print(f"[llm] LOCAL(Ollama) 모드: {self.model} @ {config.OLLAMA_BASE_URL}")
        else:
            raise ValueError(f"알 수 없는 LLM_BACKEND: {self.backend!r} (mock|remote|local)")

        self.history = [{"role": "system", "content": config.SYSTEM_PROMPT}]

    async def respond(self, user_text: str):
        """async generator: 완성된 문장을 하나씩 yield."""
        self.history.append({"role": "user", "content": user_text})

        # --- MOCK 모드: 실제 호출 없이 항상 고정 메시지 응답 ---
        if self.backend == "mock":
            self.history.append({"role": "assistant", "content": config.MOCK_MESSAGE})
            yield config.MOCK_MESSAGE
            return

        full, buf = "", ""
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=self.history,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if not delta:
                    continue
                buf += delta
                full += delta
                if _SENTENCE_END.search(buf):
                    sentence = buf.strip()
                    buf = ""
                    if sentence:
                        yield sentence

            if buf.strip():            # 마지막 자투리
                yield buf.strip()
        finally:
            # 정상 종료든 barge-in 취소든, '말한 만큼'을 대화기록에 남겨 일관성 유지
            self.history.append(
                {"role": "assistant", "content": full.strip() or "(중단됨)"}
            )
