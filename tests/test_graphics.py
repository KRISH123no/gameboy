"""The PPU, the disassembler, and the whole machine running a real cartridge."""

import pytest

from gameboy.demo import Assembler, build_demo_rom
from gameboy.disassembler import disassemble, disassemble_range
from gameboy.machine import GameBoy
from gameboy.png import to_ascii
from gameboy.ppu import HEIGHT, LINE_CYCLES, PPU, WIDTH


def solid_tile(shade: int) -> bytes:
    return bytes([0xFF if shade & 1 else 0x00, 0xFF if shade & 2 else 0x00] * 8)


@pytest.fixture
def ppu():
    unit = PPU()
    unit.lcdc = 0x91  # LCD on, background on, tile data at 0x8000
    unit.bgp = 0xE4  # identity palette
    for index, byte in enumerate(b"".join(solid_tile(s) for s in range(4))):
        unit.write_vram(0x8000 + index, byte)
    return unit


def fill_map(ppu, tile: int, base: int = 0x9800):
    for offset in range(32 * 32):
        ppu.write_vram(base + offset, tile)


# -------------------------------------------------------------------- timing


def test_the_beam_walks_down_the_screen(ppu):
    ppu.step(LINE_CYCLES)
    assert ppu.ly == 1
    ppu.step(LINE_CYCLES * 100)
    assert ppu.ly == 101


def test_a_frame_is_154_lines(ppu):
    ppu.step(LINE_CYCLES * 154)
    assert ppu.ly == 0
    assert ppu.frames == 1


def test_vblank_is_requested_once_per_frame(ppu):
    fired = []
    ppu._request_interrupt = fired.append
    ppu.step(LINE_CYCLES * 154)
    assert fired.count(0x01) == 1


def test_the_mode_cycles_through_the_line(ppu):
    ppu.step(10)
    assert ppu.mode == 2, "scanning sprites"
    ppu.step(100)
    assert ppu.mode == 3, "drawing"
    ppu.step(200)
    assert ppu.mode == 0, "horizontal blank"


def test_nothing_advances_while_the_lcd_is_off(ppu):
    ppu.lcdc = 0x00
    ppu.step(LINE_CYCLES * 10)
    assert ppu.ly == 0


def test_the_coincidence_flag_tracks_lyc(ppu):
    ppu.lyc = 3
    ppu.step(LINE_CYCLES * 3)
    assert ppu.read_register(0xFF41) & 0x04


# ------------------------------------------------------------------- drawing


def test_the_background_is_drawn_from_the_tilemap(ppu):
    fill_map(ppu, 2)
    ppu.render_line(0)
    assert set(ppu.framebuffer[:WIDTH]) == {2}


def test_the_palette_remaps_the_shades(ppu):
    fill_map(ppu, 1)
    ppu.bgp = 0b11_11_11_11  # every index becomes shade 3
    ppu.render_line(0)
    assert set(ppu.framebuffer[:WIDTH]) == {3}


def test_scrolling_moves_the_picture(ppu):
    for offset in range(32 * 32):
        ppu.write_vram(0x9800 + offset, (offset % 32) % 4)
    ppu.render_line(0)
    unscrolled = bytes(ppu.framebuffer[:WIDTH])
    ppu.scx = 8
    ppu.render_line(0)
    assert bytes(ppu.framebuffer[:WIDTH]) != unscrolled


def test_scrolling_by_eight_shifts_by_one_tile(ppu):
    for offset in range(32 * 32):
        ppu.write_vram(0x9800 + offset, (offset % 32) % 4)
    ppu.render_line(0)
    first = bytes(ppu.framebuffer[:WIDTH])
    ppu.scx = 8
    ppu.render_line(0)
    shifted = bytes(ppu.framebuffer[:WIDTH])
    assert shifted[:WIDTH - 8] == first[8:]


def test_a_disabled_background_leaves_the_screen_blank(ppu):
    fill_map(ppu, 3)
    ppu.lcdc &= ~0x01
    ppu.render_line(0)
    assert set(ppu.framebuffer[:WIDTH]) == {0}


def test_the_second_tilemap_can_be_selected(ppu):
    fill_map(ppu, 1)
    fill_map(ppu, 3, base=0x9C00)
    ppu.lcdc |= 0x08
    ppu.render_line(0)
    assert set(ppu.framebuffer[:WIDTH]) == {3}


# ------------------------------------------------------------------- sprites


def place_sprite(ppu, x, y, tile=1, flags=0, slot=0):
    base = 0xFE00 + slot * 4
    ppu.write_oam(base, y + 16)
    ppu.write_oam(base + 1, x + 8)
    ppu.write_oam(base + 2, tile)
    ppu.write_oam(base + 3, flags)


def test_a_sprite_is_drawn_over_the_background(ppu):
    fill_map(ppu, 0)
    ppu.obp0 = 0xE4
    ppu.lcdc |= 0x02
    place_sprite(ppu, x=16, y=0, tile=3)
    ppu.render_line(0)
    assert set(ppu.framebuffer[16:24]) == {3}
    assert ppu.framebuffer[0] == 0, "the background is untouched elsewhere"


def test_sprite_colour_zero_is_transparent(ppu):
    fill_map(ppu, 2)
    ppu.lcdc |= 0x02
    place_sprite(ppu, x=16, y=0, tile=0)  # an all-colour-0 tile
    ppu.render_line(0)
    assert set(ppu.framebuffer[16:24]) == {2}, "the background shows through"


def test_sprites_are_hidden_behind_the_background_when_asked(ppu):
    fill_map(ppu, 2)
    ppu.obp0 = 0xE4
    ppu.lcdc |= 0x02
    place_sprite(ppu, x=16, y=0, tile=3, flags=0x80)  # priority bit
    ppu.render_line(0)
    assert set(ppu.framebuffer[16:24]) == {2}


def test_only_ten_sprites_are_drawn_on_a_line(ppu):
    """The eleventh vanishes. Games used this deliberately, for flicker."""
    fill_map(ppu, 0)
    ppu.obp0 = 0xE4
    ppu.lcdc |= 0x02
    for slot in range(12):
        place_sprite(ppu, x=slot * 8, y=0, tile=3, slot=slot)
    ppu.render_line(0)
    drawn = sum(1 for x in range(0, 96, 8) if ppu.framebuffer[x] == 3)
    assert drawn == 10


def test_a_sprite_can_be_flipped(ppu):
    """Tile 3's rows are asymmetric, so a flip is visible."""
    for index, byte in enumerate(bytes([0b1000_0000, 0b1000_0000] * 8)):
        ppu.write_vram(0x8000 + 5 * 16 + index, byte)
    fill_map(ppu, 0)
    ppu.obp0 = 0xE4
    ppu.lcdc |= 0x02
    place_sprite(ppu, x=0, y=0, tile=5)
    ppu.render_line(0)
    assert ppu.framebuffer[0] == 3 and ppu.framebuffer[7] == 0

    place_sprite(ppu, x=0, y=0, tile=5, flags=0x20)  # horizontal flip
    ppu.render_line(0)
    assert ppu.framebuffer[7] == 3 and ppu.framebuffer[0] == 0


def test_tall_sprites_cover_sixteen_rows(ppu):
    fill_map(ppu, 0)
    ppu.obp0 = 0xE4
    ppu.lcdc |= 0x06  # sprites on, 8x16
    place_sprite(ppu, x=0, y=0, tile=2)
    ppu.render_line(12)
    assert ppu.framebuffer[12 * WIDTH] != 0


# -------------------------------------------------------------------- window


def test_the_window_covers_the_background(ppu):
    fill_map(ppu, 1)
    fill_map(ppu, 3, base=0x9C00)
    ppu.lcdc |= 0x60  # window on, using the second map
    ppu.wy = 0
    ppu.wx = 7  # flush left
    ppu.render_line(0)
    assert set(ppu.framebuffer[:WIDTH]) == {3}


def test_the_window_does_not_start_before_its_line(ppu):
    fill_map(ppu, 1)
    fill_map(ppu, 3, base=0x9C00)
    ppu.lcdc |= 0x60
    ppu.wy = 100
    ppu.wx = 7
    ppu.render_line(0)
    assert set(ppu.framebuffer[:WIDTH]) == {1}


# -------------------------------------------------------------- disassembler


@pytest.mark.parametrize(
    "raw,text",
    [
        (b"\x00", "NOP"),
        (b"\x3e\x42", "LD A,$42"),
        (b"\x21\x34\x12", "LD HL,$1234"),
        (b"\xc3\x50\x01", "JP $0150"),
        (b"\xcd\x00\xc0", "CALL $C000"),
        (b"\x76", "HALT"),
        (b"\xcb\x7c", "BIT 7,H"),
        (b"\xcb\x30", "SWAP B"),
        (b"\xe0\x47", "LDH ($FF47),A"),
        (b"\x22", "LD (HL+),A"),
        (b"\xff", "RST $38"),
        (b"\xd9", "RETI"),
        (b"\xe8\xfe", "ADD SP,-2"),
    ],
)
def test_instructions_disassemble(raw, text):
    memory = bytearray(0x10000)
    memory[0x1000 : 0x1000 + len(raw)] = raw
    assert disassemble(lambda a: memory[a], 0x1000).text == text


def test_a_relative_jump_shows_its_destination():
    memory = bytearray(0x10000)
    memory[0x1000:0x1002] = b"\x18\xfe"  # JR -2
    assert disassemble(lambda a: memory[a], 0x1000).text == "JR $1000"


def test_disassembly_walks_forward_by_instruction_length():
    memory = bytearray(0x10000)
    memory[0x1000:0x1006] = b"\x00\x21\x34\x12\x00\x00"
    listing = disassemble_range(lambda a: memory[a], 0x1000, 3)
    assert [i.address for i in listing] == [0x1000, 0x1001, 0x1004]


# ------------------------------------------------------------------ assembler


def test_the_assembler_resolves_a_backward_jump():
    a = Assembler(origin=0x0150)
    a.label("top")
    a.emit(0x00)
    a.jr("top")
    assert a.assemble() == bytes([0x00, 0x18, 0xFD])


def test_a_jump_that_is_too_far_is_refused():
    a = Assembler()
    a.label("top")
    a.emit(*([0x00] * 200))
    a.jr("top")
    with pytest.raises(ValueError):
        a.assemble()


# --------------------------------------------------------- the whole machine


def test_the_demo_cartridge_is_valid():
    gameboy = GameBoy.from_bytes(build_demo_rom())
    assert gameboy.mmu.cartridge.header.checksum_ok
    assert gameboy.title == "GBDEMO"


def test_the_cpu_builds_the_screen_by_itself():
    """End to end: nothing here touches video memory from Python."""
    gameboy = GameBoy.from_bytes(build_demo_rom())
    frame = gameboy.run_frames(3)

    assert gameboy.ppu.lcdc & 0x80, "the ROM turned the LCD on"
    # Tile 0 is deliberately blank, so check tile 1's bytes instead.
    assert any(gameboy.ppu.vram[16:32]), "the ROM copied tiles into video memory"
    assert any(gameboy.ppu.vram[0x1800:0x1C00]), "and built the tilemap"
    assert len(set(frame)) > 1, "the screen is not one flat colour"
    assert len(frame) == WIDTH * HEIGHT


def test_the_demo_animates():
    gameboy = GameBoy.from_bytes(build_demo_rom())
    first = gameboy.run_frames(4)
    later = gameboy.run_frames(30)
    assert first != later
    assert gameboy.ppu.scx > 0, "the background scrolled"


def test_the_sprite_moves_and_bobs():
    gameboy = GameBoy.from_bytes(build_demo_rom())
    gameboy.run_frames(4)
    positions = []
    for _ in range(6):
        gameboy.run_frames(8)
        positions.append((gameboy.ppu.oam[1], gameboy.ppu.oam[0]))
    xs = [x for x, _ in positions]
    ys = [y for _, y in positions]
    assert xs == sorted(xs) and xs[0] != xs[-1], "it walks right"
    assert len(set(ys)) > 1, "and it bobs"


def test_a_frame_can_be_rendered_as_text():
    gameboy = GameBoy.from_bytes(build_demo_rom())
    art = to_ascii(gameboy.run_frames(3), WIDTH, HEIGHT)
    assert art.count("\n") > 10
