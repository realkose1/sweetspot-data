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
