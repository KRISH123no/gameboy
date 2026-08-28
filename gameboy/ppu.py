"""The picture processing unit.

The Game Boy has no framebuffer. There is nowhere in memory holding the image
on screen — the hardware builds each of the 144 lines as the beam reaches it,
out of tiles, and then throws it away. A game changes the scroll register
between two lines and the top half of the screen scrolls independently of the
bottom. That is not a trick, it is just what the hardware does.

So this renders line by line, on the same clock the CPU runs on:

    mode 2   80 cycles   scanning sprites for this line
    mode 3  172 cycles   drawing it
    mode 0  204 cycles   horizontal blank
                         456 total, times 144 lines
    mode 1              ten more lines of vertical blank

Everything a tile-based console does — scrolling, sprites, the status bar that
does not move while the world does — falls out of those numbers.
"""

from __future__ import annotations

WIDTH = 160
HEIGHT = 144

OAM_CYCLES = 80
DRAW_CYCLES = 172
HBLANK_CYCLES = 204
LINE_CYCLES = OAM_CYCLES + DRAW_CYCLES + HBLANK_CYCLES  # 456
VBLANK_LINES = 10
TOTAL_LINES = HEIGHT + VBLANK_LINES  # 154

MODE_HBLANK = 0
MODE_VBLANK = 1
MODE_OAM = 2
MODE_DRAW = 3

INT_VBLANK = 0x01
INT_LCD_STAT = 0x02

#: Ten sprites per line, and no more. Games exploit this: put an eleventh
#: sprite on a line and it vanishes, which is how flicker effects were made.
MAX_SPRITES_PER_LINE = 10


class PPU:
    def __init__(self, request_interrupt=None) -> None:
        self.vram = bytearray(0x2000)
        self.oam = bytearray(0xA0)
        self._request_interrupt = request_interrupt or (lambda bit: None)

        # Registers
        self.lcdc = 0x91
        self.stat = 0x00
        self.scy = 0
        self.scx = 0
        self.ly = 0
        self.lyc = 0
        self.bgp = 0xFC
        self.obp0 = 0xFF
        self.obp1 = 0xFF
        self.wy = 0
        self.wx = 0

        self.mode = MODE_OAM
        self.dot = 0
        #: Shade 0-3 per pixel. Palette is applied here, so the front end only
        #: has to map four greys to four colours.
        self.framebuffer = bytearray(WIDTH * HEIGHT)
        #: Set true for one step when a full frame is ready to be shown.
        self.frame_ready = False
        self.frames = 0
        #: The window has its own line counter that only advances on lines
        #: where it is actually drawn — forgetting this shears the window.
        self._window_line = 0

    # -------------------------------------------------------------- registers

    @property
    def lcd_on(self) -> bool:
        return bool(self.lcdc & 0x80)

    def read_register(self, address: int) -> int:
        if address == 0xFF40:
            return self.lcdc
        if address == 0xFF41:
            # The low three bits are the live mode and coincidence flag, so
            # they are computed rather than stored.
            coincidence = 0x04 if self.ly == self.lyc else 0x00
            return 0x80 | (self.stat & 0x78) | coincidence | self.mode
        if address == 0xFF42:
            return self.scy
        if address == 0xFF43:
            return self.scx
        if address == 0xFF44:
            return self.ly
        if address == 0xFF45:
            return self.lyc
        if address == 0xFF47:
            return self.bgp
        if address == 0xFF48:
            return self.obp0
        if address == 0xFF49:
            return self.obp1
        if address == 0xFF4A:
            return self.wy
        if address == 0xFF4B:
            return self.wx
        return 0xFF

    def write_register(self, address: int, value: int) -> None:
        value &= 0xFF
        if address == 0xFF40:
            was_on = self.lcd_on
            self.lcdc = value
            if was_on and not self.lcd_on:
                # Switching the LCD off resets the counters. Games do this to
                # rewrite video memory safely.
                self.ly = 0
                self.dot = 0
                self.mode = MODE_HBLANK
                self._window_line = 0
                self.framebuffer = bytearray(WIDTH * HEIGHT)
        elif address == 0xFF41:
            self.stat = value & 0x78
        elif address == 0xFF42:
            self.scy = value
        elif address == 0xFF43:
            self.scx = value
        elif address == 0xFF44:
            self.ly = 0  # writing LY resets it, whatever was written
        elif address == 0xFF45:
            self.lyc = value
        elif address == 0xFF47:
            self.bgp = value
        elif address == 0xFF48:
            self.obp0 = value
        elif address == 0xFF49:
            self.obp1 = value
        elif address == 0xFF4A:
            self.wy = value
        elif address == 0xFF4B:
            self.wx = value

    # ------------------------------------------------------------------ memory

    def read_vram(self, address: int) -> int:
        return self.vram[address - 0x8000]

    def write_vram(self, address: int, value: int) -> None:
        self.vram[address - 0x8000] = value & 0xFF

    def read_oam(self, address: int) -> int:
        return self.oam[address - 0xFE00]

    def write_oam(self, address: int, value: int) -> None:
        self.oam[address - 0xFE00] = value & 0xFF

    # -------------------------------------------------------------------- step

    def step(self, t_cycles: int) -> None:
        """Advance the beam. Called with the cycles the CPU just consumed."""
        if not self.lcd_on:
            return

        self.dot += t_cycles
        while self.dot >= LINE_CYCLES:
            self.dot -= LINE_CYCLES
            self._end_of_line()

        if self.ly >= HEIGHT:
            self._set_mode(MODE_VBLANK)
        elif self.dot < OAM_CYCLES:
            self._set_mode(MODE_OAM)
        elif self.dot < OAM_CYCLES + DRAW_CYCLES:
            self._set_mode(MODE_DRAW)
        else:
            self._set_mode(MODE_HBLANK)

    def _end_of_line(self) -> None:
        if self.ly < HEIGHT:
            # The line is rendered in one go at its end. A game that changes
            # SCX mid-line will not see the split, which is the one visual
            # effect this simplification gives up.
            self.render_line(self.ly)

        self.ly += 1
        if self.ly == HEIGHT:
            self.frames += 1
            self.frame_ready = True
            self._request_interrupt(INT_VBLANK)
            if self.stat & 0x10:
                self._request_interrupt(INT_LCD_STAT)
        elif self.ly >= TOTAL_LINES:
            self.ly = 0
            self._window_line = 0

        if self.ly == self.lyc and (self.stat & 0x40):
            self._request_interrupt(INT_LCD_STAT)

    def _set_mode(self, mode: int) -> None:
        if mode == self.mode:
            return
        self.mode = mode
        # Each mode can raise the STAT interrupt, if the game asked for it.
        if mode == MODE_HBLANK and self.stat & 0x08:
            self._request_interrupt(INT_LCD_STAT)
        elif mode == MODE_OAM and self.stat & 0x20:
            self._request_interrupt(INT_LCD_STAT)

    # ------------------------------------------------------------------ render

    def render_line(self, line: int) -> None:
        row = line * WIDTH
        # Shade 0 everywhere first: with the background disabled the screen is
        # white, and sprites still draw over it.
        self.framebuffer[row : row + WIDTH] = bytes(WIDTH)

        background_indices = [0] * WIDTH
        if self.lcdc & 0x01:
            self._render_background(line, background_indices)
            if (self.lcdc & 0x20) and line >= self.wy:
                self._render_window(line, background_indices)
        if self.lcdc & 0x02:
            self._render_sprites(line, background_indices)

    def _tile_row(self, tile_index: int, y: int, signed: bool) -> tuple[int, int]:
        """The two bitplane bytes for one row of a tile."""
        if signed and tile_index > 127:
            tile_index -= 256
        base = (0x1000 + tile_index * 16) if signed else (tile_index * 16)
        offset = (base + y * 2) & 0x1FFF
        return self.vram[offset], self.vram[(offset + 1) & 0x1FFF]

    def _tile_pixel(self, tile_index: int, x: int, y: int, signed: bool) -> int:
        """Two bits per pixel, split across two bytes — one holds the low bit
        of every pixel in the row, the next holds the high bit."""
        if signed and tile_index > 127:
            tile_index -= 256
        base = (0x1000 + tile_index * 16) if signed else (tile_index * 16)
        offset = base + y * 2
        low = self.vram[offset & 0x1FFF]
        high = self.vram[(offset + 1) & 0x1FFF]
        bit = 7 - x
        return ((high >> bit) & 1) << 1 | ((low >> bit) & 1)

    def _render_background(self, line: int, indices: list[int]) -> None:
        map_base = 0x1C00 if (self.lcdc & 0x08) else 0x1800
        signed = not (self.lcdc & 0x10)
        y = (line + self.scy) & 0xFF
        tile_row = (y // 8) * 32
        fine_y = y % 8
        row = line * WIDTH
        vram = self.vram
        framebuffer = self.framebuffer
        palette = self.bgp

        # One tile at a time: the two bitplane bytes are fetched once per eight
        # pixels instead of once per pixel, which is most of the render cost.
        x = 0
        scrolled = self.scx & 0xFF
        while x < WIDTH:
            tile = vram[map_base + tile_row + (scrolled >> 3)]
            low, high = self._tile_row(tile, fine_y, signed)
            start_bit = scrolled & 7
            for bit in range(start_bit, 8):
                if x >= WIDTH:
                    break
                shift = 7 - bit
                index = ((high >> shift) & 1) << 1 | ((low >> shift) & 1)
                indices[x] = index
                framebuffer[row + x] = (palette >> (index * 2)) & 0x03
                x += 1
            scrolled = (scrolled + (8 - start_bit)) & 0xFF

    def _render_window(self, line: int, indices: list[int]) -> None:
        left = self.wx - 7
        if left >= WIDTH:
            return
        map_base = 0x1C00 if (self.lcdc & 0x40) else 0x1800
        signed = not (self.lcdc & 0x10)
        y = self._window_line
        tile_row = (y // 8) * 32
        row = line * WIDTH

        drawn = False
        for x in range(max(0, left), WIDTH):
            window_x = x - left
            tile = self.vram[map_base + tile_row + (window_x // 8)]
            index = self._tile_pixel(tile, window_x % 8, y % 8, signed)
            indices[x] = index
            self.framebuffer[row + x] = (self.bgp >> (index * 2)) & 0x03
            drawn = True

        if drawn:
            self._window_line += 1

    def _render_sprites(self, line: int, background_indices: list[int]) -> None:
        height = 16 if (self.lcdc & 0x04) else 8
        row = line * WIDTH

        # Gather the sprites on this line, keeping only the first ten in OAM
        # order, exactly as the hardware does.
        visible = []
        for entry in range(40):
            base = entry * 4
            sprite_y = self.oam[base] - 16
            if sprite_y <= line < sprite_y + height:
                visible.append((self.oam[base + 1] - 8, entry, base, sprite_y))
                if len(visible) == MAX_SPRITES_PER_LINE:
                    break

        # Lower X wins; ties go to the earlier OAM entry. Drawing in reverse
        # priority order lets the winner simply overwrite.
        visible.sort(key=lambda item: (item[0], item[1]), reverse=True)

        for sprite_x, _entry, base, sprite_y in visible:
            tile = self.oam[base + 2]
            flags = self.oam[base + 3]
            if height == 16:
                tile &= 0xFE

            y = line - sprite_y
            if flags & 0x40:  # vertical flip
                y = height - 1 - y
            if y >= 8:
                tile += 1
                y -= 8

            palette = self.obp1 if (flags & 0x10) else self.obp0
            behind = bool(flags & 0x80)

            for x in range(8):
                screen_x = sprite_x + x
                if not 0 <= screen_x < WIDTH:
                    continue
                index = self._tile_pixel(tile, 7 - x if (flags & 0x20) else x, y, False)
                if index == 0:
                    continue  # colour 0 is transparent for sprites
                if behind and background_indices[screen_x] != 0:
                    continue
                self.framebuffer[row + screen_x] = (palette >> (index * 2)) & 0x03

    # ------------------------------------------------------------------- views

    def take_frame(self) -> bytes:
        self.frame_ready = False
        return bytes(self.framebuffer)
