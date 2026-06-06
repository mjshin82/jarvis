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
  "hello", "source", "translation_ko", "translation_en", "partial",
  "gap", "info", "end", "kicked", "publisher_disconnected",
]);

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
      this.attachViewer(server, "public");
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
      this.broadcast(this.buildEvent(msg));
      try { this.publisher?.close(1000, "end"); } catch { /* */ }
      this.publisher = null;
      return;
    }
    // partial: 직전이 partial 이면 덮어쓰기, 아니면 append
    if (msg.kind === "partial") {
      const last = this.events[this.events.length - 1];
      if (last && last.kind === "partial") {
        // 덮어쓰기 — seq 도 새로 부여(클라이언트가 "갱신"으로 인식)
        const replaced = this.buildEvent(msg);
        this.events[this.events.length - 1] = replaced;
        this.broadcast(replaced);
      } else {
        this.append(this.buildEvent(msg));
      }
      return;
    }
    // navigate: 일시적 명령(상태 아님) — 현재 viewer 에게만 broadcast, replay 버퍼 미적재.
    // (append 하면 이후 접속한 viewer 가 replay 로 받아 회의가 아닐 때도 /meeting 으로 이동함)
    if (msg.kind === "navigate") {
      this.broadcast(this.buildEvent(msg));
      return;
    }
    // 그 외: append + broadcast
    this.append(this.buildEvent(msg));
  }

  // --- viewer ---

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
