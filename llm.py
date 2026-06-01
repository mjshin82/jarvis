"""DeepSeek V4 스트리밍 + 문장 단위 청킹.

토큰을 스트리밍으로 받아 문장 종결부호가 보이면 즉시 yield 한다.
→ LLM 이 답을 끝내기 전에 첫 문장부터 TTS로 흘려보낼 수 있다(체감 지연 ↓).
대화 맥락은 self.history 에 누적한다(알렉사식 멀티턴).
"""
import re

from openai import AsyncOpenAI

import config

# 문장 끝으로 볼 부호 (한국어/영어). 이게 나오면 한 문장 flush.
_SENTENCE_END = re.compile(r"[.!?。…？！]\s*$|[\n]")


class LLM:
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
        )
        self.history = [{"role": "system", "content": config.SYSTEM_PROMPT}]

    async def respond(self, user_text: str):
        """async generator: 완성된 문장을 하나씩 yield."""
        self.history.append({"role": "user", "content": user_text})

        full, buf = "", ""
        try:
            stream = await self.client.chat.completions.create(
                model=config.DEEPSEEK_MODEL,
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
