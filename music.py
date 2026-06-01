"""음악: 인앱(yt-dlp+ffmpeg→엔진) 또는 Chrome 폴백."""
import asyncio
import shutil
import subprocess

import numpy as np

import config

_CHUNK = 9600  # 0.2s @ 48k mono f32


def _resolve_sync(query: str):
    from yt_dlp import YoutubeDL
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "skip_download": True, "format": "bestaudio/best"}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=False)
    entries = (info or {}).get("entries") or []
    if not entries:
        return None
    e = entries[0]
    return e.get("id"), e.get("title", "")


async def resolve_track(query: str):
    return await asyncio.to_thread(_resolve_sync, query)


def _ffmpeg_cmd(vid: str):
    url = f"https://www.youtube.com/watch?v={vid}"
    return ["ffmpeg", "-loglevel", "quiet", "-i", url,
            "-f", "f32le", "-ac", "1", "-ar", "48000", "pipe:1"]


async def start_ffmpeg_pump(vid: str, sink):
    """ffmpeg 디코드 → 48k mono f32 청크를 sink(pcm) 코루틴으로 전달. handle 반환."""
    proc = await asyncio.create_subprocess_exec(
        *_ffmpeg_cmd(vid), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def pump():
        nbytes = _CHUNK * 4
        while True:
            data = await proc.stdout.read(nbytes)
            if not data:
                break
            await sink(np.frombuffer(data, dtype="<f4").copy())

    task = asyncio.create_task(pump())
    return (proc, task)


async def stop_ffmpeg_pump(handle):
    proc, task = handle
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    if proc.returncode is None:
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        proc.kill()


# --- Chrome 폴백 (기존 동작) ---
def _open_in_browser(url: str) -> bool:
    if not shutil.which("open"):
        return False
    try:
        subprocess.run(["open", "-a", config.BROWSER_APP, url], check=True)
    except Exception:
        subprocess.run(["open", url])
    return True


async def chrome_play(query: str) -> str:
    track = await resolve_track(query)
    if not track or not track[0]:
        return f"'{query}' 에 맞는 영상을 찾지 못했습니다."
    vid, title = track
    if _open_in_browser(f"https://www.youtube.com/watch?v={vid}&autoplay=1"):
        return f"재생 시작: {title}"
    return "브라우저를 열지 못했습니다."


def _stop_sync() -> str:
    app = config.BROWSER_APP
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
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
        out = (r.stdout or "").strip()
        n = int(out) if out.lstrip("-").isdigit() else 0
    except Exception as e:
        return f"음악을 끄지 못했습니다: {e}"
    if n < 0:
        return "브라우저가 실행 중이 아닙니다."
    return "음악을 껐습니다." if n else "재생 중인 음악이 없습니다."


async def chrome_stop() -> str:
    return await asyncio.to_thread(_stop_sync)
