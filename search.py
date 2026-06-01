"""웹 검색 백엔드 — Serper.dev (구글 결과).

web_search(query) -> 텍스트(상위 결과 요약). LLM 도구 호출에서 사용한다.
백엔드 교체 시 이 함수의 입출력만 맞추면 나머지(llm.py)는 그대로.
"""
import httpx

import config

_ENDPOINT = "https://google.serper.dev/search"


async def web_search(query: str, n: int = 5) -> str:
    if not config.SERPER_API_KEY:
        return "검색 기능을 쓸 수 없습니다(SERPER_API_KEY 미설정)."
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                _ENDPOINT,
                headers={"X-API-KEY": config.SERPER_API_KEY},
                json={"q": query, "gl": "kr", "hl": "ko", "num": n},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"검색에 실패했습니다: {e}"

    lines = []
    ab = data.get("answerBox") or {}
    if ab.get("answer") or ab.get("snippet"):
        lines.append("요약: " + (ab.get("answer") or ab.get("snippet")))
    kg = data.get("knowledgeGraph") or {}
    if kg.get("description"):
        lines.append(f"{kg.get('title','')}: {kg['description']}")
    for item in (data.get("organic") or [])[:n]:
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        lines.append(f"- {title}: {snippet}")
    return "\n".join(lines) if lines else "검색 결과가 없습니다."
