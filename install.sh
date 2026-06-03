#!/usr/bin/env bash
# Jarvis 한 번에 설치 스크립트 (macOS Apple Silicon 기준).
#
# 사용: ./install.sh           # Python + 모델 (meeting-web 은 제외)
#       ./install.sh --no-models   # 모델 사전 다운로드 X
#       ./install.sh --web         # meeting-web (Node) 까지 설치
#
# 각 단계는 idempotent — 이미 설치된 항목은 건너뜁니다.
# 실패해도 명확히 보고하고, 다음 단계는 가급적 이어서 진행.

set -uo pipefail
cd "$(dirname "$0")"

# --- 옵션 ---
INSTALL_MODELS=1
INSTALL_WEB=0
for arg in "$@"; do
  case "$arg" in
    --no-models) INSTALL_MODELS=0 ;;
    --web)       INSTALL_WEB=1 ;;
    --no-web)    INSTALL_WEB=0 ;;   # 하위 호환 (기본값이므로 의미 없음)
    -h|--help)
      sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "❗ 알 수 없는 옵션: $arg"; exit 2 ;;
  esac
done

# --- 색 + 로깅 ---
if [[ -t 1 ]]; then
  GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; CYAN=$'\033[36m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
else
  GREEN=""; YELLOW=""; RED=""; CYAN=""; BOLD=""; OFF=""
fi
log()  { printf "%s==> %s%s\n" "$CYAN$BOLD" "$*" "$OFF"; }
ok()   { printf "%s  ✓ %s%s\n"  "$GREEN"   "$*" "$OFF"; }
warn() { printf "%s  ⚠ %s%s\n"  "$YELLOW"  "$*" "$OFF"; }
fail() { printf "%s  ✗ %s%s\n"  "$RED"     "$*" "$OFF"; }

# 진행 누적 (마지막에 요약)
declare -a SUMMARY=()
mark()  { SUMMARY+=("$1"); }

# --- 사전 점검 ---
log "환경 점검"
OS="$(uname -s)"
if [[ "$OS" != "Darwin" ]]; then
  warn "이 스크립트는 macOS 기준입니다 (감지된 OS: $OS). 계속 진행은 가능."
fi
PY="${PY:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  fail "python3 를 찾을 수 없습니다. https://www.python.org 에서 설치 후 재시도."
  exit 1
fi
ok "python3: $($PY --version)"

# --- Homebrew ---
log "Homebrew 의존성"
if command -v brew >/dev/null 2>&1; then
  ok "brew: $(brew --version | head -1)"
  # portaudio — PyAudio(=RealtimeSTT) 빌드에 필요
  if brew list --formula portaudio >/dev/null 2>&1; then
    ok "portaudio (이미 설치됨)"
  else
    log "portaudio 설치"
    if brew install portaudio; then ok "portaudio 설치 완료"; mark "portaudio 설치"
    else fail "portaudio 설치 실패 — 수동: brew install portaudio"; fi
  fi
  # ollama (cask) — local LLM. 이미 있으면 건너뜀.
  if command -v ollama >/dev/null 2>&1; then
    ok "ollama: $(ollama --version 2>&1 | head -1)"
  else
    log "ollama (cask) 설치"
    if brew install --cask ollama-app; then
      ok "ollama 설치 완료. 첫 실행: open -a Ollama"
      mark "ollama 설치"
    else
      warn "ollama 자동 설치 실패. 수동: brew install --cask ollama-app"
    fi
  fi
else
  warn "Homebrew 미설치. brew 없으면 portaudio/ollama 수동 설치 필요."
  warn "  설치: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
fi

# --- Python venv + 패키지 ---
log "Python venv (.venv)"
if [[ ! -d .venv ]]; then
  if "$PY" -m venv .venv; then ok "venv 생성"; mark "venv 생성"
  else fail "venv 생성 실패"; exit 1; fi
else
  ok ".venv (이미 존재)"
fi
# shellcheck source=/dev/null
source .venv/bin/activate
ok "venv 활성: $(which python)"

log "pip 업그레이드"
python -m pip install --quiet --upgrade pip
ok "pip: $(pip --version | awk '{print $2}')"

log "requirements.txt 설치"
if pip install -r requirements.txt; then
  ok "Python 패키지 설치 완료"
  mark "requirements 설치"
else
  fail "Python 패키지 설치 실패 — 위 에러 메시지 확인"
fi

# --- .env 생성 ---
log ".env 준비"
if [[ -f .env ]]; then
  ok ".env (이미 존재) — 건드리지 않음"
else
  if [[ -f .env.example ]]; then
    cp .env.example .env
    ok ".env 생성됨 — 키를 채워주세요 (DEEPSEEK_API_KEY 등)"
    mark ".env 생성"
  else
    warn ".env.example 이 없어 .env 를 만들지 못함"
  fi
fi

# --- 효과음 ---
log "효과음 (wake.wav / ok.wav)"
if [[ -f sound/fx/wake.wav && -f sound/fx/ok.wav ]]; then
  ok "효과음 파일 존재"
else
  if python scripts/make_fx.py >/dev/null 2>&1; then
    ok "효과음 생성"
    mark "효과음 생성"
  else
    warn "효과음 생성 실패 — 필요 시 python scripts/make_fx.py 수동"
  fi
fi

# --- 모델 사전 다운로드 (선택) ---
if (( INSTALL_MODELS )); then
  log "모델 사전 다운로드 (~수 GB, 시간 걸릴 수 있음)"

  # 1) faster-whisper STT (small, 기본)
  log "  faster-whisper STT 모델"
  if python - <<'PY'
import os, sys
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
from faster_whisper import WhisperModel
WhisperModel("small", device="cpu", compute_type="int8")
print("ok")
PY
  then ok "faster-whisper small 준비"; mark "STT 모델"
  else warn "faster-whisper 모델 다운로드 실패 — 첫 실행 시 자동 재시도"
  fi

  # 2) Supertonic TTS
  log "  Supertonic TTS 모델"
  if python - <<'PY'
from supertonic import TTS as SupertonicTTS
SupertonicTTS(model="supertonic-3", auto_download=True)
print("ok")
PY
  then ok "Supertonic 준비"; mark "TTS 모델"
  else warn "Supertonic 다운로드 실패 — 첫 실행 시 자동 재시도"
  fi

  # 3) openWakeWord
  log "  openWakeWord 'hey_jarvis'"
  if python - <<'PY'
import openwakeword.utils
openwakeword.utils.download_models(["hey_jarvis"])
print("ok")
PY
  then ok "openWakeWord 준비"; mark "Wake 모델"
  else warn "openWakeWord 다운로드 실패"
  fi

  # 4) Ollama 모델 (qwen3:4b-instruct)
  if command -v ollama >/dev/null 2>&1; then
    log "  Ollama 모델 (qwen3:4b-instruct, ~2.5GB)"
    if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "qwen3:4b-instruct"; then
      ok "qwen3:4b-instruct (이미 있음)"
    else
      # Ollama 서버가 안 떠있으면 ollama pull 이 실패할 수 있음 — 안내
      if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
        if ollama pull qwen3:4b-instruct; then
          ok "qwen3:4b-instruct 받음"; mark "Ollama 모델"
        else
          warn "qwen3:4b-instruct 다운로드 실패"
        fi
      else
        warn "Ollama 서버가 떠있지 않음. open -a Ollama 후 다시 시도:"
        warn "  ollama pull qwen3:4b-instruct"
      fi
    fi
  else
    warn "ollama 미설치 — local LLM 모드는 비활성. mock 또는 remote(DeepSeek) 로 사용 가능."
  fi
else
  warn "모델 사전 다운로드 건너뜀 (--no-models). 첫 실행 시 자동 다운로드됩니다."
fi

# --- meeting-web (Node, --web 옵션으로 명시했을 때만) ---
if (( INSTALL_WEB )); then
  log "meeting-web (회의 자막 중계 — Cloudflare Workers)"
  if [[ -d meeting-web ]]; then
    if command -v npm >/dev/null 2>&1; then
      pushd meeting-web >/dev/null
      if [[ -d node_modules ]]; then
        ok "meeting-web/node_modules (이미 설치됨)"
      else
        log "  npm install"
        if npm install --silent; then ok "meeting-web 의존성 설치"; mark "meeting-web 의존성"
        else warn "npm install 실패 — 수동: cd meeting-web && npm install"; fi
      fi
      if [[ -f .dev.vars ]]; then
        ok "meeting-web/.dev.vars (이미 있음)"
      elif [[ -f .dev.vars.example ]]; then
        cp .dev.vars.example .dev.vars
        ok "meeting-web/.dev.vars 생성됨 (RELAY_TOKEN=devtoken)"
        mark "meeting-web .dev.vars"
      fi
      popd >/dev/null
    else
      warn "npm 미설치 — meeting-web 은 별도 설치 필요 (brew install node)"
    fi
  else
    warn "meeting-web 폴더가 없음."
  fi
else
  warn "meeting-web 설치 건너뜀 (기본). 필요하면: ./install.sh --web"
fi

# --- 요약 ---
echo
log "${BOLD}설치 요약${OFF}"
if (( ${#SUMMARY[@]} == 0 )); then
  ok "새로 설치된 항목 없음 — 모든 의존성이 이미 준비된 상태"
else
  for s in "${SUMMARY[@]}"; do ok "$s"; done
fi

echo
log "${BOLD}다음 단계${OFF}"
cat <<EOM
  1. .env 의 키를 채워주세요:
       DEEPSEEK_API_KEY   (회의 번역 / remote LLM 사용 시)
       SERPER_API_KEY     (웹 검색 도구 사용 시)
       RELAY_URL / RELAY_TOKEN (회의 자막 외부 중계 사용 시)

  2. 실행:
       source .venv/bin/activate
       python main.py

  3. (선택) 회의 자막 중계 로컬 dev 서버:
       cd meeting-web && npm run dev
       # .env 에 RELAY_URL=ws://localhost:8787 / RELAY_TOKEN=devtoken

  4. Ollama 서버: open -a Ollama   (LLM_BACKEND=local 사용 시)
EOM
echo
ok "${BOLD}완료${OFF}"
