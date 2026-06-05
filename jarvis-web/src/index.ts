/**
 * Meeting Web Relay — Cloudflare Worker entry.
 *
 * 라우트:
 *   GET  /healthz             상태 확인
 *   GET  /:name               홈 HTML (정적, 로그인)
 *   GET  /:name/meeting       회의 자막 뷰 HTML (정적, 로그인)
 *   GET  /subscribe/:key      WebSocket: viewer (ADMIN_PASSWORD 필요)
 *   GET  /publish/:key        WebSocket: publisher=jarvis (RELAY_TOKEN 필요)
 *   GET  /mic/:key            WebSocket: 마이크 송신 (ADMIN_PASSWORD 필요)
 *   GET  /mic-recv/:key       WebSocket: 마이크 수신=jarvis (RELAY_TOKEN 필요)
 *
 * 회의 키별로 MeetingDO (Durable Object) 1 개 인스턴스로 라우팅한다.
 */
import { Hono } from "hono";
import { MeetingDO } from "./meeting_do";
// HTML 을 텍스트로 번들. wrangler 가 esbuild loader 로 처리.
import APP_HTML from "./static/app.html";

export { MeetingDO };

interface Env {
  RELAY_TOKEN: string;
  ADMIN_PASSWORD: string;
  MEETING_DO: DurableObjectNamespace;
}

const app = new Hono<{ Bindings: Env }>();

app.get("/healthz", (c) => c.json({ ok: true }));

app.get("/subscribe/:key", async (c) => {
  if (!requireAdmin(c)) return c.text("unauthorized", 401);
  if (c.req.header("Upgrade") !== "websocket") {
    return c.text("expected websocket", 426);
  }
  return forwardToDO(c.env, c.req.param("key"), "subscribe", c.req.raw);
});

app.get("/publish/:key", async (c) => {
  // 토큰 검증
  const auth = c.req.header("Authorization") || "";
  const token = auth.replace(/^Bearer\s+/i, "").trim();
  if (!token || token !== c.env.RELAY_TOKEN) {
    return c.text("unauthorized", 401);
  }
  if (c.req.header("Upgrade") !== "websocket") {
    return c.text("expected websocket", 426);
  }
  return forwardToDO(c.env, c.req.param("key"), "publish", c.req.raw);
});

app.get("/mic/:key", async (c) => {
  if (!requireAdmin(c)) return c.text("unauthorized", 401);
  if (c.req.header("Upgrade") !== "websocket") return c.text("expected websocket", 426);
  return forwardToDO(c.env, c.req.param("key"), "mic", c.req.raw);
});

app.get("/mic-recv/:key", async (c) => {
  if (!requireRelayToken(c)) return c.text("unauthorized", 401);
  if (c.req.header("Upgrade") !== "websocket") return c.text("expected websocket", 426);
  return forwardToDO(c.env, c.req.param("key"), "mic-recv", c.req.raw);
});

app.get("/control/:key", async (c) => {
  if (!requireAdmin(c)) return c.text("unauthorized", 401);
  if (c.req.header("Upgrade") !== "websocket") return c.text("expected websocket", 426);
  return forwardToDO(c.env, c.req.param("key"), "control", c.req.raw);
});

app.get("/control-recv/:key", async (c) => {
  if (!requireRelayToken(c)) return c.text("unauthorized", 401);
  if (c.req.header("Upgrade") !== "websocket") return c.text("expected websocket", 426);
  return forwardToDO(c.env, c.req.param("key"), "control-recv", c.req.raw);
});

app.get("/:name/meeting", (c) => {
  return new Response(APP_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});

app.get("/:name", (c) => {
  return new Response(APP_HTML, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
});

app.notFound((c) => c.text("not found", 404));

function requireRelayToken(c: any): boolean {
  return checkSecret(c, c.env.RELAY_TOKEN);
}

function requireAdmin(c: any): boolean {
  return checkSecret(c, c.env.ADMIN_PASSWORD);
}

function checkSecret(c: any, expected: string): boolean {
  const auth = c.req.header("Authorization") || "";
  const headerTok = auth.replace(/^Bearer\s+/i, "").trim();
  const queryTok = (c.req.query("token") || "").trim();
  const tok = headerTok || queryTok;
  return !!tok && !!expected && tok === expected;
}

function forwardToDO(env: Env, key: string, role: "publish" | "subscribe" | "mic" | "mic-recv" | "control" | "control-recv", original: Request): Promise<Response> {
  const id = env.MEETING_DO.idFromName(key);
  const stub = env.MEETING_DO.get(id);
  // DO 가 라우팅에 활용할 내부 경로
  const internalUrl = new URL(original.url);
  internalUrl.pathname = `/__do/${role}/${encodeURIComponent(key)}`;
  const req = new Request(internalUrl.toString(), original);
  return stub.fetch(req);
}

export default app;
