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
import wordbook
from search import web_search
from music import play_music, stop_music
from simulation import (
    MODE, list_scenarios, PRACTICE_MODES,
    classify_choice, ST_ASKING, ST_WAITING_TRY, ST_WAITING_CHOICE,
)
import coach

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

_TOOL_START_SIM = {
    "type": "function",
    "function": {
        "name": "start_simulation",
        "description": (
            "영어(또는 다른 언어) 대화 연습/롤플레이 모드로 진입한다. "
            "사용자가 연습/시뮬 진입 의도를 보이면(예: '영어 대화 연습하려고 해', "
            "'영어로 얘기하자', '영어 연습 하자', '롤플레이 해줘', '미팅 연습', "
            "'예시 보면서 연습하자', '랜덤으로 질문해줘', '실전처럼 해보자') 무조건 호출. "
            "단순히 '영어로 답해줘' 같은 일회성 요청은 호출하지 않는다. "
            "호출 후 시스템이 자동으로 시나리오·모드로 전환하므로 별도 응답은 짧게 한 줄만."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scenario": {
                    "type": "string",
                    "description": "시나리오 키(scenarios/<key>.md). 지정 없으면 빈 값 → 기본값.",
                },
                "mode": {
                    "type": "string",
                    "enum": list(PRACTICE_MODES),
                    "description": (
                        "연습 방식. 'guided'=질문+답변예시+사용자시도+피드백 5단계 "
                        "(트리거: '예시 보면서', '가이드 받으면서', '답변 예시 알려주면서'). "
                        "'random'=랜덤 질문 빠르게 (트리거: '랜덤으로', '무작위로 물어봐'). "
                        "'live'=실전 시뮬레이션 인사~마무리 자유 대화 (트리거: '실전처럼', "
                        "'진짜 미팅처럼', '롤플레이'). 사용자가 명시하지 않으면 'live'."
                    ),
                },
            },
        },
    },
}

_TOOL_END_SIM = {
    "type": "function",
    "function": {
        "name": "end_simulation",
        "description": (
            "현재 연습/시뮬레이션 모드를 종료하고 평상시 비서로 돌아간다. "
            "연습/시뮬 활성 중에 사용자가 종료 의도를 보이면(예: '그만', '그만하자', "
            "'연습 그만하자', '끝내자', '한국어로 돌아가자', '평소 모드로', "
            "'Let's stop', 'Stop practice', 'End simulation') 무조건 이 도구를 호출."
        ),
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

        # local: 모델을 메모리에 유지(콜드 로드 방지). 매 호출에 keep_alive 전달.
        self.extra = {"keep_alive": config.OLLAMA_KEEP_ALIVE} if self.backend == "local" else {}

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
            if config.SIM_ENABLED:
                self.tools.append(_TOOL_START_SIM)
                self.tools.append(_TOOL_END_SIM)
                names = ", ".join(list_scenarios()) or "(없음)"
                base += (
                    "\n사용자가 영어 미팅/연습/롤플레이 시뮬레이션 진입을 요청하면 "
                    "start_simulation 을, 종료를 요청하면 end_simulation 을 호출한다. "
                    f"사용 가능한 시나리오: {names}."
                )
        self.use_tools = bool(self.tools)
        if self.use_tools:
            names = ", ".join(t["function"]["name"] for t in self.tools)
            print(f"[llm] 도구 활성화: {names}")
        # 날짜·시간은 매 응답마다 갱신(_refresh_now)하므로 base 만 보관
        self.base_system = base
        self.history = [{"role": "system", "content": base}]

    def _refresh_now(self):
        """현재 날짜·시간을 시스템 메시지에 반영(매 턴 갱신).
        시뮬 모드일 때는 시나리오의 페르소나 프롬프트를 그대로 시스템으로 쓴다."""
        sim = MODE.system_prompt()
        if sim is not None:
            # 시뮬 모드: 페르소나 통째로 + 도구는 종료용만 남기는 의미로 짧은 메타 한 줄
            self.history[0]["content"] = (
                sim + "\n\n(System note: If the user clearly asks to quit or end the simulation, "
                "call the end_simulation tool. Otherwise stay fully in character.)"
            )
            return
        now = datetime.now()
        self.history[0]["content"] = (
            self.base_system
            + f"\n지금은 {now:%Y년 %m월 %d일} {_WEEKDAYS[now.weekday()]}요일 "
              f"{now:%H시 %M분}이다. 날짜·시간 질문은 검색하지 말고 이 정보로 답한다."
        )

    def _enter_simulation(self, key: str | None, mode: str | None) -> tuple[str, str | None]:
        """시뮬 모드 진입. (안내 멘트, opening or None) 반환.
        history 를 비워 시나리오·모드 프롬프트가 깨끗한 컨텍스트에서 시작하게 한다.
        guided/random 은 opening 을 생략 — 코치가 곧장 첫 질문을 던지도록 LLM 에 맡김."""
        scenario_key = key or config.SIM_DEFAULT_SCENARIO
        practice = mode if mode in PRACTICE_MODES else "live"
        sc = MODE.start(scenario_key, practice=practice)
        if sc is None:
            avail = ", ".join(list_scenarios()) or "(없음)"
            return (f"시나리오 '{scenario_key}' 를 찾지 못했습니다. 사용 가능: {avail}", None)
        # history 리셋. _refresh_now 가 시나리오+모드 프롬프트로 채움
        self.history = [{"role": "system", "content": ""}]
        self._refresh_now()
        label = {"guided": "가이드 연습", "random": "랜덤 질문", "live": "실전 시뮬레이션"}[practice]
        notice = f"{config.SIM_ENTER_FILLER} ({sc.name} · {label})"
        # live 만 미리 정해둔 영어 오프닝을 읽고 시작. guided/random 은 모델이 첫 턴을 만들게 둠.
        opening = sc.opening if practice == "live" else None
        return notice, opening

    def _exit_simulation(self) -> str:
        sc = MODE.end()
        self.history = [{"role": "system", "content": self.base_system}]
        return config.SIM_EXIT_FILLER + (f" ({sc.name} 종료)" if sc else "")

    async def _coach_ask_new(self):
        """새 질문을 만들고 TURN A 출력. (질문 / 예시 답변: / 한번 직접 답해보세요.)"""
        sc = MODE.scenario
        data = await coach.make_question(
            self.client, self.model,
            sc.system_prompt if sc else "",
            MODE.asked_topics, self.extra,
        )
        MODE.current_question = data["question"]
        MODE.current_example = data["example"]
        if data.get("topic"):
            MODE.asked_topics.append(data["topic"])
        MODE.state = ST_WAITING_TRY
        yield data["question"]
        # random 모드는 예시 없이 곧장 사용자 답을 기다림
        if MODE.practice == "guided":
            yield f"{coach.EXAMPLE_PREFIX} {data['example']}"
            yield coach.TRY_PROMPT_KO

    async def _coach_evaluate_and_offer(self, attempt: str):
        """TURN B: 평가 한 줄 + (guided 일 때만) 3-선택 안내. 상태 전이."""
        feedback = await coach.evaluate(
            self.client, self.model,
            MODE.current_question or "", attempt, self.extra,
        )
        yield feedback
        if MODE.practice == "guided":
            yield coach.CHOICES_PROMPT
            MODE.state = ST_WAITING_CHOICE
        else:
            # random: 곧장 다음 랜덤 질문
            MODE.state = ST_ASKING
            async for s in self._coach_ask_new():
                yield s

    async def _coach_respond(self, user_text: str):
        """guided/random 상태머신 응답 생성기."""
        choice = classify_choice(user_text)

        # 종료 의도는 상태 무관 즉시 처리
        if choice == "stop":
            yield coach.STOP_PROMPT_KO
            yield self._exit_simulation()
            return

        # 첫 진입 직후처럼 아직 질문이 없으면 ASKING 으로 시작
        if MODE.state == ST_ASKING or MODE.current_question is None:
            async for s in self._coach_ask_new():
                yield s
            return

        if MODE.state == ST_WAITING_TRY:
            # 선택이 잡혀도(예: 'next') 시도 없이 다음으로 가는 게 자연스럽다
            if choice == "next":
                MODE.state = ST_ASKING
                async for s in self._coach_ask_new():
                    yield s
                return
            if choice == "example" and MODE.current_example:
                yield f"{coach.EXAMPLE_PREFIX} {MODE.current_example}"
                yield coach.TRY_AGAIN_PROMPT
                return
            if choice == "again":
                yield coach.TRY_AGAIN_PROMPT
                return
            # 그 외엔 영어 시도로 간주 → 평가
            async for s in self._coach_evaluate_and_offer(user_text):
                yield s
            return

        if MODE.state == ST_WAITING_CHOICE:
            if choice == "next":
                MODE.state = ST_ASKING
                async for s in self._coach_ask_new():
                    yield s
                return
            if choice == "example" and MODE.current_example:
                yield f"{coach.EXAMPLE_PREFIX} {MODE.current_example}"
                yield coach.TRY_AGAIN_PROMPT
                MODE.state = ST_WAITING_TRY
                return
            if choice == "again":
                yield coach.TRY_AGAIN_PROMPT
                MODE.state = ST_WAITING_TRY
                return
            # 선택이 안 잡히면 새 시도로 간주하고 다시 평가
            async for s in self._coach_evaluate_and_offer(user_text):
                yield s
            return

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
        # guided/random 활성 중에는 일반 LLM 흐름을 건너뛰고 상태머신이 응답을 만든다.
        # (live 와 평상시는 기존 흐름 유지)
        if MODE.active() and MODE.practice in ("guided", "random") and self.client is not None:
            async for s in self._coach_respond(user_text):
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
            # 폴백: 모델이 도구 호출 대신 도구 이름만 평문으로 흘릴 때 그대로 실행.
            # 시뮬 종료처럼 의도가 명백한 케이스에서 일관성↑.
            stripped = content.strip().strip("'\"`").lower()
            if MODE.active() and stripped == "end_simulation":
                yield self._exit_simulation()
                return
            self.history.append({"role": "assistant", "content": content or "(무응답)"})
            for s in _split_sentences(content):
                yield s
            return

        # 시뮬 모드 진입/탈출은 즉시 상태 전환 + 정해진 안내 멘트만. (모델 재호출 X)
        # 진입 후 같은 턴에 모델을 다시 부르면 페르소나가 채 적용되기 전이라 어색해진다.
        names = [tc.function.name for tc in msg.tool_calls]
        if "start_simulation" in names:
            tc = next(t for t in msg.tool_calls if t.function.name == "start_simulation")
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except Exception:
                args = {}
            notice, opening = self._enter_simulation(args.get("scenario"), args.get("mode"))
            yield notice
            if opening:
                yield opening
                return
            # guided/random 은 코드 상태머신이 첫 라운드의 TURN A 를 만든다.
            if MODE.active() and MODE.practice in ("guided", "random"):
                async for s in self._coach_ask_new():
                    yield s
            return
        if "end_simulation" in names:
            yield self._exit_simulation()
            return

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
