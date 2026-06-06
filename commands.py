"""슬래시 명령어 디스패처.

콘솔에서 '/'로 시작하는 입력을 LLM 으로 보내지 않고 코드가 직접 처리한다.
새 명령어는 @command 데코레이터로 등록만 하면 끝.

핸들러 시그니처:
    async def handler(args: str, ctx: dict) -> None

  args : '/' 와 명령 이름을 뺀 나머지 문자열 (양옆 공백 제거됨, 비어있을 수 있음)
  ctx  : main.py 가 제공하는 자원 dict. 키:
           'log'         → console.log
           'set_status'  → console.set_status
           'player'      → Player 인스턴스
           'tts'         → TTS 인스턴스
           'request_exit' → callable, 호출 시 프로세스 종료 요청

처리 방식: main.py 의 text_worker 가 일반 입력처럼 큐에서 순서대로 꺼내
dispatch() 를 호출 → 진행 중 응답이 끝난 뒤에 실행 → 큐잉/Esc 정책 그대로 적용.
"""
from dataclasses import dataclass
from typing import Awaitable, Callable

Handler = Callable[[str, dict], Awaitable[None]]


@dataclass
class Command:
    name: str               # 슬래시 뺀 이름 (예: 'bye')
    handler: Handler
    help: str               # 한 줄 설명
    usage: str = ""         # 인자 사용법 (예: '<문장>')


_REGISTRY: dict[str, Command] = {}


def command(name: str, help: str = "", usage: str = ""):
    """명령어 등록 데코레이터. @command('bye', help='종료')"""
    def deco(fn: Handler) -> Handler:
        _REGISTRY[name] = Command(name=name, handler=fn, help=help, usage=usage)
        return fn
    return deco


def is_command(text: str) -> bool:
    """입력이 슬래시 명령인지(맨 앞이 '/')."""
    return text.startswith("/")


def parse(text: str) -> tuple[str, str]:
    """'/cmd 나머지 인자' → ('cmd', '나머지 인자'). 인자는 trim."""
    body = text[1:].lstrip()   # '/' 제거
    if " " in body:
        name, args = body.split(" ", 1)
        return name.lower(), args.strip()
    return body.lower(), ""


async def dispatch(text: str, ctx: dict) -> None:
    """명령 실행. 모르는 명령이면 안내. 핸들러는 예외를 던지지 않게."""
    name, args = parse(text)
    if not name:
        ctx["log"]("사용법: /<명령> [인자]")
        return
    cmd = _REGISTRY.get(name)
    if cmd is None:
        avail = ", ".join(sorted(_REGISTRY))
        ctx["log"](f"알 수 없는 명령: /{name}. 사용 가능: {avail}")
        return
    try:
        await cmd.handler(args, ctx)
    except Exception as e:
        ctx["log"](f"명령 실행 중 오류: /{name} → {e}")


# --- 기본 명령어 ---

@command("bye", help="프로그램 종료")
async def _bye(args: str, ctx: dict):
    ctx["log"]("👋 종료합니다.")
    ctx["request_exit"]()


@command("tts", help="입력 문장을 그대로 읽기", usage="<문장>")
async def _tts(args: str, ctx: dict):
    text = args.strip()
    if not text:
        ctx["log"]("사용법: /tts <읽을 문장>")
        return
    ctx["log"](f"🤖 {text}")
    wav, sr = await ctx["tts"].synth(text)
    await ctx["player"].enqueue(wav, sr)


@command("mic", help="듣기 모드 진입 / 마이크 소스 전환", usage="[phone|system|auto]")
async def _mic(args: str, ctx: dict):
    arg = args.strip().lower()
    if arg in ("phone", "remote", "system", "local", "auto"):
        router = ctx.get("mic_router")
        if router is None:
            ctx["log"]("원격 마이크가 비활성화되어 있습니다 (REMOTE_MIC_ENABLED).")
            return
        mode = {"phone": "remote", "remote": "remote",
                "system": "local", "local": "local", "auto": "auto"}[arg]
        router.set_override(mode)
        label = {"remote": "원격(폰)", "local": "시스템", "auto": "자동"}[mode]
        ctx["log"](f"🎙️ 마이크 소스: {label}")
        return
    # 무인자 → 기존 동작(듣기 모드 진입 = 'Hey Jarvis' 와 동일)
    trigger = ctx.get("trigger_wake")
    if trigger is None:
        ctx["log"]("이 환경에서는 마이크 트리거를 사용할 수 없습니다.")
        return
    await trigger()
    ctx["handled_state"] = True


@command("trans", help="번역 모드 — 발화를 한국어로 옮김 (/stop 까지)",
         usage="[en|ja|...]")
async def _trans(args: str, ctx: dict):
    """선택 인자로 입력 언어 강제 가능. 없으면 자동 감지."""
    starter = ctx.get("start_translate")
    if starter is None:
        ctx["log"]("이 환경에서는 번역 모드를 사용할 수 없습니다.")
        return
    lang = args.strip() or None
    await starter(lang)
    ctx["handled_state"] = True   # LISTENING 상태로 진입했으니 idle 막기


@command("meet", help="회의 모드 — 실시간 자막 + 양방향 번역")
async def _meet(args: str, ctx: dict):
    starter = ctx.get("start_meeting")
    if starter is None:
        ctx["log"]("이 환경에서는 회의 모드를 사용할 수 없습니다.")
        return
    await starter()
    ctx["handled_state"] = True   # 회의 진입 상태로, idle 막기


@command("stop", help="현재 진행 모드(번역/회의) 종료")
async def _stop(args: str, ctx: dict):
    # 회의 모드 우선
    if ctx.get("in_meeting") and ctx["in_meeting"]():
        await ctx["stop_meeting"]()
        return
    stopper = ctx.get("stop_translate")
    if stopper is None:
        ctx["log"]("종료할 모드가 없습니다.")
        return
    await stopper()


@command("reload-settings", help="setting.yaml 재로드")
async def _reload_settings(args: str, ctx: dict):
    import json
    import settings
    settings.load()
    _llm = ctx.get("llm")
    if _llm is not None:
        _llm.set_backend(settings.get("conversation_llm_backend"))
    ctx["log"](f"⚙️ setting.yaml 재로드: {settings.current()}")
    web_pub = ctx.get("web_pub")
    if web_pub is not None:
        web_pub.emit("settings", json.dumps(settings.current()))
