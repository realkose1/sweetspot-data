# `curated.json` — 몇관몇열 curated data feed

This file is the app's entire editorial value: the film list (`works`) and
the per-hall format/aspect-ratio facts (`halls`) shown in the 포맷 탭. Editing
and publishing this one file is how you update the app **without** an Xcode
build or an App Store review.

This exact file is also bundled inside the app itself (via `project.yml`), so
a fresh install with no network renders identically to whatever this file
says at build time. The app then checks the published URL once a day and
switches over automatically if what it finds there is newer and valid.

## The one rule that matters: never guess

Every value in this file was originally hand-verified against a real source
(나무위키, each chain's own site, or news coverage of a specific hall/format).
**If you don't know a value, leave it `null`.** The app is built to render
"—" or omit a row for a `null` field rather than show a wrong number — a
guessed seat count or an invented aspect ratio is worse than no answer,
because someone will plan a trip to a specific seat around it. This applies
especially to:

- `seats` — most Korean chains don't publish this. Don't estimate from photos.
- `price` — no chain publishes premium-format surcharge pricing anywhere
  this app's original research found. Leave it `null` until you have an
  actual receipt or a published rate card.
- `ratioInfo.projectedRatio` — this must be the ratio the **projector**
  actually outputs, not the ratio the **screen** is shaped for. Korean IMAX
  halls in particular: a GT-shaped screen does NOT mean 1.43:1 output unless
  the projector is also GT-generation (see CGV 천호 vs CGV 용산아이파크몰 in
  this file for the canonical example, and `ratioInfo.mismatchNote` for how
  to flag that kind of case explicitly rather than let it look consistent).
- `screen` — physical dimensions. Only fill in when a source states them.
- `lat`/`lon` — building-level GPS coordinates of the physical complex a hall
  is in. Only fill in from an actual source (a landmark's published
  coordinates, e.g. Wikipedia's infobox, or a named subway station/exit the
  hall is confirmed to sit at/inside) — never eyeball a location on a map.
  Leave both `null` (never one without the other) until you have a real
  basis. A hall with no coordinates simply gets no distance treatment
  anywhere in the app (onboarding's map pin still shows every hall marker
  it can, but distance-based ranking/sorting skips that one hall) — exactly
  like a nil `seats` renders as "—" instead of a guess.

If a chain's marketing page changes but you can't independently confirm the
new number is correct, it's fine to leave the old value in place and note
your uncertainty in `ratioNote`/`mismatchNote` rather than publish a guess.

## What each field means

Top level:

| Field | Meaning |
|---|---|
| `schemaVersion` | Always `1` right now. Only change this if you're deliberately restructuring the schema **and** have shipped an app update that understands the new shape — an app build that doesn't recognize the version number ignores the whole file and keeps using its last-known-good data. Don't bump this to "force" an update; it does the opposite. |
| `dataTimestamp` | ISO-8601 UTC timestamp, e.g. `2026-08-14T00:00:00Z`. Update this every time you publish, even for a small edit — it's what "포맷 데이터 기준" in 설정 shows the user. |
| `works` | The film list. Order doesn't matter to the app. |
| `halls` | The theater/hall list. Order doesn't matter to the app. |

### `works[]` (films)

| Field | Meaning |
|---|---|
| `id` | Stable slug, lowercase, no spaces (e.g. `odyssey`). Never reuse an id for a different film, and never change an existing film's id — the app persists per-id state (favorites-adjacent references) keyed on this. |
| `title` | Korean display title. |
| `en` | Original/English title — used to search TMDB for a poster when `tmdbId` is absent. |
| `pc1`, `pc2` | Hex colors (`"#rrggbb"`) for the film's gradient placeholder card, used before/if a poster loads. Pick two dark, film-appropriate tones. |
| `date` | Curated fallback release date, **exactly** `"yyyy.MM.dd"` — anything else is rejected as invalid and the whole file is ignored. This is only a fallback: the app prefers a live TMDB Korean theatrical date when available and only falls back to this string on failure/offline. Set it anyway; it's the only thing shown when TMDB has nothing. |
| `run` | Runtime, free text (e.g. `"172분"`), or `null` if unknown. |
| `genre` | Free text, or `null`. |
| `meta` | Must be exactly `"{date} 개봉 · {rest}"` — the app swaps only the leading date at display time and keeps `{rest}` verbatim, so make sure `meta` actually starts with the same date you put in `date`. |
| `badges` | Array of format codes this film is actually released in. Valid values: `"IMAX43"`, `"IMAX190"`, `"DOLBY"`, `"SCREENX"`, `"4DX"`, `"SUPERPLEX"`, `"STD"`. Must have at least one entry — use `["STD"]` for a film with no special format. **Do not invent a new code** — the seven above are the only ones the app knows how to render (name/color/icon); a genuinely new premium format needs an app update, not just a JSON edit. |
| `hook` | One short line of editorial claim about the best way to see this film (e.g. `"CGV 용산 IMAX — 국내 유일 1.43:1"`), or `null` if there's nothing to say beyond "일반관으로 충분". |
| `open` | Ticket-open announcement line, only when a chain has actually announced one publicly. `null` otherwise — don't guess a date. |
| `tmdbId` | The film's TMDB movie id (integer), or `null`. Strongly recommended: look it up at themoviedb.org and pin it here — without it the app falls back to a fuzzy title search that can mismatch retitled or common-name films. Must be a positive integer if present. |
| `recommendedFormat` | One format code (not an array) — the single best format for this film, must be one of the codes also present in `badges`. |

### `halls[]` (theaters)

| Field | Meaning |
|---|---|
| `id` | Stable slug (e.g. `yongsan`). **Never rename an existing hall's id** — `Theater.swift` (the seat-map geometry, compiled into the app separately, not part of this file) joins to this list by matching `id`. Renaming an id silently disconnects that hall's seat map. |
| `badge` | Which format code this hall's home-screen badge shows (usually its most notable format). |
| `full` | Full display name. |
| `short` | Short display name for compact chips. |
| `place` | Region label (e.g. `"서울 용산"`). |
| `fmt` | Array of format codes this hall actually runs — same valid-code list as `works[].badges`. Must be non-empty. |
| `screen` | Free-text physical screen description, or `null` if not confirmed. Include dimensions and a note if a source disagrees or the figure is disputed. |
| `ratioInfo.shapeLabel` | The screen's physical shape, only when a source distinctively calls it out (e.g. `"GT 형상 (1.43:1)"`). `null` otherwise. |
| `ratioInfo.projectedRatio` | **The single most important field in this file.** What the projector actually outputs, as `"N.NN:1"` (e.g. `"1.43:1"`), or a plain label like `"표준"` / `"해당 없음 (측면 확장)"` for formats with no single-frame ratio (ScreenX, 4DX). A numeric `"N:1"`-shaped value must actually parse as two positive numbers — the app rejects the whole file if it doesn't. |
| `ratioInfo.ratioNote` | Supporting detail, or `null`. |
| `ratioInfo.mismatchNote` | Set **only** when `shapeLabel` and `projectedRatio` disagree — i.e. the screen looks like it should do more than the projector delivers (CGV 천호 is the model case). This is the field that keeps the app honest about screen-shape-vs-actual-output; don't skip it for a hall where it applies. |
| `proj` | Projector description, or `null`. |
| `sound` | Sound system description, or `null`. |
| `seats` | Integer seat count, or `null`. Must be a positive number if present — see the "never guess" section above. |
| `price` | Premium-format surcharge, free text, or `null`. See above — almost certainly `null` for every hall today. |
| `lat`, `lon` | Building-level GPS coordinates (decimal degrees, e.g. `37.5119`), or both `null`. Added 2026-08-15 — optional at every layer, so a payload without them (or an older cached one) still validates; schema stays v1. Must travel together — one set without the other fails the whole payload. The app rejects the whole payload if either falls outside a generous Korea bounding box (`lat` 33-39, `lon` 124-132), as a sanity check against a swapped-digit typo. See the "never guess" section above — same discipline as `seats`/`screen`. |

## How to publish an update

1. Edit `curated.json` directly (this file, not a copy). Keep it valid JSON —
   any JSON editor or even a plain text editor works, but validate before
   publishing (`python3 -m json.tool curated.json` is a quick sanity check,
   or paste into any online JSON validator).
2. Update `dataTimestamp` to the current time.
3. Double-check every new/changed value against the "never guess" rule above.
4. Upload the file somewhere stable and publicly reachable over HTTPS. You
   have two options already available, pick whichever is easier day to day:
   - **Vercel**: drop `curated.json` into a static site/project you deploy
     there, so it's served at a fixed URL.
   - **GitHub**: commit it to a repo and use the raw file URL
     (`https://raw.githubusercontent.com/<user>/<repo>/<branch>/curated.json`).
   Either way, the app doesn't care what serves the file as long as it's a
   plain HTTPS GET returning this JSON with a normal `200`/`304` response —
   no API, no auth, no server code needed.
5. Set that URL once as `CURATED_FEED_URL` in `Config/Base.xcconfig` (already
   wired through to the app — see that file's comment). This only needs to
   be done once, at the next app build; after that, publishing just means
   repeating steps 1-4 with no further app changes or releases.
   **Watch out**: a bare `//` in an `.xcconfig` file starts a comment and
   silently truncates the URL — write `https:/$()/host/curated.json`, with
   the empty `$()` actually splitting the two slash characters (not just
   placed next to them), instead of a plain `https://...`. Base.xcconfig's
   own comment shows the exact syntax.
6. The app checks the URL at most once per day (on launch/foreground) and
   only switches over if the new file parses and passes validation — a
   broken upload is silently ignored and the previous good data keeps
   showing, so there's no way to "break" the live app by publishing a bad
   file. Give it up to a day (or force-quit/relaunch the app) to see a
   published change take effect.

## What's intentionally NOT in this file

Seat-grid geometry (row/column counts, aisle positions, row pitch, riser
height, etc. — the data behind the 극장 탭's 명당 seat map) stays compiled
into the app (`Sources/Models/Theater.swift`), not published here. It changes
far less often than film/format data, and every value in it is already this
app's own best-effort estimate rather than a published fact — see that
file's own header comment for the full reasoning. A hall listed here with no
matching seat-grid entry just shows without a seat guide, which the app
already handles gracefully.

---

## `privacy.html` — 앱 개인정보 처리방침

이 저장소는 데이터 피드 외에 개인정보 처리방침 페이지도 하나 서빙합니다. App
Store가 개인정보 처리방침 URL을 필수로 요구하는데, 그 페이지 하나를 위해
호스팅을 따로 두는 것보다 이미 있는 이 저장소를 GitHub Pages로 여는 편이
간단해서 여기에 뒀습니다.

- 게시 주소: `https://realkose1.github.io/sweetspot-data/privacy.html`
- **원본은 이 저장소가 아니라 앱 저장소의 `appstore/privacy-policy.md`입니다.**
  문구를 고칠 때는 그 파일을 먼저 고치고 여기로 옮기세요. 방침 문구는 App
  Store Connect의 "앱 개인정보 보호" 설문 답안과 반드시 일치해야 하며, 그
  대조표가 원본 파일에 함께 정리돼 있습니다.

`curated.json`과는 완전히 무관합니다. 큐레이션 봇(`.github/workflows`)은
`curated.json`만 건드리므로 이 파일을 덮어쓰지 않고, 앱은 피드를
`raw.githubusercontent.com`에서 직접 읽으므로 Pages 활성화 여부와 무관하게
동작합니다.

---

## 자동 큐레이션 봇 — 매일 점검, 조건부 실행 (2026-09-01~)

`.github/workflows/daily-curation.yml` 이 매일 06:00 KST에 돈다. 세 단계이고,
앞의 둘은 무료다. (0단계인 `scripts/screening.py`는 아래 "상영 데이터" 절에 있다.)

1. **`scripts/precheck.py` — 무료 사전 점검.** Claude를 부를 이유가 있는지 판정한다.
   화·목 기본 점검 / 어떤 작품의 개봉일 ±3일 / TMDB 한국 상영작에 새 작품 등장 /
   TMDB 개봉일과 `curated.json` 불일치 / 수동 실행 / **KOBIS 특수관 상영 후보
   잔존**. 이유가 하나도 없으면 Claude는
   부르지 않고, `dataTimestamp`만 오늘로 옮겨 커밋한다 — 앱의 "포맷 데이터 기준
   N월 N일" 라벨이 이 값이라, "오늘 살펴봤고 새 개봉·개봉일 변동이 없었다"는 사실을
   날짜로 보여주기 위해서다(2026-09-04 owner 결정). 그래서 `curated.json`에는 매일
   한 줄짜리 커밋이 쌓인다. 의도된 동작이다.
2. **`scripts/daily_curation.py` — Claude 큐레이션.** 사전 점검이 켠 날만 돈다. 회당
   $0.4 안팎(Sonnet 5 정가, 웹 검색 6회). 2026-09-20부터 **작품 추가**도 한다 —
   아래 "상영 데이터 · 작품 추가와 은퇴" 절.

이전(월·금 무조건 실행)과 비교하면 요일은 수요일 개봉 사이클에 맞춰지고, 비용은
"판이 바뀐 날"에만 나간다. 왜 이렇게 짰는지는 `precheck.py` 머리말에 있다.

**`tmdb_snapshot.json`** 은 사전 점검의 기억이다 — 지금까지 본 상영작 id와 각
작품의 TMDB 개봉일. 새 작품이 나타난 날만 바뀌고 그 날 큐레이션 커밋에 함께
실린다. 손으로 고칠 일은 없다. 지우면 다음 실행에서 다시 시딩되며, 그 한 번은
상영작 40편이 전부 "새 작품"으로 잡혀 Claude가 돈다.

**`TMDB_READ_TOKEN`** 시크릿이 없어도 동작한다 — 그 경우 TMDB 검사 두 개만 빠지고
화·목 + 개봉일 창으로만 돈다(옛 "주 2회"와 비용 동일).

**배선만 확인하고 싶을 때:** Actions → 이 워크플로우 → Run workflow → `dry_run` 체크.
상영 수집(`--dry-run`)과 사전 점검만 돌고 Claude·커밋·이슈는 모두 건너뛴다. 비용 0.

## 상영 데이터 — KOBIS 매일 수집 (2026-09-20~)

`docs/screening-contract.md` v1의 구현이다. 앱에 "이 작품이 지금 어느 관에서
그 포맷으로 상영 중인가"를 넘긴다. 소스는 영화진흥위원회 통합전산망(KOBIS)
상영스케줄 — 공개·무료·무키이며, 체인이 법에 따라 보고한 데이터다. 앱은 KOBIS를
직접 부르지 않는다(접속처를 늘리지 않는다).

### 파일

| 파일 | 누가 쓰나 | 누가 읽나 |
|---|---|---|
| `kobis_halls.json` | 사람 (수작업 검수) | `screening.py` |
| `kobis_movies.json` | 사람 + `daily_curation.py`(작품 추가 시) | `screening.py` |
| `screening.json` | `screening.py` | **앱** |
| `screening_state.json` | `screening.py`, `daily_curation.py` | 봇만 |
| `screening_candidates.json` | `screening.py` | `precheck.py`, `daily_curation.py` |
| `screening_changes.json` | 두 스크립트 | 워크플로우(이슈) |
| `screening_abort.txt` | `screening.py` | 워크플로우(이슈) |

`screening.json`은 `curated.json`과 **섞지 않는다.** 두 봇은 실패 모드도 갱신
주기도 다르다. 하나가 죽어도 다른 하나는 살아야 한다. 앱도 별도 캐시 키·별도
검증기로 받고, `checkedAt`이 3일 넘게 지나면 파일을 무시하고 기존 포맷 후보
모드로 돌아간다.

### 순서

```
screening.py  (무료, 매일)  →  precheck.py  (무료)  →  daily_curation.py  (유료, 조건부)  →  커밋
```

`screening.py`는 `kobis_halls.json`의 극장코드 83개에 대해 3일치(당일·+1·+2)를
`findSchedule.do`로 받는다. 249회 호출, 비용 0. **GitHub 러너에서는 14분쯤
걸린다** — KOBIS 응답이 회당 3.4초로 느리기 때문이고(로컬 한국에서는 0.2~0.6초),
실패가 아니라 그냥 느린 것이다(2026-09-20 CI 실측 83/83 성공). 진행 줄에 극장마다
경과 시간과 회당 지연을 찍으므로 로그만 보고 "느린 것"과 "멈춘 것"을 구분할 수 있다.
시간 방어는 세 겹이다: 스크립트 예산 25분(`--budget-seconds`) → 단계 타임아웃
30분 → 잡 타임아웃 50분. 안쪽이 먼저 걸려야 중단 사유가 파일에 남는다.
포맷 판정은 **관 이름이 아니라
`movieNm` 접미사**로 한다 — ScreenX 관이 2D 영화를 `(디지털)`로 틀기 때문이다.
예외는 `SUPERPLEX`·`DOLBYVISION` 같은 관 속성 포맷이다.

오프라인 재현: `python3 scripts/screening.py --offline <findSchedule 응답 픽스처>`.
아무 것도 쓰지 않고 계산만 보려면 `--dry-run`.

### 실패하면

| 상황 | 종료 코드 | 동작 |
|---|---|---|
| `curated.json`에 없는 hall/work id | 2 | **발행 중단.** 이슈 1건 |
| 어떤 날짜든 극장 절반 이상 수집 실패 | 2 | **발행 중단.** 이슈 1건 |
| 오늘 프리미엄 행 수가 지난 실행의 50% 미만 | 3 | **발행 중단.** 이슈 1건 (전송 장애 의심) |
| 시간 예산(25분) 초과 | 2 | 남은 요청을 실패로 처리 → 위 "절반 이상" 규칙이 판정 |
| 단계 타임아웃(30분)에 러너가 죽임 | (없음) | 이슈 1건. `outcome`으로 잡으므로 사유 파일이 없어도 열린다 |
| 극장 몇 곳 실패 | 0 | 건너뛰고 로그. 발행은 계속 |

발행 중단은 `screening.json`을 **건드리지 않는다**. 어제 파일이 그대로 남고,
사흘이 지나면 앱이 스스로 포맷 후보 모드로 돌아간다. "상영 안 함"이라고 쓰는
경우는 없다 — KOBIS 자신이 "일부 정보가 제공되지 않을 수 있다"고 고지한다.

`screening.py`가 실패해도 `precheck.py`·`daily_curation.py`는 그대로 돈다.

### 작품 추가와 은퇴

- **추가** — 프리미엄 접미사로 걸렸는데 `kobis_movies.json`에 없는 `movieCd`는
  `screening_candidates.json`에 후보로 쌓이고, 그것만으로 사전 점검이 유료 단계를
  켠다. 하루 최대 2편, 극장 수가 큰 것부터. `tmdbId`가 확정되지 않으면 검증기가
  거부한다 — 콘서트 실황·라이브뷰잉·스포츠 중계를 걸러내는 실질적인 장치다.
  처리한 후보는 `screening_state.json`의 `handledCandidates`에 남아 7일간 다시
  제안되지 않는다.
- **은퇴** — 프리미엄 배지가 있는 작품이 3일 창 어디에도 프리미엄 상영이 없는
  날이 7일 연속이면 `premiumEnd = lastPremiumSeen`. **배지는 지우지 않는다**(이력).
  미개봉작은 은퇴시키지 않는다.

### 이슈

작품 추가 · 배지 변경 · 프리미엄 은퇴 · 상영 수집 중단, 이 네 가지 때만 연다.
같은 제목의 열린 이슈가 있으면 다시 열지 않는다. 매일 열리던 "큐레이션 검토
필요" 이슈는 폐지했다 — 남아 있는 것은 `scripts/close_stale_issues.sh`로 한 번에
닫는다(기본 `--dry-run`, 실제로 닫으려면 `--apply`).
