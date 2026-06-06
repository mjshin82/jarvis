/**
 * MeetingDO — 회의 키 1개당 Durable Object 1개.
 *
 * 책임:
 *  - publisher(WebSocket 1개) 와 viewer(WebSocket N개) 연결 관리
 *  - publisher 가 보낸 이벤트를 모든 viewer 에게 fan-out
 *  - 최근 MAX_REPLAY 개 이벤트를 메모리에 보관 → 새 viewer 접속 시 replay
 *  - 같은 키로 새 publisher 접속 시 기존 publisher 강퇴(kicked) 후 인수
 */
import type { ClientMessage, MeetingMeta, RelayEvent } from "./types";
import { MAX_REPLAY } from "./types";

interface Env {
  // 빈 인터페이스이지만 DO 컨텍스트용
}

// 공개(public) 뷰어에게 보내도 되는 자막 계열 kind. 그 외(user/assistant/navigate/mic_source)·
// 바이너리 TTS 는 owner 전용.
const PUBLIC_KINDS = new Set([
  "hello", "source", "translation", "partial",
  "gap", "info", "end", "kicked", "publisher_disconnected",
  "meeting_archive", "meeting_summary",
]);

async function sha256hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export class MeetingDO {
  private state: DurableObjectState;
  private publisher: WebSocket | null = null;
  private viewers: Map<WebSocket, "owner" | "public"> = new Map();
  private micSender: WebSocket | null = null;
  private micReceiver: WebSocket | null = null;
  private controlSender: WebSocket | null = null;
  private controlReceiver: WebSocket | null = null;
  private lastControlNoReceiverAt = 0;
  private lastNoReceiverAt = 0;
  private lastMicSource: string | null = null;
  private currentView: string | null = null;
  private lastMeetingTitle: string | null = null;
  private pendingArchive: Map<number, WebSocket> = new Map();
  private archiveSeq = 0;
  private currentMeetingId: string | null = null;
  private currentPasswordHash: string | null = null;
  private lastMeetingInfo: string | null = null;
  private events: RelayEvent[] = [];   // 최근 N개 (deque)
  private seq = 0;
  private meta: MeetingMeta | null = null;

  constructor(state: DurableObjectState, _env: Env) {
    this.state = state;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    // 경로 형식: /__do/<role>/<key>  (worker 가 라우팅하면서 붙임)
    // role: "publish" | "subscribe"
    const parts = url.pathname.split("/").filter(Boolean);
    // parts[0] = "__do", parts[1] = role, parts[2..] = key
    const role = parts[1];
    if (role !== "publish" && role !== "subscribe" && role !== "mic" && role !== "mic-recv" && role !== "control" && role !== "control-recv" && role !== "watch") {
      return new Response("not found", { status: 404 });
    }
    if (request.headers.get("Upgrade") !== "websocket") {
      return new Response("expected websocket", { status: 426 });
    }
    const pair = new WebSocketPair();
    const client = pair[0];
    const server = pair[1];
    server.accept();

    if (role === "publish") {
      this.attachPublisher(server);
    } else if (role === "subscribe") {
      this.attachViewer(server, "owner");
    } else if (role === "watch") {
      this.attachWatchPending(server, url.searchParams.get("admin") === "1");
    } else if (role === "mic") {
      this.attachMicSender(server);
    } else if (role === "mic-recv") {
      this.attachMicReceiver(server);
    } else if (role === "control") {
      this.attachControlSender(server);
    } else {
      this.attachControlReceiver(server);
    }

    return new Response(null, { status: 101, webSocket: client });
  }

  // --- publisher ---

  private attachPublisher(ws: WebSocket): void {
    this.attachSlot(ws, () => this.publisher, (v) => (this.publisher = v), {
      kick: true,
      onMessage: (data) => {
        if (data instanceof ArrayBuffer) { this.broadcastBinary(data); return; }
        let parsed: ClientMessage | null = null;
        try { parsed = JSON.parse(data as string) as ClientMessage; } catch { return; }
        this.handlePublisherMessage(parsed);
      },
      onClose: () => this.broadcast(this.buildEvent({ kind: "publisher_disconnected" })),
    });
    this.notifyViewerCount();   // 재연결 publisher 에게 현재 owner 수 동기화
  }

  private handlePublisherMessage(msg: ClientMessage): void {
    // hello: meta 갱신 + 이벤트 채널에 기록
    if (msg.kind === "hello") {
      if (msg.meta) {
        this.meta = msg.meta;
      }
      this.append(this.buildEvent(msg));
      return;
    }
    // end: 명시적 종료. publisher 측에서 직접 close 도 함.
    if (msg.kind === "end") {
      this.currentMeetingId = null;
      this.currentPasswordHash = null;
      this.lastMeetingInfo = null;
      this.lastMeetingTitle = null;
      this.broadcast(this.buildEvent(msg));
      try { this.publisher?.close(1000, "end"); } catch { /* */ }
      this.publisher = null;
      return;
    }
    // partial: 조합중 임시 텍스트 — replay 버퍼에 넣지 않는다(재접속 시 미완성 partial 이
    // navigate 전에 replay 돼 홈 채팅 draft 로 누수되던 버그 방지). 라이브만 broadcast.
    if (msg.kind === "partial") {
      this.broadcast(this.buildEvent(msg));
      return;
    }
    // navigate: 일시적 명령(상태 아님) — 현재 viewer 에게만 broadcast, replay 버퍼 미적재.
    // (append 하면 이후 접속한 viewer 가 replay 로 받아 회의가 아닐 때도 /meeting 으로 이동함)
    if (msg.kind === "navigate") {
      this.currentView = msg.text ?? null;
      if (msg.text !== "meeting") {
        this.lastMeetingTitle = null;
        this.currentMeetingId = null;
        this.currentPasswordHash = null;
        this.lastMeetingInfo = null;
      }
      this.broadcast(this.buildEvent(msg));
      return;
    }
    if (msg.kind === "meeting_title") {
      this.lastMeetingTitle = msg.text ?? null;
      this.broadcast(this.buildEvent(msg));
      return;
    }
    if (msg.kind === "meeting_creds") {
      this.evictPublicViewers();   // 새 회의 — 이전 회의 보던 viewer 해제(재인증 강제)
      try {
        const c = JSON.parse(msg.text || "{}");
        this.currentMeetingId = c.meeting_id ?? null;
        this.currentPasswordHash = c.password_hash ?? null;
      } catch { /* */ }
      return;   // DO 전용 — broadcast/append 안 함
    }
    if (msg.kind === "meeting_info") {
      this.lastMeetingInfo = msg.text ?? null;
      this.broadcast(this.buildEvent(msg));   // 공개는 PUBLIC_KINDS 필터로 차단 → owner 만
      return;
    }
    if (msg.kind === "archive_response") {
      let d: any;
      try { d = JSON.parse(msg.text || "{}"); } catch { return; }
      const ws = this.pendingArchive.get(d.req);
      if (!ws) return;
      this.pendingArchive.delete(d.req);
      if (!d.ok) { try { ws.close(4003, "no-archive"); } catch { /* */ } return; }
      this.safeSend(ws, this.buildEvent({ kind: "meeting_archive",
        text: JSON.stringify({ title: d.title, transcript: d.transcript, summaries: d.summaries }) }));
      this.attachViewer(ws, "public");   // 이후 meeting_summary 수신
      return;
    }
    if (msg.kind === "list_response") {
      let d: any;
      try { d = JSON.parse(msg.text || "{}"); } catch { return; }
      const ws = this.pendingArchive.get(d.req);
      if (!ws) return;
      this.pendingArchive.delete(d.req);
      this.safeSend(ws, this.buildEvent({ kind: "meeting_list", text: JSON.stringify({ meetings: d.meetings || [] }) }));
      return;
    }
    if (msg.kind === "meeting_summary") {
      this.broadcast(this.buildEvent(msg));
      return;
    }
    // mic_release: 일시 신호(상태 아님) — owner 에게만 broadcast, replay 미적재.
    if (msg.kind === "mic_release") {
      this.broadcast(this.buildEvent(msg));
      return;
    }
    // 그 외: append + broadcast
    this.append(this.buildEvent(msg));
  }

  // --- viewer ---

  // 회의 종료 시 공개 viewer 전원 해제 — 다음 회의(같은 방/DO)를 비번 없이 보지 못하게.
  // 4003 으로 닫으면 viewer.html 이 비번 입력 게이트를 다시 띄운다.
  private evictPublicViewers(): void {
    for (const [ws, role] of this.viewers) {
      if (role === "public") {
        try { ws.close(4003, "meeting-ended"); } catch { /* */ }
      }
    }
  }

  // 공개 자막 소켓: 즉시 붙이지 않고 첫 메시지 {kind:"auth",mid,pw} 검증 후 합류.
  private attachWatchPending(ws: WebSocket, isAdmin: boolean): void {
    let done = false;
    const timer = setTimeout(() => {
      if (!done) { try { ws.close(4003, "no-auth"); } catch { /* */ } }
    }, 10000);
    ws.addEventListener("message", async (evt) => {
      if (done) return;
      let msg: any;
      try { msg = JSON.parse(typeof evt.data === "string" ? evt.data : ""); } catch { return; }
      if (!msg) return;
      // 관리자 목록 요청
      if (msg.kind === "list") {
        done = true; clearTimeout(timer);
        if (!isAdmin) { try { ws.close(4003, "admin-only"); } catch { /* */ } return; }
        if (!this.publisher) { try { ws.close(4003, "no-meeting"); } catch { /* */ } return; }
        const req = ++this.archiveSeq;
        this.pendingArchive.set(req, ws);
        this.safeSend(this.publisher, this.buildEvent({ kind: "list_request", text: JSON.stringify({ req }) }));
        setTimeout(() => { if (this.pendingArchive.delete(req)) { try { ws.close(4003, "list-timeout"); } catch { /* */ } } }, 10000);
        return;
      }
      if (msg.kind !== "auth") return;
      done = true; clearTimeout(timer);
      const live = this.currentMeetingId && msg.mid === this.currentMeetingId;
      // 관리자: 비번 생략
      if (isAdmin) {
        if (live) { this.attachViewer(ws, "public"); return; }
        if (!this.publisher) { try { ws.close(4003, "no-meeting"); } catch { /* */ } return; }
        const req = ++this.archiveSeq;
        this.pendingArchive.set(req, ws);
        this.safeSend(this.publisher, this.buildEvent({ kind: "archive_request", text: JSON.stringify({ req, mid: msg.mid, admin: true }) }));
        setTimeout(() => { if (this.pendingArchive.delete(req)) { try { ws.close(4003, "archive-timeout"); } catch { /* */ } } }, 10000);
        return;
      }
      // 비관리자: 라이브 비번검증 / 종료 archive
      if (live) {
        const h = await sha256hex(String(msg.pw || ""));
        if (h === this.currentPasswordHash) { this.attachViewer(ws, "public"); }
        else { try { ws.close(4003, "bad-password"); } catch { /* */ } }
        return;
      }
      if (!this.publisher) { try { ws.close(4003, "no-meeting"); } catch { /* */ } return; }
      const req = ++this.archiveSeq;
      this.pendingArchive.set(req, ws);
      this.safeSend(this.publisher, this.buildEvent({
        kind: "archive_request",
        text: JSON.stringify({ req, mid: msg.mid, pw: msg.pw }),
      }));
      setTimeout(() => {
        if (this.pendingArchive.delete(req)) { try { ws.close(4003, "archive-timeout"); } catch { /* */ } }
      }, 10000);
    });
    ws.addEventListener("close", () => clearTimeout(timer));
  }

  // owner 뷰어 수를 publisher(jarvis)에게 통지 → TTS 웹/로컬 라우팅 결정에 사용
  private notifyViewerCount(): void {
    if (!this.publisher) return;
    let count = 0;
    for (const role of this.viewers.values()) if (role === "owner") count += 1;
    this.safeSend(this.publisher, this.buildEvent({ kind: "viewers", count }));
  }

  private attachViewer(ws: WebSocket, role: "owner" | "public"): void {
    this.viewers.set(ws, role);
    this.notifyViewerCount();
    // replay 는 owner 만 (public = 라이브만, 과거 자막 미노출)
    if (role === "owner") {
      for (const ev of this.events) {
        this.safeSend(ws, ev);
      }
      if (this.lastMicSource) {
        this.safeSend(ws, this.buildEvent({ kind: "mic_source", source: this.lastMicSource as "system" | "remote" }));
      }
      if (this.currentView === "meeting") {
        this.safeSend(ws, this.buildEvent({ kind: "navigate", text: "meeting" }));
      }
      if (this.lastMeetingTitle) {
        this.safeSend(ws, this.buildEvent({ kind: "meeting_title", text: this.lastMeetingTitle }));
      }
      if (this.lastMeetingInfo) {
        this.safeSend(ws, this.buildEvent({ kind: "meeting_info", text: this.lastMeetingInfo }));
      }
      if (!this.publisher) {
        this.safeSend(ws, this.buildEvent({ kind: "publisher_disconnected" }));
      }
    }
    ws.addEventListener("close", () => {
      this.viewers.delete(ws);
      this.notifyViewerCount();
    });
    ws.addEventListener("error", () => {
      this.viewers.delete(ws);
      this.notifyViewerCount();
    });
  }

  // --- 원격 마이크: 브라우저 송신 → jarvis 수신 포워딩 ---

  private attachMicSender(ws: WebSocket): void {
    this.attachSlot(ws, () => this.micSender, (v) => (this.micSender = v), {
      kick: true,
      onMessage: (data) => {
        if (!this.micReceiver) {
          const now = Date.now();
          if (now - this.lastNoReceiverAt > 2000) {   // 프레임마다 통지하지 않도록 디바운스
            this.lastNoReceiverAt = now;
            this.safeSend(ws, { ts: now / 1000, seq: 0, kind: "no_receiver" } as RelayEvent);
          }
          return;
        }
        try { this.micReceiver.send(data); } catch { /* 수신측 끊김 — 다음 close 에서 정리 */ }
      },
    });
  }

  private attachMicReceiver(ws: WebSocket): void {
    this.attachSlot(ws, () => this.micReceiver, (v) => (this.micReceiver = v), {
      onMessage: (data) => {
        let parsed: any = null;
        try {
          const raw = typeof data === "string" ? data : new TextDecoder().decode(data as ArrayBuffer);
          parsed = JSON.parse(raw);
        } catch { return; }
        if (parsed && parsed.kind === "mic_source") {
          this.lastMicSource = parsed.source ?? null;
          this.broadcast(this.buildEvent({ kind: "mic_source", source: parsed.source }));
        }
      },
    });
  }

  private attachControlSender(ws: WebSocket): void {
    this.attachSlot(ws, () => this.controlSender, (v) => (this.controlSender = v), {
      kick: true,
      onMessage: (data) => {
        if (!this.controlReceiver) {
          const now = Date.now();
          if (now - this.lastControlNoReceiverAt > 2000) {
            this.lastControlNoReceiverAt = now;
            this.safeSend(ws, { ts: now / 1000, seq: 0, kind: "no_receiver" } as RelayEvent);
          }
          return;
        }
        try { this.controlReceiver.send(data); } catch { /* 수신측 끊김 */ }
      },
    });
  }

  private attachControlReceiver(ws: WebSocket): void {
    this.attachSlot(ws, () => this.controlReceiver, (v) => (this.controlReceiver = v), {});
  }

  // --- helpers ---

  // 단일슬롯 소켓(publisher/micSender/micReceiver/controlSender/controlReceiver) 공통 부착.
  // 기존 슬롯이 있으면 (선택)kick 통지 후 close, 새 ws 를 슬롯에 대입, 핸들러 등록.
  // close/error 시 현재 슬롯이 이 ws 면 비운다(+ 선택 onClose).
  private attachSlot(
    ws: WebSocket,
    get: () => WebSocket | null,
    set: (v: WebSocket | null) => void,
    opts: {
      kick?: boolean;
      onMessage?: (data: string | ArrayBuffer) => void;
      onClose?: () => void;
    },
  ): void {
    const cur = get();
    if (cur) {
      try {
        if (opts.kick) this.safeSend(cur, this.buildEvent({ kind: "kicked", reason: "replaced" }));
        cur.close(1000, "replaced");
      } catch { /* */ }
    }
    set(ws);
    if (opts.onMessage) {
      ws.addEventListener("message", (m) => opts.onMessage!((m as MessageEvent).data as string | ArrayBuffer));
    }
    ws.addEventListener("close", () => { if (get() === ws) { set(null); opts.onClose?.(); } });
    ws.addEventListener("error", () => { if (get() === ws) set(null); });
  }

  private buildEvent(msg: ClientMessage): RelayEvent {
    this.seq += 1;
    return {
      ts: Date.now() / 1000,
      seq: this.seq,
      ...msg,
    };
  }

  private append(ev: RelayEvent): void {
    this.events.push(ev);
    if (this.events.length > MAX_REPLAY) {
      this.events.splice(0, this.events.length - MAX_REPLAY);
    }
    this.broadcast(ev);
  }

  private broadcast(ev: RelayEvent): void {
    for (const [ws, role] of this.viewers) {
      if (role === "public" && !PUBLIC_KINDS.has(ev.kind)) continue;   // public 은 자막만
      this.safeSend(ws, ev);
    }
  }

  private broadcastBinary(data: ArrayBuffer): void {
    for (const [ws, role] of this.viewers) {
      if (role !== "owner") continue;   // TTS 오디오는 owner 만
      try { ws.send(data); } catch { /* 끊긴 소켓 — close 에서 정리 */ }
    }
  }

  private safeSend(ws: WebSocket, ev: RelayEvent): void {
    try {
      ws.send(JSON.stringify(ev));
    } catch {
      // 끊긴 소켓 — close 이벤트에서 정리됨
    }
  }
}
