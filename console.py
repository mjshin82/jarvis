"""콘솔 UI — prompt_toolkit Application 기반 "Claude Code" 스타일.

로그는 위로 흐르고, 화면 하단엔 입력 박스가 고정된다. 입력 박스는 위/아래
가로선으로 감싸 시각적으로 구분되며, 길어지면 자연스럽게 멀티라인으로
확장된다.

키 바인딩
  Enter           제출
  Shift+Enter     줄바꿈 (또는 Alt+Enter)
  Alt+B / Alt+F   단어 단위 이동 (= macOS Option+←/→)
  Alt+Backspace   단어 단위 삭제
  Ctrl+A / Ctrl+E 줄 처음/끝
  ↑ / ↓           멀티라인일 때 줄 이동, 단일 라인일 때 히스토리
  Ctrl+C          요청 취소(빈 줄 제출과 동일), 두 번 누르면 종료
  Ctrl+D          종료

macOS 터미널 설정 팁
  iTerm2: Preferences → Profiles → Keys 에서 "Natural Text Editing" 프리셋
  Terminal.app: Preferences → Profiles → Keyboard → "Use Option as Meta key"
  위 설정이 켜져 있어야 Option+←/→ 가 단어 단위 이동으로 동작한다.
"""
import asyncio
import sys
from typing import AsyncIterator

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, VSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.patch_stdout import patch_stdout

_queue: asyncio.Queue[str | None] | None = None
_app: Application | None = None
_app_task: asyncio.Task | None = None
_patch_cm = None
_started = False


def log(*args, sep: str = " ", end: str = "\n", flush: bool = False) -> None:
    """print 와 동일 API. start() 호출 후엔 입력 박스 위로 출력된다."""
    msg = sep.join(str(a) for a in args) + end
    sys.stdout.write(msg)
    if flush:
        sys.stdout.flush()


def _build_app() -> tuple[Application, Buffer]:
    """입력 박스 + 위/아래 가로선 레이아웃의 Application 생성."""
    history = InMemoryHistory()
    buffer = Buffer(multiline=True, history=history)

    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):
        """Enter: 제출. 빈 줄이면 무시."""
        text = buffer.text.strip()
        if not text:
            buffer.text = ""
            return
        buffer.append_to_history()
        buffer.text = ""
        if _queue is not None:
            _queue.put_nowait(text)

    @kb.add("escape", "enter")   # Alt+Enter
    @kb.add("c-j")               # Shift+Enter (대부분의 터미널에서 ^J)
    def _newline(event):
        """Shift+Enter / Alt+Enter: 줄바꿈."""
        buffer.insert_text("\n")

    @kb.add("c-c")
    def _cancel(event):
        """Ctrl+C: 입력 중이면 비우기, 비어 있으면 종료."""
        if buffer.text:
            buffer.text = ""
        else:
            event.app.exit(exception=KeyboardInterrupt)

    @kb.add("c-d")
    def _eof(event):
        event.app.exit(exception=EOFError)

    # 입력 박스: 멀티라인이라 줄 수에 따라 1~10줄 사이에서 자동 조절
    input_window = Window(
        BufferControl(
            buffer=buffer,
            input_processors=[BeforeInput(ANSI("\x1b[36m> \x1b[0m"))],
            focusable=True,
        ),
        height=Dimension(min=1, max=10),
        wrap_lines=True,
        always_hide_cursor=False,
    )

    # 위/아래 가로선 (전폭). char='─' 로 구분선 표시.
    def _hline():
        return Window(
            FormattedTextControl(""),
            height=1,
            char="─",
            style="class:hline",
        )

    layout = Layout(
        HSplit([
            _hline(),
            VSplit([input_window]),
            _hline(),
        ]),
        focused_element=input_window,
    )

    app = Application(
        layout=layout,
        key_bindings=kb,
        full_screen=False,           # 위 로그 영역은 일반 스크롤로 유지
        mouse_support=False,
        erase_when_done=False,
    )
    return app, buffer


def start() -> None:
    """입력 박스를 활성화하고 백그라운드에서 Application 실행."""
    global _app, _app_task, _patch_cm, _queue, _started
    if _started:
        return
    _queue = asyncio.Queue()
    _app, _ = _build_app()
    # patch_stdout: 다른 모듈의 print() 가 입력 영역을 망가뜨리지 않게 한다
    _patch_cm = patch_stdout(raw=True)
    _patch_cm.__enter__()
    _app_task = asyncio.create_task(_app.run_async())
    _started = True


def stop() -> None:
    """종료 정리."""
    global _started
    if not _started:
        return
    if _app is not None and _app.is_running:
        try:
            _app.exit()
        except Exception:
            pass
    if _app_task and not _app_task.done():
        _app_task.cancel()
    if _patch_cm is not None:
        try:
            _patch_cm.__exit__(None, None, None)
        except Exception:
            pass
    _started = False


async def lines() -> AsyncIterator[str]:
    """사용자가 제출한 입력을 하나씩 yield. 종료 시 stop."""
    assert _queue is not None, "console.start() 를 먼저 호출하세요"
    while True:
        # app 이 종료되었으면 더는 입력이 없음
        get_task = asyncio.create_task(_queue.get())
        done, _ = await asyncio.wait(
            {get_task} | ({_app_task} if _app_task else set()),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if get_task in done:
            item = get_task.result()
            if item is None:
                return
            yield item
        else:
            get_task.cancel()
            return
