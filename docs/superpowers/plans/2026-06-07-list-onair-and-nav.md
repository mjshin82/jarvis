# viewer→목록 네비 + 목록 i18n + 진행중 on-air Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** viewer 아이콘을 admin일 때 회의 목록으로 이동하게 하고, 목록 페이지에 i18n을 적용하며, 진행 중인 회의를 목록 최상단에 실시간 on-air로 표시한다.

**Architecture:** 전부 jarvis-web 전용. 공용 `i18n.js` 카탈로그에 `list.*`/`nav.toList` 키를 추가하고 `list.html`에 i18n 적용. DO(`meeting_do.ts`)는 인스턴스 메모리의 라이브 상태(`publisher`+`currentMeetingId`)를 신규 `meeting_live` 이벤트로 admin watcher(별도 `adminWatchers` 셋)에게 실시간 푸시한다. 파이썬/DB 변경 없음.

**Tech Stack:** Cloudflare Worker (Hono) + Durable Object + 바닐라 JS 정적 페이지, vitest + happy-dom.

**Spec:** `docs/superpowers/specs/2026-06-07-list-onair-and-nav-design.md`

---

## File Structure

| 파일 | 책임 | 변경 |
|------|------|------|
| `jarvis-web/src/static/i18n.js` | 공용 i18n 카탈로그 | Modify — `list.*` + `nav.toList` 키(ko/en/ja) |
| `jarvis-web/test/i18n.test.ts` | i18n 단위 테스트 | Modify — 신규 키 검증 |
| `jarvis-web/src/types.ts` | 이벤트 스키마 | Modify — `meeting_live` EventKind |
| `jarvis-web/src/meeting_do.ts` | 릴레이 DO | Modify — `adminWatchers` + live 브로드캐스트 |
| `jarvis-web/src/static/list.html` | 회의 목록 페이지 | Modify — i18n + on-air 렌더 |
| `jarvis-web/src/static/viewer.html` | 자막 뷰어 | Modify — admin 아이콘 네비 |

**작업 디렉터리:** 명령은 `jarvis-web/`에서 실행(별도 명시 없으면).

**의존:** Task 1(i18n 키) → Task 4·5에서 사용. Task 2(`meeting_live` 타입) → Task 3에서 사용. 순서대로 진행.

---

### Task 1: i18n.js — list.* + nav.toList 카탈로그 + 테스트

**Files:**
- Modify: `jarvis-web/src/static/i18n.js`
- Test: `jarvis-web/test/i18n.test.ts`

- [ ] **Step 1: 실패하는 테스트 추가**

`jarvis-web/test/i18n.test.ts`의 `describe("I18N._t (카탈로그 조회 + 치환)", ...)` 블록 안, 마지막 `it(...)` 다음에 새 `it` 블록을 추가한다:

```ts
  it("list 네임스페이스 + nav 키를 로케일별로 반환", () => {
    expect(I18N._t("ko", "list.header")).toBe("최근 회의");
    expect(I18N._t("en", "list.header")).toBe("Recent meetings");
    expect(I18N._t("ja", "list.header")).toBe("最近の会議");
    expect(I18N._t("ko", "list.empty")).toBe("저장된 회의 없음");
    expect(I18N._t("en", "list.deleteConfirm")).toBe("Delete this meeting?");
    expect(I18N._t("ja", "list.liveDefault")).toBe("進行中の会議");
    expect(I18N._t("en", "list.onAir")).toBe("🔴 ON AIR");
    expect(I18N._t("ja", "list.onAir")).toBe("🔴 ON AIR");
    expect(I18N._t("ko", "nav.toList")).toBe("회의 목록");
    expect(I18N._t("en", "nav.toList")).toBe("Meeting list");
  });
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

Run: `cd jarvis-web && npm run test`
Expected: FAIL — 신규 키가 없어 `_t`가 키 문자열(`"list.header"` 등)을 반환 → `toBe` 단언 실패.

- [ ] **Step 3: 카탈로그에 키 추가 (ko)**

`jarvis-web/src/static/i18n.js`의 `ko:` 블록에서 `"card.taken": "— 새 publisher 가 채널을 인수했습니다 —",` 줄 바로 다음에 추가:

```js
      "list.title": "회의 목록",
      "list.adminTitle": "🔒 관리자 로그인",
      "list.adminPwPlaceholder": "관리자 비번",
      "list.header": "최근 회의",
      "list.loading": "불러오는 중…",
      "list.empty": "저장된 회의 없음",
      "list.defaultTitle": "회의",
      "list.deleteTitle": "삭제",
      "list.deleteConfirm": "이 회의를 삭제할까요?",
      "list.adminOnly": "관리자 전용입니다.",
      "list.onAir": "🔴 ON AIR",
      "list.liveDefault": "진행 중인 회의",
      "nav.toList": "회의 목록",
```

- [ ] **Step 4: 카탈로그에 키 추가 (en)**

`en:` 블록에서 `"card.taken": "— A new publisher took over the channel —",` 줄 바로 다음에 추가:

```js
      "list.title": "Meetings",
      "list.adminTitle": "🔒 Admin login",
      "list.adminPwPlaceholder": "Admin password",
      "list.header": "Recent meetings",
      "list.loading": "Loading…",
      "list.empty": "No saved meetings",
      "list.defaultTitle": "Meeting",
      "list.deleteTitle": "Delete",
      "list.deleteConfirm": "Delete this meeting?",
      "list.adminOnly": "Admins only.",
      "list.onAir": "🔴 ON AIR",
      "list.liveDefault": "Live meeting",
      "nav.toList": "Meeting list",
```

- [ ] **Step 5: 카탈로그에 키 추가 (ja)**

`ja:` 블록에서 `"card.taken": "— 新しいパブリッシャーがチャンネルを引き継ぎました —",` 줄 바로 다음에 추가:

```js
      "list.title": "会議一覧",
      "list.adminTitle": "🔒 管理者ログイン",
      "list.adminPwPlaceholder": "管理者パスワード",
      "list.header": "最近の会議",
      "list.loading": "読み込み中…",
      "list.empty": "保存された会議はありません",
      "list.defaultTitle": "会議",
      "list.deleteTitle": "削除",
      "list.deleteConfirm": "この会議を削除しますか？",
      "list.adminOnly": "管理者専用です。",
      "list.onAir": "🔴 ON AIR",
      "list.liveDefault": "進行中の会議",
      "nav.toList": "会議一覧",
```

- [ ] **Step 6: 테스트 실행 — 통과 확인**

Run: `cd jarvis-web && npm run test`
Expected: PASS — 모든 i18n 테스트 통과(신규 `it` 포함) + smoke 통과.

- [ ] **Step 7: Commit**

```bash
cd jarvis-web && git add src/static/i18n.js test/i18n.test.ts
git commit -m "feat(web): i18n 카탈로그에 list.* / nav.toList 키 추가 (ko/en/ja)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: types.ts — meeting_live EventKind

**Files:**
- Modify: `jarvis-web/src/types.ts`

- [ ] **Step 1: EventKind 에 meeting_live 추가**

`jarvis-web/src/types.ts`의 `EventKind` 유니온에서 `| "meeting_deleted"      // DO → viewer: 삭제 완료. text=JSON{id,ok}` 줄 바로 다음에 추가:

```ts
  | "meeting_live"         // DO → admin watcher(list.html): 진행중 회의 상태. text=JSON{live,id?,title?}
```

- [ ] **Step 2: 타입체크 — 통과 확인**

Run: `cd jarvis-web && npm run typecheck`
Expected: PASS — 에러 없음. (`meeting_live`가 EventKind에 포함되어 이후 Task 3의 `buildEvent({kind:"meeting_live",...})`가 타입 충족.)

- [ ] **Step 3: Commit**

```bash
cd jarvis-web && git add src/types.ts
git commit -m "feat(web): meeting_live EventKind 추가 (DO→admin watcher 진행중 회의 상태)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: meeting_do.ts — adminWatchers + 라이브 상태 브로드캐스트

DO가 admin watch 소켓(list.html)을 별도 셋으로 추적하고, 라이브 회의 시작/제목변경/종료/끊김 시 `meeting_live`를 그 소켓들에만 푸시한다. 워커 런타임 테스트 하네스가 없어 단위 테스트는 없고 typecheck + dry-run으로 검증한다(스펙 테스트 전략 참조).

**Files:**
- Modify: `jarvis-web/src/meeting_do.ts`

- [ ] **Step 1: adminWatchers 필드 추가**

`private viewers: Map<WebSocket, "owner" | "public"> = new Map();` 줄 바로 다음에 추가:

```ts
  private adminWatchers: Set<WebSocket> = new Set();   // list.html admin 소켓 — meeting_live 푸시 대상
```

- [ ] **Step 2: 라이브 상태 헬퍼 추가**

`private notifyViewerCount(): void {` 메서드 정의 바로 위에 세 헬퍼를 추가한다:

```ts
  private isLive(): boolean {
    return this.publisher != null && this.currentMeetingId != null;
  }

  private liveTitle(): string | null {
    if (this.lastMeetingTitle) return this.lastMeetingTitle;
    if (this.meta) return `${this.meta.partner} ↔ ${this.meta.user}`;
    return null;
  }

  private sendLiveStatus(ws: WebSocket): void {
    const text = this.isLive()
      ? JSON.stringify({ live: true, id: this.currentMeetingId, title: this.liveTitle() })
      : JSON.stringify({ live: false });
    this.safeSend(ws, this.buildEvent({ kind: "meeting_live", text }));
  }

  private broadcastLiveStatus(): void {
    for (const ws of this.adminWatchers) this.sendLiveStatus(ws);
  }

```

- [ ] **Step 3: admin 등록 + 초기 스냅샷 (attachWatchPending)**

`attachWatchPending`의 list/delete 분기에서, `if (!this.publisher) { try { ws.close(4003, "no-meeting"); } catch { /* */ } return; }` 줄 바로 다음에 추가:

```ts
        if (!this.adminWatchers.has(ws)) { this.adminWatchers.add(ws); this.sendLiveStatus(ws); }
```

- [ ] **Step 4: close 시 adminWatchers 정리**

`attachWatchPending` 끝의 close 리스너를 교체한다. 변경 전:

```ts
    ws.addEventListener("close", () => clearTimeout(timer));
```

변경 후:

```ts
    ws.addEventListener("close", () => { clearTimeout(timer); this.adminWatchers.delete(ws); });
```

- [ ] **Step 5: publisher onClose 에 라이브 상태 갱신**

`attachPublisher`의 `attachSlot(...)` 옵션에서 `onClose`를 교체한다. 변경 전:

```ts
      onClose: () => this.broadcast(this.buildEvent({ kind: "publisher_disconnected" })),
```

변경 후:

```ts
      onClose: () => { this.broadcast(this.buildEvent({ kind: "publisher_disconnected" })); this.broadcastLiveStatus(); },
```

- [ ] **Step 6: meeting_creds 후 라이브 시작 브로드캐스트**

`handlePublisherMessage`의 `meeting_creds` 분기에서 `} catch { /* */ }` 다음, `return;` 앞에 추가. 변경 전:

```ts
      try {
        const c = JSON.parse(msg.text || "{}");
        this.currentMeetingId = c.meeting_id ?? null;
        this.currentPasswordHash = c.password_hash ?? null;
      } catch { /* */ }
      return;   // DO 전용 — broadcast/append 안 함
```

변경 후:

```ts
      try {
        const c = JSON.parse(msg.text || "{}");
        this.currentMeetingId = c.meeting_id ?? null;
        this.currentPasswordHash = c.password_hash ?? null;
      } catch { /* */ }
      this.broadcastLiveStatus();
      return;   // DO 전용 — broadcast/append 안 함
```

- [ ] **Step 7: meeting_title 시 제목 갱신 브로드캐스트**

`meeting_title` 분기를 교체한다. 변경 전:

```ts
    if (msg.kind === "meeting_title") {
      this.lastMeetingTitle = msg.text ?? null;
      this.broadcast(this.buildEvent(msg));
      return;
    }
```

변경 후:

```ts
    if (msg.kind === "meeting_title") {
      this.lastMeetingTitle = msg.text ?? null;
      this.broadcast(this.buildEvent(msg));
      this.broadcastLiveStatus();
      return;
    }
```

- [ ] **Step 8: end 시 라이브 종료 브로드캐스트**

`end` 분기에서 `this.publisher = null;` 다음, `return;` 앞에 추가. 변경 전:

```ts
      this.broadcast(this.buildEvent(msg));
      try { this.publisher?.close(1000, "end"); } catch { /* */ }
      this.publisher = null;
      return;
```

변경 후:

```ts
      this.broadcast(this.buildEvent(msg));
      try { this.publisher?.close(1000, "end"); } catch { /* */ }
      this.publisher = null;
      this.broadcastLiveStatus();
      return;
```

- [ ] **Step 9: navigate(홈 복귀) 시 라이브 종료 브로드캐스트**

`navigate` 분기의 `if (msg.text !== "meeting")` 블록 안, `this.events = [];` 다음 줄(블록 닫기 `}` 앞)에 추가. 변경 전:

```ts
      if (msg.text !== "meeting") {
        this.lastMeetingTitle = null;
        this.currentMeetingId = null;
        this.currentPasswordHash = null;
        this.lastMeetingInfo = null;
        this.events = [];                // 회의 종료(홈 복귀) — replay 버퍼 비움
      }
      this.broadcast(this.buildEvent(msg));
      return;
```

변경 후:

```ts
      if (msg.text !== "meeting") {
        this.lastMeetingTitle = null;
        this.currentMeetingId = null;
        this.currentPasswordHash = null;
        this.lastMeetingInfo = null;
        this.events = [];                // 회의 종료(홈 복귀) — replay 버퍼 비움
        this.broadcastLiveStatus();
      }
      this.broadcast(this.buildEvent(msg));
      return;
```

- [ ] **Step 10: 타입체크 — 통과 확인**

Run: `cd jarvis-web && npm run typecheck`
Expected: PASS — 에러 없음.

- [ ] **Step 11: 번들 검증 (dry-run)**

Run: `cd jarvis-web && npx wrangler deploy --dry-run --outdir /tmp/jarvis-web-build`
Expected: 빌드 성공(`Total Upload` 출력).

- [ ] **Step 12: Commit**

```bash
cd jarvis-web && git add src/meeting_do.ts
git commit -m "feat(web): DO 진행중 회의 상태를 admin watcher 에 meeting_live 로 실시간 푸시

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: list.html — i18n + on-air 렌더

목록 페이지에 i18n.js를 로드하고 정적/동적 문자열을 치환하며, `meeting_live` 수신 시 저장 목록 위에 on-air 행을 실시간 표시한다.

**Files:**
- Modify: `jarvis-web/src/static/list.html`

- [ ] **Step 1: i18n.js 로드 + on-air 스타일**

`</style>` 와 `</head>` 사이에 i18n 로더를 추가한다. 변경 전:

```html
  #gate button { font-size:15px; padding:10px; border-radius:8px; cursor:pointer; }
</style>
</head>
```

변경 후:

```html
  #gate button { font-size:15px; padding:10px; border-radius:8px; cursor:pointer; }
  .onair-row { border-color:#e11d48; }
  .onair-badge { font-size:12px; font-weight:700; color:#e11d48; letter-spacing:.04em; margin-bottom:4px; }
</style>
<script src="/i18n.js"></script>
</head>
```

- [ ] **Step 2: 정적 문자열 속성화 + on-air 컨테이너**

`<title>`(L6)에 `data-i18n`을 부여한다. 변경 전:

```html
<title>회의 목록</title>
```

변경 후:

```html
<title data-i18n="list.title">회의 목록</title>
```

gate / header 정적 문자열에 속성을 부여하고 `<main>`에 on-air 컨테이너를 넣는다. 변경 전:

```html
  <div id="gate" class="hidden">
    <div class="gate-box">
      <div style="font-weight:600">🔒 관리자 로그인</div>
      <input id="gate-pw" type="password" placeholder="관리자 비번" />
      <button id="gate-go">입장</button>
    </div>
  </div>
  <header>최근 회의</header>
  <main><div id="msg">불러오는 중…</div><div id="list"></div></main>
```

변경 후:

```html
  <div id="gate" class="hidden">
    <div class="gate-box">
      <div style="font-weight:600" data-i18n="list.adminTitle">🔒 관리자 로그인</div>
      <input id="gate-pw" type="password" placeholder="관리자 비번" data-i18n-ph="list.adminPwPlaceholder" />
      <button id="gate-go" data-i18n="gate.enter">입장</button>
    </div>
  </div>
  <header data-i18n="list.header">최근 회의</header>
  <main>
    <div id="onair"></div>
    <div id="msg" data-i18n="list.loading">불러오는 중…</div>
    <div id="list"></div>
  </main>
```

- [ ] **Step 3: render() 동적 문자열 치환**

`render(meetings)` 함수 내 하드코딩 문자열을 `I18N.t()`로 교체한다. 변경 전:

```js
    if (!meetings || !meetings.length) { $("msg").textContent = "저장된 회의 없음"; return; }
    $("msg").textContent = "";
    for (const m of meetings) {
      const row = document.createElement("div");
      row.className = "row";
      row.dataset.id = m.id;
      const main = document.createElement("div");
      main.className = "row-main";
      main.innerHTML = `<div class="t"></div><div class="d"></div>`;
      main.querySelector(".t").textContent = m.title || "회의";
      main.querySelector(".d").textContent = fmt(m.started_at);
      main.addEventListener("click", () => { location.href = `/${encodeURIComponent(room)}/meeting/${encodeURIComponent(m.id)}`; });
      const del = document.createElement("button");
      del.className = "row-del"; del.textContent = "🗑"; del.title = "삭제";
      del.addEventListener("click", () => {
        if (!confirm("이 회의를 삭제할까요?")) return;
        if (ws && ws.readyState === 1) ws.send(JSON.stringify({ kind: "delete", id: m.id }));
      });
```

변경 후:

```js
    if (!meetings || !meetings.length) { $("msg").textContent = I18N.t("list.empty"); return; }
    $("msg").textContent = "";
    for (const m of meetings) {
      const row = document.createElement("div");
      row.className = "row";
      row.dataset.id = m.id;
      const main = document.createElement("div");
      main.className = "row-main";
      main.innerHTML = `<div class="t"></div><div class="d"></div>`;
      main.querySelector(".t").textContent = m.title || I18N.t("list.defaultTitle");
      main.querySelector(".d").textContent = fmt(m.started_at);
      main.addEventListener("click", () => { location.href = `/${encodeURIComponent(room)}/meeting/${encodeURIComponent(m.id)}`; });
      const del = document.createElement("button");
      del.className = "row-del"; del.textContent = "🗑"; del.title = I18N.t("list.deleteTitle");
      del.addEventListener("click", () => {
        if (!confirm(I18N.t("list.deleteConfirm"))) return;
        if (ws && ws.readyState === 1) ws.send(JSON.stringify({ kind: "delete", id: m.id }));
      });
```

- [ ] **Step 4: renderLive() on-air 함수 추가**

`render(meetings)` 함수 정의 바로 다음(닫는 `}` 다음 줄)에 새 함수를 추가한다:

```js
  function renderLive(info) {
    const box = $("onair");
    box.innerHTML = "";
    if (!info || !info.live || !info.id) return;
    const row = document.createElement("div");
    row.className = "row onair-row";
    row.style.cursor = "pointer";
    const main = document.createElement("div");
    main.className = "row-main";
    main.innerHTML = `<div class="onair-badge"></div><div class="t"></div>`;
    main.querySelector(".onair-badge").textContent = I18N.t("list.onAir");
    main.querySelector(".t").textContent = info.title || I18N.t("list.liveDefault");
    row.addEventListener("click", () => { location.href = `/${encodeURIComponent(room)}/meeting/${encodeURIComponent(info.id)}`; });
    row.appendChild(main);
    box.appendChild(row);
  }
```

- [ ] **Step 5: meeting_live 메시지 처리 + 런타임 로딩/관리자전용 문구 치환**

WebSocket `message` 핸들러에 `meeting_live` 분기를 추가한다. 변경 전:

```js
        const ev = JSON.parse(e.data);
        if (ev.kind === "meeting_list") { render(JSON.parse(ev.text || "{}").meetings); }
        else if (ev.kind === "meeting_deleted") {
```

변경 후:

```js
        const ev = JSON.parse(e.data);
        if (ev.kind === "meeting_list") { render(JSON.parse(ev.text || "{}").meetings); }
        else if (ev.kind === "meeting_live") { try { renderLive(JSON.parse(ev.text || "{}")); } catch {} }
        else if (ev.kind === "meeting_deleted") {
```

close 핸들러의 "관리자 전용입니다." 치환. 변경 전:

```js
        localStorage.removeItem(KEY);
        $("msg").textContent = "관리자 전용입니다.";
        $("gate").classList.remove("hidden");
```

변경 후:

```js
        localStorage.removeItem(KEY);
        $("msg").textContent = I18N.t("list.adminOnly");
        $("gate").classList.remove("hidden");
```

gate 입장 시 런타임 로딩 문구 치환. 변경 전:

```js
    $("gate").classList.add("hidden");
    $("msg").textContent = "불러오는 중…";
    connect(pw);
```

변경 후:

```js
    $("gate").classList.add("hidden");
    $("msg").textContent = I18N.t("list.loading");
    connect(pw);
```

- [ ] **Step 6: 정적 속성 검증**

Run: `cd jarvis-web && grep -c 'data-i18n' src/static/list.html`
Expected: `6` (list.title, list.adminTitle, gate.enter, list.header = `data-i18n` 4개 + list.loading 1개 + `data-i18n-ph` list.adminPwPlaceholder 1개 = 6).

Run: `cd jarvis-web && grep -F '<script src="/i18n.js">' src/static/list.html`
Expected: 1줄 매칭.

- [ ] **Step 7: 동적 하드코딩 제거 + t() 사용 검증**

다음 grep은 모두 매칭 0줄(JS에서 제거 확인):

Run: `cd jarvis-web && grep -F '저장된 회의 없음' src/static/list.html`
Expected: 매칭 없음(exit 1).

Run: `cd jarvis-web && grep -F '이 회의를 삭제할까요?' src/static/list.html`
Expected: 매칭 없음(exit 1).

Run: `cd jarvis-web && grep -F '관리자 전용입니다.' src/static/list.html`
Expected: 매칭 없음(exit 1).

Run: `cd jarvis-web && grep -o 'I18N.t(' src/static/list.html | wc -l`
Expected: `8` (list.empty, list.defaultTitle, list.deleteTitle, list.deleteConfirm, list.adminOnly, list.loading, list.onAir, list.liveDefault).

- [ ] **Step 8: 번들 검증 + 회귀 테스트**

Run: `cd jarvis-web && npx wrangler deploy --dry-run --outdir /tmp/jarvis-web-build`
Expected: 빌드 성공(`Total Upload`).

Run: `cd jarvis-web && npm run test`
Expected: PASS — 기존 테스트 전부 통과(list.html은 테스트 대상 아님, 회귀 없음).

- [ ] **Step 9: Commit**

```bash
cd jarvis-web && git add src/static/list.html
git commit -m "feat(web): 목록 페이지 i18n 적용 + 진행중 회의 on-air 실시간 표시

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: viewer.html — admin 아이콘 → 목록 네비

viewer 상단 좌측 아이콘을 admin일 때만 클릭 가능하게 해 `/{room}/meeting`으로 이동시킨다.

**Files:**
- Modify: `jarvis-web/src/static/viewer.html`

- [ ] **Step 1: 아이콘 네비 로직 추가**

본문 IIFE에서 `const $ = (id) => document.getElementById(id);` 줄 바로 다음에 추가한다. 변경 전:

```js
  const adminPw = localStorage.getItem("jarvis_admin_pw") || "";
  const $ = (id) => document.getElementById(id);
  let lastCard = null, draftCard = null, lockedToBottom = true;
```

변경 후:

```js
  const adminPw = localStorage.getItem("jarvis_admin_pw") || "";
  const $ = (id) => document.getElementById(id);
  if (adminPw) {
    const icon = document.querySelector(".app-icon");
    if (icon) {
      icon.style.cursor = "pointer";
      icon.title = I18N.t("nav.toList");
      icon.addEventListener("click", () => { location.href = `/${encodeURIComponent(key)}/meeting`; });
    }
  }
  let lastCard = null, draftCard = null, lockedToBottom = true;
```

- [ ] **Step 2: 적용 검증**

Run: `cd jarvis-web && grep -F 'I18N.t("nav.toList")' src/static/viewer.html`
Expected: 1줄 매칭.

Run: `cd jarvis-web && grep -F 'location.href = `/${encodeURIComponent(key)}/meeting`' src/static/viewer.html`
Expected: 1줄 매칭.

- [ ] **Step 3: 번들 검증 + 회귀 테스트**

Run: `cd jarvis-web && npx wrangler deploy --dry-run --outdir /tmp/jarvis-web-build`
Expected: 빌드 성공.

Run: `cd jarvis-web && npm run test`
Expected: PASS — 기존 테스트 전부 통과.

- [ ] **Step 4: Commit**

```bash
cd jarvis-web && git add src/static/viewer.html
git commit -m "feat(web): viewer 아이콘 클릭 시 admin 이면 회의 목록으로 이동

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 배포 (finishing 단계에서)

[[auto-deploy-web]]: 머지 후 `cd jarvis-web && npx wrangler deploy` 자동 실행. jarvis 재시작/push는 사용자 몫.

수동 확인(배포 후):
- viewer(`/Concode/meeting/<mid>`)에서 admin 로그인 상태로 좌측 아이콘 클릭 → `/Concode/meeting` 이동.
- 목록 페이지 `?lang=ja`/`?lang=en` → UI 문구 전환.
- 회의 시작 → 목록 최상단에 🔴 ON AIR 행 실시간 등장, 클릭 시 라이브 뷰어 입장. 회의 종료 → 행 소멸.

---

## Self-Review

**1. Spec coverage**

- 변경1 viewer 아이콘(admin 한정 네비): Task 5 ✓
- 변경2 list.html i18n(정적 5 + 동적): Task 1(카탈로그) + Task 4(속성+t()) ✓
- 변경2 게이트 버튼 gate.enter 재사용: Task 4 Step 2 ✓
- 변경3 meeting_live 이벤트: Task 2(타입) ✓
- 변경3 adminWatchers 추적 + 등록/정리: Task 3 Step 1,3,4 ✓
- 변경3 라이브 판정/제목 폴백/스냅샷/브로드캐스트 헬퍼: Task 3 Step 2 ✓
- 변경3 호출 시점(creds/title/end/navigate/onClose): Task 3 Step 5–9 ✓
- 변경3 list.html on-air 렌더 + 실시간 갱신: Task 4 Step 1,4,5 ✓
- 테스트 전략(i18n 단위 + DO/list/viewer typecheck·dry-run·수동): Task 1 테스트, Task 3/4/5 검증 단계 ✓

갭 없음.

**2. Placeholder scan**

TBD/TODO 없음. 모든 코드 스텝에 전체 코드. grep/typecheck/dry-run 검증은 정확한 명령·기대 결과 명시. DO 단위 테스트 부재는 스펙에서 합의된 의도(워커 테스트 하네스 미도입) — 각 검증 단계에 명시.

**3. Type/name consistency**

- `meeting_live` JSON 형태 `{live, id, title}`: DO `sendLiveStatus`(Task 3 Step 2) 생성 ↔ list.html `renderLive`(Task 4 Step 4) 소비 — 필드명 `live`/`id`/`title` 일치 ✓
- 카탈로그 키: Task 1 정의 ↔ Task 4 속성/`t()` 인자(`list.title`,`list.adminTitle`,`list.adminPwPlaceholder`,`list.header`,`list.loading`,`list.empty`,`list.defaultTitle`,`list.deleteTitle`,`list.deleteConfirm`,`list.adminOnly`,`list.onAir`,`list.liveDefault`) ↔ Task 5(`nav.toList`) 전부 일치 ✓
- 헬퍼명 `isLive`/`liveTitle`/`sendLiveStatus`/`broadcastLiveStatus` Task 3 내부 일관 ✓
- `gate.enter` 키는 기존 i18n.js에 이미 존재(이전 기능) — Task 4에서 재사용, 신규 정의 불필요 ✓
- `adminWatchers` 필드명 Task 3 Step 1 정의 ↔ Step 3,4,2(broadcast) 사용 일치 ✓
