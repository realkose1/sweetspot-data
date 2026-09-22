#!/usr/bin/env python3
"""KOBIS 후보 → TMDB 작품 확정 (큐레이션 프롬프트에 넘길 '확인된 사실' 만들기).

## 왜 파이썬이 하는가

2026-09-21·22 실행에서 모델이 후보 세 편(시간을 달리는 소녀·인턴·퇴마록)을
전부 이렇게 거절했다:

    "TMDB 실제 ID 및 한국 극장 개봉일(type 3)을 웹 검색으로 확인하지 못함
     (검색 도구 사용 한도 초과로 조회 불가)"

웹 검색 6회 예산을 "TMDB에 이 영화가 있나"를 확인하는 데 써 버린 것이다.
그런데 그건 검색으로 알아낼 일이 아니라 **API가 결정론적으로 답하는 질문**이고,
`TMDB_READ_TOKEN`은 이미 저장소 시크릿으로 있다(precheck.py가 쓴다).

그래서 후보 단계에서 여기가 먼저 답을 내고, 모델은 검색 예산을 `hook`과
`meta`처럼 실제로 판단이 필요한 것에만 쓴다.

## 동명이작을 가르는 것은 인기도가 아니라 연도다

`인턴`을 검색하면 TMDB가 이렇게 준다:

    id=257211  '인턴'  The Intern    2015-09-24  인기도 21
    id=607833  '인턴'  인턴           2026-09-16  인기도  4

인기도로 고르면 2015년 낸시 마이어스 영화를 집는다. 지금 전국 24개 극장
ScreenX에 걸려 있는 것은 2026년 한국 영화 쪽이다. KOBIS `movieCd`의 앞 네
자리가 등록연도이고(`20256308` → 2025), 실측해 보면 등록연도와 TMDB의
**한국 개봉연도**(`region=KR`로 조회하면 KR 기준 날짜가 온다) 차이가

    오디세이 +1 · 호프 +3 · 스파이더맨 0 · 모아나 +1 · 미니언즈 0 ·
    시간을 달리는 소녀 0

으로 0~3에 모인다. 등록이 개봉보다 늦는 경우는 드물어 아래를 -1까지만
허용한다. 이 창은 **후보를 고르는 데** 쓰고, 후보가 하나뿐일 때 떨어뜨리는
데는 쓰지 않는다 — 연도가 이상하면 그렇다고 적어 넘길 뿐이다.

## 재개봉

`어벤져스: 엔드게임 앙코르`는 그대로 검색하면 **0건**이다. `앙코르` 같은
행사 표기를 떼야 걸린다. 그리고 그 작품의 KR release_dates에는 type 3이 둘
있다 — 2019-04-24(원래)와 2026-09-23(이번 재개봉). `date`는 계약상 원래
개봉일이므로 **가장 이른** type 3을 쓰고, 재개봉일은 `reReleaseDate`로 따로
넘긴다. 2026-09-22 실행에서 모델이 meta에 "2026.09.23 재개봉"이라고 옳게
적었지만 그건 검색으로 알아낸 것이었다 — 여기서 주면 추측할 이유가 없다.

확정하지 못하면(0건 · 동명이작 동률 · KR 개봉 정보 없음) `needsHuman`으로
표시해 프롬프트에서 빼고 이슈를 연다. 모르는 것을 모델에게 떠넘기지 않는다.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

TMDB = "https://api.themoviedb.org/3"
TIMEOUT = 15

# 등록연도 대비 허용 개봉연도 창 (위 주석의 실측값 0~+3에 여유를 둔 것).
YEAR_WINDOW = (-1, 4)

# 상영 행사 표기. 작품 제목이 아니라 편성 꼬리표라 TMDB에는 없다.
EVENT_MARKERS = (
    "앙코르", "재개봉", "재상영", "특별상영", "단독상영",
    "4K 리마스터링", "디지털 리마스터링", "리마스터링",
)
SPACE_RE = re.compile(r"\s+")
TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")


class TmdbError(RuntimeError):
    pass


def _get(token: str, path: str) -> dict:
    req = urllib.request.Request(
        TMDB + path,
        headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError) as e:
        raise TmdbError(f"{path.split('?')[0]}: {e}") from e


def norm(s: str) -> str:
    """제목 비교용 정규화 — 공백만 접는다. 문장부호는 남긴다
    (`암살자(들)`와 `암살자들`은 다른 제목일 수 있다)."""
    return SPACE_RE.sub(" ", (s or "").strip()).lower()


def query_variants(kobis_nm: str) -> list:
    """검색어 후보를 우선순위대로. 앞의 것이 정확히 맞으면 뒤는 보지 않는다."""
    out = []

    def add(v):
        v = SPACE_RE.sub(" ", (v or "").strip()).strip(" :·-")
        if v and v not in out:
            out.append(v)

    add(kobis_nm)
    stripped = kobis_nm
    for marker in EVENT_MARKERS:
        stripped = stripped.replace(marker, " ")
    add(stripped)
    add(TRAILING_PAREN_RE.sub("", kobis_nm))
    return out


def reg_year(movie_cd: str):
    """`movieCd` 앞 네 자리 = KOBIS 등록연도. 이상하면 None."""
    head = str(movie_cd or "")[:4]
    return int(head) if head.isdigit() and 1900 <= int(head) <= 2200 else None


def release_year(result: dict):
    d = (result.get("release_date") or "")[:4]
    return int(d) if d.isdigit() else None


def search(token: str, kobis_nm: str) -> tuple:
    """(정확히 일치한 결과들, 참고용 상위 결과들, 실제로 걸린 검색어)."""
    seen_all = []
    for variant in query_variants(kobis_nm):
        data = _get(token, "/search/movie?language=ko-KR&region=KR"
                           "&include_adult=false&query=" + urllib.parse.quote(variant))
        results = data.get("results") or []
        for r in results:
            if not any(r["id"] == x["id"] for x in seen_all):
                seen_all.append(r)
        target = norm(variant)
        exact = [r for r in results if norm(r.get("title")) == target]
        if exact:
            return exact, seen_all, variant
    return [], seen_all, (query_variants(kobis_nm) or [kobis_nm])[0]


def pick(exact: list, movie_cd: str) -> tuple:
    """동명이작 가르기. (고른 결과 또는 None, 동률 후보들)."""
    if len(exact) == 1:
        return exact[0], []
    ry = reg_year(movie_cd)
    if ry is None:
        return None, exact

    def distance(r):
        y = release_year(r)
        return None if y is None else y - ry

    scored = []
    for r in exact:
        d = distance(r)
        if d is None:
            continue
        # 창 안이면 |차이|로, 밖이면 큰 벌점을 얹어 뒤로 민다.
        inside = YEAR_WINDOW[0] <= d <= YEAR_WINDOW[1]
        scored.append(((0 if inside else 1, abs(d)), r))
    if not scored:
        return None, exact
    scored.sort(key=lambda x: x[0])
    best = scored[0][0]
    tied = [r for score, r in scored if score == best]
    return (tied[0], []) if len(tied) == 1 else (None, tied)


def kr_release(detail: dict) -> tuple:
    """(가장 이른 KR 개봉일, 쓴 type, 재개봉일 또는 None).

    type 3(극장)이 우선이고 없으면 2 → 1로 내려가며 어느 것을 썼는지 남긴다.
    KR 항목이 아예 없으면 (None, None, None).
    """
    entries = []
    for block in (detail.get("release_dates") or {}).get("results") or []:
        if block.get("iso_3166_1") == "KR":
            entries.extend(block.get("release_dates") or [])
    for want in (3, 2, 1):
        stamps = sorted((e.get("release_date") or "")[:10]
                        for e in entries if e.get("type") == want
                        and e.get("release_date"))
        if stamps:
            rerelease = stamps[-1] if len(stamps) > 1 and stamps[-1] != stamps[0] else None
            return stamps[0], want, rerelease
    return None, None, None


def dotted(iso_date: str) -> str:
    return iso_date.replace("-", ".")


def resolve(token: str, candidate: dict) -> dict:
    """후보 하나를 확정한다. 항상 dict를 돌려주고 `status`로 갈린다.

    status: "resolved" | "needsHuman"
    """
    movie_cd = str(candidate.get("movieCd") or "")
    kobis_nm = candidate.get("kobisNm") or ""
    base = {"movieCd": movie_cd, "kobisNm": kobis_nm}

    def needs_human(reason, pool=()):
        return dict(base, status="needsHuman", reason=reason, candidates=[
            {"tmdbId": r["id"], "title": r.get("title"),
             "originalTitle": r.get("original_title"),
             "releaseDate": r.get("release_date")}
            for r in list(pool)[:5]
        ])

    try:
        exact, seen, used_query = search(token, kobis_nm)
    except TmdbError as e:
        return needs_human(f"TMDB 검색 실패: {e}")

    if not exact:
        return needs_human(
            f"제목이 정확히 일치하는 TMDB 항목이 없음 (검색어 {used_query!r})", seen)

    chosen, tied = pick(exact, movie_cd)
    if chosen is None:
        return needs_human(
            f"동명이작 {len(tied or exact)}건이 등록연도({reg_year(movie_cd)})로도 "
            f"갈리지 않음", tied or exact)

    tmdb_id = chosen["id"]
    try:
        detail = _get(token, f"/movie/{tmdb_id}?language=ko-KR"
                             "&append_to_response=release_dates")
        en_title = detail.get("original_title") or ""
        if (detail.get("original_language") or "") != "en":
            en_title = (_get(token, f"/movie/{tmdb_id}?language=en-US")
                        .get("title") or en_title)
    except TmdbError as e:
        return needs_human(f"TMDB 상세 조회 실패: {e}")

    date_iso, date_type, rerelease = kr_release(detail)
    if not date_iso:
        return needs_human(f"TMDB에 KR 개봉 정보가 없음 (tmdbId {tmdb_id})", [chosen])

    ry, y = reg_year(movie_cd), release_year(chosen)
    gap = None if (ry is None or y is None) else y - ry
    runtime = detail.get("runtime") or 0
    genres = [g.get("name") for g in (detail.get("genres") or []) if g.get("name")]

    return dict(
        base,
        status="resolved",
        tmdbId=tmdb_id,
        koTitle=detail.get("title") or chosen.get("title") or kobis_nm,
        en=(en_title or "").upper(),
        date=dotted(date_iso),
        dateType=date_type,
        reReleaseDate=dotted(rerelease) if rerelease else None,
        run=f"{runtime}분" if runtime else None,
        genre=" · ".join(genres) if genres else None,
        yearGap=gap,
        usedQuery=used_query,
    )


def resolve_all(token: str, candidates: list) -> tuple:
    """(확정된 목록, needsHuman 목록). 입력 순서를 유지한다."""
    resolved, needs = [], []
    for c in candidates:
        r = resolve(token, c)
        (resolved if r["status"] == "resolved" else needs).append(r)
    return resolved, needs
