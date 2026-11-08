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

from . import font
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

#: Geometry. The card is the shape it is because a Game Boy frame is 160x144,
#: and cover art that has been squashed stops being a photograph of the
#: cartridge. Every other number is laid out around that one ratio.
SIDEBAR = 210
DETAIL = 284
GAP = 18
ROW = 46
CARD_W = 220
THUMB_W = CARD_W - 2
THUMB_H = round(THUMB_W * HEIGHT / WIDTH)
CARD_H = THUMB_H + 58
TOPBAR = 78
LABEL_ROW = 26
WINDOW = (1280, 800)

NAV = ("Home", "Library")
#: What the grid is called in each view. Home is ordered by what you played
#: last, Library alphabetically, which is the only difference between them.
TITLES = {"Home": "CONTINUE", "Library": "ALL CARTRIDGES"}


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
    launches: int = 0


def clean_title(raw: str) -> str:
    """Keep only what a header title is allowed to contain.

    The sixteen title bytes are padded with whatever the build tool left
    there, and the last four of them are a manufacturer code on later
    cartridges. Anything that is not printable becomes a box in a bitmap
    font, so `2048-GB` arrives on screen as `2048-GB XXXX`.
    """
    kept = "".join(
        c for c in raw if c.isascii() and (c.isalnum() or c in " -_.'")
    )
    return " ".join(kept.split()).strip(" -_")


def best_label(header_title: str, filename: str) -> str:
    """Whichever of the two names tells you more.

    The header is authoritative but has sixteen bytes to work with, so
    `Tobu Tobu Girl` ships as `TOBU`. When the filename is longer and says
    the same thing, the filename is the better label.
    """
    header, stem = clean_title(header_title), clean_title(filename)
    if not header:
        return stem
    if len(stem) > len(header) and header.lower() in stem.lower():
        return stem
    return header


def cartridge_facts(data: bytes) -> tuple[str, str]:
    """Title and controller, read from the cartridge header."""
    from .cartridge import parse_header

    try:
        header = parse_header(data)
    except ValueError:
        return "", "unreadable"
    return clean_title(header.title), header.controller


def shade_counts(pixels) -> list[int]:
    counts = [0, 0, 0, 0]
    for value in pixels:
        counts[value & 3] += 1
    return counts


def interest(pixels) -> float:
    """How much of a picture a frame is, from 0 to 1.

    A cartridge spends its first second on a blank screen and its second on
    a logo, so frame 150 is usually nothing. Rather than guess a number of
    frames per game, several frames are scored and the best one kept. The
    score is simply how far the frame is from being one flat colour, with a
    bonus for using more than two shades — which is what separates a title
    screen from a fade.
    """
    counts = shade_counts(pixels)
    total = sum(counts) or 1
    spread = 1 - max(counts) / total
    used = sum(1 for c in counts if c > total * 0.01)
    return spread + 0.15 * used


def _surface_from(pygame, pixels, scale: int):
    from .png import DMG_PALETTE

    table = [bytes(colour) for colour in DMG_PALETTE]
    buffer = b"".join([table[p & 3] for p in pixels])
    image = pygame.image.frombuffer(buffer, (WIDTH, HEIGHT), "RGB").convert()
    return pygame.transform.scale(image, (WIDTH * scale, HEIGHT * scale))


def thumbnail(pygame, data: bytes, *, frames: int = 420, every: int = 30, scale: int = 2):
    """Run the cartridge and photograph the best frame it reaches.

    Anything that crashes gets a blank panel rather than taking the launcher
    down with it — a cartridge this emulator cannot run is a card with no
    picture, not a stack trace.
    """
    from .machine import GameBoy

    try:
        machine = GameBoy.from_bytes(data)
    except Exception:  # noqa: BLE001 - a bad ROM is a blank card, not a crash
        surface = pygame.Surface((WIDTH * scale, HEIGHT * scale))
        surface.fill(PANEL_2)
        return surface

    best, best_score = None, -1.0
    try:
        for index in range(frames):
            pixels = machine.run_frame()
            if index % every != every - 1:
                continue
            score = interest(pixels)
            if score > best_score:
                best, best_score = list(pixels), score
            # A frame using all four shades over a good spread is as good a
            # cover as this cartridge is going to give; stop paying for more.
            if best_score > 1.4:
                break
    except Exception:  # noqa: BLE001 - keep whatever was captured before it died
        pass

    if best is None:
        surface = pygame.Surface((WIDTH * scale, HEIGHT * scale))
        surface.fill(PANEL_2)
        return surface
    return _surface_from(pygame, best, scale)


def cover(pygame, digest: str, data: bytes, *, cache: Path | None = None):
    """A cover, from the cache if it is there and generated if it is not.

    Searching four hundred frames of every cartridge costs a few seconds
    each, which is tolerable once and insulting every time. Covers are
    keyed by the digest of the ROM, so the cache is never stale: different
    bytes are a different cartridge.
    """
    from .library import home

    folder = cache or home() / "covers"
    path = folder / f"{digest}.png"
    if path.exists():
        try:
            return pygame.image.load(str(path)).convert()
        except Exception:  # noqa: BLE001 - a damaged cache entry is regenerated
            pass
    surface = thumbnail(pygame, data)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        pygame.image.save(surface, str(path))
    except Exception:  # noqa: BLE001 - an unwritable cache is slow, not broken
        pass
    return surface


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
        """The cards this view shows, in this view's order.

        Home puts whatever you played last first, which is the only reason
        the two views differ; Library is alphabetical so a known title can be
        found by eye. The search box filters either one.
        """
        rows = self.tiles
        if self.query:
            needle = self.query.lower()
            rows = [t for t in rows if needle in t.label.lower()]
        if self.view == "Home":
            return sorted(rows, key=lambda t: (-t.last_played, t.label))
        return sorted(rows, key=lambda t: t.label)

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

    def switch(self, view: str) -> None:
        self.view = view
        self.index = 0

    def columns(self, width: int) -> int:
        """How many cards fit beside the sidebar and the detail panel."""
        usable = width - SIDEBAR - DETAIL - 3 * GAP
        return max(1, (usable + GAP) // (CARD_W + GAP))

    def rows(self, height: int) -> int:
        usable = height - TOPBAR - LABEL_ROW - GAP
        return max(1, (usable + GAP) // (CARD_H + GAP))

    def page(self, width: int, height: int) -> tuple[int, int]:
        """The slice of cards on screen, scrolled to keep the cursor visible.

        Scrolling is by whole rows rather than by pixels: a grid that slides
        under a fixed cursor is harder to read than one that pages, and there
        is no smooth scrolling in a four-shade handheld anyway.
        """
        shown = self.visible
        if not shown:
            return 0, 0
        columns, rows = self.columns(width), self.rows(height)
        cursor_row = min(self.index, len(shown) - 1) // columns
        first_row = max(0, cursor_row - rows + 1)
        start = first_row * columns
        return start, start + rows * columns

    def set_query(self, text: str) -> None:
        self.query = text
        self.index = 0


def build_tiles(pygame, entries, library: Library, progress=None) -> list[Tile]:
    """Turn the cartridge list into cards, with art and history attached.

    Each cover costs 150 emulated frames, so a folder of cartridges takes a
    noticeable moment. `progress` is called before each one with (done,
    total, label) so the window can say what it is doing instead of hanging.
    """
    from .demo import build_demo_rom

    tiles: list[Tile] = []
    for done, entry in enumerate(entries):
        if progress is not None:
            progress(done, len(entries), entry.label)
        try:
            data = build_demo_rom() if entry.path is None else entry.path.read_bytes()
        except OSError:
            continue
        digest = digest_of(data)
        title, controller = cartridge_facts(data)
        record = library.records.get(digest)
        tiles.append(
            Tile(
                label=best_label(title, entry.label).title(),
                path=entry.path,
                digest=digest,
                kind=controller,
                size_label=f"{len(data) // 1024} KB",
                thumb=cover(pygame, digest, data),
                played=record.played_label if record else "never",
                last_played=record.last_played if record else 0.0,
                launches=record.launches if record else 0,
            )
        )
    return tiles


def load_machine(tile: Tile):
    from .demo import build_demo_rom
    from .machine import GameBoy

    data = build_demo_rom() if tile.path is None else tile.path.read_bytes()
    return GameBoy.from_bytes(data), data


def ago(when: float, now: float | None = None) -> str:
    """A rough age, in the largest unit that is still a small number."""
    if not when:
        return "never"
    seconds = max(0.0, (time.time() if now is None else now) - when)
    for size, unit in ((86400 * 7, "w"), (86400, "d"), (3600, "h"), (60, "min")):
        if seconds >= size:
            return f"{seconds / size:.0f} {unit} ago"
    return "just now"


def greeting(now: float | None = None) -> str:
    hour = time.localtime(now).tm_hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


# --------------------------------------------------------------- drawing
#
# Everything below takes `pygame` as an argument rather than importing it, so
# the module can be imported — and the layout above tested — on a machine
# with no window system at all. It is the same arrangement the console's own
# menu uses in shell.py.


def fit(text: str, width: int, scale: int) -> str:
    """Shorten text until it fits, marking that it was cut."""
    if font.measure_at(text, scale) <= width:
        return text
    while text and font.measure_at(text + ".", scale) > width:
        text = text[:-1]
    return text + "." if text else ""


def wrap(text: str, width: int, scale: int, lines: int = 2) -> list[str]:
    """Break on spaces, then hard-cut the last line if it still overflows."""
    out: list[str] = []
    word_list = text.split()
    current = ""
    for word in word_list:
        candidate = f"{current} {word}".strip()
        if font.measure_at(candidate, scale) <= width or not current:
            current = candidate
        else:
            out.append(current)
            current = word
            if len(out) == lines - 1:
                break
    remainder = " ".join(word_list[sum(len(line.split()) for line in out):])
    out.append(fit(remainder or current, width, scale))
    return out[:lines]


def _panel(pygame, surface, rect, fill=PANEL, edge=EDGE, radius=10, border=1) -> None:
    pygame.draw.rect(surface, fill, rect, border_radius=radius)
    if border:
        pygame.draw.rect(surface, edge, rect, width=border, border_radius=radius)


_VEILS: dict[tuple[int, int], object] = {}


def _veil(pygame, size: tuple[int, int]):
    """Scanlines, cached per size.

    The covers are photographs of an LCD, so they get the lines back. The
    cards are not scaled by a whole number, which means a per-pixel grid like
    the console's own would alias — horizontal lines every third row read as
    a screen without needing to line up with anything.
    """
    found = _VEILS.get(size)
    if found is not None:
        return found
    veil = pygame.Surface(size, pygame.SRCALPHA)
    for y in range(0, size[1], 3):
        veil.fill((0, 0, 0, 46), (0, y, size[0], 1))
    _VEILS[size] = veil
    return veil


def draw_sidebar(pygame, screen, state: State, library: Library) -> None:
    height = screen.get_height()
    screen.fill(PANEL, (0, 0, SIDEBAR, height))
    screen.fill(EDGE, (SIDEBAR - 1, 0, 1, height))

    # The wordmark, with the power light the real console had.
    screen.fill(LIME, (GAP, 29, 10, 10))
    font.draw(screen, "GAMEBOY", GAP + 20, 28, INK, scale=2)
    font.draw(screen, "LAUNCHER", GAP + 20, 46, FAINT, scale=1)

    counts = {"Home": len(library.recent(99)), "Library": len(state.tiles)}
    y = 100
    for name in NAV:
        active = state.view == name
        if active:
            _panel(pygame, screen, pygame.Rect(8, y, SIDEBAR - 17, ROW),
                   fill=PANEL_2, edge=EDGE, radius=9)
            screen.fill(LIME, (8, y + (ROW - 22) // 2, 3, 22))
        font.draw(screen, name.upper(), GAP + 10, y + (ROW - 14) // 2,
                  INK if active else DIM, scale=2)
        tally = str(counts.get(name, 0))
        font.draw(screen, tally, SIDEBAR - 20 - font.measure_at(tally, 1),
                  y + (ROW - 7) // 2, LIME if active else FAINT, scale=1)
        y += ROW + 6

    # The two numbers at the bottom are the only statistics in the whole
    # application, and both are counted rather than estimated.
    hours = library.total_hours
    played = f"{hours:.1f} H" if hours < 100 else f"{hours:.0f} H"
    for offset, (label, value, colour) in enumerate((
        ("CARTRIDGES", str(len(state.tiles)), INK),
        ("TIME PLAYED", played, AMBER),
    )):
        base = height - 118 + offset * 54
        font.draw(screen, label, GAP, base, FAINT, scale=1)
        font.draw(screen, value, GAP, base + 12, colour, scale=2)


def draw_topbar(pygame, screen, state: State, grid_right: int) -> None:
    left = SIDEBAR + GAP
    font.draw(screen, greeting().upper(), left, 26, INK, scale=2)
    shown = len(state.visible)
    note = f"{shown} OF {len(state.tiles)} SHOWN" if state.query else "PICK A CARTRIDGE"
    font.draw(screen, note, left, 48, FAINT, scale=1)

    box = pygame.Rect(grid_right - 240, 24, 240, 32)
    _panel(pygame, screen, box, fill=PANEL_2,
           edge=LIME if state.typing else EDGE, radius=8,
           border=2 if state.typing else 1)
    if state.query or state.typing:
        font.draw(screen, fit(state.query.upper(), 198, 1), box.x + 14,
                  box.y + 13, INK, scale=1)
        if state.typing:
            caret = box.x + 16 + font.measure_at(state.query.upper(), 1)
            screen.fill(LIME, (caret, box.y + 11, 5, 11))
    else:
        font.draw(screen, "/ SEARCH", box.x + 14, box.y + 13, FAINT, scale=1)


def draw_card(pygame, screen, tile: Tile, rect, selected: bool) -> None:
    _panel(pygame, screen, rect, fill=PANEL_2,
           edge=LIME if selected else EDGE, radius=10,
           border=2 if selected else 1)
    thumb = pygame.Rect(rect.x + 1, rect.y + 1, THUMB_W, THUMB_H)
    if tile.thumb is not None:
        screen.blit(pygame.transform.scale(tile.thumb, thumb.size), thumb)
        screen.blit(_veil(pygame, thumb.size), thumb)
    else:
        screen.fill(PANEL, thumb)
    # The border is drawn again so the cover does not square off the corners.
    pygame.draw.rect(screen, LIME if selected else EDGE, rect,
                     width=2 if selected else 1, border_radius=10)

    font.draw(screen, fit(tile.label.upper(), CARD_W - 24, 2), rect.x + 12,
              rect.y + THUMB_H + 12, INK if selected else DIM, scale=2)
    played = tile.played.upper()
    font.draw(screen, played, rect.x + 12, rect.y + THUMB_H + 34,
              AMBER if played != "NEVER" else FAINT, scale=1)
    size = tile.size_label.upper()
    font.draw(screen, size, rect.right - 12 - font.measure_at(size, 1),
              rect.y + THUMB_H + 34, FAINT, scale=1)


def draw_grid(pygame, screen, state: State) -> int:
    """Draw the cards on screen. Returns the right edge of the grid column."""
    width, height = screen.get_size()
    columns = state.columns(width)
    left = SIDEBAR + GAP
    grid_right = width - DETAIL - 2 * GAP

    label = TITLES.get(state.view, state.view.upper())
    font.draw(screen, label, left, TOPBAR + 8, LIME, scale=1)
    shown = state.visible
    if shown:
        count = f"{len(shown)}"
        font.draw(screen, count, left + font.measure_at(label, 1) + 10,
                  TOPBAR + 8, FAINT, scale=1)

    start, end = state.page(width, height)
    top = TOPBAR + LABEL_ROW
    for offset, tile in enumerate(shown[start:end]):
        column, row = offset % columns, offset // columns
        rect = pygame.Rect(left + column * (CARD_W + GAP),
                           top + row * (CARD_H + GAP), CARD_W, CARD_H)
        draw_card(pygame, screen, tile, rect, start + offset == state.index)

    if not shown:
        message = "NOTHING MATCHES" if state.query else "NO CARTRIDGES FOUND"
        font.draw(screen, message, left, top + 20, DIM, scale=2)
        hint = "PUT .GB FILES IN ~/DESKTOP/ROMS OR PASS --ROMS"
        font.draw(screen, hint, left, top + 46, FAINT, scale=1)
    return grid_right


def draw_detail(pygame, screen, state: State) -> None:
    width, height = screen.get_size()
    panel = pygame.Rect(width - DETAIL - GAP, TOPBAR, DETAIL, height - TOPBAR - GAP)
    _panel(pygame, screen, panel, fill=PANEL, edge=EDGE, radius=12)

    tile = state.selected
    inner, x = DETAIL - 32, panel.x + 16
    if tile is None:
        font.draw(screen, "NOTHING SELECTED", x, panel.y + 24, DIM, scale=1)
        return

    cover = pygame.Rect(x, panel.y + 16, inner, round(inner * HEIGHT / WIDTH))
    if tile.thumb is not None:
        screen.blit(pygame.transform.scale(tile.thumb, cover.size), cover)
        screen.blit(_veil(pygame, cover.size), cover)
    else:
        screen.fill(PANEL_2, cover)
    pygame.draw.rect(screen, EDGE, cover, width=1)

    y = cover.bottom + 20
    for line in wrap(tile.label.upper(), inner, 2):
        font.draw(screen, line, x, y, INK, scale=2)
        y += 20
    y += 6

    # Five facts. The first three are read out of the cartridge header and
    # the last two out of the play history — nothing here is invented, which
    # is the whole reason the panel is short.
    facts = [
        ("CONTROLLER", tile.kind.upper() or "UNKNOWN", DIM),
        ("SIZE", tile.size_label.upper(), DIM),
        ("ID", tile.digest[:8].upper() or "—", FAINT),
        ("PLAYED", tile.played.upper(), AMBER if tile.played != "never" else DIM),
        ("LAST", ago(tile.last_played).upper(), DIM if tile.last_played else FAINT),
    ]
    if tile.launches:
        facts.append(("SESSIONS", str(tile.launches), DIM))
    screen.fill(EDGE, (x, y - 6, inner, 1))
    y += 6
    for label, value, colour in facts:
        font.draw(screen, label, x, y, FAINT, scale=1)
        font.draw(screen, fit(value, inner - 104, 1),
                  x + 104, y, colour, scale=1)
        y += 19

    button = pygame.Rect(x, panel.bottom - 100, inner, 42)
    pygame.draw.rect(screen, LIME, button, border_radius=9)
    word = "PLAY"
    font.draw(screen, word, button.centerx - font.measure_at(word, 2) // 2,
              button.centery - 7, BG, scale=2)
    pygame.draw.rect(screen, LIME_DARK, button, width=1, border_radius=9)

    hints = "ENTER PLAY  TAB VIEW  / FIND  ESC QUIT"
    font.draw(screen, fit(hints, inner, 1), x, panel.bottom - 40, FAINT, scale=1)


def render(pygame, screen, state: State, library: Library) -> None:
    """One whole frame of the launcher."""
    screen.fill(BG)
    grid_right = draw_grid(pygame, screen, state)
    draw_sidebar(pygame, screen, state, library)
    draw_topbar(pygame, screen, state, grid_right)
    draw_detail(pygame, screen, state)


def draw_loading(pygame, screen, done: int, total: int, title: str) -> None:
    """Shown while covers are generated, because that takes a visible moment."""
    width, height = screen.get_size()
    screen.fill(BG)
    heading = "READING CARTRIDGES"
    font.draw(screen, heading, width // 2 - font.measure_at(heading, 2) // 2,
              height // 2 - 40, INK, scale=2)
    bar = pygame.Rect(width // 2 - 180, height // 2, 360, 10)
    _panel(pygame, screen, bar, fill=PANEL_2, edge=EDGE, radius=5)
    if total:
        screen.fill(LIME, (bar.x + 2, bar.y + 2,
                           int((bar.width - 4) * done / total), bar.height - 4))
    note = fit(title.upper(), 360, 1)
    font.draw(screen, note, width // 2 - font.measure_at(note, 1) // 2,
              height // 2 + 24, FAINT, scale=1)
