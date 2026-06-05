// meeting-web/scripts/mic_relay_check.mjs
// 사용법: 터미널 1) cd meeting-web && npm run dev   (.dev.vars 에 RELAY_TOKEN, ADMIN_PASSWORD)
//         터미널 2) cd meeting-web && RELAY_TOKEN=devtoken ADMIN_PASSWORD=adminpw node scripts/mic_relay_check.mjs
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
  const viewer = await open(`${BASE}/subscribe/${KEY}`);
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
  const viewer2 = await open(`${BASE}/subscribe/${KEY}`);
  const v2 = (async () => {
    for (;;) {
      const m = await nextMsg(viewer2);
      if (m.text && m.text.includes('"mic_source"')) return m.text;
    }
  })();
  const sync = await Promise.race([v2, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 3000))]).catch((e) => fail(e.message));
  console.log("신규 viewer 동기화:", sync.includes("remote") ? "OK" : `FAIL (${sync})`);

  [recv, send, viewer, viewer2].forEach((w) => w.close());
  process.exit(0);
}
main().catch((e) => fail(e));
