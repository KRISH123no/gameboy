"""The window, the keyboard and the frame pacing.

Kept apart from everything else on purpose: the emulator core has no
dependencies and no idea a screen exists, so it can be tested headlessly and
driven from a script. This module is the only place pygame is imported.
"""

from __future__ import annotations

import time

from .joypad import Button
from .machine import GameBoy
from .png import DMG_PALETTE, GREY_PALETTE, write_png
from .ppu import HEIGHT, WIDTH

FPS = 59.7  # the real refresh rate, not 60

#: Arrows for the pad, Z and X for A and B, as every emulator has done since
#: the nineties.
#:
#: The names are pygame's own constants, spelled exactly, rather than built
#: from the key's label — because pygame is not consistent about case. Named
#: keys are upper (`K_UP`, `K_RETURN`) and letters are lower (`K_z`), so
#: `f"K_{name}"` produces `K_up`, which does not exist, and the window dies
#: on the line that builds the map. It did, for two months.
KEY_MAP = {
    "K_UP": Button.UP,
    "K_DOWN": Button.DOWN,
    "K_LEFT": Button.LEFT,
    "K_RIGHT": Button.RIGHT,
    "K_z": Button.A,
    "K_x": Button.B,
    "K_RETURN": Button.START,
    "K_RSHIFT": Button.SELECT,
}

HELP = """
  arrows  d-pad          z  A          x  B
  enter   start          shift  select
  space   pause          tab (hold)  fast forward
  p       switch palette f2  save a screenshot
  esc     back to the menu
"""

#: The chassis around the screen. Dark enough that the LCD is the brightest
#: thing in the window, which is how a handheld actually reads.
CHASSIS = (18, 19, 23)
BEZEL = (10, 11, 14)
LABEL = (104, 108, 120)
#: Space around the LCD, in window pixels.
PAD = 26
FOOT = 30


def _chrome(pygame, screen, lcd_rect, caption: str, note: str) -> None:
    """The case, the bezel and the two lines of text under the screen."""
    screen.fill(CHASSIS)
    pygame.draw.rect(screen, BEZEL, lcd_rect.inflate(10, 10), border_radius=7)
    font = pygame.font.Font(None, 19)
    small = pygame.font.Font(None, 16)
    screen.blit(font.render(caption, True, (228, 230, 236)),
                (lcd_rect.left, lcd_rect.bottom + 10))
    label = small.render(note, True, LABEL)
    screen.blit(label, (lcd_rect.right - label.get_width(), lcd_rect.bottom + 12))


def play(gameboy: GameBoy, *, scale: int = 4, palette=DMG_PALETTE, title: str | None = None) -> int:
    try:
        import pygame
    except ImportError:
        print('the window needs pygame: pip install "gameboy[play]"')
        return 1

    from .shell import lcd_grid

    pygame.init()
    pygame.display.set_caption(title or f"gameboy — {gameboy.title}")
    lcd_size = (WIDTH * scale, HEIGHT * scale)
    screen = pygame.display.set_mode((lcd_size[0] + PAD * 2, lcd_size[1] + PAD + FOOT))
    lcd_rect = pygame.Rect(PAD, PAD, *lcd_size)
    grid = lcd_grid(pygame, lcd_size, scale)
    clock = pygame.time.Clock()
    print(HELP)

    keys = {getattr(pygame, name): button for name, button in KEY_MAP.items()}
    palettes = [DMG_PALETTE, GREY_PALETTE]
    palette_index = palettes.index(palette) if palette in palettes else 0
    rgb_tables = [[bytes(colour) for colour in option] for option in palettes]
    paused = False
    turbo = False
    quit_all = True  # the window was closed, rather than escaped out of
    shots = 0
    running = True
    started = time.perf_counter()
    frames = 0

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
                quit_all = True
            elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
                down = event.type == pygame.KEYDOWN
                if event.key in keys:
                    button = keys[event.key]
                    gameboy.press(button) if down else gameboy.release(button)
                elif down and event.key == pygame.K_ESCAPE:
                    running = False
                    quit_all = False
                elif down and event.key == pygame.K_SPACE:
                    paused = not paused
                elif down and event.key == pygame.K_p:
                    palette_index = (palette_index + 1) % len(palettes)
                elif down and event.key == pygame.K_F2:
                    shots += 1
                    name = f"screenshot-{shots:03d}.png"
                    write_png(name, gameboy.screen(), WIDTH, HEIGHT, scale=3,
                              palette=palettes[palette_index])
                    print(f"wrote {name}")
                elif event.key == pygame.K_TAB:
                    turbo = down

        if not paused:
            # In turbo, run several frames but only draw the last one.
            for _ in range(8 if turbo else 1):
                pixels = gameboy.run_frame()
            frames += 8 if turbo else 1

            # Four shades, so the RGB triples are precomputed and the frame is
            # one join rather than 23040 index assignments.
            table = rgb_tables[palette_index]
            buffer = b"".join([table[shade & 3] for shade in pixels])
            # convert() matches the display's pixel format; scaling between
            # mismatched formats is an error rather than a slow path.
            image = pygame.image.frombuffer(buffer, (WIDTH, HEIGHT), "RGB").convert()
            # Nearest-neighbour: smooth scaling turns pixel art into soup.
            panel = pygame.transform.scale(image, lcd_size)
            panel.blit(grid, (0, 0))
            _chrome(pygame, screen, lcd_rect, gameboy.title or "GAME BOY",
                    f"{clock.get_fps():.0f} fps" if not turbo else "fast forward")
            screen.blit(panel, lcd_rect)
            pygame.display.flip()

        clock.tick(0 if turbo else FPS)

    elapsed = time.perf_counter() - started
    print(f"{frames} frames in {elapsed:.1f}s ({frames / max(elapsed, 1e-6):.1f} fps)")
    return 1 if quit_all else 0


def home(*, scale: int = 4, palette=DMG_PALETTE, roms: str | None = None) -> int:
    """The menu, and whatever it launches, until the window is closed.

    `play` returns 0 when someone pressed escape and 1 when they closed the
    window, which is how the two exits are told apart: one comes back here,
    the other leaves.
    """
    try:
        import pygame
    except ImportError:
        print('the window needs pygame: pip install "gameboy[play]"')
        return 1

    from .machine import GameBoy
    from .shell import Menu, draw_home, find_roms, lcd_grid

    pygame.init()
    pygame.display.set_caption("gameboy")
    lcd_size = (WIDTH * scale, HEIGHT * scale)
    screen = pygame.display.set_mode((lcd_size[0] + PAD * 2, lcd_size[1] + PAD + FOOT))
    lcd_rect = pygame.Rect(PAD, PAD, *lcd_size)
    grid = lcd_grid(pygame, lcd_size, scale)
    clock = pygame.time.Clock()

    palettes = [DMG_PALETTE, GREY_PALETTE]
    index = palettes.index(palette) if palette in palettes else 0
    menu = Menu(find_roms(roms))
    tick = 0

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return 0
            if event.type != pygame.KEYDOWN:
                continue
            if event.key in (pygame.K_ESCAPE, pygame.K_q):
                pygame.quit()
                return 0
            if event.key == pygame.K_p:
                index = (index + 1) % len(palettes)
            elif event.key == pygame.K_UP:
                menu.move(-1)
            elif event.key == pygame.K_DOWN:
                menu.move(1)
            elif event.key in (pygame.K_RETURN, pygame.K_z):
                chosen = menu.selected
                if chosen is None:
                    continue
                machine = _machine_for(chosen, GameBoy)
                if machine is None:
                    continue
                # The window is rebuilt by play(); tear this one down first so
                # two displays never exist at once.
                pygame.display.quit()
                if play(machine, scale=scale, palette=palettes[index]):
                    return 0
                pygame.display.init()
                pygame.display.set_caption("gameboy")
                screen = pygame.display.set_mode(
                    (lcd_size[0] + PAD * 2, lcd_size[1] + PAD + FOOT)
                )

        tick += 1
        page = draw_home(pygame, menu, tick=tick)
        colours = palettes[index]
        page.set_palette([colours[i & 3] for i in range(256)])
        panel = pygame.transform.scale(page.convert(), lcd_size)
        panel.blit(grid, (0, 0))
        _chrome(pygame, screen, lcd_rect, "GAME BOY",
                f"{len(menu.entries)} cartridge{'s' if len(menu.entries) != 1 else ''}")
        screen.blit(panel, lcd_rect)
        pygame.display.flip()
        clock.tick(60)


def _machine_for(entry, GameBoy):
    """Load a cartridge, or say why it could not be loaded and carry on."""
    from .demo import build_demo_rom

    try:
        if entry.path is None:
            return GameBoy.from_bytes(build_demo_rom())
        with open(entry.path, "rb") as handle:
            return GameBoy.from_bytes(handle.read())
    except Exception as error:  # noqa: BLE001 - a bad ROM must not end the session
        print(f"could not load {entry.label}: {error}")
        return None


def launch(*, roms: str | None = None, palette=DMG_PALETTE) -> int:
    """The launcher: a sidebar, covers, and a cartridge you can start.

    This window is drawn at window resolution, unlike the console's own menu
    in `shell.py`, which is drawn at 160x144. The difference is deliberate:
    the menu is part of the handheld and should look like it, while the
    launcher is the application around the handheld and has a desktop's room
    to work in. Both are made of the same bitmap font, so they still read as
    one product.
    """
    try:
        import pygame
    except ImportError:
        print('the launcher needs pygame: pip install "gameboy[play]"')
        return 1

    from . import launcher
    from .library import Library
    from .shell import find_roms

    pygame.init()
    pygame.display.set_caption("gameboy")
    screen = pygame.display.set_mode(launcher.WINDOW, pygame.RESIZABLE)
    clock = pygame.time.Clock()

    library = Library.load()

    def progress(done: int, total: int, label: str) -> None:
        # Pumped by hand: generating covers blocks, and an unpumped window is
        # reported by macOS as not responding after a couple of seconds.
        pygame.event.pump()
        launcher.draw_loading(pygame, screen, done, total, label)
        pygame.display.flip()

    state = launcher.State(
        tiles=launcher.build_tiles(pygame, find_roms(roms), library, progress)
    )

    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                return 0
            if event.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode(
                    (max(event.w, 900), max(event.h, 620)), pygame.RESIZABLE
                )
                continue
            if event.type != pygame.KEYDOWN:
                continue

            if state.typing:
                if event.key in (pygame.K_RETURN, pygame.K_ESCAPE):
                    state.typing = False
                    if event.key == pygame.K_ESCAPE:
                        state.set_query("")
                elif event.key == pygame.K_BACKSPACE:
                    state.set_query(state.query[:-1])
                elif event.unicode and event.unicode.isprintable():
                    state.set_query(state.query + event.unicode)
                continue

            columns = state.columns(screen.get_width())
            if event.key == pygame.K_ESCAPE:
                if state.query:
                    state.set_query("")
                    continue
                pygame.quit()
                return 0
            elif event.key in (pygame.K_SLASH, pygame.K_f):
                state.typing = True
            elif event.key == pygame.K_TAB:
                state.switch(NAV_NEXT[state.view])
            elif event.key == pygame.K_LEFT:
                state.move(-1)
            elif event.key == pygame.K_RIGHT:
                state.move(1)
            elif event.key == pygame.K_UP:
                state.move(-columns)
            elif event.key == pygame.K_DOWN:
                state.move(columns)
            elif event.key in (pygame.K_RETURN, pygame.K_z, pygame.K_SPACE):
                tile = state.selected
                if tile is None:
                    continue
                _play_from_launcher(pygame, tile, library, palette)
                screen = pygame.display.set_mode(
                    screen.get_size(), pygame.RESIZABLE
                )
                pygame.display.set_caption("gameboy")
                record = library.records.get(tile.digest)
                if record is not None:
                    tile.played = record.played_label
                    tile.last_played = record.last_played

        launcher.render(pygame, screen, state, library)
        pygame.display.flip()
        clock.tick(60)


#: Tab cycles the sidebar, so the order lives in one place.
NAV_NEXT = {"Home": "Library", "Library": "Home"}


def _play_from_launcher(pygame, tile, library, palette) -> None:
    """Run a cartridge, then write down how long it was played for.

    The window is torn down first so two displays never exist at once, the
    same way the console's own menu hands over to `play`.
    """
    from .launcher import load_machine

    try:
        machine, _ = load_machine(tile)
    except Exception as error:  # noqa: BLE001 - a bad ROM must not end the session
        print(f"could not load {tile.label}: {error}")
        return
    pygame.display.quit()
    started = time.perf_counter()
    try:
        play(machine, palette=palette, title=f"gameboy — {tile.label}")
    finally:
        # Recorded even if the emulator raised, because the time was still
        # spent, and a crash that silently erases an hour is worse than a
        # crash.
        library.played(tile.digest, tile.label, time.perf_counter() - started)
        pygame.display.init()
