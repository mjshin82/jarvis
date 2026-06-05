// 회의 중계 이벤트 스키마. Jarvis(publisher) → Worker → viewer 모두 동일 형식.

export type EventKind =
  | "hello"                // publisher 첫 메시지: meta 전달
  | "source"               // STT 확정 원문
  | "translation_ko"       // 번역 결과 (한국어)
  | "translation_en"       // 번역 결과 (영어)
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
  | "user"
  | "assistant"
  | "navigate";

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
  meta?: MeetingMeta;
  reason?: string;
  source?: "system" | "remote";
}

// worker → viewer: 서버 메타 부착
export interface RelayEvent extends ClientMessage {
  ts: number;     // epoch seconds (소수 가능)
  seq: number;    // 채널 내 일련번호 (1부터)
}

export const MAX_REPLAY = 100;
