// 회의 중계 이벤트 스키마. Jarvis(publisher) → Worker → viewer 모두 동일 형식.

export type EventKind =
  | "hello"                // publisher 첫 메시지: meta 전달
  | "source"               // STT 확정 원문
  | "translation"          // 번역 결과 (lang 필드로 대상 언어: ko|en|ja|zh)
  | "partial"              // STT 부분 결과 (스트리밍)
  | "info"                 // 일반 안내 텍스트
  | "gap"                  // 발화 묶음 사이 빈 줄
  | "end"                  // publisher 종료
  | "kicked"               // 새 publisher 가 들어와 기존 publisher 강퇴
  | "publisher_disconnected" // viewer 에게 publisher 연결 끊김 통보
  | "mic_start"
  | "mic_stop"
  | "no_receiver"
  | "mic_source"           // jarvis 가 듣는 소스 상태 (system|remote)
  | "mic_release"          // jarvis → owner: 무발화 타임아웃, 웹 마이크 해제 신호
  | "meeting_title"        // jarvis → owner: 회의 제목(헤더 표시)
  | "meeting_creds"        // jarvis → DO: 현재 회의 인증(미broadcast). text=JSON{meeting_id,password_hash}
  | "meeting_info"         // jarvis → owner: 공유용 링크/비번/언어. text=JSON{meeting_id,password,languages}
  | "archive_request"      // DO → jarvis: 종료 회의 열람 요청. text=JSON{req,mid,pw}
  | "archive_response"     // jarvis → DO: 열람 응답. text=JSON{req,ok,title,transcript,summaries}
  | "meeting_archive"      // DO → viewer: 종료 회의 기록. text=JSON{title,transcript,summaries}
  | "meeting_summary"      // jarvis → viewer: 언어별 요약(준비되면). text=JSON{mid,summaries}
  | "list"                 // viewer(admin) → DO: 최근 회의 목록 요청
  | "list_request"         // DO → jarvis: 목록 요청. text=JSON{req}
  | "list_response"        // jarvis → DO: 목록 응답. text=JSON{req,ok,meetings}
  | "meeting_list"         // DO → viewer: 목록. text=JSON{meetings}
  | "delete"               // viewer(admin) → DO: 회의 삭제 요청. {id}
  | "delete_request"       // DO → jarvis: 삭제 요청. text=JSON{req,id}
  | "delete_response"      // jarvis → DO: 삭제 응답. text=JSON{req,ok,id}
  | "meeting_deleted"      // DO → viewer: 삭제 완료. text=JSON{id,ok}
  | "meeting_live"         // DO → admin watcher(list.html): 진행중 회의 상태. text=JSON{live,id?,title?}
  | "user"
  | "assistant"
  | "navigate"
  | "viewers"
  | "settings";        // worker → owner viewer: 현재 설정 스냅샷(JSON in text)

export interface MeetingMeta {
  key: string;
  partner: string;
  partner_lang: string;
  user: string;
  user_lang: string;
  started_at?: string;
}

// publisher → worker: 클라이언트가 보내는 원형
export interface ClientMessage {
  kind: EventKind;
  text?: string;
  lang?: string;
  lid?: number;        // 소스 줄 id — source/translation 버블 매칭
  meta?: MeetingMeta;
  reason?: string;
  source?: "system" | "remote";
  count?: number;
}

// worker → viewer: 서버 메타 부착
export interface RelayEvent extends ClientMessage {
  ts: number;     // epoch seconds (소수 가능)
  seq: number;    // 채널 내 일련번호 (1부터)
}

export const MAX_REPLAY = 100;
