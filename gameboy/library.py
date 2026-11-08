"""What the launcher remembers between sessions.

Three things, and only three: which cartridges exist, how long each has been
played, and when it was last opened. Everything else a games launcher usually
shows — achievements, friends, a store — would be invented, and a launcher
that invents its own statistics is a mockup with a process attached.

One JSON file. A cartridge is identified by the digest of its bytes, so
renaming or moving a ROM keeps its history, and two copies of the same game
are correctly one entry.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path


def home() -> Path:
    return Path.home() / ".gameboy"


@dataclass(slots=True)
class Record:
    digest: str
    title: str
    seconds: float = 0.0
    last_played: float = 0.0
    launches: int = 0

    @property
    def hours(self) -> float:
        return self.seconds / 3600

    @property
    def played_label(self) -> str:
        if self.seconds < 60:
            return "never" if not self.seconds else "under a minute"
        if self.seconds < 3600:
            return f"{self.seconds / 60:.0f} min played"
        return f"{self.hours:.0f} h played"


@dataclass(slots=True)
class Library:
    path: Path = field(default_factory=lambda: home() / "library.json")
    records: dict[str, Record] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> Library:
        library = cls(path=path or home() / "library.json")
        try:
            raw = json.loads(library.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return library
        for digest, row in (raw.get("records") or {}).items():
            library.records[digest] = Record(
                digest=digest,
                title=row.get("title", ""),
                seconds=float(row.get("seconds", 0)),
                last_played=float(row.get("last_played", 0)),
                launches=int(row.get("launches", 0)),
            )
        return library

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "records": {
                d: {"title": r.title, "seconds": round(r.seconds, 1),
                    "last_played": r.last_played, "launches": r.launches}
                for d, r in self.records.items()
            }
        }
        # Written beside and moved, so a crash mid-write cannot leave a
        # half-file that loses every hour you ever played.
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        temporary.replace(self.path)

    def record(self, digest: str, title: str) -> Record:
        found = self.records.get(digest)
        if found is None:
            found = Record(digest=digest, title=title)
            self.records[digest] = found
        return found

    def played(self, digest: str, title: str, seconds: float) -> None:
        entry = self.record(digest, title)
        entry.seconds += max(0.0, seconds)
        entry.last_played = time.time()
        entry.launches += 1
        self.save()

    @property
    def total_hours(self) -> float:
        return sum(r.seconds for r in self.records.values()) / 3600

    def recent(self, limit: int = 4) -> list[Record]:
        played = [r for r in self.records.values() if r.last_played]
        return sorted(played, key=lambda r: -r.last_played)[:limit]


def digest_of(data: bytes) -> str:
    """Identify a cartridge by its contents, not its filename."""
    return hashlib.blake2b(data, digest_size=8).hexdigest()
