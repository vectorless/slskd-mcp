"""Standing searches that re-run on a schedule and report only what's new.

Soulseek is a live network of people's hard drives -- the thing you want may
simply not be shared today and appear next week. A wishlist turns "search and
get nothing" into "tell me when it shows up".

Results already reported are remembered, so a scheduled run is silent unless
something genuinely new has appeared. Silence is the point; a watcher that
reports the same twelve files every hour gets muted.

The on-disk format is byte-compatible with the Rust implementation's, so the
two can share a ``wishlist.json``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class Entry:
    #: Search text, or the label name when ``label_filter`` is set.
    query: str
    #: Apply the bracketed-label-tag filter rather than a plain search.
    label_filter: bool = False
    #: Optional format spec, e.g. "lossless" or "flac,wav".
    formats: str | None = None
    added: str = ""

    def to_json(self) -> dict:
        return {
            "query": self.query,
            "label_filter": self.label_filter,
            "formats": self.formats,
            "added": self.added,
        }

    @staticmethod
    def from_json(d: dict) -> "Entry":
        return Entry(
            query=d.get("query", ""),
            label_filter=bool(d.get("label_filter", False)),
            formats=d.get("formats"),
            added=d.get("added", ""),
        )


def home() -> Path:
    """Home directory across platforms.

    ``HOME`` on Unix and macOS, ``USERPROFILE`` on Windows. Falls back to the
    working directory rather than failing, so the wishlist still works
    somewhere sensible.
    """
    return Path(os.environ.get("HOME") or os.environ.get("USERPROFILE") or ".")


def path() -> Path:
    override = os.environ.get("SLSKD_WISHLIST")
    if override:
        return Path(override)
    return home() / "slskd" / "wishlist.json"


def now_stamp() -> str:
    """Matches the Rust implementation's timestamp so files stay interchangeable."""
    return f"epoch:{int(time.time())}"


@dataclass
class Wishlist:
    entries: list[Entry] = field(default_factory=list)
    #: Filenames already reported. Keeps scheduled runs quiet.
    seen: set[str] = field(default_factory=set)

    @staticmethod
    def load() -> "Wishlist":
        try:
            d = json.loads(path().read_text())
        except (OSError, ValueError):
            return Wishlist()
        return Wishlist(
            entries=[Entry.from_json(e) for e in d.get("entries", [])],
            seen=set(d.get("seen", [])),
        )

    def save(self) -> None:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        # `seen` is sorted so the file is stable across runs and diffs cleanly;
        # it also matches the Rust BTreeSet ordering.
        p.write_text(
            json.dumps(
                {
                    "entries": [e.to_json() for e in self.entries],
                    "seen": sorted(self.seen),
                },
                indent=2,
            )
        )

    def add(self, e: Entry) -> bool:
        """Returns False if an equivalent entry is already present."""
        dup = any(
            x.query.lower() == e.query.lower()
            and x.label_filter == e.label_filter
            and x.formats == e.formats
            for x in self.entries
        )
        if not dup:
            self.entries.append(e)
        return not dup

    def remove(self, index: int) -> Entry | None:
        """Removes by 1-based index as shown to the user."""
        if index < 1 or index > len(self.entries):
            return None
        return self.entries.pop(index - 1)

    def mark_seen(self, files: Iterable[str]) -> int:
        """Marks filenames as reported; returns how many were genuinely new."""
        n = 0
        for f in files:
            if f not in self.seen:
                self.seen.add(f)
                n += 1
        return n

    def is_new(self, filename: str) -> bool:
        return filename not in self.seen
