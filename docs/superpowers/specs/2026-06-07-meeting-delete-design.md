# 회의 목록 항목 삭제 설계

날짜: 2026-06-07

## 목표
관리자 회의 목록(`/{room}/meeting`)의 각 항목을 🗑 버튼 + `confirm()` 으로 삭제. 삭제 = `meetings.db` 에서 영구 제거. 기존 list/archive 온디맨드 중계 패턴 답습(관리자 검증 Worker, DO 무보관, jarvis SQLite).

## 비범위 (YAGNI)
- 일괄 삭제, 휴지통/복구, 커스텀 확인 모달(네이티브 `confirm()` 사용).
- 삭제 권한 분리(관리자 = 기존 ADMIN_PASSWORD 그대로).

---

## 데이터 흐름
list.html(관리자, 이미 열린 목록 소켓) → `{kind:"delete", id}` → DO(관리자 검증됨) → `delete_request{req,id}` → jarvis → `meeting_store.delete(id)` → `delete_response{req,ok,id}` → DO → 소켓에 `meeting_deleted{id,ok}` → list.html 이 해당 행 DOM 제거.

## meeting_store.py
- `delete(self, meeting_id)`: `DELETE FROM meetings WHERE id=?`.

## relay_client.py
- `__init__`: `self.on_delete_request = None`.
- `_handle_inbound`: `delete_request` → `self.on_delete_request(m)` 콜백(archive/list 패턴 동일).

## main.py
- `_serve_delete(msg)`: text 파싱 `{req,id}` → `store.delete(id)` → `web_pub.emit("delete_response", json.dumps({"req":req,"ok":True,"id":id}))`. `web_pub.on_delete_request = _serve_delete`.

## types.ts
- `EventKind` 추가: `"delete"`(viewer→DO), `"delete_request"`(DO→jarvis), `"delete_response"`(jarvis→DO), `"meeting_deleted"`(DO→viewer). PUBLIC_KINDS 미포함.

## meeting_do.ts — 목록 소켓을 영구 관리자 명령 채널로
현재 `attachWatchPending` 의 `list` 분기는 `done=true` 로 후속 메시지를 막고, 응답 타임아웃 시 소켓을 닫는다. 이를 **list/delete 를 반복 처리**하도록 교체:
- 상단 `if (done) return` 유지(auth 일회성 viewer 만 차단 — 목록 소켓은 auth 안 보내 done=false 유지).
- `list` 분기를 `list`/`delete` 공통 분기로 교체:
  ```typescript
  if (msg.kind === "list" || msg.kind === "delete") {
    clearTimeout(timer);
    if (!isAdmin) { try { ws.close(4003, "admin-only"); } catch { /* */ } return; }
    if (!this.publisher) { try { ws.close(4003, "no-meeting"); } catch { /* */ } return; }
    const req = ++this.archiveSeq;
    this.pendingArchive.set(req, ws);
    if (msg.kind === "list") {
      this.safeSend(this.publisher, this.buildEvent({ kind: "list_request", text: JSON.stringify({ req }) }));
    } else {
      this.safeSend(this.publisher, this.buildEvent({ kind: "delete_request", text: JSON.stringify({ req, id: msg.id }) }));
    }
    setTimeout(() => { this.pendingArchive.delete(req); }, 10000);   // 응답 타임아웃 — 소켓 유지(닫지 않음)
    return;
  }
  ```
  (`done` 안 세팅 → 같은 소켓으로 list 후 delete 여러 번 가능. 타임아웃 시 소켓 닫지 않고 pending 만 정리.)
- `handlePublisherMessage` 에 `delete_response` 핸들 추가(`list_response` 옆):
  ```typescript
  if (msg.kind === "delete_response") {
    let d: any; try { d = JSON.parse(msg.text || "{}"); } catch { return; }
    const ws = this.pendingArchive.get(d.req);
    if (!ws) return;
    this.pendingArchive.delete(d.req);
    this.safeSend(ws, this.buildEvent({ kind: "meeting_deleted", text: JSON.stringify({ id: d.id, ok: d.ok }) }));
    return;
  }
  ```

## list.html — 🗑 버튼 + 행 구조 변경
- 현재 행은 `<button class="row">`(클릭 시 이동). 중첩 버튼 불가 → 행을 **`<div class="row">`** 로 변경: 클릭 가능한 좌측 영역(제목+시각) + 우측 🗑 `<button>`.
  - 좌측 영역 클릭 → `location.href = /{room}/meeting/{id}`.
  - 🗑 클릭 → `if (confirm("이 회의를 삭제할까요?")) ws.send(JSON.stringify({kind:"delete", id}))`.
- `ws` 를 모듈 스코프로 보관(현재 `connect` 내부 const → 밖에서 delete 전송 위해). `connect` 가 `ws =` 에 할당.
- `handle`/onmessage: `meeting_list` → render(기존); `meeting_deleted{id, ok}` → ok 면 `data-id` 로 해당 행 제거(없으면 무시).
- CSS: `.row` 를 flex(`justify-content: space-between; align-items:center`), 좌측 `.row-main`(cursor:pointer, flex:1), `.row-del`(투명 버튼, 🗑).

## 테스트
- jarvis: `tests/test_meeting_store.py` — `delete(id)` 후 `get(id)` None, 다른 행 유지. `tests/test_relay_client.py` — `_handle_inbound` 의 `delete_request` → `on_delete_request` 호출.
- 웹/DO: `npm run typecheck` + list.html JS 구문. 수동: 목록에서 🗑 → confirm → Yes → 행 사라지고 DB 에서 삭제(새로고침해도 없음); 비관리자는 목록 자체 차단.

## 검증
- `.venv/bin/python -m pytest -q` 통과.
- `cd jarvis-web && npm run typecheck` 0, list.html JS 구문 OK.
- 수동(배포·재시작): 삭제·확인·행 제거·영속.

## 영향 파일
| 파일 | 변경 |
|---|---|
| `meeting_store.py` | `delete(id)` |
| `relay_client.py` | `on_delete_request` + `_handle_inbound` delete_request |
| `main.py` | `_serve_delete` 배선 |
| `jarvis-web/src/types.ts` | delete·delete_request·delete_response·meeting_deleted kind |
| `jarvis-web/src/meeting_do.ts` | list/delete 공통 분기(영구 채널) + delete_response 핸들 |
| `jarvis-web/src/static/list.html` | 행 구조 변경(div+🗑) + delete 전송 + meeting_deleted 행 제거 |
| `tests/test_*` | delete/relay 테스트 |

배포: jarvis 재시작 + 웹 `wrangler deploy`(자동). origin push 직접.
