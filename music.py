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
