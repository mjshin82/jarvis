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

export class MeetingDO {
  private state: DurableObjectState;
  private publisher: WebSocket | null = null;
  private viewers: Set<WebSocket> = new Set();
  private micSender: WebSocket | null = null;
  private micReceiver: WebSocket | null = null;
  private lastNoReceiverAt = 0;
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
    if (role !== "publish" && role !== "subscribe" && role !== "mic" && role !== "mic-recv") {
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
      this.attachViewer(server);
    } else if (role === "mic") {
      this.attachMicSender(server);
    } else {
      this.attachMicReceiver(server);
    }

    return new Response(null, { status: 101, webSocket: client });
  }

  // --- publisher ---

  private attachPublisher(ws: WebSocket): void {
    // 기존 publisher 가 있으면 강퇴 후 인수 (회의 키 충돌 정책: 새 것이 이김)
    if (this.publisher) {
      try {
        this.safeSend(this.publisher, this.buildEvent({ kind: "kicked", reason: "replaced" }));
        this.publisher.close(1000, "replaced");
      } catch {
        // ignore
      }
    }
    this.publisher = ws;

    ws.addEventListener("message", (msg) => {
      let parsed: ClientMessage | null = null;
      try {
        const raw = typeof msg.data === "string" ? msg.data : new TextDecoder().decode(msg.data);
        parsed = JSON.parse(raw) as ClientMessage;
      } catch {
        // 무효 메시지 무시
        return;
      }
      this.handlePublisherMessage(parsed);
    });

    ws.addEventListener("close", () => {
      if (this.publisher === ws) {
        this.publisher = null;
        this.broadcast(this.buildEvent({ kind: "publisher_disconnected" }));
      }
    });
    ws.addEventListener("error", () => {
      if (this.publisher === ws) {
        this.publisher = null;
      }
    });
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
    // 그 외: append + broadcast
    this.append(this.buildEvent(msg));
  }

  // --- viewer ---

  private attachViewer(ws: WebSocket): void {
    this.viewers.add(ws);
    // replay: 최근 events 그대로 1회 전송
    for (const ev of this.events) {
      this.safeSend(ws, ev);
    }
    ws.addEventListener("close", () => {
      this.viewers.delete(ws);
    });
    ws.addEventListener("error", () => {
      this.viewers.delete(ws);
    });
  }

  // --- 원격 마이크: 브라우저 송신 → jarvis 수신 포워딩 ---

  private attachMicSender(ws: WebSocket): void {
    if (this.micSender) {
      try { this.micSender.close(1000, "replaced"); } catch { /* */ }
    }
    this.micSender = ws;
    ws.addEventListener("message", (msg) => {
      const data = (msg as MessageEvent).data as string | ArrayBuffer;
      if (!this.micReceiver) {
        const now = Date.now();
        if (now - this.lastNoReceiverAt > 2000) {   // 프레임마다 통지하지 않도록 디바운스
          this.lastNoReceiverAt = now;
          this.safeSend(ws, { ts: now / 1000, seq: 0, kind: "no_receiver" } as RelayEvent);
        }
        return;
      }
      try {
        // binary(오디오) 와 string(제어) 둘 다 그대로 전달
        this.micReceiver.send(data);
      } catch { /* 수신측 끊김 — 다음 close 에서 정리 */ }
    });
    ws.addEventListener("close", () => { if (this.micSender === ws) this.micSender = null; });
    ws.addEventListener("error", () => { if (this.micSender === ws) this.micSender = null; });
  }

  private attachMicReceiver(ws: WebSocket): void {
    if (this.micReceiver) {
      try { this.micReceiver.close(1000, "replaced"); } catch { /* */ }
    }
    this.micReceiver = ws;
    ws.addEventListener("close", () => { if (this.micReceiver === ws) this.micReceiver = null; });
    ws.addEventListener("error", () => { if (this.micReceiver === ws) this.micReceiver = null; });
  }

  // --- helpers ---

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
    for (const ws of this.viewers) {
      this.safeSend(ws, ev);
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
