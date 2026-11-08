#!/usr/bin/env bash
# End-to-end verification for the Watermarks Remover Manual web UI.
# Usage: ./verify.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/app"
HOST="127.0.0.1"
PORT="${PORT:-8770}"
BASE="http://$HOST:$PORT"
PASS=0
FAIL=0

ok()   { echo "PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL: $1"; FAIL=$((FAIL+1)); }

check() { # name, condition-result(0=ok)
  if [ "$2" -eq 0 ]; then ok "$1"; else bad "$1"; fi
}

echo "== starting server =="
PORT="$PORT" HOST="$HOST" python3 "$APP/server.py" >/tmp/opencode/wm-web.log 2>&1 &
SRV=$!
trap 'kill "$SRV" 2>/dev/null' EXIT
for i in $(seq 1 30); do
  curl -sf "$BASE/api/health" >/dev/null 2>&1 && break
  sleep 0.3
done

echo "== health =="
curl -sf "$BASE/api/health" | grep -q '"ok": *true'; check "GET /api/health" $?

echo "== index + assets =="
curl -sf "$BASE/" | grep -qi "<html"; check "GET / returns HTML" $?
curl -sf "$BASE/assets/styles.css" >/dev/null; check "GET /assets/styles.css" $?
curl -sf "$BASE/assets/app.js" >/dev/null; check "GET /assets/app.js" $?

echo "== inspect text (U+200B) =="
R=$(curl -s -X POST "$BASE/api/inspect" -H 'Content-Type: application/json' \
  --data-binary '{"text":"Hello\u200bWorld"}')
echo "$R" | grep -q '"suspicious": *true'; check "inspect marks suspicious" $?
echo "$R" | grep -q '"kind": *"text"'; check "inspect kind=text" $?

echo "== clean text =="
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' \
  --data-binary '{"text":"Hello\u200bWorld\u00ad! caf\u00e9\u00a0fin"}')
echo "$R" | grep -q '"ok": *true'; check "clean ok" $?
if echo "$R" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("cleaned") is not None and "\u200b" not in d["cleaned"] and "\u00ad" not in d["cleaned"]'; then ok "cleaned removes U+200B/U+00AD"; else bad "cleaned removes U+200B/U+00AD"; fi

echo "== clean file (png) =="
PNG="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' \
  --data-binary "{\"filename\":\"t.png\",\"file_base64\":\"$PNG\"}")
echo "$R" | grep -q '"ok": *true'; check "clean file ok" $?
if echo "$R" | python3 -c 'import sys,json,base64; d=json.load(sys.stdin); assert d.get("cleaned_base64"); base64.b64decode(d["cleaned_base64"]); assert d.get("kind")=="image"'; then ok "png returns decodable cleaned_base64"; else bad "png returns decodable cleaned_base64"; fi

echo "== unknown format rejected =="
UB=$(printf 'randombytes\x00\x01\x02' | base64 -w0)
CODE=$(curl -s -o /tmp/opencode/wm-unknown.json -w '%{http_code}' -X POST "$BASE/api/clean" \
  -H 'Content-Type: application/json' \
  --data-binary "{\"filename\":\"u.bin\",\"file_base64\":\"$UB\"}")
[ "$CODE" = "422" ]; check "unknown -> HTTP 422" $?
grep -q '"ok": *false' /tmp/opencode/wm-unknown.json; check "unknown -> ok:false" $?

echo "== clean text strips AI tells =="
AI_BODY='{"text":"The Mysterious Letter \u2014 Surat Misterius. \u201cHalo\u201d \ud83d\ude0a"}'
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' --data-binary "$AI_BODY")
echo "$R" | grep -q '"ok": *true'; check "clean AI ok" $?
if echo "$R" | python3 -c 'import sys,json; d=json.load(sys.stdin); c=d.get("cleaned",""); assert "\u2014" not in c and "\u201c" not in c and "\u201d" not in c and "\"Halo\"" in c and "\U0001F60A" not in c'; then ok "cleaned strips dash/curly quotes/emoji"; else bad "cleaned strips dash/curly quotes/emoji"; fi

echo "== strip_ai_tells=false keeps the dash =="
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' \
  --data-binary '{"text":"The Mysterious Letter \u2014 Surat Misterius. \u201cHalo\u201d \ud83d\ude0a","options":{"strip_ai_tells":false}}')
echo "$R" | grep -q '"ok": *true'; check "clean AI opt-out ok" $?
if echo "$R" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert "\u2014" in d.get("cleaned","")'; then ok "opt-out keeps em dash"; else bad "opt-out keeps em dash"; fi

echo "== keyboard hyphen must survive =="
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' \
  --data-binary '{"text":"e-mail a-b 10-20"}')
if echo "$R" | python3 -c 'import sys,json; d=json.load(sys.stdin); c=d.get("cleaned",""); assert "e-mail a-b 10-20" == c, c'; then ok "ascii hyphen preserved"; else bad "ascii hyphen preserved"; fi

echo "== dash between words keeps one space =="
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' \
  --data-binary '{"text":"well\u2014known"}')
if echo "$R" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("cleaned")=="well known", d.get("cleaned")'; then ok "no-space dash -> single space"; else bad "no-space dash -> single space"; fi

echo "== inspect reports AI tells =="
R=$(curl -s -X POST "$BASE/api/inspect" -H 'Content-Type: application/json' --data-binary "$AI_BODY")
echo "$R" | grep -q '"suspicious": *true'; check "inspect AI tells -> suspicious" $?

echo "== deep scrub: residual invisibles (bidi/tag/private/noncharacter) =="
DEEP='{"text":"a\u202eb\u061cc\u200fd\ue000e\ufffef\u0640g\u200bh"}'
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' --data-binary "$DEEP")
if echo "$R" | python3 -c '
import sys,json
d=json.load(sys.stdin); c=d["cleaned"]; r=d["report"]
bad=[hex(ord(ch)) for ch in c if ord(ch) in (0x202e,0x061c,0x200f,0xe000,0xfffe,0x0640,0x200b)]
assert not bad, bad
assert c=="abcdefgh", c
assert r["verify"]["clean"] is True, r.get("verify")
# Upstream already removes 4 of these 7; deep_scrub only sees what it leaves
# behind (bidi marks + tatweel here), so 3 is the real clean-time count.
assert r["deep_scrub"]["total"]>=3, r.get("deep_scrub")
'; then ok "deep scrub removes bidi/tag/private/noncharacter + verify clean"; else bad "deep scrub removes bidi/tag/private/noncharacter + verify clean"; fi

echo "== deep scrub keeps load-bearing glue (ZWJ/ZWNJ) =="
GLUE='{"text":"\u0915\u094d\u200d\u0937"}'
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' --data-binary "$GLUE")
if echo "$R" | python3 -c '
import sys,json
d=json.load(sys.stdin); c=d["cleaned"]
assert c=="\u0915\u094d\u200d\u0937", repr(c)
'; then ok "Devanagari ZWJ survives (no space injected)"; else bad "Devanagari ZWJ survives (no space injected)"; fi

echo "== deai_style level 1 rewrites stock phrases =="
DEAI='{"text":"In order to utilize the tool \u2014 which is very useful \u2014 we delve into it.","options":{"deai_style":1}}'
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' --data-binary "$DEAI")
if echo "$R" | python3 -c '
import sys,json
d=json.load(sys.stdin); c=d["cleaned"]
assert "To use the tool, which is very useful, we explore it." == c, c
'; then ok "deai_style=1 rewrites phrases and dashes"; else bad "deai_style=1 rewrites phrases and dashes"; fi

echo "== deai_style=0 is a no-op =="
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' \
  --data-binary '{"text":"in order to utilize","options":{"deai_style":0,"strip_ai_tells":false}}')
if echo "$R" | python3 -c 'import sys,json; assert "in order to utilize"==json.load(sys.stdin)["cleaned"]'; then ok "deai_style=0 leaves phrasing"; else bad "deai_style=0 leaves phrasing"; fi

echo "== deai_style level 2 drops intensifiers =="
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' \
  --data-binary '{"text":"in order to utilize very good stuff","options":{"deai_style":2}}')
if echo "$R" | python3 -c 'import sys,json; assert "to use good stuff"==json.load(sys.stdin)["cleaned"]'; then ok "deai_style=2 drops very"; else bad "deai_style=2 drops very"; fi

echo "== numeric range and CLI flags untouched =="
R=$(curl -s -X POST "$BASE/api/clean" -H 'Content-Type: application/json' \
  --data-binary '{"text":"tahun 2019 \u2013 2020, jalankan --json sekarang","options":{"deai_style":1,"strip_ai_tells":false}}')
if echo "$R" | python3 -c '
import sys,json
c=json.load(sys.stdin)["cleaned"]
assert c=="tahun 2019 \u2013 2020, jalankan --json sekarang", c
'; then ok "range + flag preserved"; else bad "range + flag preserved"; fi

echo "== bad deai_style values never crash =="
for V in '"banana"' 'null' '99' '-5' 'true' '2.7'; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/clean" \
    -H 'Content-Type: application/json' \
    --data-binary "{\"text\":\"in order to test\",\"options\":{\"deai_style\":$V}}")
  [ "$CODE" = "200" ]; check "deai_style=$V -> 200" $?
done

echo "== js syntax =="
if command -v node >/dev/null; then node --check "$APP/static/app.js"; check "node --check app.js" $?; fi

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
