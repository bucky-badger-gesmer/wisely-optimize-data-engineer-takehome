"""Where the RealGM CSV's mess gets absorbed: name matching/display forms and
per-game field coercions. Verified against the real data in research.md §2.
"""

from __future__ import annotations

import re
import unicodedata

# NFKD does NOT decompose Æ/æ into A+E / a+e (verified) — fold explicitly before
# the general NFKD combining-mark strip picks up the rest (á, í, é, ...).
_LIGATURE_FOLDS = {
    "Æ": "Ae", "æ": "ae",
    "Ø": "O", "ø": "o",
    "Đ": "D", "đ": "d",
    "Þ": "Th", "þ": "th",
    "ß": "ss",
}

_WHITESPACE_RE = re.compile(r"\s+")


def _flip_comma(raw: str) -> str:
    """"Last, First" -> "First Last". Only the first comma is treated as the
    flip point (multi-token surnames like "Cerro, Adrian del" have none after)."""
    raw = raw.strip()
    if "," not in raw:
        return raw
    last, _, first = raw.partition(",")
    return f"{first.strip()} {last.strip()}"


def display_name(raw: str) -> str:
    """Human-readable display form: comma-flip + whitespace collapse only.
    Diacritics are preserved — folding is a *matching* concern, not display."""
    flipped = _flip_comma(raw)
    return _WHITESPACE_RE.sub(" ", flipped).strip()


def canonical_name(raw: str) -> str:
    """The matching form used for cross-source identity resolution:
    comma-flip, explicit ligature fold, NFKD + strip combining marks, casefold,
    whitespace collapse. Verified: with this, all 10 overlapping players match
    RealGM rows to API rows deterministically."""
    name = _flip_comma(raw)
    name = "".join(_LIGATURE_FOLDS.get(ch, ch) for ch in name)
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.casefold()
    return _WHITESPACE_RE.sub(" ", name).strip()


def parse_minutes(raw: str | float | int | None) -> float | None:
    """Per-game minutes: most rows are decimal ("20.5"); all Agravanis rows are
    "MM:SS" ("26:36" -> 26.6). Empty -> None. Anything else raises ValueError
    so the caller can turn it into a Rejection instead of loading garbage."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        if raw < 0:
            raise ValueError(f"negative minutes: {raw!r}")
        return round(float(raw), 1)

    text = raw.strip()
    if not text:
        return None

    if ":" in text:
        mm_str, _, ss_str = text.partition(":")
        try:
            mm, ss = int(mm_str), int(ss_str)
        except ValueError as e:
            raise ValueError(f"unparseable MM:SS minutes: {raw!r}") from e
        if mm < 0 or not (0 <= ss < 60):
            raise ValueError(f"out-of-range MM:SS minutes: {raw!r}")
        return round(mm + ss / 60, 1)

    try:
        value = float(text)
    except ValueError as e:
        raise ValueError(f"unparseable minutes: {raw!r}") from e
    if value < 0:
        raise ValueError(f"negative minutes: {raw!r}")
    return round(value, 1)


def parse_optional_float(raw: str | float | int | None) -> float | None:
    """Empty/None -> None; else float(raw); unparseable -> ValueError."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = raw.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as e:
        raise ValueError(f"unparseable float: {raw!r}") from e


def parse_optional_int(raw: str | float | int | None) -> int | None:
    """Empty/None -> None; else int(raw); unparseable -> ValueError."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    text = raw.strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError as e:
        raise ValueError(f"unparseable int: {raw!r}") from e
