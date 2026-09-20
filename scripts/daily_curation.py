#!/usr/bin/env python3
"""몇관몇열 큐레이션 데이터 일일 자동 점검 (GitHub Actions에서 매일 실행).

Claude API(웹 검색 포함)로 curated.json의 영화 데이터를 사실 확인하고,
공식 출처로 확인된 변경만 적용한 뒤 dataTimestamp를 갱신한다.

안전 장치 (앱 쪽 CuratedFeedValidator와 같은 정신):
- 영화(works)의 허용된 필드만 수정 가능. 상영관(halls) 데이터·영화 삭제는 자동화 범위 밖.
- 모든 변경은 스크립트가 재검증 (필드 화이트리스트, 날짜 형식, 알려진 포맷 코드).
- 한 번에 MAX_CHANGES개 초과 변경 제안 시 전체 거부 (모델 폭주 방어).
- 파싱/검증 실패 시 아무것도 쓰지 않고 실패 종료 → Actions 로그에 표시.

## 작품 추가 (2026-09-20~, docs/screening-contract.md "봇 동작" 2)

`screening.py`가 KOBIS에서 프리미엄 접미사로 걸렸는데 `kobis_movies.json`에 없는
`movieCd`를 후보로 넘긴다. 예전에는 프롬프트가 작품 추가를 아예 금지했지만, 이제는
하루 최대 MAX_ADDITIONS편까지 **극장 수가 많은 후보부터** 추가할 수 있다.

추가는 프롬프트의 부탁이 아니라 `validate_additions()`가 보증한다. 필수 필드가
하나라도 비면 그 추가는 거부된다 — 특히 `tmdbId`는 TMDB 검색으로 확정된 정수여야
한다. 이것이 **콘서트 실황·라이브뷰잉·스포츠 중계를 걸러내는 실질적인 장치**다.
KOBIS 특수관 편성의 상당수가 그런 콘텐츠이고(아이유 콘서트가 26개 극장의 IMAX·
ScreenX·4DX에 걸려 있었다), 그것들은 TMDB 영화 DB에 없다.

후보는 처리(추가 또는 이유를 달아 거절)하면 `screening_state.json`의
`handledCandidates`에 날짜와 함께 남아 일정 기간 다시 제안되지 않는다. 모델이
아무 판단도 하지 않은 후보도 "확인되지 않음"으로 처리 기록을 남긴다 — 그러지
않으면 같은 후보로 매일 유료 단계가 켜진다.
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from anthropic import Anthropic

import screening

KST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).resolve().parent.parent
CURATED = REPO_ROOT / "curated.json"
MOVIES_MAP = REPO_ROOT / "kobis_movies.json"
CHANGES = REPO_ROOT / "screening_changes.json"

# ios/Sources/Models/FormatData.swift의 FormatCode raw value와 동일해야 한다.
# DOLBYVISION은 2026-09-20에 추가했다 — 돌비 시네마 전용관이 아닌 Dolby Atmos관
# 코드이고, screening.py가 그 관의 상영을 이 코드로 낸다. 앱 enum에는 원래 있었다.
KNOWN_CODES = {"IMAX43", "IMAX190", "DOLBY", "DOLBYVISION", "SCREENX", "4DX",
               "SUPERPLEX", "STD"}
# 관이 그런 관이라서 붙는 포맷. 작품의 속성이 아니므로 "작품 배지가 KOBIS에서 본
# 포맷을 모두 포함해야 한다"는 규칙에서 빼 준다 (계약 "관 속성 포맷" 절).
HALL_ATTRIBUTE_CODES = {"SUPERPLEX", "DOLBYVISION"}
MUTABLE_FIELDS = {"date", "run", "meta", "hook", "open", "premiumEnd", "badges", "recommendedFormat"}
DATE_FIELDS = {"date", "premiumEnd"}
NULLABLE_FIELDS = {"date", "run", "hook", "open", "premiumEnd"}
MAX_CHANGES = 6
# 추가 1편에 웹검색이 여럿 든다(개봉일·상영시간·TMDB id·hook 근거). 검색 상한이
# 6회이므로 하루 2편이 현실적인 한계다. 남은 후보는 다음 실행으로 넘긴다.
MAX_ADDITIONS = 2
DATE_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG_RE = re.compile(r"[^a-z0-9]+")
# 추가 시 모델이 반드시 채워야 하는 필드. 하나라도 비면 그 추가를 거부한다.
ADDITION_REQUIRED = ("movieCd", "title", "en", "date", "run", "genre", "meta",
                     "badges", "recommendedFormat", "hook", "tmdbId", "pc1", "pc2")


def slug_from_en(en: str) -> str:
    """영문 제목 → 소문자 ascii work id. 기존 id 규칙을 그대로 재현한다
    ("TOY STORY 5" → toystory5, "KING'S WARDEN" → kingswarden)."""
    return SLUG_RE.sub("", (en or "").lower())


def add_movie_mapping(work_id: str, movie_cd: str, kobis_nm: str) -> None:
    """`kobis_movies.json`에 workId → movieCd를 넣는다 (계약 "파일 2").

    curated.json에 작품만 넣고 여기를 빼먹으면 screening.py가 그 movieCd를
    계속 "작품이 아닌 후보"로 보고 매일 다시 제안한다. 추가와 매핑은 한 트랜잭션.
    """
    doc = json.loads(MOVIES_MAP.read_text(encoding="utf-8"))
    doc.setdefault("works", {})[work_id] = {"movieCd": movie_cd, "kobisNm": kobis_nm}
    MOVIES_MAP.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def build_prompt(works: list, candidates: list, today: datetime) -> str:
    works_json = json.dumps(works, ensure_ascii=False, indent=1)
    cand_json = json.dumps(candidates, ensure_ascii=False, indent=1)
    return f"""당신은 한국 특수 상영관(IMAX · 돌비 시네마 · ScreenX · 4DX) 정보 앱 '몇관몇열'의 데이터 큐레이터입니다.
오늘 날짜: {today.strftime('%Y년 %m월 %d일')} (KST)

아래는 현재 발행 중인 영화 데이터입니다:

{works_json}

임무: 웹 검색으로 아래 항목을 확인하고, 변경이 필요한 필드만 제안하세요.

1. premiumEnd — 각 영화의 특수관 상영 종료가 극장 체인(CGV · 롯데시네마 · 메가박스) 공지나 공식 보도로 **명시적으로** 확인된 경우에만 "yyyy.MM.dd"로 설정합니다. 스크린 수 감소, 예매율, 추측으로는 절대 설정하지 않습니다.
2. date — 미개봉작의 개봉일이 공식적으로 변경/확정된 경우 "yyyy.MM.dd"로 수정합니다.
3. hook — 아래 두 경우에 씁니다. 한 문장, 담백하게.
   (a) 현재 문구가 사실과 어긋나게 된 경우 갱신 (예: 이미 종료된 IMAX 상영을 '상영 중'처럼 표현).
   (b) **badges를 STD에서 특수관 포맷으로 올리는 경우, 같은 응답에서 hook도 반드시 함께 제안합니다.**
       앱은 특수관 목록의 각 작품 아래에 이 한 줄을 띄웁니다. 배지만 올리고 hook을 비워두면
       그 작품만 설명 없이 덩그러니 놓입니다. hook이 비어도 되는 것은 badges가 STD일 때뿐입니다.
       내용은 "어느 포맷으로 볼지" 판단에 도움이 되는 확인된 사실이어야 합니다 — 확정된 개봉 포맷,
       촬영·마스터링 방식, 화면비 같은 것. 홍보 문구나 줄거리 요약이 아닙니다.
4. badges / recommendedFormat — 특수관 포맷 상영이 공식 확정된 경우, 또는 아래 KOBIS 관측 목록에서
   기존 작품에 새 포맷이 잡힌 경우 그 포맷을 **추가**합니다. badges 값은 다음 코드만 사용:
   IMAX43, IMAX190, DOLBY, DOLBYVISION, SCREENX, 4DX, SUPERPLEX, STD.
   **badges는 줄어들지 않습니다** — 상영이 끝난 것은 배지를 지우는 게 아니라 premiumEnd로 적습니다(이력).
   검증기가 이것을 강제하므로 기존 값을 뺀 badges를 제안하면 발행 전체가 실패합니다.
5. meta — 카드에 그대로 찍히는 한 줄이며 `날짜 개봉 · 상영시간 · 장르` 형식입니다(예: "2026.08.05 개봉 · 172분 · 액션/어드벤처").
   date를 바꾸면 meta의 날짜도 같은 값으로 맞추고, badges를 STD에서 올리면 meta에 남아 있는
   "일반관" 표기를 실제 상영시간·장르로 바꿉니다. **meta는 badges와 모순되면 안 됩니다** —
   특수관 배지를 단 작품의 meta가 "일반관"이라고 말하는 상태가 실제로 있었습니다.

절대 규칙:
- 확인되지 않은 정보는 절대 만들지 않습니다. 불확실하면 변경하지 않습니다. "변경 없음"이 완벽하게 정상적인 결과입니다.
- 출처 URL이 없는 변경은 제안하지 않습니다.
- 영화 **삭제**와 상영관(halls) 데이터는 이 자동화의 범위 밖입니다. 상영관 개·폐관 소식은 notices에만 적으세요.
- 뉴스 기사·블로그 문장을 그대로 옮기지 않습니다. 사실만 추려 새로 씁니다.
- 웹 검색은 최대 6회입니다. 여러 작품을 한 검색어로 묶는 등 꼭 필요한 확인에만 아껴 쓰세요. 검색으로 확인 못 한 작품은 변경하지 않으면 됩니다.

---

## KOBIS 특수관 후보 (작품 추가 검토)

아래는 어제·오늘·내일 전국 특수관 편성에서 `(IMAX)`·`(4D)`·`(ScreenX)`·`(DOLBYCINEMA)` 접미사로
실제로 잡혔는데 위 목록에 없는 콘텐츠입니다. `formats`는 KOBIS에서 관측된 포맷, `theaterCount`는
그 포맷으로 걸린 극장 수입니다(많을수록 큰 편성).

{cand_json}

이 중 **영화인 것만** `additions`로 추가하세요. 규칙:

- **한 번에 최대 {MAX_ADDITIONS}편.** theaterCount가 큰 것부터 봅니다. 검색 예산이 부족하면 더 적게 넣으세요.
- **영화가 아닌 것은 추가하지 않습니다.** 콘서트 실황·라이브뷰잉·뮤지컬·스포츠 중계·팬미팅·
  재개봉 특가 이벤트는 이 앱의 대상이 아닙니다. 판단 기준은 단순합니다 — **TMDB 영화 DB에
  그 작품이 없으면 추가하지 않습니다.**
- 추가할 작품마다 아래를 모두 채웁니다. 하나라도 확인 못 하면 그 작품은 넣지 말고 `rejected`에
  이유를 적으세요. 검증기가 빈 필드를 거부하므로 "일단 넣고 나중에"는 불가능합니다.
  - `movieCd` — 위 후보 목록의 값 그대로.
  - `tmdbId` — TMDB에서 **실제로 찾은** 정수 id. 추측 금지. 못 찾으면 추가하지 않습니다.
  - `title` 한국어 제목 / `en` 영문 제목(대문자). `en`에서 work id를 자동 생성합니다.
  - `date` — TMDB의 **한국 극장 개봉일(type 3)** "yyyy.MM.dd". 한국 극장 개봉일을 확인할 수
    없으면 그렇게 말하고 추가하지 않습니다.
  - `run` "123분" / `genre` "액션 · 스릴러"
  - `meta` — "`date` 개봉 · `run` · 장르" 형식. 반드시 `date`로 시작해야 합니다.
  - `badges` — 위 후보의 `formats`를 **모두 포함**해야 합니다(SUPERPLEX·DOLBYVISION은 관 속성이라
    예외). 확인된 포맷을 더 넣어도 됩니다.
  - `recommendedFormat` — `badges` 안의 값 하나.
  - **재개봉작이면** (예: 2006년 작품이 2026년에 다시 걸린 경우) `date`는 그대로 TMDB의
    한국 극장 개봉일이고, `meta`와 `hook`에 "재개봉"임을 적습니다. 재개봉 날짜를 새로
    만들어 넣지 않습니다 — 확인된 개봉일은 원래 개봉일 하나뿐입니다.
  - `hook` — 웹으로 확인된 한 문장. "어느 포맷으로 볼지" 판단에 쓰이는 사실만(촬영 포맷,
    마스터링, 화면비, 확정된 개봉 포맷). 홍보 문구·줄거리 금지. 비울 수 없습니다.
  - `pc1`·`pc2` — 카드 배경 그라디언트용 어두운 hex 2개("#1b3550" 형식). 포스터 색감에서 고르면
    됩니다. 사실 주장이 아니므로 판단해서 채우세요.
  - `source` — 근거 URL 하나.
- 추가하지 않기로 한 후보는 **전부** `rejected`에 `{{"movieCd": "...", "reason": "..."}}`로 적으세요.
  이유가 남지 않으면 같은 후보가 매일 다시 올라옵니다.

---

응답 마지막에 아래 형식의 json 코드블록을 정확히 하나만 출력하세요:

```json
{{"changes": [{{"workId": "...", "field": "...", "value": "...", "reason": "...", "source": "https://..."}}],
 "additions": [{{"movieCd": "...", "title": "...", "en": "...", "date": "yyyy.MM.dd", "run": "...", "genre": "...", "meta": "...", "badges": ["..."], "recommendedFormat": "...", "hook": "...", "tmdbId": 123456, "pc1": "#______", "pc2": "#______", "reason": "...", "source": "https://..."}}],
 "rejected": [{{"movieCd": "...", "reason": "..."}}],
 "notices": ["사람 확인이 필요한 사항"],
 "summary": "오늘 점검 요약 한두 문장"}}
```

변경이 없으면 changes·additions는 빈 배열로 두세요."""


def extract_result(text: str) -> dict:
    blocks = re.findall(r"```json\s*(.*?)```", text, re.DOTALL)
    if not blocks:
        raise ValueError("응답에서 json 코드블록을 찾지 못함")
    return json.loads(blocks[-1])


def validate_hook_invariant(works: list) -> None:
    """특수관 배지를 단 작품에는 반드시 hook이 있어야 한다.

    2026-09-08에 실제로 깨진 불변식이다. 봇이 모아나·미니언즈의 badges를 STD에서
    IMAX190/SCREENX/4DX로 올렸는데 hook은 건드리지 않아서, 두 작품이 앱의 "지금
    특수관에서" 목록에 금색 한 줄 없이 올라왔다. 형제 카드에는 전부 있는 줄이라
    빠진 것이 눈에 띈다.

    앱 쪽 `FormatData.Work.recommendedFormat` 주석이 이미 이 불변식을 문서로
    적어두고 있었다 — "hook이 nil이고 badges가 [.standard]일 때"만 예외라고. 문서에만
    있고 어디서도 강제하지 않았던 것이 문제였으므로, 여기서 기계적으로 막는다.

    프롬프트에도 같은 규칙을 넣었지만(3-b) 프롬프트는 부탁이고 이 함수는 보증이다.
    모델이 잊으면 발행이 실패하고 Actions 로그에 남는다 — 조용히 빈 줄이 나가는
    것보다 낫다.
    """
    offenders = [
        w["id"] for w in works
        if w.get("badges") != ["STD"] and not (w.get("hook") or "").strip()
    ]
    if offenders:
        raise ValueError(
            f"특수관 배지가 있는데 hook이 비어 있음: {', '.join(offenders)} — "
            "badges를 올릴 때는 hook도 함께 제안해야 한다 (프롬프트 3-b)"
        )


def validate_meta_invariant(works: list) -> None:
    """meta 한 줄이 같은 레코드의 date·badges와 모순되지 않아야 한다.

    meta는 date/run/genre를 문자열로 한 번 더 적어둔 비정규화 필드다. 앱은
    `ReleaseDateStore.effectiveMeta(for:)`로 표시 시점에 맨 앞 날짜만 갈아끼우고
    나머지는 그대로 쓰므로, 뒤쪽이 틀어지면 그대로 화면에 나간다.

    2026-09-08에 실제로 그렇게 됐다. 봇이 모아나·미니언즈의 badges를 STD에서
    특수관 포맷으로 올리면서 meta는 "2026.07.08 개봉 · 일반관" 그대로 뒀고,
    앱에서 IMAX·SCREENX·4D 배지 바로 아래에 "일반관"이라고 적힌 카드가 나왔다.

    두 가지만 본다. 둘 다 자동으로 판정 가능한 모순이다:
      - meta가 그 작품의 date로 시작하는가
      - 특수관 배지를 달고도 meta가 "일반관"이라 말하고 있지 않은가
    상영시간·장르가 맞는지는 여기서 알 수 없다 — 그건 프롬프트(5)의 몫이다.
    """
    problems = []
    for w in works:
        meta = (w.get("meta") or "").strip()
        if not meta:
            problems.append(f"{w['id']}: meta 비어 있음")
            continue
        date = w.get("date")
        if date and not meta.startswith(date):
            problems.append(f"{w['id']}: meta가 date({date})로 시작하지 않음 — {meta!r}")
        if w.get("badges") != ["STD"] and "일반관" in meta:
            problems.append(f"{w['id']}: 특수관 배지({w.get('badges')})인데 meta가 '일반관' — {meta!r}")
    if problems:
        raise ValueError("meta 불일치: " + " / ".join(problems))


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
            # 배지는 이력이다 — 늘기만 한다. 끝난 상영은 premiumEnd로 적는다
            # (계약 "봇 동작" 3: "배지는 지우지 않는다"). 프롬프트 4에도 있지만
            # 프롬프트는 부탁이고 이 줄이 보증이다.
            dropped = set(works_by_id[wid].get("badges") or []) - set(value)
            if dropped:
                raise ValueError(
                    f"badges에서 {', '.join(sorted(dropped))}가 빠졌다 (workId={wid}) — "
                    f"배지는 줄어들 수 없다. 종료는 premiumEnd로 적는다")
        elif field == "recommendedFormat":
            if value not in KNOWN_CODES:
                raise ValueError(f"recommendedFormat 값 오류: {wid} = {value!r}")
        else:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} 값이 빈 문자열 (workId={wid})")


def validate_additions(additions: list, works_by_id: dict,
                       candidates: list) -> tuple:
    """새 작품 추가를 기계적으로 검증하고 (work, meta) 목록을 만든다.

    프롬프트로 부탁한 것과 별개로 여기서 전부 다시 본다. 특히:

    - `tmdbId`가 확정된 정수여야 한다. 이 한 줄이 콘서트 실황·라이브뷰잉·
      스포츠 중계를 걸러내는 실질적인 장치다 — KOBIS 특수관 편성에는 그런
      콘텐츠가 적지 않고(전국 26개 극장의 IMAX·ScreenX·4DX에 걸려 있던
      아이유 콘서트), TMDB 영화 DB에는 없다.
    - `badges`가 KOBIS에서 실제로 본 포맷을 모두 담아야 한다. 단 SUPERPLEX·
      DOLBYVISION은 뺀다 — 계약의 "관 속성 포맷"이고 작품의 속성이 아니다.
      (영화가 SUPERPLEX용으로 마스터링되는 게 아니라 관이 그런 관이다.)
    - work id는 모델이 고르지 않는다. `en`에서 기계적으로 만든다.

    반환은 (통과한 work 목록, [(movieCd, 거절 이유), ...])이다. 부실한 추가는
    예외를 던지지 않는다 — 한 편이 부실하다고 그 날 점검 전체를 버릴 이유는
    없고, 그 후보는 거절로 기록되어 다음 기회에 다시 올라온다. `changes`의
    검증은 반대로 하나라도 틀리면 전체를 거부한다(기존 동작 유지).
    """
    by_cd = {str(c.get("movieCd")): c for c in candidates}
    taken = set(works_by_id)
    accepted, rejected = [], []

    if len(additions) > MAX_ADDITIONS:
        # 넘치는 쪽은 잘라낸다. 폭주 방어이자 검색 예산 보호.
        for extra in additions[MAX_ADDITIONS:]:
            rejected.append((str(extra.get("movieCd")),
                             f"한 번에 {MAX_ADDITIONS}편까지만 추가한다 — 다음 실행으로 넘김"))
        additions = additions[:MAX_ADDITIONS]

    for add in additions:
        movie_cd = str(add.get("movieCd") or "")
        try:
            cand = by_cd.get(movie_cd)
            if cand is None:
                raise ValueError(f"후보 목록에 없는 movieCd: {movie_cd!r}")
            missing = [f for f in ADDITION_REQUIRED
                       if add.get(f) is None
                       or (isinstance(add[f], str) and not add[f].strip())
                       or (isinstance(add[f], list) and not add[f])]
            if missing:
                raise ValueError(f"필수 필드 누락: {', '.join(missing)}")
            if not str(add.get("source", "")).startswith("http"):
                raise ValueError("출처 URL 누락")

            tmdb_id = add["tmdbId"]
            if isinstance(tmdb_id, bool) or not isinstance(tmdb_id, int) or tmdb_id <= 0:
                raise ValueError(f"tmdbId가 확정된 정수가 아님: {tmdb_id!r} — "
                                 "TMDB에 없는 콘텐츠(콘서트 실황·라이브뷰잉 등)는 추가하지 않는다")
            if not DATE_RE.match(str(add["date"])):
                raise ValueError(f"date 형식 오류: {add['date']!r} (yyyy.MM.dd)")

            badges = add["badges"]
            if (not isinstance(badges, list)
                    or not all(isinstance(b, str) and b in KNOWN_CODES for b in badges)):
                raise ValueError(f"badges 값 오류: {badges!r}")
            required = {f for f in (cand.get("formats") or [])
                        if f not in HALL_ATTRIBUTE_CODES}
            lacking = required - set(badges)
            if lacking:
                raise ValueError(f"KOBIS에서 본 포맷 {', '.join(sorted(lacking))}가 "
                                 f"badges에 없음 (본 포맷: {cand.get('formats')})")
            if add["recommendedFormat"] not in badges:
                raise ValueError(f"recommendedFormat {add['recommendedFormat']!r}이 "
                                 f"badges {badges} 안에 없음")
            for key in ("pc1", "pc2"):
                if not HEX_RE.match(str(add[key])):
                    raise ValueError(f"{key} 색상 형식 오류: {add[key]!r} (#rrggbb)")

            work_id = slug_from_en(add["en"])
            if not work_id:
                raise ValueError(f"en에서 work id를 만들 수 없음: {add['en']!r}")
            if work_id in taken:
                raise ValueError(f"work id {work_id!r}가 이미 있음 — en을 확인할 것")
            taken.add(work_id)

            accepted.append({
                "id": work_id,
                "title": add["title"].strip(),
                "en": add["en"].strip(),
                "pc1": add["pc1"],
                "pc2": add["pc2"],
                "date": add["date"],
                "run": add["run"].strip(),
                "genre": add["genre"].strip(),
                "meta": add["meta"].strip(),
                "badges": list(badges),
                "hook": add["hook"].strip(),
                "open": add.get("open") or None,
                "tmdbId": tmdb_id,
                "recommendedFormat": add["recommendedFormat"],
                "premiumEnd": None,
                "_movieCd": movie_cd,
                "_source": add["source"],
                "_reason": add.get("reason", ""),
            })
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            rejected.append((movie_cd, str(e)))
    return accepted, rejected


def record_changes(entries: list, checked_at: str) -> None:
    """이슈로 만들 변경을 `screening_changes.json`에 **덧붙인다**.

    screening.py가 먼저 돌아 은퇴를 여기에 적으므로 덮어쓰면 안 된다. 워크플로우는
    이 파일 하나만 보고 이슈를 연다 — 작품 추가·배지 변경·은퇴·screening 중단
    네 가지뿐이다 (계약 "봇 동작" 4).
    """
    if not entries:
        return
    payload = {"generatedAt": checked_at, "changes": []}
    if CHANGES.exists():
        try:
            existing = json.loads(CHANGES.read_text(encoding="utf-8"))
            payload["changes"] = existing.get("changes") or []
        except (json.JSONDecodeError, AttributeError):
            pass
    payload["changes"].extend(entries)
    CHANGES.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")


def main() -> int:
    data = json.loads(CURATED.read_text(encoding="utf-8"))
    works_by_id = {w["id"]: w for w in data["works"]}
    today = datetime.now(KST)

    # 후보는 screening.py가 만든 목록에서, 아직 처리하지 않은 것만. 극장 수가 큰
    # 것부터 MAX_ADDITIONS편만 프롬프트에 넣는다 — 검색 예산이 6회뿐이므로 후보를
    # 전부 던지면 아무것도 제대로 확인하지 못한다. 남은 것은 다음 실행 몫이다.
    state = screening.load_state_file()
    pool = screening.offerable_candidates(
        screening.load_candidates_file(), state, today.date())

    # 비영화 편성은 프롬프트에 넣지 않고 여기서 자른다. 모델에게 "이건 콘서트다"를
    # 확인시키는 데 웹 검색 6회를 쓸 이유가 없다. 자른 것도 처리 기록에 남겨
    # MAX_ADDITIONS 자리를 차지하지 않게 한다.
    offered = []
    for cand in pool:
        keyword = screening.nonfilm_reason(cand["kobisNm"])
        if keyword:
            screening.mark_candidate_handled(
                state, cand["movieCd"], "rejected",
                f"nonfilm-keyword: {keyword}", today.date())
            print(f"::warning::후보 제외 {cand['movieCd']} {cand['kobisNm']} — "
                  f"비영화 키워드 {keyword!r} (영화라면 kobis_movies.json에 직접 넣을 것)")
            continue
        offered.append(cand)
    offered = offered[:MAX_ADDITIONS]
    if offered:
        print("후보 " + ", ".join(
            f"{c['kobisNm']}({c['movieCd']}, {','.join(c['formats'])}, "
            f"{c['theaterCount']}곳)" for c in offered))

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
        messages=[{"role": "user",
                   "content": build_prompt(data["works"], offered, today)}],
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
    additions = result.get("additions") or []
    rejected = result.get("rejected") or []
    notices = result.get("notices") or []
    summary = result.get("summary") or ""

    validate_changes(changes, works_by_id)

    issue_entries = []
    for ch in changes:
        work = works_by_id[ch["workId"]]
        old_value = work.get(ch["field"])
        work[ch["field"]] = ch["value"]
        print(f"변경: {ch['workId']}.{ch['field']}: {old_value!r} -> {ch['value']!r}")
        print(f"  이유: {ch.get('reason', '')}")
        print(f"  출처: {ch['source']}")
        if ch["field"] == "badges":
            issue_entries.append({
                "kind": "badgeChange", "workId": ch["workId"],
                "field": "badges", "value": ch["value"], "previous": old_value,
                "reason": ch.get("reason", ""), "source": ch["source"],
            })

    accepted, add_rejects = validate_additions(additions, works_by_id, offered)
    for movie_cd, why in add_rejects:
        print(f"::warning::추가 거부 {movie_cd}: {why}")

    for work in accepted:
        movie_cd = work.pop("_movieCd")
        source = work.pop("_source")
        reason = work.pop("_reason")
        data["works"].append(work)
        works_by_id[work["id"]] = work
        # 매핑표에도 넣어야 다음 screening.py 실행이 이 작품을 상영 중으로 잡는다.
        # 넣지 않으면 같은 movieCd가 영원히 후보로 다시 올라온다. kobisNm은 모델이
        # 쓴 제목이 아니라 KOBIS가 실제로 보낸 제목을 그대로 둔다 — 제목 매칭은
        # 보조 수단이고, 그 보조 수단은 KOBIS 표기와 맞아야 쓸모가 있다.
        kobis_nm = next((c["kobisNm"] for c in offered
                         if str(c["movieCd"]) == movie_cd), work["title"])
        add_movie_mapping(work["id"], movie_cd, kobis_nm)
        screening.mark_candidate_handled(state, movie_cd, "added",
                                         f"{work['id']} 추가", today.date())
        print(f"추가: {work['id']} ({work['title']} / {work['en']}) "
              f"movieCd={movie_cd} tmdbId={work['tmdbId']} badges={work['badges']}")
        print(f"  이유: {reason}")
        print(f"  출처: {source}")
        issue_entries.append({
            "kind": "newWork", "workId": work["id"], "title": work["title"],
            "movieCd": movie_cd, "tmdbId": work["tmdbId"],
            "badges": work["badges"], "recommendedFormat": work["recommendedFormat"],
            "hook": work["hook"], "reason": reason, "source": source,
        })

    # 모델이 거절한 것 + 검증기가 거부한 것 + 아무 말 없이 지나간 것을 모두
    # 처리 기록에 남긴다. 마지막 경우를 빼먹으면 같은 후보로 매일 유료 단계가
    # 켜진다 — precheck의 트리거 6이 이 기록을 보고 판단하기 때문이다.
    verdicts = {str(r.get("movieCd")): (r.get("reason") or "이유 없음")
                for r in rejected if isinstance(r, dict)}
    verdicts.update({cd: why for cd, why in add_rejects})
    added_cds = {e["movieCd"] for e in issue_entries if e["kind"] == "newWork"}
    for cand in offered:
        movie_cd = str(cand["movieCd"])
        if movie_cd in added_cds:
            continue
        screening.mark_candidate_handled(
            state, movie_cd, "rejected",
            verdicts.get(movie_cd, "이번 실행에서 영화로 확인되지 않음"), today.date())

    # 변경을 적용한 *뒤* 검사한다 — 이 실행이 badges를 올려놓고 hook을 빠뜨렸는지가
    # 관심사이므로, 반영 전 상태가 아니라 반영 후 상태를 봐야 한다. 새로 추가한
    # 작품도 같은 불변식을 지나간다.
    validate_hook_invariant(data["works"])
    validate_meta_invariant(data["works"])

    # 변경이 없어도 '이 날짜에 점검됨'을 앱의 "N월 N일 기준" 라벨에 반영한다.
    data["dataTimestamp"] = today.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    CURATED.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    screening.save_state_file(state)
    record_changes(issue_entries, data["dataTimestamp"])

    # notices는 이슈로 만들지 않는다 — 매일 열리던 "확인 필요" 이슈를 폐지했다
    # (계약 "봇 동작" 4). Actions 로그와 단계 요약에만 남긴다.
    if notices:
        print("\n확인 필요:")
        for n in notices:
            print(f"  - {n}")

    print(f"\n점검 완료 — 변경 {len(changes)}건, 추가 {len(accepted)}편, "
          f"추가 거부 {len(add_rejects)}건, 확인 필요 {len(notices)}건")
    if summary:
        print(f"요약: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
