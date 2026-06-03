#!/usr/bin/env bash
# Jarvis 실행 스크립트 (macOS Apple Silicon 기준).
#
# 사용: ./run.sh                # 자비스 본체 실행 (venv 활성 + Ollama 확인 후 python main.py)
#       ./run.sh meeting-web    # meeting-web 로컬 dev 서버 (wrangler dev)
#       ./run.sh --no-ollama    # Ollama 자동 기동/대기 건너뜀
#       ./run.sh --help
#
# 점검 항목 (자비스 실행 시):
#   - .venv/ 존재 여부 (없으면 ./install.sh 안내)
#   - .env 존재 여부 (없으면 .env.example 복사 후 경고)
#   - LLM_BACKEND=local 일 때 Ollama 서버 응답 확인 (없으면 'open -a Ollama' 시도)
#   - 위에서 어떤 모드든 실패 시 명확한 메시지로 종료

set -uo pipefail
cd "$(dirname "$0")"

# --- 색 ---
if [[ -t 1 ]]; then
  GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; CYAN=$'\033[36m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
else
  GREEN=""; YELLOW=""; RED=""; CYAN=""; BOLD=""; OFF=""
fi
log()  { printf "%s==> %s%s\n" "$CYAN$BOLD" "$*" "$OFF"; }
ok()   { printf "%s  ✓ %s%s\n"  "$GREEN"   "$*" "$OFF"; }
warn() { printf "%s  ⚠ %s%s\n"  "$YELLOW"  "$*" "$OFF"; }
fail() { printf "%s  ✗ %s%s\n"  "$RED"     "$*" "$OFF"; }

# --- 옵션 ---
TARGET="jarvis"
SKIP_OLLAMA=0
for arg in "$@"; do
  case "$arg" in
    meeting-web|web) TARGET="meeting-web" ;;
    jarvis)          TARGET="jarvis" ;;
    --no-ollama)     SKIP_OLLAMA=1 ;;
    -h|--help)
      sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "❗ 알 수 없는 옵션: $arg"; exit 2 ;;
  esac
done

# --- meeting-web 분기 ---
if [[ "$TARGET" == "meeting-web" ]]; then
  log "meeting-web 로컬 dev 서버"
  if [[ ! -d meeting-web ]]; then
    fail "meeting-web 폴더가 없습니다."; exit 1
  fi
  cd meeting-web
  if [[ ! -d node_modules ]]; then
    fail "node_modules 가 없습니다. 먼저 설치: ../install.sh --web"
    exit 1
  fi
  if [[ ! -f .dev.vars ]]; then
    if [[ -f .dev.vars.example ]]; then
      cp .dev.vars.example .dev.vars
      ok ".dev.vars 생성 (RELAY_TOKEN=devtoken)"
    fi
  fi
  ok "wrangler dev (http://localhost:8787)"
  exec npm run dev
fi

# --- 자비스 본체 ---
log "Jarvis 실행 준비"

# venv 확인
if [[ ! -d .venv ]]; then
  fail ".venv 가 없습니다. 먼저 설치: ./install.sh"
  exit 1
fi
# shellcheck source=/dev/null
source .venv/bin/activate
ok "venv 활성: $(which python)"

# .env 확인
if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    cp .env.example .env
    warn ".env 가 없어 .env.example 로 복사했습니다. 키를 채워주세요."
  else
    fail ".env 도 .env.example 도 없습니다."
    exit 1
  fi
else
  ok ".env"
fi

# .env 에서 LLM_BACKEND 만 가볍게 파싱 (다른 값은 python 이 알아서 로드)
LLM_BACKEND_VAL="$(grep -E '^LLM_BACKEND=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' | xargs || true)"
LLM_BACKEND_VAL="${LLM_BACKEND_VAL:-mock}"

# Ollama 점검 (LLM_BACKEND=local 일 때만)
if (( ! SKIP_OLLAMA )) && [[ "$LLM_BACKEND_VAL" == "local" ]]; then
  if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
    ok "Ollama 서버 응답"
  else
    warn "Ollama 서버가 응답하지 않습니다 — 'open -a Ollama' 로 시도"
    if open -a Ollama 2>/dev/null; then
      # 최대 ~15초 대기
      until curl -sf http://localhost:11434/api/version >/dev/null 2>&1; do
        sleep 1
        ((++TICK)) >/dev/null
        if (( ${TICK:-0} > 15 )); then
          warn "Ollama 응답 대기 시간 초과 — 'LLM_BACKEND=mock' 또는 'remote' 로 진행 가능"
          break
        fi
      done
      if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
        ok "Ollama 기동 완료"
      fi
    else
      warn "'open -a Ollama' 실패 — 수동 실행 필요"
    fi
  fi
fi

# 실행
log "python main.py"
exec python main.py
