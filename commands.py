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
        ctx["log"]("사용법: /<명령> [인자]. 도움말은 /help.")
        return
    cmd = _REGISTRY.get(name)
    if cmd is None:
        ctx["log"](f"알 수 없는 명령: /{name}. 도움말은 /help.")
        return
    try:
        await cmd.handler(args, ctx)
    except Exception as e:
        ctx["log"](f"명령 실행 중 오류: /{name} → {e}")


# --- 기본 명령어 ---

@command("help", help="사용 가능한 명령 목록")
async def _help(args: str, ctx: dict):
    log = ctx["log"]
    log("사용 가능한 명령:")
    for cmd in sorted(_REGISTRY.values(), key=lambda c: c.name):
        usage = f" {cmd.usage}" if cmd.usage else ""
        log(f"  /{cmd.name}{usage}  — {cmd.help}")


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


@command("mic", help="듣기 모드로 전환 ('Hey Jarvis' 호출과 동일)")
async def _mic(args: str, ctx: dict):
    trigger = ctx.get("trigger_wake")
    if trigger is None:
        ctx["log"]("이 환경에서는 마이크 트리거를 사용할 수 없습니다.")
        return
    await trigger()
    # 명령이 직접 상태(LISTENING)를 잡았음을 알려 후속 idle 을 막는다.
    ctx["handled_state"] = True
