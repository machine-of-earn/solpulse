"""Number and text formatting shared by the Markdown and HTML renderers.

One rule governs this module: a missing value renders as the literal string
"unknown", never as 0, "-" or "". A reader must always be able to tell a real
zero from an absent measurement.
"""

from __future__ import annotations

UNKNOWN = "unknown"

_UNITS = [(1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")]


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def num(value, digits: int = 0, suffix: str = "") -> str:
    """Thousands-separated number, or "unknown"."""
    if not _is_num(value):
        return UNKNOWN
    return f"{value:,.{digits}f}{suffix}"


def compact(value, digits: int = 2) -> str:
    """Short magnitude form: 5_236_869_703 -> "5.24B"."""
    if not _is_num(value):
        return UNKNOWN
    magnitude = abs(value)
    for size, mark in _UNITS:
        if magnitude >= size:
            return f"{value / size:,.{digits}f}{mark}"
    return f"{value:,.{digits}f}"


def usd(value, digits: int = 2) -> str:
    if not _is_num(value):
        return UNKNOWN
    return f"${value:,.{digits}f}"


def usd_compact(value, digits: int = 2) -> str:
    if not _is_num(value):
        return UNKNOWN
    return f"${compact(value, digits)}"


def pct(value, digits: int = 2) -> str:
    if not _is_num(value):
        return UNKNOWN
    return f"{value:,.{digits}f}%"


def signed_pct(value, digits: int = 2) -> str:
    """Percentage that always carries its sign, for deltas."""
    if not _is_num(value):
        return UNKNOWN
    return f"{value:+,.{digits}f}%"


def direction(value) -> str:
    """"up" / "down" / "flat" / "unknown" — the non-color channel for a delta.

    Deltas are never signalled by color alone; every caller pairs this with an
    arrow glyph and the signed number itself.
    """
    if not _is_num(value):
        return "unknown"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


ARROWS = {"up": "▲", "down": "▼", "flat": "–", "unknown": "?"}


def duration(seconds) -> str:
    """Seconds -> "6h 50m", or "unknown"."""
    if not _is_num(seconds) or seconds < 0:
        return UNKNOWN
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def shorten(text, head: int = 6, tail: int = 6) -> str:
    """Middle-elide a long identifier such as a validator pubkey."""
    if not isinstance(text, str) or not text:
        return UNKNOWN
    if len(text) <= head + tail + 1:
        return text
    return f"{text[:head]}…{text[-tail:]}"
