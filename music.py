"""음악 재생 — 유튜브 검색(yt-dlp) → 크롬으로 재생.

play_music(query) -> 상태 텍스트. LLM 의 play_music 도구에서 사용.
yt-dlp 로 상위 1개 영상을 찾아(키 불필요) 브라우저로 watch 페이지를 연다.
"""
import asyncio
import shutil
import subprocess

import config


def _resolve(query: str):
    """ytsearch 로 상위 1개 영상의 (video_id, title) 반환. 실패 시 None."""
    from yt_dlp import YoutubeDL

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "extract_flat": True,   # 메타만 (빠름)
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=False)
    entries = (info or {}).get("entries") or []
    if not entries:
        return None
    e = entries[0]
    return e.get("id"), e.get("title", "")


def _open_in_browser(url: str) -> bool:
    """지정 브라우저(없으면 기본 브라우저)로 url 을 연다. macOS 'open' 사용."""
    if not shutil.which("open"):
        return False
    try:
        subprocess.run(["open", "-a", config.BROWSER_APP, url], check=True)
    except Exception:
        subprocess.run(["open", url])   # 기본 브라우저 폴백
    return True


def _stop_sync() -> str:
    """크롬에 열린 유튜브 탭을 닫아 재생을 멈춘다 (macOS AppleScript)."""
    app = config.BROWSER_APP
    # 크롬이 실행 중일 때만 (tell 이 앱을 새로 띄우지 않도록 running 체크)
    script = f'''
    if application "{app}" is running then
      tell application "{app}"
        set n to 0
        repeat with w in windows
          repeat with t in (tabs of w)
            try
              if (URL of t) contains "youtube.com" then
                close t
                set n to n + 1
              end if
            end try
          end repeat
        end repeat
        return n
      end tell
    else
      return -1
    end if
    '''
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=10)
        out = (r.stdout or "").strip()
        n = int(out) if out.lstrip("-").isdigit() else 0
    except Exception as e:
        return f"음악을 끄지 못했습니다: {e}"
    if n < 0:
        return "브라우저가 실행 중이 아닙니다."
    if n == 0:
        return "재생 중인 음악이 없습니다."
    return f"음악을 껐습니다(탭 {n}개)."


async def stop_music() -> str:
    return await asyncio.to_thread(_stop_sync)


async def play_music(query: str) -> str:
    def work():
        try:
            r = _resolve(query)
        except Exception as e:
            return f"검색에 실패했습니다: {e}"
        if not r or not r[0]:
            return f"'{query}' 에 맞는 영상을 찾지 못했습니다."
        vid, title = r
        url = f"https://www.youtube.com/watch?v={vid}&autoplay=1"
        if _open_in_browser(url):
            return f"재생 시작: {title}"
        return f"브라우저를 열지 못했습니다. 링크: {url}"

    return await asyncio.to_thread(work)
