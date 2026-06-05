// meeting-web/scripts/mic_relay_check.mjs
// 사용법: 터미널 1) cd meeting-web && RELAY_TOKEN=devtoken npm run dev
//         터미널 2) cd meeting-web && RELAY_TOKEN=devtoken node scripts/mic_relay_check.mjs
import WebSocket from "ws";

const BASE = process.env.BASE || "ws://localhost:8787";
const TOKEN = process.env.RELAY_TOKEN || "devtoken";
const KEY = "checkroom";

function open(url, opts = {}) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url, opts);
    ws.on("open", () => resolve(ws));
    ws.on("error", reject);
  });
}

async function main() {
  // 1) 무토큰 /mic 은 거부되어야
  let rejected = false;
  try { await open(`${BASE}/mic/${KEY}`); }
  catch { rejected = true; }
  console.log("무토큰 거부:", rejected ? "OK" : "FAIL");

  // 2) 수신자(jarvis 모사) 먼저 연결
  const recv = await open(`${BASE}/mic-recv/${KEY}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  const got = new Promise((res) => recv.on("message", (d, isBinary) => res({ isBinary, len: d.length })));

  // 3) 송신자(브라우저 모사) 연결 후 binary 전송
  const send = await open(`${BASE}/mic/${KEY}?token=${TOKEN}`);
  send.send(Buffer.from(new Int16Array([1, 2, 3, 4]).buffer));

  const r = await Promise.race([got, new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 3000))]);
  console.log("binary 포워딩:", r.isBinary && r.len === 8 ? "OK" : `FAIL (${JSON.stringify(r)})`);

  recv.close(); send.close();
  process.exit(0);
}
main().catch((e) => { console.error("FAIL", e); process.exit(1); });
