"""The home screen, and the chassis the screen sits in.

Two ideas hold this together.

**The menu is drawn at 160x144.** Not as an overlay at window resolution — at
the Game Boy's own, in its own four shades, then scaled up with the picture.
Every pixel in the window is therefore the same size, and the menu looks like
part of the console rather than a dialog bolted to the front of it.

**Scaling is nearest-neighbour, and a grid goes over the top.** Smooth scaling
turns pixel art into soup. Hard edges plus a one-pixel gap between each
original pixel reads as an LCD, which is the look people mean by "pixelated"
when they do not mean "blurry".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import font
from .ppu import HEIGHT, WIDTH

#: Where a ROM is looked for, in the order someone would expect.
ROM_DIRS = ("roms", ".", "~/Downloads", "~/Desktop")
SUFFIXES = (".gb", ".gbc")

#: Shade indices, not colours. The palette is applied at the very end, so the
#: menu changes with `p` exactly like the game does.
BG, DIM, MID, INK = 0, 1, 2, 3


@dataclass(slots=True)
class Entry:
    label: str
    path: Path | None  # None is the built-in cartridge
    size: str = ""


def find_roms(extra: str | None = None) -> list[Entry]:
    """The demo first, then whatever is lying about.

    Deduplicated by resolved path, because `.` and `~/Desktop` are the same
    folder often enough to matter.
    """
    entries = [Entry("Demo cartridge", None, "built in")]
    seen: set[Path] = set()

    folders = [Path(extra).expanduser()] if extra else []
    folders += [Path(d).expanduser() for d in ROM_DIRS]

    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in SUFFIXES:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            entries.append(
                Entry(path.stem[:18], resolved, f"{path.stat().st_size // 1024} KB")
            )
    return entries


class Menu:
    """Which row is highlighted, and nothing else.

    Kept apart from drawing so the behaviour — wrapping at the ends, paging a
    long list — can be tested without a window.
    """

    def __init__(self, entries: list[Entry], *, rows: int = 7) -> None:
        self.entries = entries
        self.rows = rows
        self.index = 0

    def move(self, step: int) -> None:
        if not self.entries:
            return
        self.index = (self.index + step) % len(self.entries)

    @property
    def selected(self) -> Entry | None:
        return self.entries[self.index] if self.entries else None

    @property
    def window(self) -> tuple[int, int]:
        """Which slice of the list to draw, keeping the cursor inside it."""
        if len(self.entries) <= self.rows:
            return 0, len(self.entries)
        start = min(max(self.index - self.rows // 2, 0), len(self.entries) - self.rows)
        return start, start + self.rows


def draw_home(pygame, menu: Menu, *, tick: int = 0):
    """Render the menu to a 160x144 surface of shade indices.

    Returns a pygame Surface in the four-shade palette's index space; the
    caller colours it, so the home screen follows whichever palette is on.
    """
    surface = pygame.Surface((WIDTH, HEIGHT), depth=8)
    surface.set_palette([(i, i, i) for i in range(256)])
    surface.fill(BG)

    # Title bar.
    pygame.draw.rect(surface, MID, (0, 0, WIDTH, 15))
    font.draw(surface, "GAME BOY", 6, 4, BG)
    font.draw(surface, "PYTHON", WIDTH - 6 - font.measure("PYTHON"), 4, BG)

    if not menu.entries:
        font.draw(surface, "NO CARTRIDGES", 10, 60, MID)
        font.draw(surface, "PUT .GB FILES IN ROMS/", 10, 72, DIM)
        return surface

    start, end = menu.window
    y = 22
    for position in range(start, end):
        entry = menu.entries[position]
        chosen = position == menu.index
        if chosen:
            pygame.draw.rect(surface, DIM, (3, y - 2, WIDTH - 6, 11))
            # A blinking caret, so a still screenshot still reads as a cursor.
            if tick % 60 < 40:
                font.draw(surface, ">", 5, y, INK)
        font.draw(surface, entry.label, 14, y, INK if chosen else MID)
        if entry.size:
            font.draw(surface, entry.size,
                      WIDTH - 5 - font.measure(entry.size), y, MID)
        y += 13

    # Footer.
    pygame.draw.rect(surface, DIM, (0, HEIGHT - 12, WIDTH, 12))
    font.draw(surface, "UP DOWN", 4, HEIGHT - 9, MID)
    footer = "ENTER PLAY"
    font.draw(surface, footer, WIDTH - 4 - font.measure(footer), HEIGHT - 9, MID)
    return surface


def lcd_grid(pygame, size: tuple[int, int], scale: int, strength: int = 26):
    """A one-pixel gap between original pixels, which is what an LCD looks like.

    Built once and reused: it is a full-screen alpha surface, and rebuilding
    it every frame costs more than the emulator does.
    """
    width, height = size
    grid = pygame.Surface((width, height), pygame.SRCALPHA)
    if scale < 3:
        return grid  # at small scales the lines would eat the picture
    for x in range(0, width, scale):
        pygame.draw.line(grid, (0, 0, 0, strength), (x, 0), (x, height))
    for y in range(0, height, scale):
        pygame.draw.line(grid, (0, 0, 0, strength), (0, y), (width, y))
    return grid
