#!/usr/bin/env python3
"""Stdlib-only backend for the "watermarks-remover Manual" web UI.

Serves the static frontend and exposes a small JSON API that shells out to the
upstream Layer A CLI scripts (inspect_file.py / clean_file.py). No third-party
dependencies, no `cgi` module (removed in Python 3.13) - input and output are
plain JSON with base64 for file payloads.

See API_CONTRACT.md for the frozen request/response shapes.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = (APP_DIR / "static").resolve()

_repo_env = os.environ.get("WATERMARKS_REPO")
REPO_DIR = (
    Path(_repo_env).expanduser().resolve()
    if _repo_env
    else (APP_DIR.parent / "watermarks-remover").resolve()
)
INSPECT_SCRIPT = REPO_DIR / "service" / "scripts" / "inspect_file.py"
CLEAN_SCRIPT = REPO_DIR / "service" / "scripts" / "clean_file.py"

VERSION = "1.0.0"
SERVICE_NAME = "watermarks-web"

MAX_INPUT_BYTES = 25 * 1024 * 1024  # 25 MiB raw input limit
# 25 MiB raw -> at most ~34 MiB base64, plus JSON envelope slack.
MAX_BODY_BYTES = (MAX_INPUT_BYTES * 4) // 3 + (1 << 20)
SUBPROCESS_TIMEOUT = 60

DEFAULT_OPTIONS = {
    "nfkc": False,
    "normalize_spaces": True,
    "strip_emoji_glue": False,
    "strip_bidi": False,
    "aggressive_homoglyphs": False,
    "strip_ai_tells": True,
    "deep_scrub": True,
    "deai_style": 0,
    "verify": True,
}

# Options whose value is an int, not a bool (everything else is coerced to bool).
_INT_OPTION_BOUNDS = {"deai_style": (0, 2)}

# Report keys that can carry absolute host paths; never leak these.
_PATH_REPORT_KEYS = ("path", "input", "output")

_MIME_OVERRIDES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}


class ApiError(Exception):
    """Error that maps directly onto a JSON error response."""

    def __init__(self, status: int, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def sanitize_name(name: object, fallback: str = "pasted.txt") -> str:
    """Reduce a client-supplied filename to a safe basename."""
    if not isinstance(name, str) or not name.strip():
        return fallback
    cleaned = name.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = cleaned.replace("\x00", "")
    cleaned = re.sub(r"[\r\n\t]", "_", cleaned).strip()
    if cleaned in ("", ".", ".."):
        return fallback
    return cleaned[:255]


def cleaned_name(name: str, suffix: str = ".cleaned") -> str:
    """mirror upstream common.cleaned_path(): file.ext -> file.cleaned.ext."""
    p = Path(name)
    if p.suffix:
        return f"{p.stem}{suffix}{p.suffix}"
    return f"{name}{suffix}"


def coerce_int(raw: object, low: int, high: int, default: int) -> int:
    """Best-effort int coercion: bools count as 0/1, numeric strings work,
    anything else (None, "banana", lists, ...) falls back to ``default`` and the
    result is clamped to ``low..high``. Never raises."""
    if isinstance(raw, bool):
        value = int(raw)
    elif isinstance(raw, int):
        value = raw
    elif isinstance(raw, float):
        value = int(raw) if raw == raw and abs(raw) != float("inf") else default
    elif isinstance(raw, str):
        try:
            value = int(raw.strip())
        except (TypeError, ValueError):
            try:
                value = int(float(raw.strip()))
            except (TypeError, ValueError, OverflowError):
                value = default
    else:
        value = default
    return max(low, min(high, value))


def normalize_options(raw: object) -> dict:
    opts = dict(DEFAULT_OPTIONS)
    if isinstance(raw, dict):
        for key in DEFAULT_OPTIONS:
            if key not in raw:
                continue
            bounds = _INT_OPTION_BOUNDS.get(key)
            if bounds is not None:
                opts[key] = coerce_int(raw[key], bounds[0], bounds[1], DEFAULT_OPTIONS[key])
            else:
                opts[key] = bool(raw[key])
    return opts


def sanitize_report(report: object):
    """Drop keys that can carry absolute host paths; pass the rest through."""
    if not isinstance(report, dict):
        return report
    return {k: v for k, v in report.items() if k not in _PATH_REPORT_KEYS}


def parse_json_stdout(raw: bytes | str | None) -> dict:
    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    raw = raw.strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def run_cli(script: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        cwd=str(REPO_DIR),
        timeout=SUBPROCESS_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# clean_file flag capability detection
#
# The frozen contract maps options onto clean_file flags that this checkout of
# upstream does not all define (--no-normalize-spaces / --strip-emoji-glue /
# --strip-bidi live on clean_text.py, not clean_file.py). Passing an
# unsupported flag makes argparse exit 2, which we would misreport as 422
# "unrecognized format". Detect what the script actually accepts once and drop
# only the unsupported extras; the contract mapping is used verbatim when the
# flags exist.
# ---------------------------------------------------------------------------

_clean_flag_cache: set[str] | None = None
_clean_flag_lock = threading.Lock()


def clean_supported_flags() -> set[str]:
    global _clean_flag_cache
    if _clean_flag_cache is None:
        with _clean_flag_lock:
            if _clean_flag_cache is None:
                try:
                    proc = subprocess.run(
                        [sys.executable, str(CLEAN_SCRIPT), "--help"],
                        capture_output=True,
                        cwd=str(REPO_DIR),
                        timeout=30,
                    )
                    text = (proc.stdout or b"").decode("utf-8", "replace") + (
                        proc.stderr or b""
                    ).decode("utf-8", "replace")
                except Exception:
                    text = ""
                _clean_flag_cache = set(re.findall(r"--[a-z][a-z0-9-]+", text))
    return _clean_flag_cache


def build_clean_flags(options: dict) -> list[str]:
    flags: list[str] = []
    if options["nfkc"]:
        flags.append("--nfkc")
    if not options["normalize_spaces"]:
        flags.append("--no-normalize-spaces")
    if options["strip_emoji_glue"]:
        flags.append("--strip-emoji-glue")
    if options["strip_bidi"]:
        flags.append("--strip-bidi")
    if options["aggressive_homoglyphs"]:
        flags.append("--aggressive-homoglyphs")
    supported = clean_supported_flags()
    if supported:
        flags = [f for f in flags if f in supported]
    return flags


def build_inspect_flags(options: dict) -> list[str]:
    flags: list[str] = []
    if options["aggressive_homoglyphs"]:
        flags.append("--aggressive")
    return flags


# ---------------------------------------------------------------------------
# Request parsing / derives
# ---------------------------------------------------------------------------


def parse_input(body: dict):
    """Return (mode, name, payload_bytes, is_text, text_or_None)."""
    options = normalize_options(body.get("options"))

    if isinstance(body.get("file_base64"), str):
        encoded = body["file_base64"].strip()
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(400, "File base64 tidak valid")
        if len(data) > MAX_INPUT_BYTES:
            raise ApiError(413, "Ukuran masukan melebihi 25 MiB")
        name = sanitize_name(body.get("filename"), "upload.bin")
        return "file", name, data, False, None, options

    if isinstance(body.get("text"), str):
        text = body["text"]
        data = text.encode("utf-8", "surrogateescape")
        if len(data) > MAX_INPUT_BYTES:
            raise ApiError(413, "Ukuran masukan melebihi 25 MiB")
        supplied = body.get("filename")
        name = sanitize_name(supplied, "pasted.txt") if supplied else "pasted.txt"
        return "text", name, data, True, text, options

    raise ApiError(
        400,
        "Permintaan tidak lengkap: sertakan 'text' atau 'filename' + 'file_base64'",
    )


def derive_suspicious(kind: str, report: dict) -> bool:
    if kind == "text":
        try:
            return int(report.get("suspicious_total", 0)) > 0
        except (TypeError, ValueError):
            return False
    return bool(
        report.get("has_c2pa")
        or report.get("has_ai_metadata")
        or (report.get("layer_a_total") or 0)
    )


def inspect_summary(kind: str, report: dict) -> str:
    if kind == "text":
        try:
            count = int(report.get("suspicious_total", 0))
        except (TypeError, ValueError):
            count = 0
        ai = report.get("ai_tells") if isinstance(report.get("ai_tells"), dict) else {}
        try:
            count += int(ai.get("total", 0))
        except (TypeError, ValueError):
            pass
    else:
        findings = report.get("findings")
        count = len(findings) if isinstance(findings, list) else 0
    if count > 0:
        return f"{count} tanda ditemukan"
    return "Tidak ada tanda yang ditemukan"


def text_clean_summary(report: dict) -> str:
    stats = report.get("stats") if isinstance(report.get("stats"), dict) else {}
    try:
        removed = int(stats.get("removed_count", 0))
    except (TypeError, ValueError):
        removed = 0
    try:
        replaced = int(stats.get("replaced_count", 0))
    except (TypeError, ValueError):
        replaced = 0
    ai = report.get("ai_tells") if isinstance(report.get("ai_tells"), dict) else {}
    try:
        ai_total = int(ai.get("total", 0))
    except (TypeError, ValueError):
        ai_total = 0
    deep = report.get("deep_scrub") if isinstance(report.get("deep_scrub"), dict) else {}
    try:
        deep_total = int(deep.get("total", 0))
    except (TypeError, ValueError):
        deep_total = 0
    deai = report.get("deai_style") if isinstance(report.get("deai_style"), dict) else {}
    try:
        deai_total = int(deai.get("total", 0))
    except (TypeError, ValueError):
        deai_total = 0
    parts: list[str] = []
    if removed > 0:
        parts.append(f"{removed} karakter tak terlihat dihapus")
    if replaced > 0:
        parts.append(f"{replaced} spasi diganti")
    if ai_total > 0:
        parts.append(f"{ai_total} tanda AI dibuang")
    if deep_total > 0:
        parts.append(f"{deep_total} karakter tersembunyi lanjutan dihapus")
    if deai_total > 0:
        parts.append(f"{deai_total} frasa khas AI dinetralkan")
    if not parts:
        return "Tidak ada tanda yang ditemukan"
    return ", ".join(parts)


def guess_mime(download_name: str, fallback: str = "application/octet-stream") -> str:
    import mimetypes

    ctype, _ = mimetypes.guess_type(download_name)
    return ctype or fallback


# ---------------------------------------------------------------------------
# App-side deep scrub (second invisible-character sweep)
#
# The upstream Layer A clean keeps several invisible/format codepoint groups on
# purpose (bidi embedding/isolate controls, Arabic letter mark, tatweel,
# unassigned codepoints) and only strips them behind explicit flags. This pass
# removes what is left over. Pure function - no side effects.
#
# Deliberately NOT removed (load-bearing, handled by the existing
# strip_emoji_glue / strip_ai_tells options instead):
#   U+200C ZWNJ  - Indic/script joining
#   U+200D ZWJ   - emoji family sequences, script joining
#   U+FE0E / U+FE0F - variation selectors (text/emoji presentation)
# ---------------------------------------------------------------------------

_DEEP_BIDI = frozenset((0x061C, 0x200E, 0x200F, 0x2066, 0x2067, 0x2068, 0x2069))
_DEEP_BIDI_RANGES = ((0x202A, 0x202E),)
_DEEP_TAGS = frozenset((0xE0001,))
_DEEP_TAGS_RANGES = ((0xE0020, 0xE007F),)
_DEEP_INVISIBLE = frozenset(
    (
        0x00AD,  # soft hyphen
        0x200B,  # zero width space
        0x2060,  # word joiner
        0x2061,  # function application
        0x2062,  # invisible times
        0x2063,  # invisible separator
        0x2064,  # invisible plus
        0xFEFF,  # zero width no-break space / BOM
        0x180E,  # Mongolian vowel separator
        0x034F,  # combining grapheme joiner
        0x115F,  # Hangul choseong filler
        0x1160,  # Hangul jungseong filler
        0x3164,  # Hangul filler
        0xFFA0,  # halfwidth Hangul filler
        0xFFF9,  # interlinear annotation anchor
        0xFFFA,  # interlinear annotation separator
        0xFFFB,  # interlinear annotation terminator
    )
)
_DEEP_PRIVATE_RANGES = (
    (0xE000, 0xF8FF),
    (0xF0000, 0xFFFFD),
    (0x100000, 0x10FFFD),
)
_DEEP_NONCHARACTER_RANGES = ((0xFDD0, 0xFDEF),)

_DEEP_COUNT_KEYS = (
    "bidi",
    "tags",
    "invisible",
    "private",
    "noncharacter",
    "tatweel",
    "unassigned",
)


def _in_ranges(cp: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    for low, high in ranges:
        if low <= cp <= high:
            return True
    return False


def deep_scrub_group(cp: int) -> str | None:
    """Return the deep-scrub group name for ``cp``, or None when it is kept.

    Single predicate shared by ``deep_scrub`` (removal) and ``verify_residue``
    (verification) so both always agree. Order matters: the explicit tag/bidi/
    invisible sets come first, and private-use ranges are checked before the
    "Cn" category test so those codepoints land in their own group.
    """
    if cp in _DEEP_TAGS or _in_ranges(cp, _DEEP_TAGS_RANGES):
        return "tags"
    if cp in _DEEP_BIDI or _in_ranges(cp, _DEEP_BIDI_RANGES):
        return "bidi"
    if cp in _DEEP_INVISIBLE:
        return "invisible"
    if _in_ranges(cp, _DEEP_PRIVATE_RANGES):
        return "private"
    if _in_ranges(cp, _DEEP_NONCHARACTER_RANGES) or (cp & 0xFFFE) == 0xFFFE:
        return "noncharacter"
    if cp == 0x0640:
        return "tatweel"
    # Skipped codepoints are deliberately preserved (see the comment above).
    if cp in (0x200C, 0x200D, 0xFE0E, 0xFE0F):
        return None
    try:
        if unicodedata.category(chr(cp)) == "Cn":
            return "unassigned"
    except (TypeError, ValueError):  # pragma: no cover - defensive
        pass
    return None


def deep_scrub(text: str) -> tuple[str, dict]:
    """Remove residual invisible/format codepoints from ``text``.

    Returns ``(cleaned_text, counts)`` where ``counts`` has one key per group
    plus ``total``. Single O(n) pass. Pure function - no side effects.
    """
    counts = {key: 0 for key in _DEEP_COUNT_KEYS}
    counts["total"] = 0
    if not text:
        return text, counts

    out: list[str] = []
    for ch in text:
        group = deep_scrub_group(ord(ch))
        if group is None:
            out.append(ch)
        else:
            counts[group] += 1
    for key in _DEEP_COUNT_KEYS:
        counts["total"] += counts[key]
    return "".join(out), counts


def verify_residue(text: str) -> dict:
    """Re-scan a final text for anything invisible/format-like still present."""
    per_cp: dict[int, int] = {}
    residual = 0
    for ch in text or "":
        cp = ord(ch)
        if deep_scrub_group(cp) is None:
            continue
        residual += 1
        per_cp[cp] = per_cp.get(cp, 0) + 1
    samples = [
        {"codepoint": f"U+{cp:04X}", "count": n}
        for cp, n in sorted(per_cp.items(), key=lambda item: (-item[1], item[0]))[:10]
    ]
    return {"clean": residual == 0, "residual": residual, "samples": samples}


# ---------------------------------------------------------------------------
# App-side style normalisation (deai_style)
#
# Deterministic phrasing cleanup for the "AI flavour" of generated prose.
# Level 1 = only edits that are always correct; level 2 adds filler removal and
# duplicate-word collapsing. Pure function - no side effects.
# ---------------------------------------------------------------------------

# Phrase swaps: (pattern, replacement), matched case-insensitively on word
# boundaries. Longer phrases must come before their shorter prefixes.
_DEAI_PHRASES: tuple[tuple[str, str], ...] = (
    ("due to the fact that", "because"),
    ("at this point in time", "now"),
    ("in the event that", "if"),
    ("it is important to note that", "note that"),
    ("it is worth noting that", "note that"),
    ("has the ability to", "can"),
    ("have the ability to", "can"),
    ("in order to", "to"),
    ("delving into", "exploring"),
    ("delved into", "explored"),
    ("delve into", "explore"),
    ("delves into", "explores"),
    ("utilizing", "using"),
    ("utilized", "used"),
    ("utilizes", "uses"),
    ("utilize", "use"),
)

_DEAI_PHRASE_RES = tuple(
    (re.compile(r"(?<!\w)" + re.escape(pattern) + r"(?!\w)", re.IGNORECASE), replacement)
    for pattern, replacement in _DEAI_PHRASES
)

# Em/en dash used as a clause break. Numeric ranges ("2019–2020" and
# "2019 – 2020") are kept: the digit test happens in _dash_sub on the characters
# just outside the consumed whitespace, because a `\s*(?<!\d)` style lookaround
# only sees the space next to the dash, never the digit before it.
_DEAI_DASH_RE = re.compile(r"(?P<pre>\s*)[\u2013\u2014](?P<post>\s*)")
_DEAI_COMMA_COLLAPSE_RE = re.compile(r",\s*,")
_DEAI_COMMA_PAD_RE = re.compile(r" ?,(?!,) ?(?=[^\s,])")
_DEAI_COMMA_BEFORE_PUNCT_RE = re.compile(r"\s*,\s*([.!?])")

_DEAI_FILLERS = frozenset(
    (
        "very",
        "really",
        "actually",
        "basically",
        "simply",
        "quite",
        "rather",
        "truly",
        "essentially",
        "literally",
        "definitely",
        "certainly",
        "absolutely",
        "extremely",
        "incredibly",
        "honestly",
        "frankly",
    )
)
_DEAI_FILLER_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(sorted(_DEAI_FILLERS)) + r")(?!\w)[ \t]*",
    re.IGNORECASE,
)
_DEAI_DUP_WORD_RE = re.compile(r"(?<!\w)(\w+)([ \t]+)\1(?!\w)", re.IGNORECASE)


def _match_case(source: str, replacement: str) -> str:
    """Copy the capitalization of ``source``'s first letter onto ``replacement``."""
    if not replacement:
        return replacement
    if source[:1].isupper():
        if source.isupper() and len(source) > 1:
            return replacement.upper()
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _deai_level1(text: str, counts: dict) -> str:
    for regex, replacement in _DEAI_PHRASE_RES:
        def _sub(match: re.Match, repl: str = replacement) -> str:
            counts["phrases"] += 1
            return _match_case(match.group(0), repl)

        text = regex.sub(_sub, text)

    def _dash_sub(match: re.Match) -> str:
        # Numeric range: digits on both sides of the dash (spaces allowed).
        before = text[match.start("pre") - 1] if match.start("pre") > 0 else ""
        after = text[match.end("post")] if match.end("post") < len(text) else ""
        if before.isdigit() and after.isdigit():
            return match.group(0)
        counts["dashes"] += 1
        return ", "

    text = _DEAI_DASH_RE.sub(_dash_sub, text)
    text = _deai_double_hyphen(text, counts)
    if counts["dashes"]:
        text = _DEAI_COMMA_COLLAPSE_RE.sub(", ", text)
        text = _DEAI_COMMA_PAD_RE.sub(", ", text)
        text = _DEAI_COMMA_BEFORE_PUNCT_RE.sub(r"\1", text)
        text = re.sub(r" {2,}", " ", text)
    return text


def _deai_double_hyphen(text: str, counts: dict) -> str:
    """Turn a prose ``--`` into ", " but never touch a CLI flag.

    A flag looks like ``--json``, ``--flag=1`` or ``--foo-bar`` with ``--`` at a
    token start (string start or a non-alnum char right before it). As a
    fallback for runs like ``text--flag``, the ``--`` also stays when the next
    word looks like a slug (``json``, ``foo-bar``, ``stdout=1``) rather than
    prose; prose almost always has spaces around the break. When the break is
    rewritten, the spaces around it are absorbed so only ", " remains.
    """
    if "--" not in text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("--", i) and not text.startswith("---", i):
            nxt = text[i + 2] if i + 2 < n else ""
            if nxt.isalnum():
                prev = text[i - 1] if i > 0 else ""
                at_token_start = prev == "" or not prev.isalnum()
                if at_token_start or _looks_like_flag_word(text, i + 2):
                    out.append("--")
                    i += 2
                    continue
            counts["dashes"] += 1
            # Drop the break's leading spaces ("Ini bagus " + ", " + " benar"
            # would otherwise leave a space before the comma). "a--b" still
            # gets exactly one space after the comma.
            while out and out[-1] in (" ", "\t"):
                out.pop()
            out.append(", ")
            i += 2
            # Eat the break's trailing spaces: ", " already supplies one.
            while i < n and text[i] in (" ", "\t"):
                i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _looks_like_flag_word(text: str, start: int) -> bool:
    """True when the word at ``start`` looks like a flag/slug, not prose."""
    end = start
    while end < len(text) and (text[end].isalnum() or text[end] in "-_=."):
        end += 1
    word = text[start:end]
    if not word or not word[0].isalnum():
        return False
    for ch in word:
        if not (ch.isalnum() or ch in "-_=."):
            return False
    if any(ch.isdigit() for ch in word) or any(ch in "-_=." for ch in word):
        return True
    return word.islower()


def _deai_level2(text: str, counts: dict) -> str:
    def _filler_sub(match: re.Match) -> str:
        counts["fillers"] += 1
        return ""

    text = _DEAI_FILLER_RE.sub(_filler_sub, text)
    if counts["fillers"]:
        text = re.sub(r"[ \t]{2,}", " ", text)

    def _dup_sub(match: re.Match) -> str:
        counts["fillers"] += 1
        return match.group(1)

    text = _DEAI_DUP_WORD_RE.sub(_dup_sub, text)
    return text


def deai_style(text: str, level: int) -> tuple[str, dict]:
    """Neutralise AI-flavoured phrasing in ``text`` at ``level`` (0/1/2).

    Returns ``(new_text, counts)`` where ``counts`` has keys ``phrases``,
    ``dashes``, ``fillers`` and ``total``. Level 0 is a no-op.
    """
    counts = {"phrases": 0, "dashes": 0, "fillers": 0, "total": 0}
    if not text or level <= 0:
        return text, counts
    text = _deai_level1(text, counts)
    if level >= 2:
        text = _deai_level2(text, counts)
    counts["total"] = counts["phrases"] + counts["dashes"] + counts["fillers"]
    return text, counts


# ---------------------------------------------------------------------------
# App-side AI-tell cleanup (post-processing pass)
#
# The upstream Layer A CLI removes invisible Unicode, exotic spaces, bidi and
# tag characters, but leaves visible typographic "AI tells": em/en dashes,
# curly quotes and emoji. This pass strips those from the cleaned text. It is a
# pure function - no side effects beyond returning the new string + counts.
# ---------------------------------------------------------------------------

# Dashes to remove entirely (no replacement).
_AI_DASH_CODEPOINTS = frozenset(
    (
        0x2010,  # hyphen
        0x2011,  # non-breaking hyphen
        0x2012,  # figure dash
        0x2013,  # en dash
        0x2014,  # em dash
        0x2015,  # horizontal bar
        0x2043,  # hyphen bullet
        0x2212,  # minus sign
        0x2E3A,  # two-em dash
        0x2E3B,  # three-em dash
        0xFE58,  # small em dash
        0xFE63,  # small hyphen-minus
        0xFF0D,  # fullwidth hyphen-minus
    )
)

# Curly quotes -> straight ASCII.
_AI_QUOTE_MAP = {
    0x2018: "'",  # left single quotation mark
    0x2019: "'",  # right single quotation mark
    0x201A: "'",  # single low-9 quotation mark
    0x201B: "'",  # single high-reversed-9 quotation mark
    0x201C: '"',  # left double quotation mark
    0x201D: '"',  # right double quotation mark
    0x201E: '"',  # double low-9 quotation mark
    0x201F: '"',  # double high-reversed-9 quotation mark
    0x2032: "'",  # prime
    0x2033: '"',  # double prime
}

# Emoji codepoint ranges (stdlib has no emoji property).
_AI_EMOJI_RANGES = (
    (0x1F1E6, 0x1F1FF),  # regional indicators
    (0x1F300, 0x1F5FF),  # misc symbols and pictographs
    (0x1F600, 0x1F64F),  # emoticons
    (0x1F680, 0x1F6FF),  # transport and map
    (0x1F700, 0x1F77F),  # alchemical
    (0x1F780, 0x1F7FF),  # geometric shapes extended
    (0x1F800, 0x1F8FF),  # supplemental arrows-C
    (0x1F900, 0x1F9FF),  # supplemental symbols and pictographs
    (0x1FA00, 0x1FAFF),  # symbols and pictographs extended-A
    (0x1FB00, 0x1FBFF),  # symbols for legacy computing
    (0x2600, 0x26FF),  # misc symbols
    (0x2700, 0x27BF),  # dingbats
    (0x2B00, 0x2BFF),  # misc symbols and arrows
    (0x1F3FB, 0x1F3FF),  # skin tone modifiers
)

# Individual emoji / glue codepoints.
_AI_EMOJI_SINGLES = frozenset(
    (
        0x200D,  # zero width joiner
        0x20E3,  # combining enclosing keycap
        0xFE0E,  # variation selector-15 (text)
        0xFE0F,  # variation selector-16 (emoji)
        0x2049,  # exclamation question mark
        0x203C,  # double exclamation mark
        0x2122,  # trade mark sign
        0x2139,  # information source
        0x24C2,  # circled latin capital letter M
        0x25AA,  # black small square
        0x25AB,  # white small square
        0x25B6,  # black right-pointing triangle
        0x25C0,  # black left-pointing triangle
        0x25FB,  # white medium square
        0x25FC,  # black medium square
        0x25FD,  # white medium small square
        0x25FE,  # black medium small square
        0x2934,  # arrow pointing rightwards then curving upwards
        0x2935,  # arrow pointing rightwards then curving downwards
        0x2B05,  # leftwards black arrow
        0x2B06,  # upwards black arrow
        0x2B07,  # downwards black arrow
        0x2B1B,  # black large square
        0x2B1C,  # white large square
        0x2B50,  # white medium star
        0x2B55,  # heavy large circle
        0x3030,  # wavy dash
        0x303D,  # part alternation mark
        0x3297,  # circled ideograph congratulation
        0x3299,  # circled ideograph secret
    )
)

# Zero-width glue: removed silently (no space inserted), and only when it is
# actually part of an emoji sequence. U+200D is also a legitimate script joiner
# (Devanagari kssa, Persian, ...) so a bare one between letters must survive.
_AI_GLUE_CODEPOINTS = frozenset((0x200D, 0x20E3, 0xFE0E, 0xFE0F))

# Conservative ASCII emoticons. Boundaries keep normal words untouched.
_AI_EMOTICON_RE = re.compile(
    r"(?<!\w)(?:"
    r":'-\("
    r"|:'\("
    r"|:-\)"
    r"|:-D"
    r"|:-P"
    r"|:-p"
    r"|:-/"
    r"|:-\|"
    r"|:-\("
    r"|;-\)"
    r"|:\)"
    r"|:\("
    r"|;\)"
    r"|:D"
    r"|:P"
    r"|:p"
    r"|:/"
    r"|:\|"
    r"|=\)"
    r"|=\("
    r"|<3"
    r"|\^_\^"
    r"|xD"
    r")(?!\w)"
)


def _is_ai_emoji(cp: int) -> bool:
    if cp in _AI_EMOJI_SINGLES:
        return True
    for low, high in _AI_EMOJI_RANGES:
        if low <= cp <= high:
            return True
    return False


def _is_script_joiner(text: str, i: int) -> bool:
    """True when the ZWJ at ``i`` joins two non-emoji letters (script joiner).

    U+200D is legitimate in Devanagari (क्‍ष), Persian, Sinhala and others. Only
    treat it as emoji glue when it sits between emoji, or at a word edge next to
    an emoji. A ZWJ between two plain letters is left alone so real scripts are
    not corrupted.

    A neighbour may be a combining mark rather than a letter: Devanagari puts a
    virama (U+094D, category Mn) before the ZWJ, and ``isalpha()`` is False for
    it. Both letters and combining marks count as script context.
    """
    prev_ch = text[i - 1] if i > 0 else ""
    next_ch = text[i + 1] if i + 1 < len(text) else ""
    if not prev_ch or not next_ch:
        return False
    if _is_ai_emoji(ord(prev_ch)) or _is_ai_emoji(ord(next_ch)):
        return False

    def _scriptish(ch: str) -> bool:
        if ch.isalpha():
            return True
        # Combining marks (Mn/Mc) and format-adjacent marks are script context.
        return unicodedata.category(ch) in ("Mn", "Mc", "Me")

    return _scriptish(prev_ch) and _scriptish(next_ch)


def strip_ai_tells(text: str) -> tuple[str, dict]:
    """Remove visible typographic AI tells from ``text``.

    Returns ``(cleaned_text, counts)`` where ``counts`` has keys
    ``dashes``, ``quotes``, ``emoji``, ``emoticons`` and ``total``.
    Pure function - no side effects.
    """
    counts = {"dashes": 0, "quotes": 0, "emoji": 0, "emoticons": 0, "total": 0}
    if not text:
        return text, counts

    out: list[str] = []
    for i, ch in enumerate(text):
        cp = ord(ch)
        if cp in _AI_GLUE_CODEPOINTS:
            # Glue is zero-width: drop it silently. Never insert a space here -
            # that would corrupt scripts that legitimately join letters.
            if cp == 0x200D and _is_script_joiner(text, i):
                out.append(ch)
                continue
            counts["emoji"] += 1
            continue
        if cp in _AI_DASH_CODEPOINTS or _is_ai_emoji(cp):
            if cp in _AI_DASH_CODEPOINTS:
                counts["dashes"] += 1
            else:
                counts["emoji"] += 1
            # Tidy spacing around the removed char so tokens neither merge nor
            # leave a double space: "a — b" -> "a b", "a—b" -> "a b".
            nxt = text[i + 1] if i + 1 < len(text) else ""
            prev_space = bool(out) and out[-1] in (" ", "\t")
            next_space = nxt in (" ", "\t")
            if prev_space and next_space:
                out.pop()  # both sides spaced: keep the following space only
            elif out and nxt and not prev_space and not next_space:
                out.append(" ")  # no space either side: keep one between tokens
            continue
        if cp in _AI_QUOTE_MAP:
            counts["quotes"] += 1
            out.append(_AI_QUOTE_MAP[cp])
            continue
        out.append(ch)

    cleaned = "".join(out)
    cleaned, emoticons = _AI_EMOTICON_RE.subn("", cleaned)
    counts["emoticons"] = emoticons
    counts["total"] = (
        counts["dashes"] + counts["quotes"] + counts["emoji"] + counts["emoticons"]
    )
    # Emoticon runs can leave doubled spaces behind; collapse them only when we
    # actually removed something, so clean text is never rewritten otherwise.
    if counts["emoticons"] > 0:
        cleaned = re.sub(r" {2,}", " ", cleaned)
    return cleaned, counts


# ---------------------------------------------------------------------------
# Post-processing pipeline for cleaned TEXT
#
# Order is load-bearing: upstream clean -> deep_scrub -> deai_style ->
# strip_ai_tells -> verify_residue. deai_style must run before strip_ai_tells so
# a clause dash becomes ", " rather than being deleted into a bare space.
# ---------------------------------------------------------------------------


def apply_text_passes(
    text: str, report: dict, options: dict, changed: bool
) -> tuple[str, bool]:
    """Run the app-side passes over ``text``, annotate ``report`` in place."""
    if options.get("deep_scrub"):
        text, deep_counts = deep_scrub(text)
        if deep_counts["total"] > 0:
            report["deep_scrub"] = deep_counts
            changed = True

    level = coerce_int(options.get("deai_style", 0), 0, 2, 0)
    if level > 0:
        text, deai_counts = deai_style(text, level)
        if deai_counts["total"] > 0:
            report["deai_style"] = deai_counts
            changed = True

    if options.get("strip_ai_tells"):
        text, ai_counts = strip_ai_tells(text)
        if ai_counts["total"] > 0:
            report["ai_tells"] = ai_counts
            changed = True

    if options.get("verify"):
        report["verify"] = verify_residue(text)

    return text, changed


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE_NAME}/{VERSION}"

    # -- low level responses -------------------------------------------------

    def _send_bytes(self, status: int, ctype: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if payload:
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _send_json(self, status: int, obj: dict) -> None:
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", payload)

    def _send_error_json(self, status: int, message: str, extra: dict | None = None) -> None:
        body = {"ok": False, "error": message, "kind": "unknown"}
        if extra:
            body.update(extra)
        self._send_json(status, body)

    def _send_plain(self, status: int, message: str) -> None:
        self._send_bytes(status, "text/plain; charset=utf-8", message.encode("utf-8"))

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib hook
        sys.stderr.write(
            "%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args)
        )

    # -- GET -----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        path = urlparse(self.path).path
        if path == "/":
            self._serve_static("index.html")
            return
        if path == "/favicon.ico":
            self._serve_static("favicon.ico")
            return
        if path.startswith("/assets/"):
            self._serve_static(path[len("/assets/"):])
            return
        if path == "/api/health":
            repo_ok = bool(INSPECT_SCRIPT.is_file() and CLEAN_SCRIPT.is_file())
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": SERVICE_NAME,
                    "version": VERSION,
                    "repo": repo_ok,
                    "python": platform.python_version(),
                },
            )
            return
        self._send_error_json(404, "Tidak ditemukan")

    def _serve_static(self, rel: str) -> None:
        rel = unquote(rel).lstrip("/")
        if not rel or "\x00" in rel or ".." in rel.split("/"):
            self._send_error_json(404, "Tidak ditemukan")
            return
        base = STATIC_DIR
        target = (base / rel).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            self._send_error_json(404, "Tidak ditemukan")
            return
        if not target.is_file():
            self._send_error_json(404, "Tidak ditemukan")
            return
        ctype = _MIME_OVERRIDES.get(target.suffix.lower())
        if ctype is None:
            import mimetypes

            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        try:
            payload = target.read_bytes()
        except OSError:
            self._send_error_json(500, "Gagal membaca berkas")
            return
        self._send_bytes(200, ctype, payload)

    # -- POST ----------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        path = urlparse(self.path).path
        if path not in ("/api/inspect", "/api/clean"):
            self._send_error_json(404, "Tidak ditemukan")
            return
        try:
            body = self._read_json_body()
            if path == "/api/inspect":
                self._handle_inspect(body)
            else:
                self._handle_clean(body)
        except ApiError as exc:
            self._send_error_json(exc.status, exc.message, exc.extra)
        except subprocess.TimeoutExpired:
            self._send_error_json(500, "Waktu proses habis")
        except Exception:  # pragma: no cover - defensive
            traceback.print_exc()
            self._send_error_json(500, "Terjadi kesalahan internal")

    def _read_json_body(self) -> dict:
        length = self.headers.get("Content-Length")
        if not length:
            raw = b""
        else:
            try:
                n = int(length)
            except (TypeError, ValueError):
                raise ApiError(400, "Content-Length tidak valid")
            if n > MAX_BODY_BYTES:
                raise ApiError(413, "Ukuran masukan melebihi 25 MiB")
            raw = self.rfile.read(n) if n > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "JSON tidak valid")
        if not isinstance(body, dict):
            raise ApiError(400, "JSON tidak valid")
        return body

    # -- endpoint logic ------------------------------------------------------

    def _handle_inspect(self, body: dict) -> None:
        mode, name, data, is_text, text, options = parse_input(body)
        tmp_dir = tempfile.mkdtemp(prefix="wmr-")
        try:
            in_path = Path(tmp_dir) / name
            in_path.write_bytes(data)
            try:
                proc = run_cli(
                    INSPECT_SCRIPT, [str(in_path), "--json", *build_inspect_flags(options)]
                )
            except subprocess.TimeoutExpired:
                raise ApiError(500, "Waktu proses habis")
            if proc.returncode not in (0, 1):
                raise ApiError(500, "Gagal memeriksa berkas")
            report = sanitize_report(parse_json_stdout(proc.stdout)) or {}
            kind = report.get("kind") or "unknown"

            # App-side AI-tell detection on the original text.
            ai_counts: dict | None = None
            deep_counts: dict | None = None
            if kind == "text":
                if mode == "text" and isinstance(text, str):
                    original = text
                else:
                    original = data.decode("utf-8", "replace")
                _, ai_counts = strip_ai_tells(original)
                report["ai_tells"] = ai_counts
                _, deep_counts = deep_scrub(original)
                report["deep_scrub"] = deep_counts

            suspicious = derive_suspicious(kind, report)
            if ai_counts and ai_counts.get("total", 0) > 0:
                suspicious = True
            if deep_counts and deep_counts.get("total", 0) > 0:
                suspicious = True

            self._send_json(
                200,
                {
                    "ok": True,
                    "kind": kind,
                    "name": name,
                    "suspicious": suspicious,
                    "summary": inspect_summary(kind, report),
                    "report": report,
                },
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _handle_clean(self, body: dict) -> None:
        mode, name, data, is_text, _text, options = parse_input(body)
        inspect_flags = build_inspect_flags(options)
        clean_flags = build_clean_flags(options)
        out_name = cleaned_name(name)
        download_name = out_name

        tmp_dir = tempfile.mkdtemp(prefix="wmr-")
        try:
            in_path = Path(tmp_dir) / name
            out_path = Path(tmp_dir) / out_name
            in_path.write_bytes(data)

            # 1) before-report (fail-soft -> null)
            report_before = None
            try:
                iproc = run_cli(INSPECT_SCRIPT, [str(in_path), "--json", *inspect_flags])
                if iproc.returncode in (0, 1):
                    report_before = sanitize_report(parse_json_stdout(iproc.stdout))
            except Exception:
                report_before = None

            # 2) clean
            try:
                cproc = run_cli(
                    CLEAN_SCRIPT, [str(in_path), "-o", str(out_path), "--json", *clean_flags]
                )
            except subprocess.TimeoutExpired:
                raise ApiError(500, "Waktu proses habis")

            # 3) unrecognized format -> 422
            if cproc.returncode == 2:
                raise ApiError(
                    422,
                    "Format berkas tidak dikenali",
                    extra={"report_before": report_before},
                )
            if cproc.returncode not in (0, 1):
                raise ApiError(500, "Gagal membersihkan berkas")

            report = sanitize_report(parse_json_stdout(cproc.stdout)) or {}

            # 4) read cleaned output (fall back to original bytes if absent)
            if out_path.is_file():
                out_bytes = out_path.read_bytes()
            else:
                out_bytes = data

            kind = report.get("kind") or (
                report_before.get("kind") if isinstance(report_before, dict) else None
            ) or "unknown"
            changed = bool(report.get("changed", True))

            if mode == "text":
                cleaned = out_bytes.decode("utf-8", "replace")
                # Order is load-bearing: deai_style runs before strip_ai_tells so
                # a clause dash becomes ", " instead of being deleted into a
                # bare space. deep_scrub first so residue never reaches style.
                cleaned, changed = apply_text_passes(cleaned, report, options, changed)
                out_bytes = cleaned.encode("utf-8")
                summary = text_clean_summary(report)
                resp = {
                    "ok": True,
                    "kind": kind,
                    "name": name,
                    "download_name": download_name,
                    "mime": guess_mime(download_name, "text/plain"),
                    "changed": changed,
                    "cleaned": cleaned,
                    "cleaned_base64": None,
                    "content_length": len(out_bytes),
                    "summary": summary,
                    "report_before": report_before,
                    "report": report,
                }
            else:
                if kind == "text":
                    text = out_bytes.decode("utf-8", "replace")
                    text, changed = apply_text_passes(text, report, options, changed)
                    out_bytes = text.encode("utf-8")
                if kind == "text":
                    summary = text_clean_summary(report)
                else:
                    summary = "Metadata dibersihkan" if changed else "Tidak ada yang perlu diubah"
                resp = {
                    "ok": True,
                    "kind": kind,
                    "name": name,
                    "download_name": download_name,
                    "mime": guess_mime(download_name),
                    "changed": changed,
                    "cleaned": None,
                    "cleaned_base64": base64.b64encode(out_bytes).decode("ascii"),
                    "content_length": len(out_bytes),
                    "summary": summary,
                    "report_before": report_before,
                    "report": report,
                }
            self._send_json(200, resp)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def parse_bind(argv: list[str]) -> tuple[str, int]:
    host = os.environ.get("HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("PORT", "8770"))
    except ValueError:
        port = 8770
    args = argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif arg == "--port" and i + 1 < len(args):
            try:
                port = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif arg.startswith("--host="):
            host = arg.split("=", 1)[1]
            i += 1
        elif arg.startswith("--port="):
            try:
                port = int(arg.split("=", 1)[1])
            except ValueError:
                pass
            i += 1
        else:
            i += 1
    return host, port


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    host, port = parse_bind(argv)
    httpd = Server((host, port), Handler)
    print(
        f"{SERVICE_NAME} {VERSION} on http://{host}:{port}  (repo={REPO_DIR}, python={platform.python_version()})",
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
