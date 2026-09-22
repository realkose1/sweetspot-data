#!/usr/bin/env python3
"""KOBIS 상영스케줄 → `screening.json` (앱이 읽는 "지금 상영 중" 목록).

`docs/screening-contract.md` v1의 "봇 동작" 1~3과 "파일 3/4"를 구현한 것이다.
표준 라이브러리만 쓴다 — 인증·쿠키·CSRF·API 키가 없는 공개 엔드포인트다.

    POST https://www.kobis.or.kr/kobis/business/mast/thea/findSchedule.do
         theaCd=<6자리>&showDt=<yyyyMMdd>
    → {"theater": [...], "schedule": [{"scrnNm", "movieNm", "movieCd", "showTm"}, ...]}

## 판정의 핵심 — 관 이름이 아니라 제목 접미사가 포맷이다

CGV 용산 16관(SCREENX)이 2D 영화를 `(디지털)`로 튼다. 그래서 "이 관에서 이
작품이 포맷 Y로 상영 중"은 `scrnNm`이 그 관이고 **`movieNm` 접미사가 Y**일
때만 성립한다. 예외는 `SUPERPLEX`·`DOLBYVISION`처럼 *관 속성* 포맷이다 —
영화가 그 포맷으로 마스터링되는 게 아니라 관이 그런 관이라서, 이 관에서
`(디지털)`로 걸리면 그 관 속성 코드로 인정한다.

## 계약과 다르게 구현한 두 곳 (앱의 FormatCode가 근거)

- `(4D)` → 계약 표는 "`4DX` 또는 `MX4D`"라고 적었지만 `FormatCode`에 `MX4D`가
  없다. 실제로 MX4D 관(coexMx4d 등 3곳)의 `curated.json` `fmt`도 `["4DX"]`이고
  `MX4D`는 화면 표시용 `badge`일 뿐이다. 그래서 `(4D)`는 언제나 `4DX`를 낸다.
- 관 속성 돌비 코드는 계약 표의 `DOLBY_VISION`이 아니라 `DOLBYVISION`이다
  (`FormatCode.dolbyVision` raw value). 전용 돌비 시네마관의 `DOLBY`와 다른
  코드이며, 섞으면 앱의 포맷 적합도 점수가 틀어진다.

## 매칭할 때 공백

`scrnNm`에 꼬리 공백이 붙어 오는 극장이 있다(005043의 `"10관 "`). 반대로
매핑표에는 머리 공백을 그대로 넣어 만든 패턴이 있다(`"^\\ 4DX관$"` 3곳 —
012057·001298·001289는 KOBIS가 실제로 `" 4DX관"`을 보낸다). 그래서 strip한
이름으로 먼저 맞춰 보고, 안 맞으면 원문으로 한 번 더 맞춘다. 원문으로만
맞은 관은 매핑표를 고치라는 신호로 경고에 남긴다.
(`"Dolby Atoms관"`(002309)은 KOBIS의 오타지만 실제 문자열이므로 패턴이
그대로 오타를 담고 있다 — 고치면 안 된다.)

종료 코드: 0 성공 / 2 발행 불가(매핑·수집 실패) / 3 프리미엄 행 급감(전송
장애 의심). 2·3은 `screening_abort.txt`에 한 줄 이유를 남겨 워크플로우가
이슈로 만든다.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).resolve().parent.parent

HALLS_MAP = REPO_ROOT / "kobis_halls.json"
MOVIES_MAP = REPO_ROOT / "kobis_movies.json"
CURATED = REPO_ROOT / "curated.json"
SCREENING = REPO_ROOT / "screening.json"
STATE = REPO_ROOT / "screening_state.json"
CANDIDATES = REPO_ROOT / "screening_candidates.json"
CHANGES = REPO_ROOT / "screening_changes.json"
ABORT = REPO_ROOT / "screening_abort.txt"

KOBIS_URL = "https://www.kobis.or.kr/kobis/business/mast/thea/findSchedule.do"

# 브라우저형 헤더. 국내 공공 사이트는 UA나 Referer가 없는 요청을 말없이 버리는
# 경우가 있고, 이 엔드포인트는 페이지 안에서 XHR로 호출되는 것이라 Referer가
# 자연스럽다. robots.txt는 `allow: /`이고 자동 수집 금지 문구도 없다(분석 문서
# 2-4). 초당 한 건 아래로 두드리므로 위장해서 부하를 숨기는 것이 아니다.
REQUEST_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/140.0.0.0 Safari/537.36"),
    "Referer": "https://www.kobis.or.kr/kobis/business/mast/thea/findSchedule.do",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

HORIZON_DAYS = 3
# 로컬(한국)에서는 회당 0.44초인데 GitHub 러너(미국)에서는 3.4초가 나온다.
# 2026-09-20 CI dry run: 83곳 × 3일 = 249회에 14분 03초. 성공은 하지만 느리다.
REQUEST_TIMEOUT = 10
# 서버 자체 지연(러너에서 ~3.4초)이 이미 호출 간격을 벌려 주므로 잠깐만 쉰다.
# 이 값이 실제로 의미를 갖는 건 응답이 빠른 곳에서 돌 때(로컬 0.44초)뿐이다.
REQUEST_SLEEP = 0.1
# 전체 벽시계 예산. 넘으면 수집을 멈추고 남은 요청을 실패로 처리한 뒤 아래
# "절반 이상 실패" 규칙이 발행 여부를 정한다. 워크플로우의 step timeout(30분)
# 보다 짧게 둔다 — 러너가 단계를 죽이면 이유를 적을 기회조차 없기 때문이다.
DEFAULT_BUDGET_SECONDS = 25 * 60
# 연속 실패가 이만큼 쌓이면 예산이 남았어도 즉시 접는다. 2026-09-21 실행이
# 정확히 이 경우였다 — KOBIS가 러너에서 아예 응답하지 않아 126회 시도가 전부
# 10초 타임아웃으로 죽었고, 성공이 단 한 건도 없는 채로 예산 25분을 다 썼다.
# 실패는 빈 응답이 아니라 예외다(응답이 오면 스케줄이 비어도 성공으로 센다).
# 그래서 연속 예외 10건은 "이 실행은 가망이 없다"는 뜻이다 — 3분 안에 접고
# 같은 이유를 이슈로 올리는 편이 러너 25분을 태우는 것보다 낫다.
OUTAGE_FAILURE_STREAK = 10
THEATER_FAILURE_ABORT_RATIO = 0.5
PREMIUM_DROP_ABORT_RATIO = 0.5
RETIRE_STREAK_DAYS = 7
# 한 번 처리한(추가했거나 이유를 달아 거절한) 후보를 다시 제안하기까지 기다리는
# 날수. 같은 후보로 매일 유료 단계를 켜지 않기 위한 것이고, 그렇다고 영영
# 잊지도 않는다 — 극장 수가 늘거나 거절 사유가 낡을 수 있다.
CANDIDATE_REOFFER_DAYS = 7

# 작품 접미사 → 앞의 주석 참고. IMAX만 관에 따라 코드가 갈린다.
SUFFIX_IMAX = "IMAX"
SUFFIX_4D = "4D"
SUFFIX_SCREENX = "ScreenX"
SUFFIX_DOLBYCINEMA = "DOLBYCINEMA"

# 관이 그런 관인 포맷. 이 관에서 `(디지털)`은 "상영 중"이다.
HALL_ATTRIBUTE_FORMATS = ("SUPERPLEX", "DOLBYVISION")

GENERAL_SUFFIX_PREFIX = "디지털"      # "디지털", "디지털 더빙"

# 영화가 아닌 편성을 제목만으로 걸러내는 키워드. KOBIS 특수관 편성에는 콘서트
# 실황·라이브뷰잉·뮤지컬이 적지 않다 — 아이유 콘서트 한 편이 전국 26개 극장의
# IMAX·ScreenX·4DX에 걸려 있었다. 이들을 프롬프트에 넣으면 웹 검색 6회를
# "이건 영화가 아니다"를 확인하는 데 써 버린다.
#
# 최종 방어선은 어디까지나 `tmdbId` 필수 규칙이다. 이 정규식은 그 앞에 두는
# **비용 절감용 사전 필터**이고, 그래서 두 가지를 의도적으로 지켰다:
#   - `더빙`은 넣지 않는다. 더빙판은 영화다.
#   - ASCII 키워드는 단어 경계를 요구한다. `LIVE`를 부분 문자열로 잡으면
#     "DELIVERY"가, `TOUR`는 "DETOUR"가 걸린다.
# 그래도 콘서트 실황 *다큐멘터리*처럼 TMDB에 있는 작품이 이 그물에 걸릴 수
# 있다. 그때는 사람이 kobis_movies.json에 직접 넣으면 된다 — 로그에 이유가
# 남으므로 조용히 사라지지는 않는다.
NONFILM_PATTERNS = (
    "콘서트", "실황", "라이브 ?뷰잉", "뮤지컬", "오페라", "발레", "공연", "팬미팅",
    r"\bCONCERT\b", r"\bLIVE\b", r"\bTOUR\b",
)
NONFILM_RE = re.compile("|".join(NONFILM_PATTERNS), re.IGNORECASE)


def nonfilm_reason(kobis_nm: str):
    """제목이 비영화 키워드에 걸리면 걸린 키워드를, 아니면 None."""
    m = NONFILM_RE.search(kobis_nm or "")
    return m.group(0) if m else None
SUFFIX_RE = re.compile(r"\(([^()]*)\)$")
PRICE_PREFIX_RE = re.compile(r"^\s*\[[^\]]*\]\s*")
RESURVEY_RE = re.compile(r"^\s*RESURVEY\b")


class Abort(Exception):
    """발행을 멈춰야 하는 상태. code는 프로세스 종료 코드."""

    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


# ---------------------------------------------------------------- 제목 파싱

def clean_title(movie_nm: str) -> tuple[str, str | None]:
    """`movieNm` → (접미사·특가 접두를 벗긴 제목, 접미사 또는 None)."""
    nm = html.unescape(movie_nm or "").strip()
    suffix = None
    m = SUFFIX_RE.search(nm)
    if m:
        suffix = m.group(1).strip()
        nm = nm[: m.start()].strip()
    nm = PRICE_PREFIX_RE.sub("", nm).strip()
    return nm, suffix


def hall_imax_code(fmts: set) -> str:
    """관이 1.43:1을 투사하면 IMAX43, 아니면 IMAX190 (계약 표)."""
    return "IMAX43" if "IMAX43" in fmts else "IMAX190"


def format_for_row(suffix: str | None, hall_fmts: set) -> str | None:
    """접미사 + 관 포맷 목록 → 앱 FormatCode. 포맷 상영이 아니면 None."""
    if suffix is None:
        return None
    if suffix == SUFFIX_IMAX:
        return hall_imax_code(hall_fmts)
    if suffix == SUFFIX_4D:
        return "4DX"
    if suffix == SUFFIX_SCREENX:
        return "SCREENX"
    if suffix == SUFFIX_DOLBYCINEMA:
        # 메가박스는 Dolby Atmos관(= 앱의 DOLBYVISION) 상영도 `(DOLBYCINEMA)`로
        # 적는다. 6곳 전부 그렇다. 그대로 `DOLBY`를 내면 돌비 시네마 전용관과
        # 섞여 앱의 포맷 적합도 15점이 잘못 붙는다 — `FormatCode.dolbyVision`
        # 주석이 바로 그걸 막으려고 코드를 나눠 둔 것이므로, 관이 전용관이 아니라
        # 선언했으면 관 쪽을 따른다.
        if "DOLBY" not in hall_fmts and "DOLBYVISION" in hall_fmts:
            return "DOLBYVISION"
        return "DOLBY"
    if suffix.startswith(GENERAL_SUFFIX_PREFIX):
        # 일반 상영. 관 속성 포맷일 때만 인정한다.
        for code in HALL_ATTRIBUTE_FORMATS:
            if code in hall_fmts:
                return code
        return None
    return None   # (VR) 등


# ---------------------------------------------------------------- 수집

def fetch_schedule(thea_cd: str, show_dt: str) -> list:
    body = urllib.parse.urlencode({"theaCd": thea_cd, "showDt": show_dt}).encode()
    req = urllib.request.Request(KOBIS_URL, data=body, headers=dict(REQUEST_HEADERS))
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        payload = json.loads(r.read().decode("utf-8"))
    schedule = payload.get("schedule")
    return schedule if isinstance(schedule, list) else []


def collect_live(thea_cds: list, dates: list, warn, budget_seconds: float) -> dict:
    """{theaCd: {yyyyMMdd: [row, ...]}}. 실패한 (극장, 날짜)는 키가 없다.

    실패 집합을 **비관적으로** 들고 간다 — 성공한 것만 지운다. 그래서 시간
    예산이 끊겨 아예 시도조차 못한 (극장, 날짜)도 자동으로 실패로 남고, 그
    뒤의 "절반 이상 실패 → 발행 중단" 판정을 예산 초과에도 그대로 쓸 수 있다.
    끝까지 다 돌고 나서 판정하므로, 예산이 마지막 한두 극장에서 끊긴 경우에는
    (실패율이 낮으니) 그대로 발행된다 — 그게 맞다.

    진행 줄을 극장마다 하나씩, flush해서 찍는다. 2026-09-20 CI dry run에서
    이 단계가 14분 걸렸는데 로그에 "어디서 시간을 쓰는지"가 전혀 없어 느린
    것인지 멈춘 것인지 구분할 수 없었다. 회당 지연을 같이 찍으면 그 판단이
    로그만 보고 된다.
    """
    started = time.monotonic()
    raw: dict = {cd: {} for cd in thea_cds}
    failed = {dt: set(thea_cds) for dt in dates}
    budget_hit = None
    outage_hit = None
    streak = 0
    total = len(thea_cds) * len(dates)
    done = 0

    for show_dt in dates:
        if budget_hit or outage_hit:
            break
        for cd in thea_cds:
            elapsed = time.monotonic() - started
            if elapsed >= budget_seconds:
                budget_hit = (show_dt, cd, elapsed)
                break
            if streak >= OUTAGE_FAILURE_STREAK:
                outage_hit = (show_dt, streak, elapsed)
                break

            t0 = time.monotonic()
            rows = None
            for attempt in (1, 2):
                try:
                    rows = fetch_schedule(cd, show_dt)
                    break
                except (urllib.error.URLError, urllib.error.HTTPError,
                        json.JSONDecodeError, OSError, ValueError) as e:
                    if attempt == 2:
                        warn(f"{cd} {show_dt} 수집 실패: {e}")
                    else:
                        time.sleep(REQUEST_SLEEP * 2)
            latency = time.monotonic() - t0
            done += 1

            if rows is None:
                streak += 1
                mark = f"실패(연속 {streak})"
            else:
                streak = 0
                raw[cd][show_dt] = rows
                failed[show_dt].discard(cd)
                mark = f"{len(rows):4d}행"
            print(f"  [{done:3d}/{total}] {elapsed:6.1f}s {cd} {show_dt} "
                  f"{latency:5.2f}s {mark}", flush=True)
            time.sleep(REQUEST_SLEEP)

        n_failed = len(failed[show_dt])
        print(f"  {show_dt}: 수집 {len(thea_cds) - n_failed}곳 / 실패 {n_failed}곳 "
              f"(경과 {time.monotonic() - started:.0f}초)", flush=True)

    if budget_hit:
        dt, cd, elapsed = budget_hit
        warn(f"시간 예산 {budget_seconds:.0f}초 초과 ({elapsed:.0f}초 경과) — "
             f"{dt} {cd}에서 수집을 멈춘다. 남은 요청은 실패로 처리")
    if outage_hit:
        dt, n, elapsed = outage_hit
        warn(f"연속 {n}회 실패 ({elapsed:.0f}초 경과) — KOBIS 응답 없음으로 보고 "
             f"{dt}에서 수집을 접는다. 남은 요청은 실패로 처리")

    # 날짜 하나라도 절반 이상 실패하면 발행하지 않는다. 3일 창을 약속하고
    # 하루치만 싣는 것은 조용한 데이터 손실이다(빠진 날의 상영이 "없음"이 된다).
    for show_dt in dates:
        n_failed = len(failed[show_dt])
        if not thea_cds or n_failed / len(thea_cds) < THEATER_FAILURE_ABORT_RATIO:
            continue
        collected = len(thea_cds) - n_failed
        detail = (f"{show_dt} 수집 실패 극장 {n_failed}/{len(thea_cds)}곳 "
                  f"— 절반 이상 실패해 발행 중단")
        if outage_hit:
            detail = (f"KOBIS 응답 없음(연속 {outage_hit[1]}회 실패로 조기 중단): "
                      f"{collected}/{len(thea_cds)} 극장 수집 ({show_dt} 기준) — {detail}")
        elif budget_hit:
            detail = (f"시간 예산 초과: {collected}/{len(thea_cds)} 극장 수집 "
                      f"({show_dt} 기준) — {detail}")
        raise Abort(2, detail)
    return raw


def load_offline(path: Path) -> tuple[dict, list]:
    """고정 픽스처 {theaCd: {yyyyMMdd: [row,...]}} 를 읽고 날짜 창을 되돌린다.

    픽스처의 창은 픽스처를 뜬 날의 3일이라 실제 오늘과 어긋난다. 오프라인
    실행은 재현 가능해야 하므로 실제 오늘이 아니라 **픽스처가 담고 있는
    날짜**를 창으로 쓰고, 그 중 가장 이른 날을 '오늘'로 삼는다.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    dates = sorted({dt for per_thea in raw.values() for dt in per_thea})[:HORIZON_DAYS]
    if not dates:
        raise Abort(2, f"오프라인 픽스처에 날짜가 없음: {path}")
    return raw, dates


# --------------------------------------- 상태·후보 파일 (precheck·큐레이션 공용)
#
# `screening_state.json` / `screening_candidates.json`의 임자는 이 모듈이다.
# precheck.py와 daily_curation.py는 아래 함수로만 읽고 쓴다 — 같은 파일을 두
# 군데서 각자 해석하면 "처리했는데 또 제안"이 조용히 생긴다.

def load_state_file() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def save_state_file(state: dict) -> None:
    write_json(STATE, state)


def load_candidates_file() -> list:
    if not CANDIDATES.exists():
        return []
    data = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def parse_iso(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def offerable_candidates(candidates: list, state: dict, today) -> list:
    """아직 처리하지 않았거나, 처리한 지 CANDIDATE_REOFFER_DAYS일이 지난 후보."""
    handled = state.get("handledCandidates") or {}
    out = []
    for c in candidates:
        rec = handled.get(str(c.get("movieCd")))
        if rec:
            when = parse_iso((rec or {}).get("date"))
            if when is None or (today - when).days < CANDIDATE_REOFFER_DAYS:
                continue
        out.append(c)
    return out


def mark_candidate_handled(state: dict, movie_cd: str, action: str,
                           reason: str, today) -> None:
    handled = state.setdefault("handledCandidates", {})
    handled[str(movie_cd)] = {
        "date": today.isoformat(),
        "action": action,          # "added" | "rejected"
        "reason": reason,
    }


# ---------------------------------------------------------------- 입력 로딩

def load_inputs():
    halls_map = json.loads(HALLS_MAP.read_text(encoding="utf-8"))
    movies_map = json.loads(MOVIES_MAP.read_text(encoding="utf-8"))
    curated = json.loads(CURATED.read_text(encoding="utf-8"))
    return halls_map, movies_map, curated, load_state_file()


def build_hall_index(halls_map: dict, curated: dict, warn) -> dict:
    """{hallId: {theaCd, pattern, fmts}} — curated.json에 없는 id면 발행 중단."""
    curated_halls = {h["id"]: h for h in curated["halls"]}
    index = {}
    for hall_id, spec in halls_map["halls"].items():
        if hall_id not in curated_halls:
            raise Abort(2, f"kobis_halls.json의 hall id {hall_id!r}가 curated.json에 없음 "
                           f"— 발행 중단 (계약 '파일 3')")
        index[hall_id] = {
            "theaCd": spec["theaCd"],
            "pattern": re.compile(spec["scrn"]),
            "fmts": set(curated_halls[hall_id].get("fmt") or []),
        }
    for entry in halls_map.get("review", []):
        hall_id = entry.get("hallId")
        if hall_id and hall_id not in curated_halls:
            # review에 남은 항목은 발행되지 않으므로 중단 사유는 아니다.
            warn(f"review 항목 {hall_id!r}가 curated.json에 없는 id — 매핑표 확인 필요")
    return index


def resurvey_thea_cds(halls_map: dict) -> set:
    return {e["theaCd"] for e in halls_map.get("review", [])
            if e.get("theaCd") and RESURVEY_RE.match(e.get("reason", ""))}


def build_movie_index(movies_map: dict, curated: dict) -> dict:
    """{movieCd: workId}. curated.json에 없는 work id면 발행 중단."""
    curated_ids = {w["id"] for w in curated["works"]}
    index = {}
    for work_id, spec in movies_map["works"].items():
        if work_id not in curated_ids:
            raise Abort(2, f"kobis_movies.json의 work id {work_id!r}가 curated.json에 없음 "
                           f"— 발행 중단 (계약 '파일 3')")
        index[str(spec["movieCd"])] = work_id
    return index


# ---------------------------------------------------------------- 집계

def count_shows(show_tm: str) -> int:
    return len([s for s in (show_tm or "").split(",") if s.strip()])


def aggregate(raw: dict, dates: list, hall_index: dict, movie_index: dict, warn):
    """3일치 응답 → (works, premium_rows_today, candidates, works_with_premium).

    works: {workId: {hallId: {formats:set, lastSeen:str, showsToday:int}}}
    candidates: {movieCd: {kobisNm, formats:set, theaCds:set, dates:set}}
    """
    today = dates[0]
    works: dict = {}
    candidates: dict = {}
    premium_rows_today = 0
    unmapped_drift: set = set()
    raw_only_match: set = set()

    # 관 속성 포맷 관의 theaCd → 후보 발견에 쓴다(관 속성 관의 `(디지털)`도 후보).
    for hall_id, hall in hall_index.items():
        per_thea = raw.get(hall["theaCd"])
        if not per_thea:
            continue
        for show_dt in dates:
            for row in per_thea.get(show_dt, []):
                scrn = row.get("scrnNm") or ""
                if hall["pattern"].fullmatch(scrn.strip()):
                    pass
                elif hall["pattern"].fullmatch(scrn):
                    raw_only_match.add(hall_id)
                else:
                    continue

                title, suffix = clean_title(row.get("movieNm"))
                code = format_for_row(suffix, hall["fmts"])
                if code is None:
                    continue

                movie_cd = str(row.get("movieCd") or "").strip()
                if show_dt == today:
                    premium_rows_today += 1
                if code not in hall["fmts"]:
                    # 매핑 표류 신호 — 세기는 하지만 로그에 남긴다.
                    unmapped_drift.add(f"{hall_id}({code}, fmt={sorted(hall['fmts'])})")

                work_id = movie_index.get(movie_cd)
                if work_id is None:
                    cand = candidates.setdefault(movie_cd, {
                        "kobisNm": title, "formats": set(), "theaCds": set(), "dates": set(),
                    })
                    cand["formats"].add(code)
                    cand["theaCds"].add(hall["theaCd"])
                    cand["dates"].add(show_dt)
                    continue

                slot = works.setdefault(work_id, {}).setdefault(hall_id, {
                    "formats": set(), "lastSeen": "", "showsToday": 0,
                })
                slot["formats"].add(code)
                slot["lastSeen"] = max(slot["lastSeen"], show_dt)
                if show_dt == today:
                    slot["showsToday"] += count_shows(row.get("showTm"))

    for msg in sorted(unmapped_drift):
        warn(f"매핑 표류 의심 — {msg}에 없는 포맷 상영이 걸렸다 (세기는 했음)")
    for hall_id in sorted(raw_only_match):
        warn(f"{hall_id}: scrnNm 원문으로만 패턴이 맞았다 — kobis_halls.json의 "
             f"scrn에 들어간 공백을 정리할 것")
    return works, premium_rows_today, candidates


# ---------------------------------------------------------------- 출력 만들기

def iso(show_dt: str) -> str:
    return f"{show_dt[0:4]}-{show_dt[4:6]}-{show_dt[6:8]}"


def dotted(show_dt_iso: str) -> str:
    """'2026-09-21' → '2026.09.21' (curated.json의 날짜 형식)."""
    return show_dt_iso.replace("-", ".")


def build_screening(works: dict, now: datetime) -> dict:
    out_works = {}
    for work_id in sorted(works):
        halls = {}
        for hall_id in sorted(works[work_id]):
            slot = works[work_id][hall_id]
            halls[hall_id] = {
                "formats": sorted(slot["formats"]),
                "lastSeen": iso(slot["lastSeen"]),
                "showsToday": slot["showsToday"],
            }
        if halls:            # 상영 없는 작품은 키 자체가 없다
            out_works[work_id] = {"halls": halls}
    return {
        "schemaVersion": 1,
        "checkedAt": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "horizonDays": HORIZON_DAYS,
        "works": out_works,
    }


def build_candidates(candidates: dict, state: dict, today_iso: str) -> list:
    """후보 목록. firstSeenDate는 상태 파일에 남겨 실행마다 흔들리지 않게 한다."""
    first_seen = state.setdefault("candidateFirstSeen", {})
    out = []
    for movie_cd, c in candidates.items():
        earliest = iso(min(c["dates"]))
        if movie_cd not in first_seen:
            first_seen[movie_cd] = min(earliest, today_iso)
        out.append({
            "movieCd": movie_cd,
            "kobisNm": c["kobisNm"],
            "formats": sorted(c["formats"]),
            "theaterCount": len(c["theaCds"]),
            "firstSeenDate": first_seen[movie_cd],
        })
    # 잡힌 적 없는 movieCd 기억은 정리한다 — 후보에서 빠졌으면 잊는다.
    for movie_cd in list(first_seen):
        if movie_cd not in candidates:
            del first_seen[movie_cd]
    out.sort(key=lambda c: (-c["theaterCount"], c["movieCd"]))
    return out


def has_premium_badges(work: dict) -> bool:
    badges = work.get("badges") or []
    return any(b != "STD" for b in badges)


def parse_curated_date(s):
    try:
        return datetime.strptime(s, "%Y.%m.%d").date()
    except (TypeError, ValueError):
        return None


def update_state_and_retire(works: dict, curated: dict, state: dict,
                            premium_rows_today: int, today, warn):
    """lastPremiumSeen · noPremiumStreak 갱신 + 은퇴 판정. (changes, retired) 반환."""
    last_seen = state.setdefault("lastPremiumSeen", {})
    streak = state.setdefault("noPremiumStreak", {})
    changes = []

    premium_now = {}
    for work_id, halls in works.items():
        best = max((s["lastSeen"] for s in halls.values()), default="")
        if best:
            premium_now[work_id] = iso(best)

    for work_id, seen in premium_now.items():
        # 창 안에서 본 것이 상태에 남은 값보다 이를 수는 없다.
        last_seen[work_id] = max(last_seen.get(work_id, ""), seen)

    for work in curated["works"]:
        work_id = work["id"]
        if not has_premium_badges(work):
            streak.pop(work_id, None)
            continue
        if work_id in premium_now:
            streak[work_id] = 0
            continue
        streak[work_id] = int(streak.get(work_id, 0)) + 1

        if streak[work_id] < RETIRE_STREAK_DAYS or work.get("premiumEnd"):
            continue
        release = parse_curated_date(work.get("date"))
        if release and release > today:
            continue          # 미개봉작은 은퇴시키지 않는다
        end = last_seen.get(work_id)
        if not end:
            warn(f"{work_id}: 프리미엄 미상영 {streak[work_id]}일 연속이지만 "
                 f"lastPremiumSeen이 없어 premiumEnd를 정할 수 없음 — 사람 확인 필요")
            continue
        work["premiumEnd"] = dotted(end)
        changes.append({
            "kind": "retirement",
            "workId": work_id,
            "field": "premiumEnd",
            "value": work["premiumEnd"],
            "reason": f"3일 창에 프리미엄 상영이 없는 날 {streak[work_id]}일 연속 "
                      f"(계약 '봇 동작' 3). 배지는 그대로 둔다.",
        })

    state["prevPremiumRowCount"] = premium_rows_today
    return changes


# ---------------------------------------------------------------- 쓰기

def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="KOBIS 상영스케줄 → screening.json")
    ap.add_argument("--offline", metavar="PATH",
                    help="findSchedule 응답 픽스처로 네트워크 대신 동작 (테스트용)")
    ap.add_argument("--dry-run", action="store_true",
                    help="계산해서 출력만 하고 아무 파일도 쓰지 않음")
    ap.add_argument("--budget-seconds", type=float, default=DEFAULT_BUDGET_SECONDS,
                    metavar="N",
                    help=f"수집 전체 벽시계 예산 (기본 {DEFAULT_BUDGET_SECONDS:.0f}초). "
                         "넘으면 남은 요청을 실패로 처리하고 절반 규칙에 맡긴다")
    args = ap.parse_args(argv)

    warnings: list = []

    def warn(msg: str) -> None:
        warnings.append(msg)
        print(f"::warning::{msg}")

    now = datetime.now(KST)
    if ABORT.exists() and not args.dry_run:
        ABORT.unlink()

    try:
        halls_map, movies_map, curated, state = load_inputs()
        hall_index = build_hall_index(halls_map, curated, warn)
        movie_index = build_movie_index(movies_map, curated)

        thea_cds = sorted({h["theaCd"] for h in hall_index.values()}
                          | resurvey_thea_cds(halls_map))

        if args.offline:
            raw, dates = load_offline(Path(args.offline))
            print(f"오프라인 픽스처: {args.offline} — 창 {iso(dates[0])}~{iso(dates[-1])} "
                  f"(오늘 = {iso(dates[0])}), 극장 {len(raw)}곳")
            missing = [cd for cd in thea_cds if cd not in raw]
            if missing:
                warn(f"픽스처에 없는 극장 {len(missing)}곳: {', '.join(missing[:8])}"
                     + (" 외" if len(missing) > 8 else ""))
            fetched, failed = len(thea_cds) - len(missing), len(missing)
        else:
            base = now.date()
            dates = [(base + timedelta(days=i)).strftime("%Y%m%d")
                     for i in range(HORIZON_DAYS)]
            print(f"KOBIS 수집: 극장 {len(thea_cds)}곳 × 날짜 {len(dates)}일 "
                  f"({iso(dates[0])}~{iso(dates[-1])}) · 시간 예산 "
                  f"{args.budget_seconds:.0f}초", flush=True)
            raw = collect_live(thea_cds, dates, warn, args.budget_seconds)
            fetched = sum(1 for cd in thea_cds if raw.get(cd))
            failed = len(thea_cds) - fetched

        today_iso = iso(dates[0])
        works, premium_rows_today, candidates = aggregate(
            raw, dates, hall_index, movie_index, warn)

        # 프리미엄 행 급감 = 전송사업자 장애 의심. 발행하지 않는다.
        prev = state.get("prevPremiumRowCount")
        if isinstance(prev, int) and prev > 0 and \
                premium_rows_today < prev * PREMIUM_DROP_ABORT_RATIO:
            raise Abort(3, f"오늘 프리미엄 행 {premium_rows_today}건이 지난 실행"
                           f"({prev}건)의 50% 미만 — 전송 장애 의심, 발행 중단")

        screening = build_screening(works, now)
        cand_list = build_candidates(candidates, state, today_iso)
        changes = update_state_and_retire(
            works, curated, state, premium_rows_today, now.date(), warn)

        # kobis_movies.json에 들어간(= 작품이 된) movieCd의 처리 기록은 지운다.
        handled = state.get("handledCandidates") or {}
        for movie_cd in list(handled):
            if movie_cd in movie_index:
                del handled[movie_cd]

    except Abort as e:
        print(f"::error::{e.reason}")
        if not args.dry_run:
            ABORT.write_text(e.reason + "\n", encoding="utf-8")
        return e.code

    # ------------------------------------------------------------ 요약
    hall_count = sum(len(w["halls"]) for w in screening["works"].values())
    print()
    print(f"극장 {fetched}곳 수집 / {failed}곳 실패 · 프리미엄 행(오늘) "
          f"{premium_rows_today}건 · 상영 중 작품 {len(screening['works'])}편"
          f"(관 {hall_count}개) · 후보 {len(cand_list)}편 · 은퇴 {len(changes)}건")
    for work_id, w in screening["works"].items():
        fmts = sorted({f for h in w["halls"].values() for f in h["formats"]})
        shows = sum(h["showsToday"] for h in w["halls"].values())
        print(f"  {work_id:14s} 관 {len(w['halls']):3d}개  {','.join(fmts):32s} "
              f"오늘 {shows}회차")
    for c in cand_list[:12]:
        print(f"  후보 {c['movieCd']} {c['kobisNm']} "
              f"{','.join(c['formats'])} 극장 {c['theaterCount']}곳")
    for ch in changes:
        print(f"  은퇴 {ch['workId']}.premiumEnd = {ch['value']}")
    if warnings:
        print(f"경고 {len(warnings)}건")

    if args.dry_run:
        print("\n--dry-run — 아무 파일도 쓰지 않음")
        return 0

    write_json(SCREENING, screening)
    save_state_file(state)
    write_json(CANDIDATES, cand_list)
    if changes:
        write_json(CHANGES, {"generatedAt": screening["checkedAt"], "changes": changes})
        write_json(CURATED, curated)
    elif CHANGES.exists():
        CHANGES.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
