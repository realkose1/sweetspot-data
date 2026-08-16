#!/usr/bin/env python3
"""몇관몇열 큐레이션 데이터 일일 자동 점검 (GitHub Actions에서 매일 실행).

Claude API(웹 검색 포함)로 curated.json의 영화 데이터를 사실 확인하고,
공식 출처로 확인된 변경만 적용한 뒤 dataTimestamp를 갱신한다.

안전 장치 (앱 쪽 CuratedFeedValidator와 같은 정신):
- 영화(works)의 허용된 필드만 수정 가능. 상영관(halls)·영화 추가/삭제는 자동화 범위 밖.
- 모든 변경은 스크립트가 재검증 (필드 화이트리스트, 날짜 형식, 알려진 포맷 코드).
- 한 번에 MAX_CHANGES개 초과 변경 제안 시 전체 거부 (모델 폭주 방어).
- 파싱/검증 실패 시 아무것도 쓰지 않고 실패 종료 → Actions 로그에 표시.
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from anthropic import Anthropic

KST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).resolve().parent.parent
CURATED = REPO_ROOT / "curated.json"
REVIEW_NOTES = REPO_ROOT / "review_notes.md"

# ios/Sources/Models/FormatData.swift의 FormatCode raw value와 동일해야 한다.
KNOWN_CODES = {"IMAX43", "IMAX190", "DOLBY", "SCREENX", "4DX", "SUPERPLEX", "STD"}
MUTABLE_FIELDS = {"date", "run", "meta", "hook", "open", "premiumEnd", "badges", "recommendedFormat"}
DATE_FIELDS = {"date", "premiumEnd"}
NULLABLE_FIELDS = {"date", "run", "hook", "open", "premiumEnd"}
MAX_CHANGES = 6
DATE_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")


def build_prompt(works: list, today: datetime) -> str:
    works_json = json.dumps(works, ensure_ascii=False, indent=1)
    return f"""당신은 한국 특수 상영관(IMAX · 돌비 시네마 · ScreenX · 4DX) 정보 앱 '몇관몇열'의 데이터 큐레이터입니다.
오늘 날짜: {today.strftime('%Y년 %m월 %d일')} (KST)

아래는 현재 발행 중인 영화 데이터입니다:

{works_json}

임무: 웹 검색으로 아래 항목을 확인하고, 변경이 필요한 필드만 제안하세요.

1. premiumEnd — 각 영화의 특수관 상영 종료가 극장 체인(CGV · 롯데시네마 · 메가박스) 공지나 공식 보도로 **명시적으로** 확인된 경우에만 "yyyy.MM.dd"로 설정합니다. 스크린 수 감소, 예매율, 추측으로는 절대 설정하지 않습니다.
2. date — 미개봉작의 개봉일이 공식적으로 변경/확정된 경우 "yyyy.MM.dd"로 수정합니다.
3. hook — 현재 문구가 사실과 어긋나게 된 경우 갱신합니다 (예: 이미 종료된 IMAX 상영을 '상영 중'처럼 표현). 한 문장, 담백하게.
4. badges / recommendedFormat — 특수관 포맷 상영이 공식 확정되거나 취소된 경우. badges 값은 다음 코드만 사용: IMAX43, IMAX190, DOLBY, SCREENX, 4DX, SUPERPLEX, STD.

절대 규칙:
- 확인되지 않은 정보는 절대 만들지 않습니다. 불확실하면 변경하지 않습니다. "변경 없음"이 완벽하게 정상적인 결과입니다.
- 출처 URL이 없는 변경은 제안하지 않습니다.
- 영화 추가/삭제와 상영관 데이터는 이 자동화의 범위 밖입니다. 새로 특수관 개봉이 확정된 주목할 만한 영화나, 상영관 개·폐관 소식이 있으면 notices에만 적으세요 (사람이 검토합니다).
- 뉴스 기사·블로그 문장을 그대로 옮기지 않습니다. 사실만 추려 새로 씁니다.
- 웹 검색은 최대 6회입니다. 여러 작품을 한 검색어로 묶는 등 꼭 필요한 확인에만 아껴 쓰세요. 검색으로 확인 못 한 작품은 변경하지 않으면 됩니다.

응답 마지막에 아래 형식의 json 코드블록을 정확히 하나만 출력하세요:

```json
{{"changes": [{{"workId": "...", "field": "...", "value": "...", "reason": "...", "source": "https://..."}}], "notices": ["사람 확인이 필요한 사항"], "summary": "오늘 점검 요약 한두 문장"}}
```

변경이 없으면 changes는 빈 배열로 두세요."""


def extract_result(text: str) -> dict:
    blocks = re.findall(r"```json\s*(.*?)```", text, re.DOTALL)
    if not blocks:
        raise ValueError("응답에서 json 코드블록을 찾지 못함")
    return json.loads(blocks[-1])


def validate_changes(changes: list, works_by_id: dict) -> None:
    if len(changes) > MAX_CHANGES:
        raise ValueError(f"변경 제안 {len(changes)}건 > 허용치 {MAX_CHANGES}건 — 전체 거부")
    for ch in changes:
        wid, field, value = ch.get("workId"), ch.get("field"), ch.get("value")
        if wid not in works_by_id:
            raise ValueError(f"알 수 없는 workId: {wid!r}")
        if field not in MUTABLE_FIELDS:
            raise ValueError(f"수정 불가 필드: {field!r} (workId={wid})")
        if not ch.get("source", "").startswith("http"):
            raise ValueError(f"출처 URL 누락: {wid}.{field}")
        if value is None:
            if field not in NULLABLE_FIELDS:
                raise ValueError(f"{field}은(는) null 불가 (workId={wid})")
        elif field in DATE_FIELDS:
            if not isinstance(value, str) or not DATE_RE.match(value):
                raise ValueError(f"날짜 형식 오류: {wid}.{field} = {value!r}")
        elif field == "badges":
            if (not isinstance(value, list) or not value
                    or not all(isinstance(v, str) and v in KNOWN_CODES for v in value)):
                raise ValueError(f"badges 값 오류: {wid} = {value!r}")
        elif field == "recommendedFormat":
            if value not in KNOWN_CODES:
                raise ValueError(f"recommendedFormat 값 오류: {wid} = {value!r}")
        else:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} 값이 빈 문자열 (workId={wid})")


def main() -> int:
    data = json.loads(CURATED.read_text(encoding="utf-8"))
    works_by_id = {w["id"]: w for w in data["works"]}
    today = datetime.now(KST)

    client = Anthropic()
    # cache_control: 웹 검색은 검색할 때마다 누적된 대화 전체를 다시 입력으로
    # 처리하므로, 프롬프트 캐싱 없이는 입력 토큰이 검색 횟수에 따라 눈덩이처럼
    # 불어난다 (첫 실행에서 확인: 12회 검색에 입력 ~33만 토큰). 캐시된 부분은
    # 기본 단가의 ~10%로 재사용된다. max_uses도 6으로 제한 — 8편 점검에 충분.
    with client.messages.stream(
        model="claude-sonnet-5",
        max_tokens=32000,
        cache_control={"type": "ephemeral"},
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}],
        messages=[{"role": "user", "content": build_prompt(data["works"], today)}],
    ) as stream:
        message = stream.get_final_message()

    u = message.usage
    searches = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    cost = (u.input_tokens * 3 + cache_write * 3.75 + cache_read * 0.3 + u.output_tokens * 15) / 1e6 + searches * 0.01
    print(f"토큰: 입력 {u.input_tokens:,} / 캐시쓰기 {cache_write:,} / 캐시읽기 {cache_read:,} / 출력 {u.output_tokens:,} / 검색 {searches}회")
    print(f"예상 비용(정가 기준): ${cost:.2f}")

    if message.stop_reason == "refusal":
        print("::error::모델이 요청을 거부함 (stop_reason=refusal)")
        return 1
    if message.stop_reason == "max_tokens":
        print("::error::응답이 max_tokens에서 잘림 — 결과를 신뢰할 수 없어 중단")
        return 1

    text = "".join(b.text for b in message.content if b.type == "text")
    result = extract_result(text)
    changes = result.get("changes") or []
    notices = result.get("notices") or []
    summary = result.get("summary") or ""

    validate_changes(changes, works_by_id)

    for ch in changes:
        work = works_by_id[ch["workId"]]
        old = work.get(ch["field"])
        work[ch["field"]] = ch["value"]
        print(f"변경: {ch['workId']}.{ch['field']}: {old!r} -> {ch['value']!r}")
        print(f"  이유: {ch.get('reason', '')}")
        print(f"  출처: {ch['source']}")

    # 변경이 없어도 '이 날짜에 점검됨'을 앱의 "N월 N일 기준" 라벨에 반영한다.
    data["dataTimestamp"] = today.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    CURATED.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if notices:
        lines = [f"자동 점검({today.strftime('%Y-%m-%d')}) 중 사람 확인이 필요한 사항:", ""]
        lines += [f"- {n}" for n in notices]
        if summary:
            lines += ["", f"요약: {summary}"]
        REVIEW_NOTES.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n점검 완료 — 변경 {len(changes)}건, 확인 필요 {len(notices)}건")
    if summary:
        print(f"요약: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
