"""Q&A 뱅크 파서 + 선택기.

시나리오와 짝을 이루는 scenarios/<key>.qa.md 를 섹션·QA 쌍으로 파싱한다.
guided/random/live 모드는 LLM 에게 질문을 만들게 하는 대신 이 뱅크에서
코드가 선택해 그대로 사용한다 → 시나리오 충실도 ↑, 모델 부담 ↓.

파일 형식(주신 마크다운 그대로):
    ## 1. About the Team

    **Q. ...?**

    답변 본문 여러 줄. 빈 줄 포함 가능.

    ---

    **Q. 다음 질문?**

    답변 본문.

    ## 2. ...

규칙:
- '## ' 헤더 = 새 섹션 시작 (라벨로 사용).
- '**Q. ' 로 시작하고 '**' 로 끝나는 라인 = 질문.
- 질문 라인 뒤부터 다음 질문/구분선('---')/다음 섹션 헤더 전까지가 답변.
- 답변 안의 빈 줄은 한 칸 공백으로 정규화(말로 듣기 좋게).
"""
import os
import random
import re
from dataclasses import dataclass, field
from functools import lru_cache

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios")
_Q_RE = re.compile(r"^\*\*Q\.\s*(.+?)\*\*\s*$")
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_HR_RE = re.compile(r"^---+\s*$")


@dataclass
class QA:
    question: str
    answer: str
    section: str          # 토픽 라벨 (예: "About the Team")
    section_index: int    # 섹션 순번(0-based)


@dataclass
class QABank:
    items: list[QA] = field(default_factory=list)

    def sections(self) -> list[str]:
        """등장 순서대로 중복 제거된 섹션 목록."""
        seen, out = set(), []
        for qa in self.items:
            if qa.section not in seen:
                seen.add(qa.section); out.append(qa.section)
        return out

    def by_section(self) -> list[list[QA]]:
        """섹션 순서대로 묶인 QA 그룹."""
        groups: dict[int, list[QA]] = {}
        for qa in self.items:
            groups.setdefault(qa.section_index, []).append(qa)
        return [groups[i] for i in sorted(groups)]


@lru_cache(maxsize=8)
def load(key: str) -> QABank | None:
    """scenarios/<key>.qa.md 를 읽어 QABank 로. 파일 없으면 None."""
    path = os.path.join(_DIR, f"{key}.qa.md")
    if not os.path.exists(path):
        return None
    section = "General"
    section_index = -1
    cur_q: str | None = None
    cur_a: list[str] = []
    items: list[QA] = []

    def flush():
        nonlocal cur_q, cur_a
        if cur_q is not None:
            answer = _normalize_answer("\n".join(cur_a))
            if answer:
                items.append(QA(
                    question=cur_q.strip(),
                    answer=answer,
                    section=section,
                    section_index=max(section_index, 0),
                ))
        cur_q = None
        cur_a = []

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            stripped = line.strip()
            # 섹션 헤더: 진행 중인 QA 마감 후 섹션 갱신
            m = _SECTION_RE.match(stripped)
            if m and not stripped.startswith("# "):  # '# 제목'(H1)은 무시
                flush()
                section = _clean_section(m.group(1))
                section_index += 1
                continue
            # 구분선: 진행 중인 QA 마감
            if _HR_RE.match(stripped):
                flush()
                continue
            # 새 질문
            qm = _Q_RE.match(stripped)
            if qm:
                flush()
                cur_q = qm.group(1)
                continue
            # 답변 본문 누적 (질문이 아직 안 시작했으면 무시 — 파일 상단 헤더 등)
            if cur_q is not None:
                cur_a.append(line)
        flush()

    return QABank(items=items) if items else None


def _clean_section(text: str) -> str:
    """'1. About the Team' → 'About the Team' 형태로 번호 제거."""
    return re.sub(r"^\d+[\.\)]\s*", "", text.strip())


def _normalize_answer(text: str) -> str:
    """여러 빈 줄/잡스페이스를 한 칸으로. 말로 듣기 좋게 평탄화."""
    # 연속 빈 줄 → 한 줄 공백, 줄바꿈 → 공백
    lines = [l.strip() for l in text.splitlines()]
    joined = " ".join(l for l in lines if l)
    return re.sub(r"\s+", " ", joined).strip()


# --- 선택기 ---

def pick_guided(bank: QABank, asked_keys: list[str]) -> QA | None:
    """guided/live 모드: 섹션 라운드로빈. 모든 섹션을 한 번씩 거치고 다시 반복.
    asked_keys 는 'sectionIdx:qIdx' 형태로 이미 낸 항목 식별."""
    groups = bank.by_section()
    if not groups:
        return None
    n_sections = len(groups)
    # 각 섹션에서 이미 몇 개 냈는지 카운트
    per_section_done = [0] * n_sections
    for k in asked_keys:
        try:
            s, _ = k.split(":")
            per_section_done[int(s)] += 1
        except Exception:
            pass
    # 가장 덜 낸 섹션부터(라운드로빈), 같은 카운트면 등장 순서
    order = sorted(range(n_sections), key=lambda i: (per_section_done[i], i))
    for si in order:
        section_qas = groups[si]
        for qi, qa in enumerate(section_qas):
            if f"{si}:{qi}" not in asked_keys:
                return qa
    return None  # 모두 소진


def pick_random(bank: QABank, asked_keys: list[str]) -> QA | None:
    """random 모드: 무작위. 모두 소진하면 누적 리셋(재출제)."""
    groups = bank.by_section()
    candidates: list[tuple[int, int, QA]] = []
    for si, section_qas in enumerate(groups):
        for qi, qa in enumerate(section_qas):
            key = f"{si}:{qi}"
            if key not in asked_keys:
                candidates.append((si, qi, qa))
    if not candidates:
        # 한 바퀴 다 돌았으면 전체 풀에서 무작위
        all_items = [(si, qi, qa) for si, g in enumerate(groups)
                     for qi, qa in enumerate(g)]
        if not all_items:
            return None
        candidates = all_items
    _, _, qa = random.choice(candidates)
    return qa


def key_of(bank: QABank, qa: QA) -> str:
    """asked_keys 에 누적할 식별자."""
    # bank.by_section() 의 순서로 qi 를 다시 찾기
    groups = bank.by_section()
    if qa.section_index < len(groups):
        section = groups[qa.section_index]
        for qi, item in enumerate(section):
            if item is qa:
                return f"{qa.section_index}:{qi}"
    return f"{qa.section_index}:?"
