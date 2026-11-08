# API Contract — Watermarks Remover Web UI (Manual)

FROZEN CONTRACT. Backend and frontend are built against this. Do not change
response shapes without updating both sides.

## Layout

```
/home/zet/Projects/punyafajar/
  API_CONTRACT.md              <- this file
  watermarks-remover/          <- cloned upstream repo (do NOT modify)
  app/
    server.py                  <- backend (stdlib only, Python 3.10+)
    start.sh                   <- convenience launcher
    README.md                  <- usage (written last by orchestrator)
    static/
      index.html
      styles.css
      app.js
```

- Backend default bind: `http://127.0.0.1:8770` (env `HOST`, `PORT`).
- Upstream repo path: env `WATERMARKS_REPO`, default `<app>/../watermarks-remover`.
- Python is 3.13 here: **`cgi` module does not exist**. Do not use it.
- No third-party dependencies. stdlib only. No build step. No CDN (offline use).
- Frontend is served by the same server (same origin), so no CORS needed.

## What "manual" means

User pastes text copied from a web chat, OR uploads a file. The server runs the
upstream **Layer A** pipeline (`clean_file.py`) and reports + returns the result.
No LLM / Layer B is involved.

Backend shells out to (with `sys.executable`):

- `service/scripts/inspect_file.py <path> --json`
- `service/scripts/clean_file.py <path> -o <out> --json`

Repo relative to `WATERMARKS_REPO`.

## Transport rules

- Input and output are always `Content-Type: application/json; charset=utf-8`.
- Files are sent as **base64 in JSON** (no multipart — keeps Python 3.13 safe):

```jsonc
// text input
{ "text": "Hello\u200bWorld", "options": { ... } }

// file input
{ "filename": "photo.png", "file_base64": "iVBORw0...", "options": { ... } }
```

- `options` (all optional, booleans with these defaults):

```json
{
  "nfkc": false,
  "normalize_spaces": true,
  "strip_emoji_glue": false,
  "strip_bidi": false,
  "aggressive_homoglyphs": false,
  "strip_ai_tells": true,
  "deep_scrub": true,
  "deai_style": 0,
  "verify": true
}
```

Option -> CLI flag mapping:

| option (true) | inspect_file flag | clean_file flag |
|---|---|---|
| nfkc | (none) | `--nfkc` |
| normalize_spaces = false | (none) | `--no-normalize-spaces` |
| strip_emoji_glue | (none) | `--strip-emoji-glue` |
| strip_bidi | (none) | `--strip-bidi` |
| aggressive_homoglyphs | `--aggressive` | `--aggressive-homoglyphs` |

`strip_ai_tells` has **no upstream CLI flag**: it is an app-side post-processing
pass in `server.py` (see below). The same applies to `deep_scrub`, `deai_style`
and `verify` — all four are app-side, not upstream flags.

`deai_style` is an **integer** `0|1|2`, not a boolean. `0` = off, `1` =
conservative phrasing only, `2` = also drop intensifiers/filler. Any other value
(including a bool or a non-numeric string) must be coerced to `0` without
raising.

- Limits: raw input max **25 MiB** (for base64, decode first then check). Subprocess
  timeout 60s. Temp work dir via `tempfile.mkdtemp()`, always cleaned up.
- Never return absolute host paths or subprocess stderr verbatim (return a short,
  safe message).

## Endpoints

### `GET /` -> serve `static/index.html`
### `GET /assets/<file>` -> serve `static/<file>` (css/js; correct Content-Type)
### `GET /api/health` ->
```json
{ "ok": true, "service": "watermarks-web", "version": "1.0.0",
  "repo": true, "python": "3.13.13" }
```

### `POST /api/inspect`
Body = text input or file input. Runs `inspect_file.py --json` (+ `--aggressive`
when `aggressive_homoglyphs`).
```json
{
  "ok": true,
  "kind": "text",
  "name": "pasted.txt",
  "suspicious": true,
  "summary": "3 tanda ditemukan",
  "report": { }
}
```
- `kind`: `text` | `image` | `container` | `av` | `unknown`.
- `suspicious`: bool (default false). For text true when upstream
  `suspicious_total > 0` **or** the app-side `ai_tells.total > 0` (see below).
- `summary`: short human string (Indonesian) safe to display; for text it counts
  Layer A hits plus app-side AI tells.
- `report`: the raw `inspect_file.py --json` object, passed through (may be `{}`).
  For text inputs, `report["ai_tells"]` is always attached with the app-side
  detection counts (may be all zeros).

## App-side AI-tell pass (`strip_ai_tells`)

After the upstream Layer A clean, `server.py` optionally strips **visible**
typographic "AI tells" that Layer A ignores. Runs only for text inputs and for
files classified as `text`; image/container/av bytes are never rewritten.

- **Dashes -> removed** (no replacement): U+2010, U+2011, U+2012, U+2013 (en),
  U+2014 (em), U+2015, U+2043, U+2212, U+2E3A, U+2E3B, U+FE58, U+FE63, U+FF0D.
  Spacing is tidied so tokens do not merge or leave a double space:
  `"a — b"` -> `"a b"`, `"a—b"` -> `"a b"`. The keyboard hyphen-minus (U+002D)
  is **never** touched.
- **Curly quotes -> straight ASCII**: U+2018/U+2019/U+201A/U+201B -> `'`;
  U+201C/U+201D/U+201E/U+201F -> `"`; U+2032 -> `'`; U+2033 -> `"`.
- **Emoji -> removed**: emoji pictograph ranges (U+1F1E6-U+1F1FF, U+1F300-U+1FAFF,
  U+2600-U+26FF, U+2700-U+27BF, U+2B00-U+2BFF, skin tones) plus glue/selector
  singles (ZWJ U+200D, keycap U+20E3, VS15/VS16 U+FE0E/U+FE0F) and common
  standalone symbols.
- **ASCII emoticons -> removed** via a conservative, boundary-anchored regex
  (`:)`, `:-)`, `:(`, `:D`, `;)`, `<3`, `xD`, `^_^`, ...). Doubled spaces left by
  emoticon runs are collapsed.

Counts are returned as `report["ai_tells"]`:
`{"dashes":int,"quotes":int,"emoji":int,"emoticons":int,"total":int}` — attached
on `/api/clean` when `total > 0` (and `changed` becomes true), and always
attached on `/api/inspect` for text.

## App-side deep scrub (`deep_scrub`)

Layer A deliberately preserves some invisible/format characters (they are
"load-bearing" for emoji families, script joining, legitimate RTL). `deep_scrub`
is a second deterministic pass that removes the rest, so the output can be
proven to carry no hidden carrier at all.

Removed groups (one key per group, plus `total`):

| group | codepoints |
|---|---|
| `bidi` | U+061C, U+200E, U+200F, U+202A–U+202E, U+2066–U+2069 |
| `tags` | U+E0001, U+E0020–U+E007F |
| `invisible` | U+00AD, U+200B, U+2060–U+2064, U+FEFF, U+180E, U+034F, U+115F, U+1160, U+3164, U+FFA0, U+FFF9–U+FFFB |
| `private` | U+E000–U+F8FF, U+F0000–U+FFFFD, U+100000–U+10FFFD |
| `noncharacter` | U+FDD0–U+FDEF, any cp with `(cp & 0xFFFE) == 0xFFFE` |
| `tatweel` | U+0640 |
| `unassigned` | any `unicodedata.category(ch) == "Cn"` |

U+200C (ZWNJ), U+200D (ZWJ), U+FE0E/U+FE0F (variation selectors) are **never**
removed here — they are legitimate glue and are governed by the separate
`strip_emoji_glue` / `strip_ai_tells` options.

Shape: `{"bidi":int,"tags":int,"invisible":int,"private":int,"noncharacter":int,"tatweel":int,"unassigned":int,"total":int}`.
Attached to `report` on `/api/clean` when `total > 0`, and to `report` on
`/api/inspect` (detection counts on the original text, so `suspicious` is also
true when `total > 0`).

## App-side style normalisation (`deai_style`)

Deterministic phrasing cleanup for the AI "house style". Runs **before**
`strip_ai_tells`, so a clause dash becomes `", "` instead of being deleted into
a bare space.

- **Level 1** — phrase swaps (case-preserving, word-boundary):
  `in order to`→`to`, `due to the fact that`→`because`,
  `at this point in time`→`now`, `in the event that`→`if`,
  `has/have the ability to`→`can`, `it is important to note that`→`note that`,
  `it is worth noting that`→`note that`, `delve(s/d/ing) into`→`explore(d/s/ing)`,
  `utilize(s/d/ing)`→`use(s/d/ing)`.
  Also: em/en dash used as a clause break → `", "` (numeric ranges such as
  `2019–2020` and `2019 – 2020` are left intact), and prose `--` → `", "`
  (CLI flags such as `--json` are left intact).
- **Level 2** — level 1 plus removal of standalone intensifiers
  (`very`, `really`, `actually`, `basically`, `simply`, `quite`, `rather`,
  `truly`, `essentially`, `literally`, `definitely`, `certainly`, `absolutely`,
  `extremely`, `incredibly`, `honestly`, `frankly`) and duplicate words
  (`the the` → `the`).

Shape: `{"phrases":int,"dashes":int,"fillers":int,"total":int}`. Attached to
`report` on `/api/clean` when `total > 0` (and `changed` becomes true).

This rewrites **wording**. It is not a statistical-watermark removal and cannot
make text pass a vendor detector; see the README's "Batasan" section.

## App-side verification (`verify`)

Re-scans the final text with the same predicate as `deep_scrub` and reports what
survived. Shape:

```json
{ "clean": true, "residual": 0, "samples": [] }
```

`samples` is `[{"codepoint":"U+200B","count":3}, ...]`, sorted by count desc,
capped at 10. Attached to `report` on `/api/clean` for text output when
`verify` is true. `clean` is `true` iff `residual == 0`.

**Pipeline order** (text output only):
upstream clean → `deep_scrub` → `deai_style` → `strip_ai_tells` → `verify`.

### `POST /api/clean`
Body = text input or file input. Steps:
1. run `inspect_file.py --json` first (before-report; fail-soft -> `report_before: null`).
2. run `clean_file.py <in> -o <out> --json` with flags from options.
3. read the cleaned output file.

Response for **text input**:
```json
{
  "ok": true,
  "kind": "text",
  "name": "pasted.txt",
  "download_name": "draft.cleaned.txt",
  "mime": "text/plain",
  "changed": true,
  "cleaned": "HelloWorld",
  "cleaned_base64": null,
  "content_length": 10,
  "summary": "2 karakter tak terlihat dihapus, 1 spasi diganti",
  "report_before": { },
  "report": { }
}
```
Response for **file input**:
```json
{
  "ok": true,
  "kind": "container",
  "name": "notes.docx",
  "download_name": "notes.cleaned.docx",
  "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "changed": true,
  "cleaned": null,
  "cleaned_base64": "UEsDBBQ...",
  "content_length": 12345,
  "summary": "Metadata dibersihkan",
  "report_before": { },
  "report": { }
}
```
- `changed`: from the raw clean report `changed` (default true if unknown); also
  true whenever the app-side AI-tell pass removed something.
- `summary`: Indonesian, derived from report if possible:
  - text: joins the non-zero parts of `"{removed} karakter tak terlihat dihapus"`,
    `"{replaced} spasi diganti"`, `"{ai_total} tanda AI dibuang"`; if all zero ->
    `"Tidak ada tanda yang ditemukan"`.
  - file: `"Metadata dibersihkan"` when `changed` else `"Tidak ada yang perlu diubah"`;
    for files classified as `text`, the text summary above is used instead.
- `mime`: guess from `download_name` (`mimetypes.guess_type`), fallback
  `application/octet-stream`.

### Errors (all endpoints)
```json
{ "ok": false, "error": "pesan singkat aman", "kind": "unknown" }
```
- HTTP 400 bad JSON / missing `text`+`filename` / invalid base64.
- HTTP 413 input too large.
- HTTP 422 unrecognized format for `/clean` (upstream `clean_file.py` exit 2) —
  still include `report_before` if inspect succeeded and `"kind":"unknown"`.
- HTTP 500 unexpected.

## Frontend requirements (designer)

- Vanilla HTML/CSS/JS only; no frameworks, no CDN, no build.
- Indonesian UI copy, concise and grounded (no marketing fluff).
- Two modes: **Teks** (paste) and **File** (upload/drag-drop).
- Teks tab: large textarea, options checkboxes, "Bersihkan" button; result panel
  with cleaned text, "Salin", "Unduh .txt", detected-marks summary, "Sebelum /
  Sesudah" stats, collapsible raw report.
- File tab: dropzone + file picker, options, "Bersihkan"; result shows kind,
  changed/unchanged, what was found before, download button for the cleaned file
  (decode base64 in JS), collapsible raw report.
- Handle all error shapes from the contract with a visible inline error state.
- Show a loading/busy state during requests.
- Responsive (mobile -> desktop), accessible (labels, focus states, keyboard for
  dropzone), dark-friendly modern look.
- `app.js` calls relative URLs: `/api/health`, `/api/inspect`, `/api/clean`.
- For text input send `{text, options}`; for file input send `{filename, file_base64, options}`.

## Verification (orchestrator, after both lanes land)

1. `python3 app/server.py` starts, `GET /api/health` -> ok.
2. `GET /` returns index.html; assets load.
3. `POST /api/inspect` text with U+200B -> `suspicious: true`, hits present.
4. `POST /api/clean` text -> `cleaned` has no U+200B; stats counts correct.
5. `POST /api/clean` file (PNG) -> `cleaned_base64` decodes; `changed` reported.
6. `POST /api/clean` unknown bytes -> HTTP 422 with `{ok:false}` (no crash).
7. `node --check app/static/app.js`.
