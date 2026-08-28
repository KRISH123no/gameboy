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
KEY_MAP = {
    "up": Button.UP,
    "down": Button.DOWN,
    "left": Button.LEFT,
    "right": Button.RIGHT,
    "z": Button.A,
    "x": Button.B,
    "return": Button.START,
    "rshift": Button.SELECT,
}

HELP = """
  arrows  d-pad          z  A          x  B
  enter   start          shift  select
  space   pause          tab (hold)  fast forward
  p       switch palette f2  save a screenshot
  esc     quit
"""


def play(gameboy: GameBoy, *, scale: int = 4, palette=DMG_PALETTE, title: str | None = None) -> int:
    try:
        import pygame
    except ImportError:
        print('the window needs pygame: pip install "gameboy[play]"')
        return 1

    pygame.init()
    pygame.display.set_caption(title or f"gameboy — {gameboy.title}")
    screen = pygame.display.set_mode((WIDTH * scale, HEIGHT * scale))
    clock = pygame.time.Clock()
    print(HELP)

    keys = {getattr(pygame, f"K_{name}"): button for name, button in KEY_MAP.items()}
    palettes = [DMG_PALETTE, GREY_PALETTE]
    palette_index = palettes.index(palette) if palette in palettes else 0
    rgb_tables = [[bytes(colour) for colour in option] for option in palettes]
    paused = False
    turbo = False
    shots = 0
    running = True
    started = time.perf_counter()
    frames = 0

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
                down = event.type == pygame.KEYDOWN
                if event.key in keys:
                    button = keys[event.key]
                    gameboy.press(button) if down else gameboy.release(button)
                elif down and event.key == pygame.K_ESCAPE:
                    running = False
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
            pygame.transform.scale(image, screen.get_size(), screen)
            pygame.display.flip()

        clock.tick(0 if turbo else FPS)

    elapsed = time.perf_counter() - started
    pygame.quit()
    print(f"{frames} frames in {elapsed:.1f}s ({frames / max(elapsed, 1e-6):.1f} fps)")
    return 0
