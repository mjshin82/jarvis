"""콘솔 UI — prompt_toolkit Application 기반 "Claude Code" 스타일.

로그는 위로 흐르고, 화면 하단엔 입력 박스가 고정된다. 입력 박스는 위/아래
가로선으로 감싸 시각적으로 구분되며, 길어지면 자연스럽게 멀티라인으로
확장된다.

키 바인딩
  Enter             제출 (자동완성 메뉴 떠있으면 선택 항목 채우기)
  Option+Enter      줄바꿈 (멀티라인)
  Option+←/→        단어 단위 이동
  Option+Backspace  단어 단위 삭제
  Ctrl+A / Ctrl+E   줄 처음/끝
  ↑ / ↓             메뉴 떠있으면 선택 이동, 멀티라인이면 줄 이동, 그 외 히스토리
  Esc               메뉴 떠있으면 닫기 → 입력 박스 비움 → 진행 중 응답 취소
  Ctrl+C            입력 비우기 (빈 줄에서 한 번 더 누르면 종료)
  Ctrl+D            종료

슬래시 명령
  '/' 를 치면 입력 박스 아래에 사용 가능한 명령 메뉴가 뜬다. 방향키로
  선택 후 Enter/Tab 으로 채우기. 명령줄은 밝은 블루로 표시.

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
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, VSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

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


class _SlashCompleter(Completer):
    """입력이 '/' 로 시작할 때 commands._REGISTRY 항목을 후보로 제공."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        # '/' 뒤 첫 토큰만 검사. 공백이 있으면(인자 입력 중) 자동완성 안 띄움.
        partial = text[1:]
        if " " in partial or "\n" in partial:
            return
        # 지연 import: 순환 회피 + 테스트 시점 등록 가능
        try:
            from commands import _REGISTRY
        except Exception:
            return
        for name in sorted(_REGISTRY):
            if name.startswith(partial.lower()):
                cmd = _REGISTRY[name]
                # display: '/name    설명' 형태로 한 줄에
                yield Completion(
                    text=f"/{name}",
                    start_position=-len(text),   # '/' 포함 기존 입력 전체 교체
                    display=f"/{name}",
                    display_meta=cmd.help,
                )


def _build_app() -> tuple[Application, Buffer]:
    """입력 박스 + 위/아래 가로선 + (조건부) 자동완성 메뉴."""
    history = InMemoryHistory()
    buffer = Buffer(
        multiline=True,
        history=history,
        completer=_SlashCompleter(),
        complete_while_typing=True,
    )

    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event):
        """Enter: 자동완성 메뉴가 떠있으면 선택 항목으로 채우기, 아니면 제출."""
        state = event.current_buffer.complete_state
        if state and state.current_completion:
            event.current_buffer.apply_completion(state.current_completion)
            return
        if state:
            # 메뉴는 떠있는데 currrent_completion 이 없는 경우 → 메뉴 닫고 제출 단계로 폴백
            event.current_buffer.cancel_completion()
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
        """Esc: 메뉴가 떠있으면 닫기. 입력 박스에 내용 있으면 비우기.
        그 외엔 외부 콜백(진행 중 응답 취소)."""
        if event.current_buffer.complete_state:
            event.current_buffer.cancel_completion()
            return
        if buffer.text:
            buffer.text = ""
            return
        if _on_escape is not None:
            try:
                _on_escape()
            except Exception:
                pass

    # 입력 박스. '/' 로 시작하면 명령처럼 보이게 밝은 블루로 표시.
    def _input_style():
        return "class:cmd-input" if buffer.text.startswith("/") else "class:input"

    input_window = Window(
        BufferControl(
            buffer=buffer,
            input_processors=[BeforeInput(ANSI("\x1b[36m> \x1b[0m"))],
            focusable=True,
        ),
        height=Dimension(min=1, max=10),
        wrap_lines=True,
        always_hide_cursor=False,
        get_line_prefix=None,
        style=_input_style,
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

    # 자동완성 메뉴: 입력이 '/' 로 시작하고 후보가 있을 때만 보임.
    # 박스 *하단 가로선 아래* 에 배치해 사용자가 요청한 시각 구조와 일치.
    def _menu_text():
        state = buffer.complete_state
        if not state or not state.completions:
            return ""
        # 가장 긴 이름에 맞춰 정렬 표시. 최대 5개 보임(prompt_toolkit 이 스크롤 처리).
        items = state.completions
        cur = state.complete_index
        name_w = max(len(c.display[0][1] if isinstance(c.display, list) else c.display) for c in items)
        rows = []
        # 보이는 윈도우: 현재 선택을 중심으로 최대 5개
        max_visible = 5
        n = len(items)
        if n <= max_visible:
            start = 0
        else:
            start = max(0, min(n - max_visible, (cur or 0) - max_visible // 2))
        end = min(n, start + max_visible)
        # ANSI 컬러: 선택된 항목만 강조(밝은 블루 배경 비슷한 효과로 ▸ 표시),
        # 그 외는 평범한 밝은 블루 텍스트
        BLUE = "\x1b[38;5;81m"      # 밝은 시안-블루 (Claude Code 풍)
        SEL  = "\x1b[1;97;48;5;24m"  # 선택: 굵은 흰글씨 + 진한 블루 배경
        RESET = "\x1b[0m"
        for i in range(start, end):
            c = items[i]
            display = c.display[0][1] if isinstance(c.display, list) else c.display
            meta = c.display_meta[0][1] if isinstance(c.display_meta, list) else (c.display_meta or "")
            line = f"  {display:<{name_w}}  {meta}"
            if i == cur:
                rows.append(f"{SEL}{line}{RESET}")
            else:
                rows.append(f"{BLUE}{line}{RESET}")
        # 위/아래에 더 있으면 표시
        if start > 0:
            rows.insert(0, f"{BLUE}  ↑ {start} more{RESET}")
        if end < n:
            rows.append(f"{BLUE}  ↓ {n - end} more{RESET}")
        return ANSI("\n".join(rows))

    menu_window = ConditionalContainer(
        Window(
            FormattedTextControl(_menu_text),
            height=Dimension(min=0, max=7),
            dont_extend_height=True,
        ),
        filter=has_completions,
    )

    layout = Layout(
        HSplit([
            status_window,
            queue_window,
            _hline(),
            VSplit([input_window]),
            _hline(),
            menu_window,
        ]),
        focused_element=input_window,
    )

    style = Style.from_dict({
        "hline": "fg:#888888",
        "queue": "fg:#aaaaaa",
        "input": "",
        "cmd-input": "fg:#5fd7ff bold",   # 명령줄: 밝은 블루 + 굵게
    })

    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
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


async def stop() -> None:
    """종료 정리. Application 을 종료시키고 그 코루틴이 끝날 때까지 짧게 대기."""
    global _started
    if not _started:
        return
    if _app is not None and _app.is_running:
        try:
            _app.exit()
        except Exception:
            pass
    if _app_task is not None:
        # exit 후 정리가 끝날 시간을 짧게 준다(최대 1초)
        try:
            await asyncio.wait_for(_app_task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            if not _app_task.done():
                _app_task.cancel()
    # 스피너 태스크도 정리
    if _spinner_task is not None and not _spinner_task.done():
        _spinner_task.cancel()
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
