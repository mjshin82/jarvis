"""콘솔 UI — prompt_toolkit Application 기반 "Claude Code" 스타일.

로그는 위로 흐르고, 화면 하단엔 입력 박스가 고정된다. 입력 박스는 위/아래
가로선으로 감싸 시각적으로 구분되며, 길어지면 자연스럽게 멀티라인으로
확장된다.

키 바인딩
  Enter             제출
  Option+Enter      줄바꿈 (멀티라인)
  Option+←/→        단어 단위 이동
  Option+Backspace  단어 단위 삭제
  Ctrl+A / Ctrl+E   줄 처음/끝
  ↑ / ↓             멀티라인일 때 줄 이동, 단일 라인일 때 히스토리
  Esc               입력 박스에 내용 있으면 비움, 비어있으면 진행 중 응답 취소
  Ctrl+C            입력 비우기 (빈 줄에서 한 번 더 누르면 종료)
  Ctrl+D            종료

macOS 터미널 설정 팁
  iTerm2: Preferences → Profiles → Keys → "Natural Text Editing" 프리셋
  Terminal.app: Preferences → Profiles → Keyboard → "Use Option as Meta key"
  이 설정이 켜져 있어야 Option 조합이 동작한다.
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
_started = False
_on_escape = None   # Esc 키 콜백 (외부에서 set_escape_handler 로 등록)
_queue_items: list[str] = []   # 입력 박스 위에 표시할 대기 입력들
_status: str | None = None     # 진행 표시(스피너) 텍스트. None 이면 숨김
_spinner_task: asyncio.Task | None = None
_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_spinner_idx = 0


def set_escape_handler(callback) -> None:
    """Esc 키가 눌렸을 때 호출될 함수 등록. 입력 박스가 비어있을 때만 발화."""
    global _on_escape
    _on_escape = callback


def set_queue_display(items: list[str]) -> None:
    """입력 박스 위에 표시할 대기 입력 목록을 갱신. 기존 표시는 통째로 교체."""
    global _queue_items
    _queue_items = list(items)
    _invalidate()


def set_status(text: str | None) -> None:
    """진행 표시 텍스트 설정. None 또는 빈 문자열이면 숨김.
    설정되어 있는 동안 스피너 프레임이 자동 회전한다."""
    global _status, _spinner_task
    _status = text if text else None
    if _status:
        if _spinner_task is None or _spinner_task.done():
            try:
                _spinner_task = asyncio.create_task(_spin())
            except RuntimeError:
                # 이벤트 루프가 아직 없을 때 (start 전): 스피너 생략, 정적 표시만
                pass
    else:
        if _spinner_task is not None and not _spinner_task.done():
            _spinner_task.cancel()
            _spinner_task = None
    _invalidate()


async def _spin():
    """스피너 프레임 회전. status 가 None 되면 종료."""
    global _spinner_idx
    try:
        while _status:
            _spinner_idx = (_spinner_idx + 1) % len(_SPINNER_FRAMES)
            _invalidate()
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        return


def _invalidate():
    if _app is not None and _app.is_running:
        try:
            _app.invalidate()
        except Exception:
            pass


def log(*args, sep: str = " ", end: str = "\n", flush: bool = False) -> None:
    """print 와 동일 API. start() 후엔 patch_stdout 컨텍스트가 sys.stdout.write
    를 잡아 prompt_toolkit 의 안전 출력 경로로 라우팅한다 → 입력 박스 위쪽에
    누적되어 사라지지 않는다."""
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
        submitted = buffer.text.strip()
        if not submitted:
            buffer.text = ""
            return
        buffer.append_to_history()
        buffer.text = ""
        if _queue is not None:
            _queue.put_nowait(submitted)

    @kb.add("escape", "enter")   # Option+Enter (macOS) / Alt+Enter
    def _newline(event):
        """Option+Enter: 줄바꿈. (멀티라인 입력)"""
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

    @kb.add("escape", eager=True)
    def _escape(event):
        """Esc: 진행 중 응답 취소(외부 콜백). 입력 박스에 내용이 있으면 그것부터 비움.
        eager=True 로 등록해 Option+화살표 같은 다른 escape 시퀀스의 prefix 와 안 섞이게."""
        if buffer.text:
            buffer.text = ""
            return
        if _on_escape is not None:
            try:
                _on_escape()
            except Exception:
                pass

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

    # 스피너 + 큐 표시: 입력 박스 바로 위. 둘 다 비면 0줄(숨김).
    def _status_text():
        """진행 중 작업 표시. status 있을 때만 한 줄."""
        if not _status:
            return ""
        frame = _SPINNER_FRAMES[_spinner_idx]
        # 청록색
        return ANSI(f"\x1b[36m{frame} {_status}\x1b[0m")

    def _queue_text():
        # collector 가 큐를 항상 '최신 1건' 으로 유지하므로 보통 0 또는 1개.
        if not _queue_items:
            return ""
        lines = []
        for item in _queue_items:
            preview = item.replace("\n", " ").strip()
            if len(preview) > 80:
                preview = preview[:77] + "…"
            lines.append(f"⏳ {preview}")
        return "\n".join(lines)

    status_window = Window(
        FormattedTextControl(_status_text),
        height=Dimension(min=0, max=1),
        dont_extend_height=True,
    )
    queue_window = Window(
        FormattedTextControl(_queue_text),
        height=Dimension(min=0, max=8),
        style="class:queue",
        dont_extend_height=True,
    )

    layout = Layout(
        HSplit([
            status_window,
            queue_window,
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


async def _run_app_with_patch():
    """patch_stdout 컨텍스트 안에서 Application 을 끝까지 실행.
    patch_stdout 이 활성된 동안엔 print/sys.stdout 호출이 prompt_toolkit 의
    안전 출력 경로로 라우팅돼 입력 박스 위로 깔끔히 누적된다."""
    assert _app is not None
    with patch_stdout(raw=True):
        await _app.run_async()


def start() -> None:
    """입력 박스를 활성화하고 백그라운드에서 Application 실행."""
    global _app, _app_task, _queue, _started
    if _started:
        return
    _queue = asyncio.Queue()
    _app, _ = _build_app()
    _app_task = asyncio.create_task(_run_app_with_patch())
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
