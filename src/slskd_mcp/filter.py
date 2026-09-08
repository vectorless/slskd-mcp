"""Filtering search results down to actual label releases.

Soulseek search is a plain substring match over the whole path, so searching
"Planet Rhythm" also returns Leftfield's "Phat Planet" from "Rhythm & Stealth".
Observed on the live network 2026-09-08.

Releases are near-universally foldered with the label as a bracketed tag --
``Artist - Title EP [Planet Rhythm]`` -- so requiring that pattern separates
genuine label releases from coincidental word matches.
"""

from __future__ import annotations

import re

_BRACKETS = (("[", "]"), ("(", ")"), ("{", "}"))

# Soulseek paths use backslashes regardless of the sharing peer's OS, so both
# separators have to be treated as path separators here.
_TAIL = re.compile(r"[.\\/]")

_LOSSLESS = ("flac", "wav", "aiff", "aif", "ape", "alac", "wv")
_LOSSY = ("mp3", "m4a", "aac", "ogg", "opus", "wma")


def norm(s: str) -> str:
    """Lowercase, alphanumeric only.

    "Planet Rhythm" and "planet-rhythm" both become "planetrhythm", which
    absorbs the punctuation and spacing variation in folder names.
    """
    return "".join(c.lower() for c in s if c.isalnum())


def bracketed(path: str) -> list[str]:
    """Every substring enclosed in ``[]``, ``()`` or ``{}``.

    Not nesting-aware -- folder names don't nest brackets in practice.
    """
    out: list[str] = []
    for open_c, close_c in _BRACKETS:
        start: int | None = None
        for i, c in enumerate(path):
            if c == open_c:
                start = i + 1
            elif c == close_c and start is not None:
                if i > start:
                    out.append(path[start:i])
                start = None
    return out


def has_label_tag(path: str, label: str) -> bool:
    """Does this path carry ``label`` as a bracketed tag?

    Matches on the normalised form, and accepts a tag that merely *contains*
    the label so ``[Planet Rhythm Records]`` still counts.
    """
    want = norm(label)
    if len(want) < 3:
        return False  # too short to be meaningful; would match everything
    for tag in bracketed(path):
        t = norm(tag)
        if t == want or (want in t and len(t) <= len(want) + 12):
            return True
    return False


def expand_formats(spec: str) -> list[str]:
    """Shorthands an agent (or a person) is likely to reach for."""
    out: set[str] = set()
    for token in re.split(r"[,\s|]+", spec or ""):
        t = token.strip().lstrip(".").lower()
        if not t:
            continue
        if t == "lossless":
            out.update(_LOSSLESS)
        elif t == "lossy":
            out.update(_LOSSY)
        else:
            out.add(t)
    return sorted(out)


def matches_format(filename: str, extension: str | None, spec: str) -> bool:
    """Does this file match the requested format spec?

    ``spec`` accepts extensions and the shorthands ``lossless`` / ``lossy``, in
    any mix: ``"flac"``, ``"flac,wav"``, ``"lossless"``, ``".FLAC, aiff"``.

    Soulseek's ``extension`` field is frequently empty, so the filename suffix
    is the source of truth and ``extension`` is only a fallback.
    """
    wanted = expand_formats(spec)
    if not wanted:
        return True  # no filter requested

    ext: str | None = None
    if "." in filename:
        tail = _TAIL.split(filename)[-1].lower()
        if tail and len(tail) <= 5:
            ext = tail
    if ext is None and extension:
        ext = extension.lstrip(".").lower()

    return bool(ext) and ext in wanted
