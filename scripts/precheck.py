#!/usr/bin/env python3
"""큐레이션 실행 여부 사전 점검 — Claude를 부를 이유가 있는 날인지 무료로 판정한다.

daily_curation.py 는 한 번 돌 때마다 $0.4 안팎이 든다(웹 검색 6회 + Sonnet 5).
그런데 이 앱의 데이터는 매일 바뀌지 않는다. 한국 개봉은 수요일에 몰려 있고,
특수관 포맷 확정·종료 공지는 개봉 전후 며칠에 집중된다. 그래서 매일 무조건
돌리면 한 달의 대부분을 "변경 없음"을 확인하는 데 쓴다.

이 스크립트는 매일 아침 먼저 돌면서, 아래 중 하나라도 해당할 때만 Claude 단계를
켠다. 전부 무료다 — TMDB는 무료 API이고 나머지는 날짜 계산이다.

  1. 수동 실행(workflow_dispatch)          → FORCE=1
  2. 화요일 또는 목요일(KST)                 → 개봉 전날·다음날. 주 2회 기본 점검.
  3. 어떤 작품의 개봉일이 오늘 ±3일 이내      → 포맷 확정/종료 공지가 나오는 창.
  4. TMDB 한국 상영작 목록에 처음 보는 작품   → 새 개봉이 있었다 = 판이 바뀌었다.
  5. TMDB의 한국 개봉일이 curated.json과 다름  → 개봉일이 옮겨졌다.

4·5는 TMDB_READ_TOKEN 이 있을 때만 검사한다. 없으면 1~3만으로 동작하며, 그 경우
기존 "주 2회"와 비용이 같고 요일만 개봉 사이클에 맞춰진 것이다.

## 스냅샷 (tmdb_snapshot.json)

4번의 "처음 보는 작품"을 판정하려면 지난번에 무엇을 봤는지 기억해야 한다. 그
기억이 저장소에 커밋되는 tmdb_snapshot.json 이다. 두 가지를 의도적으로 지켰다:

- **추가만 감지하고 이탈은 무시한다.** now_playing은 인기순이라 순위가 바뀌면
  1~2페이지 경계에서 작품이 드나든다. 이탈까지 트리거로 삼으면 순위 흔들림
  때문에 매일 Claude가 돈다. 새 id가 *처음* 나타나는 것만 세면 개봉 한 번에
  트리거 한 번이다. 90일 지난 작품은 스냅샷에서 지워 크기를 묶어둔다.
- **점검 시각을 스냅샷에 넣지 않는다.** 넣으면 매일 파일이 바뀌어 매일 커밋이
  생긴다. 내용이 바뀐 날만 파일이 바뀌고, 그 날은 어차피 Claude가 도는 날이다.

출력: $GITHUB_OUTPUT 에 run=true|false 와 reason=... 을 쓴다. 로컬에서는 stdout.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).resolve().parent.parent
CURATED = REPO_ROOT / "curated.json"
SNAPSHOT = REPO_ROOT / "tmdb_snapshot.json"

# 화(1)·목(3). Monday=0.
BASELINE_WEEKDAYS = {1: "화요일", 3: "목요일"}
RELEASE_WINDOW_DAYS = 3
NOW_PLAYING_PAGES = 2          # 인기순 40편. 그 밖의 소규모 개봉은 특수관과 무관.
SNAPSHOT_RETENTION_DAYS = 90
TMDB = "https://api.themoviedb.org/3"


def tmdb_get(token: str, path: str) -> dict:
    req = urllib.request.Request(
        TMDB + path,
        headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def parse_curated_date(s):
    """'yyyy.MM.dd' → date, 없거나 형식이 다르면 None."""
    try:
        return datetime.strptime(s, "%Y.%m.%d").date()
    except (TypeError, ValueError):
        return None


def kr_theatrical_date(token: str, tmdb_id: int):
    """TMDB의 한국 극장 개봉일(type 3). 없으면 KR 항목 중 가장 이른 날짜, 그것도 없으면 None."""
    try:
        data = tmdb_get(token, f"/movie/{tmdb_id}/release_dates")
    except (urllib.error.URLError, json.JSONDecodeError):
        return None
    for entry in data.get("results", []):
        if entry.get("iso_3166_1") != "KR":
            continue
        dates = entry.get("release_dates", [])
        theatrical = [d for d in dates if d.get("type") == 3]
        pool = theatrical or dates
        stamps = sorted(d["release_date"][:10] for d in pool if d.get("release_date"))
        return datetime.strptime(stamps[0], "%Y-%m-%d").date() if stamps else None
    return None


def main() -> int:
    today = datetime.now(KST).date()
    reasons: list[str] = []

    data = json.loads(CURATED.read_text(encoding="utf-8"))
    works = data["works"]

    # 1. 수동 실행
    if os.environ.get("FORCE") == "1":
        reasons.append("수동 실행")

    # 2. 기본 점검 요일
    if today.weekday() in BASELINE_WEEKDAYS:
        reasons.append(f"{BASELINE_WEEKDAYS[today.weekday()]} 기본 점검")

    # 3. 개봉일 창
    for w in works:
        d = parse_curated_date(w.get("date"))
        if d and abs((d - today).days) <= RELEASE_WINDOW_DAYS:
            reasons.append(f"{w['title']} 개봉일({w['date']})이 ±{RELEASE_WINDOW_DAYS}일 이내")

    # 4·5. TMDB — 토큰이 있을 때만
    token = os.environ.get("TMDB_READ_TOKEN", "").strip()
    snapshot_changed = False
    if not token:
        print("TMDB_READ_TOKEN 없음 — 개봉 감지(4·5) 생략, 요일·개봉일 창만 적용")
    else:
        snap = {"seen": {}, "releaseDates": {}}
        if SNAPSHOT.exists():
            snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        seen: dict = snap.setdefault("seen", {})         # tmdbId(str) → release_date
        rel: dict = snap.setdefault("releaseDates", {})   # workId → "yyyy.MM.dd"

        # 4. 처음 보는 상영작
        current_ids: set[str] = set()
        try:
            new_titles = []
            for page in range(1, NOW_PLAYING_PAGES + 1):
                np = tmdb_get(token, f"/movie/now_playing?region=KR&language=ko-KR&page={page}")
                for m in np.get("results", []):
                    mid = str(m["id"])
                    current_ids.add(mid)
                    if mid not in seen:
                        seen[mid] = m.get("release_date") or today.isoformat()
                        new_titles.append(m.get("title") or mid)
                        snapshot_changed = True
            if new_titles:
                shown = ", ".join(new_titles[:5]) + (" 외" if len(new_titles) > 5 else "")
                reasons.append(f"TMDB 한국 상영작에 새 작품 {len(new_titles)}편: {shown}")
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"::warning::TMDB now_playing 조회 실패 — 이 검사만 생략 ({e})")

        # 5. 기존 작품의 개봉일 변경
        for w in works:
            cur = parse_curated_date(w.get("date"))
            tid = w.get("tmdbId")
            if not tid:
                continue
            tm = kr_theatrical_date(token, int(tid))
            if tm is None:
                continue
            key = tm.strftime("%Y.%m.%d")
            if rel.get(w["id"]) != key:
                rel[w["id"]] = key
                snapshot_changed = True
            if cur and tm != cur:
                reasons.append(f"{w['title']} 개봉일이 TMDB({key})와 curated({w['date']}) 불일치")

        # 오래된 항목 정리 — 개봉이 오래됐고 **지금 상영 목록에도 없는** 것만.
        #
        # 개봉일만 보고 지우면 안 된다. 첫 테스트에서 90일 넘게 장기 상영 중인
        # 작품(백룸 등 4편)이 스냅샷에 들어가자마자 "오래됨"으로 지워지고, 다음
        # 날 now_playing에서 다시 "처음 보는 작품"으로 잡혀 매일 Claude를
        # 깨웠다 — 이 스크립트가 막으려던 바로 그 비용이다. 현재 목록에 있는
        # 한은 개봉일과 무관하게 기억하고, 목록에서 빠진 뒤에야 정리한다.
        cutoff = (today - timedelta(days=SNAPSHOT_RETENTION_DAYS)).isoformat()
        stale = [k for k, v in seen.items()
                 if (v or "9999") < cutoff and k not in current_ids]
        for k in stale:
            del seen[k]
        if stale:
            snapshot_changed = True

        if snapshot_changed:
            SNAPSHOT.write_text(
                json.dumps(snap, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    run = bool(reasons)
    reason = " / ".join(reasons) if run else "트리거 없음 — Claude 점검 생략"
    print(f"[{today.isoformat()}] run={'true' if run else 'false'}  {reason}")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"run={'true' if run else 'false'}\n")
            f.write(f"reason={reason}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
