#!/usr/bin/env python3
"""curated.json 발행 직전 감사. 문제가 하나라도 있으면 종료 코드 1.

2026-09-24 사고에서 나왔다. 봇이 KOBIS에서 SUPERPLEX 1곳만 잡힌 `residentevil`을
배지 다섯 개(IMAX190·DOLBY·4DX·SCREENX·DOLBYVISION)와 "IMAX·돌비 시네마·4DX·
ScreenX 특별관으로 동시 개봉" 훅을 붙여 추가했다. 전부 지어낸 것이었고, 배지
다섯 개가 1.0 앱의 배지 줄을 화면 밖으로 밀어 모든 사용자의 영화 탭 레이아웃을
깨뜨렸다.

daily_curation.py의 검증기가 같은 규칙을 이 모듈에서 가져다 쓴다. 이 스크립트는
그 뒤의 마지막 그물이다 — 워크플로우가 커밋 직전에 부르고, 실패하면 커밋하지
않고 "피드 감사 실패" 이슈를 연다. 표준 라이브러리만 쓴다(Claude 단계를 건너뛴
날에는 anthropic SDK가 설치되지 않는다).

규칙 (작품 단위):
  1. 배지는 최대 MAX_BADGES개 — 1.0 앱은 5개에서 넘친다.
  2. SUPERPLEX·DOLBYVISION은 작품 배지가 될 수 없다 — 관 속성이다(계약 "관 속성 포맷").
  3. recommendedFormat ∈ badges. 예외는 "STD" 하나 — "이 작품은 일반관으로 충분"이라는
     편집 판단이고 앱(HallScoring.reasonText 0번 분기)이 그 뜻으로 읽는다(toystory5).
  4. hook·meta가 badges에 없는 포맷을 이름으로 부르지 않는다.
"""

import json
import re
import sys
from pathlib import Path

CURATED = Path(__file__).resolve().parent.parent / "curated.json"

MAX_BADGES = 4
FORBIDDEN_WORK_BADGES = ("SUPERPLEX", "DOLBYVISION")

# 문구 속 포맷 이름 → 그 이름이 가리킬 수 있는 배지 코드. 하나라도 badges에 있으면
# 그 이름을 써도 된다. IMAX는 IMAX190·IMAX43 어느 쪽이든 된다.
FORMAT_WORDS = (
    ("IMAX", re.compile(r"IMAX|아이맥스", re.IGNORECASE), {"IMAX190", "IMAX43"}),
    ("돌비", re.compile(r"돌비|Dolby", re.IGNORECASE), {"DOLBY"}),
    ("4DX", re.compile(r"4DX|MX4D", re.IGNORECASE), {"4DX"}),
    ("ScreenX", re.compile(r"Screen\s*X|스크린\s*X|스크린엑스", re.IGNORECASE), {"SCREENX"}),
)


def unbacked_format_words(text: str, badges) -> list:
    """text가 이름으로 부르는데 badges에 없는 포맷 이름 목록."""
    have = set(badges or [])
    return [label for label, rx, codes in FORMAT_WORDS
            if rx.search(text or "") and not (codes & have)]


def work_problems(work: dict) -> list:
    """작품 하나의 규칙 위반 목록(빈 목록이면 통과)."""
    wid = work.get("id", "?")
    badges = work.get("badges") or []
    out = []
    if len(badges) > MAX_BADGES:
        out.append(f"{wid}: 배지 {len(badges)}개 > {MAX_BADGES}개 {badges}")
    bad = [b for b in badges if b in FORBIDDEN_WORK_BADGES]
    if bad:
        out.append(f"{wid}: 관 속성 코드 {bad}는 작품 배지가 될 수 없다")
    rec = work.get("recommendedFormat")
    if rec != "STD" and rec not in badges:
        out.append(f"{wid}: recommendedFormat {rec!r}이 badges {badges} 안에 없음")
    for field in ("hook", "meta"):
        words = unbacked_format_words(work.get(field) or "", badges)
        if words:
            out.append(f"{wid}: {field}가 배지에 없는 포맷 {words}을 말한다 "
                       f"(badges {badges}) — {work.get(field)!r}")
    return out


def audit(works: list) -> list:
    return [p for w in works for p in work_problems(w)]


def main(argv=None) -> int:
    path = Path((argv or sys.argv[1:] or [str(CURATED)])[0])
    works = json.loads(path.read_text(encoding="utf-8"))["works"]
    problems = audit(works)
    for w in works:
        mark = "FAIL" if work_problems(w) else "ok  "
        print(f"  {mark} {w.get('id', '?'):16s} {','.join(w.get('badges') or [])}"
              f"  rec={w.get('recommendedFormat')}")
    if problems:
        print(f"\n피드 감사 실패 — {len(problems)}건:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"\n피드 감사 통과 — 작품 {len(works)}편")
    return 0


if __name__ == "__main__":
    sys.exit(main())
