"""The launcher: a sidebar, a grid of cartridges, and what you have played.

Everything on screen is something the emulator actually knows. The cover art
is not artwork — it is the cartridge, run for two seconds and photographed,
which is both honest and the only way to get a picture of a ROM nobody has
drawn a box for. Hours played come from a file this writes. The header line
under a title is read out of the cartridge itself.

What a games launcher usually also has — a store, friends, achievements — is
absent, because none of it would be real.

Drawn with the same bitmap font as the console's own menu, at larger scales,
so the whole application is made of the same square pixels.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .library import Library, digest_of
from .ppu import HEIGHT, WIDTH

# The palette: near-black greens, one lime for anything live, amber for a
# number worth noticing. Nothing else gets a colour.
INK = (233, 240, 228)
DIM = (126, 150, 128)
FAINT = (74, 94, 78)
BG = (8, 18, 12)
PANEL = (13, 30, 20)
PANEL_2 = (18, 40, 27)
EDGE = (32, 66, 44)
LIME = (150, 220, 80)
LIME_DARK = (46, 92, 40)
AMBER = (232, 176, 58)

SIDEBAR = 210
ROW = 46
CARD_W, CARD_H = 252, 196
THUMB_H = 118
GAP = 18

NAV = ("Home", "Library")


@dataclass(slots=True)
class Tile:
    """One cartridge, as the launcher sees it."""

    label: str
    path: Path | None
    digest: str = ""
    kind: str = ""
    size_label: str = ""
    thumb: object | None = None
    played: str = "never"
    last_played: float = 0.0


def cartridge_facts(data: bytes) -> tuple[str, str]:
    """Title and controller, read from the cartridge header."""
    from .cartridge import parse_header

    try:
        header = parse_header(data)
    except ValueError:
        return "", "unreadable"
    return header.title.strip(), header.controller


def thumbnail(pygame, data: bytes, *, frames: int = 150, scale: int = 2):
    """Run the cartridge and photograph it.

    Most ROMs spend their first second on a logo and their second on a title
    screen, which is exactly the picture you want on a card. Anything that
    crashes gets a blank panel rather than taking the launcher down with it.
    """
    from .machine import GameBoy
    from .png import DMG_PALETTE

    surface = pygame.Surface((WIDTH * scale, HEIGHT * scale))
    try:
        machine = GameBoy.from_bytes(data)
        pixels = machine.run_frames(frames)
    except Exception:  # noqa: BLE001 - a bad ROM is a blank card, not a crash
        surface.fill(PANEL_2)
        return surface
    table = [bytes(colour) for colour in DMG_PALETTE]
    buffer = b"".join([table[p & 3] for p in pixels])
    image = pygame.image.frombuffer(buffer, (WIDTH, HEIGHT), "RGB").convert()
    return pygame.transform.scale(image, surface.get_size())


@dataclass(slots=True)
class State:
    """Which view is open and what is selected. No drawing, so it is testable."""

    tiles: list[Tile] = field(default_factory=list)
    view: str = "Home"
    index: int = 0
    query: str = ""
    typing: bool = False

    @property
    def visible(self) -> list[Tile]:
        if not self.query:
            return self.tiles
        needle = self.query.lower()
        return [t for t in self.tiles if needle in t.label.lower()]

    @property
    def selected(self) -> Tile | None:
        shown = self.visible
        if not shown:
            return None
        return shown[min(self.index, len(shown) - 1)]

    def move(self, step: int) -> None:
        shown = self.visible
        if shown:
            self.index = (min(self.index, len(shown) - 1) + step) % len(shown)

    def columns(self, width: int) -> int:
        """How many cards fit, never fewer than one."""
        usable = width - SIDEBAR - 2 * GAP
        return max(1, (usable + GAP) // (CARD_W + GAP))

    def set_query(self, text: str) -> None:
        self.query = text
        self.index = 0


def build_tiles(pygame, entries, library: Library) -> list[Tile]:
    """Turn the cartridge list into cards, with art and history attached."""
    from .demo import build_demo_rom

    tiles: list[Tile] = []
    for entry in entries:
        try:
            data = build_demo_rom() if entry.path is None else entry.path.read_bytes()
        except OSError:
            continue
        digest = digest_of(data)
        title, controller = cartridge_facts(data)
        record = library.records.get(digest)
        tiles.append(
            Tile(
                label=(title or entry.label).title(),
                path=entry.path,
                digest=digest,
                kind=controller,
                size_label=f"{len(data) // 1024} KB",
                thumb=thumbnail(pygame, data),
                played=record.played_label if record else "never",
                last_played=record.last_played if record else 0.0,
            )
        )
    return tiles


def load_machine(tile: Tile):
    from .demo import build_demo_rom
    from .machine import GameBoy

    data = build_demo_rom() if tile.path is None else tile.path.read_bytes()
    return GameBoy.from_bytes(data), data


def greeting(now: float | None = None) -> str:
    hour = time.localtime(now).tm_hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"
