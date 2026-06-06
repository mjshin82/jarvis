# conversation_stt.py
"""일반 대화용 STT facade — 설정에 따라 로컬(RealtimeSTT) 또는 Gladia 선택.

로컬: 상시 가동(무료), 부팅/최초 resume 시 1회 start, 이후 유지.
Gladia: 클라우드 과금 → resume(LISTENING 진입) 시 연결, suspend(IDLE) 시 해제.
컨트롤러는 backend 차이를 모르고 feed_block/resume/suspend/aclose 만 쓴다.
"""


class ConversationSTT:
    def __init__(self, *, make_local, make_gladia, settings_get, on_log=print):
        self._make_local = make_local
        self._make_gladia = make_gladia
        self._settings_get = settings_get
        self._log = on_log
        self._local = None          # 상시 유지(지연 생성)
        self._local_started = False
        self._active = None         # 현재 feed 대상 backend 인스턴스
        self._active_kind = None    # "local" | "gladia" | None

    def _backend(self) -> str:
        return self._settings_get("stt_backend") or "local"

    async def _ensure_local(self):
        if self._local is None:
            self._local = self._make_local()
        if not self._local_started:
            await self._local.start()
            self._local_started = True
        return self._local

    def feed_block(self, block) -> None:
        if self._active is not None:
            self._active.feed_block(block)

    async def start(self) -> None:
        """부팅 — 기본이 local 이면 모델 프리로드(첫 발화 지연 방지)."""
        if self._backend() == "local":
            try:
                await self._ensure_local()
            except Exception as e:
                self._log(f"[stt] 로컬 STT 시작 실패: {e}")

    async def resume(self) -> None:
        """LISTENING 진입 — 설정대로 backend 준비."""
        kind = self._backend()
        if kind == self._active_kind:
            return   # 이미 해당 backend 활성
        # backend 전환 — 이전 active 가 gladia 면 연결 해제(로컬은 유지)
        if self._active_kind == "gladia" and self._active is not None:
            try:
                await self._active.close()
            except Exception:
                pass
            self._active = None
        if kind == "gladia":
            try:
                g = self._make_gladia()
                await g.start()
                self._active = g
                self._active_kind = "gladia"
                self._log("🎤 일반 대화 STT: Gladia")
            except Exception as e:
                self._log(f"[stt] Gladia 시작 실패 — 로컬 폴백: {e}")
                self._active = await self._ensure_local()
                self._active_kind = "local"
        else:
            self._active = await self._ensure_local()
            self._active_kind = "local"

    async def suspend(self) -> None:
        """IDLE 진입 — gladia 면 연결 해제, 로컬이면 상시 유지."""
        if self._active_kind == "gladia" and self._active is not None:
            try:
                await self._active.close()
            except Exception:
                pass
            self._active = None
            self._active_kind = None
        # local: no-op (상시 유지 → 다음 resume 빠름)

    async def aclose(self) -> None:
        if self._active_kind == "gladia" and self._active is not None:
            try:
                await self._active.close()
            except Exception:
                pass
        if self._local is not None and self._local_started:
            try:
                await self._local.close()
            except Exception:
                pass
        self._active = None
        self._active_kind = None
