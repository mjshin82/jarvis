// jarvis-web/scripts/mic_relay_check.mjs
// 사용법: 터미널 1) cd jarvis-web && npm run dev   (.dev.vars 에 RELAY_TOKEN, ADMIN_PASSWORD)
//         터미널 2) cd jarvis-web && RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs
import WebSocket from "ws";

const BASE = process.env.BASE || "ws://localhost:8787";
const RELAY = process.env.RELAY_TOKEN || "devtoken";
const ADMIN = process.env.ADMIN_PASSWORD || "adminpw";
const KEY = "checkroom";

function open(url, opts = {}) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url, opts);
    ws.on("open", () => resolve(ws));
    ws.on("error", reject);
  });
}
function nextMsg(ws) {
  return new Promise((res) => ws.on("message", (d, isBinary) => res({ isBinary, text: isBinary ? null : d.toString() })));
}
const fail = (m) => { console.error("FAIL", m); process.exit(1); };

async function main() {
  // 1) /mic 은 RELAY_TOKEN 으로는 거부, ADMIN_PASSWORD 로만 통과
  let relayRejected = false;
  try { await open(`${BASE}/mic/${KEY}?token=${RELAY}`); } catch { relayRejected = true; }
  console.log("relay-token /mic 거부:", relayRejected ? "OK" : "FAIL");

  // 2) jarvis 수신측(/mic-recv, RELAY_TOKEN) 연결
  const recv = await open(`${BASE}/mic-recv/${KEY}`, { headers: { Authorization: `Bearer ${RELAY}` } });

  // 3) admin 송신측(/mic, ADMIN_PASSWORD) 연결 + binary 포워딩
  const recvBin = nextMsg(recv);
  const send = await open(`${BASE}/mic/${KEY}?token=${ADMIN}`);
  send.send(Buffer.from(new Int16Array([1, 2, 3, 4]).buffer));
  const b = await Promise.race([recvBin, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000))]).catch((e) => fail(e.message));
  console.log("binary 포워딩(admin):", b.isBinary ? "OK" : "FAIL");

  // 4) viewer 접속 후, receiver 가 mic_source 송신 → viewer 가 수신
  const viewer = await open(`${BASE}/subscribe/${KEY}?token=${ADMIN}`);
  const vMsg = (async () => {
    for (;;) {
      const m = await nextMsg(viewer);
      if (m.text && m.text.includes('"mic_source"')) return m.text;
    }
  })();
  recv.send(JSON.stringify({ kind: "mic_source", source: "remote" }));
  const vm = await Promise.race([vMsg, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000))]).catch((e) => fail(e.message));
  console.log("mic_source broadcast:", vm.includes('"source":"remote"') || vm.includes('"source": "remote"') ? "OK" : `FAIL (${vm})`);

  // 5) 신규 viewer 가 lastMicSource 동기화 수신
  const viewer2 = await open(`${BASE}/subscribe/${KEY}?token=${ADMIN}`);
  const v2 = (async () => {
    for (;;) {
      const m = await nextMsg(viewer2);
      if (m.text && m.text.includes('"mic_source"')) return m.text;
    }
  })();
  const sync = await Promise.race([v2, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000))]).catch((e) => fail(e.message));
  console.log("신규 viewer 동기화:", sync.includes("remote") ? "OK" : `FAIL (${sync})`);

  // 6) last-wins: 두 번째 admin sender 가 첫 번째를 밀어내면 첫 번째가 kicked 수신
  const kicked = nextMsg(send);
  const send2 = await open(`${BASE}/mic/${KEY}?token=${ADMIN}`);
  const km = await Promise.race([kicked, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000))]).catch((e) => fail(e.message));
  console.log("takeover kicked 통지:", km.text && km.text.includes('"kicked"') ? "OK" : `FAIL (${km.text})`);

  // 7) /subscribe 는 무토큰 거부, ADMIN_PASSWORD 로 통과
  let subRejected = false;
  try { await open(`${BASE}/subscribe/${KEY}`); } catch { subRejected = true; }
  console.log("subscribe 무토큰 거부:", subRejected ? "OK" : "FAIL");
  const sub = await open(`${BASE}/subscribe/${KEY}?token=${ADMIN}`);
  console.log("subscribe admin 통과:", "OK");
  sub.close();

  // 8) publisher(=jarvis) 가 보낸 binary(TTS) 를 viewer 가 받는다
  const pub = await open(`${BASE}/publish/${KEY}`, { headers: { Authorization: `Bearer ${RELAY}` } });
  const pubViewer = await open(`${BASE}/subscribe/${KEY}?token=${ADMIN}`);
  // 접속 직후 JSON sync/replay 이벤트가 먼저 올 수 있으므로 binary 프레임만 기다린다
  const gotAudio = (async () => {
    for (;;) {
      const m = await nextMsg(pubViewer);
      if (m.isBinary) return m;
    }
  })();
  const audioFrame = Buffer.concat([
    Buffer.from(Uint32Array.of(16000).buffer),
    Buffer.from(Int16Array.of(1, 2, 3).buffer),
  ]);
  pub.send(audioFrame);
  const a = await Promise.race([
    gotAudio,
    new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000)),
  ]).catch((e) => fail(e.message));
  console.log("publisher→viewer 오디오 binary:", a.isBinary ? "OK" : "FAIL");
  pub.close(); pubViewer.close();

  // 9) publisher 가 navigate 이벤트를 보내면 viewer 가 받는다
  const navPub = await open(`${BASE}/publish/${KEY}`, { headers: { Authorization: `Bearer ${RELAY}` } });
  const navViewer = await open(`${BASE}/subscribe/${KEY}?token=${ADMIN}`);
  const navMsg = (async () => {
    for (;;) {
      const m = await nextMsg(navViewer);
      if (m.text && m.text.includes('"navigate"')) return m.text;
    }
  })();
  navPub.send(JSON.stringify({ kind: "navigate", text: "meeting" }));
  const nv = await Promise.race([navMsg, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000))]).catch((e) => fail(e.message));
  console.log("navigate broadcast:", nv.includes('"meeting"') ? "OK" : `FAIL (${nv})`);
  navPub.close(); navViewer.close();

  [recv, send, send2, viewer, viewer2].forEach((w) => w.close());
  process.exit(0);
}
main().catch((e) => fail(e));
