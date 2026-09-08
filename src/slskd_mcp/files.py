"""Presentation helpers for the file dicts slskd returns.

slskd's OpenAPI spec declares no response schemas for the search endpoints, so
these field names come from its own source: ``src/slskd/Search/Types/*.cs``.
Everything is read defensively -- a missing key is normal, not an error.
"""

from __future__ import annotations

from typing import Any


def size_human(size: int | float | None) -> str:
    """Human-readable size, because agents shouldn't do arithmetic on bytes."""
    b = float(size or 0)
    if b >= 1e9:
        return f"{b / 1e9:.2f} GB"
    if b >= 1e6:
        return f"{b / 1e6:.1f} MB"
    if b >= 1e3:
        return f"{b / 1e3:.0f} KB"
    return f"{int(b)} B"


def quality(f: dict[str, Any]) -> str:
    """e.g. "320kbps 4:56"."""
    parts = []
    br = f.get("bitRate")
    if br:
        parts.append(f"{br}kbps")
    length = f.get("length")
    if length:
        parts.append(f"{int(length) // 60}:{int(length) % 60:02d}")
    return " ".join(parts)


def describe(f: dict[str, Any]) -> str:
    """One line for a file: path, size and whatever quality info exists."""
    q = quality(f)
    detail = size_human(f.get("size"))
    if q:
        detail += f", {q}"
    return f"   {f.get('filename', '?')} [{detail}]"


def peer_line(r: dict[str, Any], file_count: int | None = None) -> str:
    """Header line for one responding peer."""
    n = len(r.get("files", [])) if file_count is None else file_count
    speed = int(r.get("uploadSpeed", 0) or 0) // 1024
    queue = int(r.get("queueLength", 0) or 0)
    bits = [f"{n} file(s)", f"{speed}kb/s"]
    if r.get("hasFreeUploadSlot"):
        bits.append("free slot")
    if queue:
        bits.append(f"queue {queue}")
    return f"\n{r.get('username', '?')} — " + ", ".join(bits)


def rank_key(r: dict[str, Any]) -> tuple[bool, int]:
    """A free upload slot and a short queue mean a download that actually starts."""
    return (not r.get("hasFreeUploadSlot", False), int(r.get("queueLength", 0) or 0))
