"""Cartridges, the memory map, the timer and the joypad."""

import pytest

from gameboy.cartridge import MBC1, MBC3, MBC5, UnsupportedCartridge, load_cartridge, parse_header
from gameboy.joypad import Button, Joypad
from gameboy.mmu import MMU
from gameboy.ppu import PPU
from gameboy.timer import Timer


def make_rom(cartridge_type: int = 0x00, banks: int = 2, ram_code: int = 0x00) -> bytes:
    size_code = max(0, banks.bit_length() - 2)
    rom = bytearray(0x4000 * banks)
    rom[0x0100:0x0104] = bytes([0x00, 0xC3, 0x50, 0x01])
    rom[0x0134:0x0143] = b"TEST".ljust(15, b"\x00")
    rom[0x0147] = cartridge_type
    rom[0x0148] = size_code
    rom[0x0149] = ram_code
    checksum = 0
    for address in range(0x0134, 0x014D):
        checksum = (checksum - rom[address] - 1) & 0xFF
    rom[0x014D] = checksum
    # Stamp each bank with its own number so banking is observable.
    for bank in range(banks):
        rom[bank * 0x4000 + 0x0010] = bank
    return bytes(rom)


# ------------------------------------------------------------------- header


def test_header_is_parsed():
    header = parse_header(make_rom(0x13, banks=8, ram_code=0x03))
    assert header.title == "TEST"
    assert header.controller == "MBC3+RAM+BATTERY"
    assert header.rom_banks == 8
    assert header.ram_banks == 4
    assert header.checksum_ok


def test_a_damaged_header_is_reported_not_hidden():
    rom = bytearray(make_rom())
    rom[0x014D] ^= 0xFF
    assert parse_header(bytes(rom)).checksum_ok is False


def test_a_truncated_file_is_rejected():
    with pytest.raises(ValueError):
        parse_header(b"\x00" * 16)


@pytest.mark.parametrize(
    "cartridge_type,expected",
    [(0x00, "Cartridge"), (0x01, "MBC1"), (0x13, "MBC3"), (0x1B, "MBC5")],
)
def test_the_right_controller_is_chosen(cartridge_type, expected):
    cartridge = load_cartridge(make_rom(cartridge_type, banks=4, ram_code=0x02))
    assert type(cartridge).__name__ == expected


def test_an_unknown_controller_is_refused_clearly():
    with pytest.raises(UnsupportedCartridge):
        load_cartridge(make_rom(0x05))  # MBC2, not implemented


# ------------------------------------------------------------------ banking


def test_a_plain_cartridge_ignores_writes_to_rom():
    cartridge = load_cartridge(make_rom(0x00))
    cartridge.write_rom(0x2000, 0x05)
    assert cartridge.read_rom(0x4010) == 1, "still bank 1"


def test_mbc1_switches_the_upper_bank():
    cartridge = load_cartridge(make_rom(0x01, banks=8))
    assert isinstance(cartridge, MBC1)
    for bank in (1, 3, 7):
        cartridge.write_rom(0x2000, bank)
        assert cartridge.read_rom(0x4010) == bank
    assert cartridge.read_rom(0x0010) == 0, "the lower half never moves"


def test_mbc1_turns_bank_zero_into_bank_one():
    """Asking for bank 0 through this register gives bank 1 on real hardware."""
    cartridge = load_cartridge(make_rom(0x01, banks=4))
    cartridge.write_rom(0x2000, 0x00)
    assert cartridge.read_rom(0x4010) == 1


def test_cartridge_ram_needs_unlocking_first():
    cartridge = load_cartridge(make_rom(0x03, banks=4, ram_code=0x02))
    cartridge.write_ram(0x0000, 0x42)
    assert cartridge.read_ram(0x0000) == 0xFF, "locked RAM reads as open bus"

    cartridge.write_rom(0x0000, 0x0A)  # the magic unlock value
    cartridge.write_ram(0x0000, 0x42)
    assert cartridge.read_ram(0x0000) == 0x42


def test_mbc3_reaches_high_banks():
    cartridge = load_cartridge(make_rom(0x13, banks=64, ram_code=0x03))
    assert isinstance(cartridge, MBC3)
    cartridge.write_rom(0x2000, 60)
    assert cartridge.read_rom(0x4010) == 60


def test_mbc5_can_select_bank_zero():
    """Unlike MBC1, bank 0 here means bank 0."""
    cartridge = load_cartridge(make_rom(0x1B, banks=8, ram_code=0x02))
    assert isinstance(cartridge, MBC5)
    cartridge.write_rom(0x2000, 0)
    assert cartridge.read_rom(0x4010) == 0


# ----------------------------------------------------------------- memory map


@pytest.fixture
def mmu():
    unit = MMU(load_cartridge(make_rom()))
    unit.ppu = PPU()
    return unit


def test_work_ram_round_trips(mmu):
    mmu.write8(0xC123, 0x99)
    assert mmu.read8(0xC123) == 0x99


def test_echo_ram_mirrors_work_ram(mmu):
    """An 8 KB mirror that exists only because a wire was left unconnected."""
    mmu.write8(0xC000, 0x77)
    assert mmu.read8(0xE000) == 0x77
    mmu.write8(0xE100, 0x55)
    assert mmu.read8(0xC100) == 0x55


def test_the_unusable_region_reads_as_open_bus(mmu):
    mmu.write8(0xFEB0, 0x12)
    assert mmu.read8(0xFEB0) == 0xFF


def test_rom_is_read_only_through_the_bus(mmu):
    before = mmu.read8(0x0100)
    mmu.write8(0x0100, before ^ 0xFF)
    assert mmu.read8(0x0100) == before


def test_video_memory_reaches_the_ppu(mmu):
    mmu.write8(0x8010, 0x3C)
    assert mmu.ppu.read_vram(0x8010) == 0x3C
    assert mmu.read8(0x8010) == 0x3C


def test_dma_copies_into_the_sprite_table(mmu):
    for index in range(0xA0):
        mmu.write8(0xC200 + index, index)
    mmu.write8(0xFF46, 0xC2)  # start a DMA from 0xC200
    assert mmu.read8(0xFE00) == 0
    assert mmu.read8(0xFE9F) == 0x9F


def test_the_serial_port_captures_what_test_roms_print(mmu):
    for character in b"OK":
        mmu.write8(0xFF01, character)
        mmu.write8(0xFF02, 0x81)  # begin transfer
    assert mmu.serial_text == "OK"


def test_the_top_bits_of_the_interrupt_flag_read_high(mmu):
    mmu.write8(0xFF0F, 0x00)
    assert mmu.read8(0xFF0F) == 0xE0


def test_high_ram_and_interrupt_enable(mmu):
    mmu.write8(0xFF80, 0x11)
    assert mmu.read8(0xFF80) == 0x11
    mmu.write8(0xFFFF, 0x1F)
    assert mmu.read8(0xFFFF) == 0x1F


# ---------------------------------------------------------------------- timer


def test_div_counts_up_on_its_own():
    timer = Timer()
    timer.step(256)
    assert timer.div == 1
    timer.step(256 * 5)
    assert timer.div == 6


def test_writing_div_clears_the_whole_internal_counter():
    timer = Timer()
    timer.step(1000)
    timer.write(0xFF04, 0x99)  # the value written is irrelevant
    assert timer.div == 0
    assert timer.counter == 0


def test_a_disabled_timer_does_not_tick():
    timer = Timer()
    timer.write(0xFF07, 0x00)  # enable bit clear
    timer.step(100_000)
    assert timer.tima == 0


@pytest.mark.parametrize(
    "tac,cycles_per_tick", [(0x04, 1024), (0x05, 16), (0x06, 64), (0x07, 256)]
)
def test_each_clock_setting_has_its_own_rate(tac, cycles_per_tick):
    timer = Timer()
    timer.write(0xFF07, tac)
    timer.step(cycles_per_tick * 3)
    assert timer.tima == 3


def test_overflow_reloads_from_tma_and_fires_an_interrupt():
    fired = []
    timer = Timer(fired.append)
    timer.write(0xFF06, 0xF0)  # TMA
    timer.write(0xFF05, 0xFF)  # TIMA, one short of overflow
    timer.write(0xFF07, 0x05)  # enabled, fastest
    timer.step(16)
    assert timer.tima == 0xF0
    assert fired == [0x04]


def test_a_long_step_counts_every_tick():
    """The arithmetic version must agree with counting one cycle at a time."""
    fast = Timer()
    fast.write(0xFF07, 0x05)
    fast.step(16 * 200)

    slow = Timer()
    slow.write(0xFF07, 0x05)
    for _ in range(16 * 200):
        slow.step(1)

    assert fast.tima == slow.tima


# --------------------------------------------------------------------- joypad


def test_a_pressed_button_reads_as_zero():
    """The buttons are wired to ground, so pressed means low."""
    joypad = Joypad()
    joypad.write(0x10)  # select the action buttons
    assert joypad.read() & 0x01 == 0x01
    joypad.press(Button.A)
    assert joypad.read() & 0x01 == 0x00


def test_the_two_button_groups_are_read_separately():
    joypad = Joypad()
    joypad.press(Button.RIGHT)
    joypad.write(0x20)  # directions
    assert joypad.read() & 0x01 == 0x00
    joypad.write(0x10)  # actions — nothing pressed there
    assert joypad.read() & 0x01 == 0x01


def test_pressing_a_button_raises_an_interrupt():
    fired = []
    joypad = Joypad(fired.append)
    joypad.press(Button.START)
    assert fired == [0x10]
    joypad.press(Button.START)
    assert fired == [0x10], "holding it is not a new press"


def test_releasing_restores_the_bit():
    joypad = Joypad()
    joypad.write(0x10)
    joypad.press(Button.B)
    joypad.release(Button.B)
    assert joypad.read() & 0x02 == 0x02


# ------------------------------------------------------------- serial port


def _serial_mmu():
    return MMU(load_cartridge(make_rom()))


def test_an_internally_clocked_transfer_completes():
    """$81 is how a test ROM prints: start a transfer on our own clock."""
    unit = _serial_mmu()
    unit.write8(0xFF01, ord("A"))
    unit.write8(0xFF02, 0x81)
    assert unit.serial_text == "A"
    assert unit.interrupt_flags & 0x08


def test_the_start_bit_clears_once_the_transfer_is_over():
    unit = _serial_mmu()
    unit.write8(0xFF01, 0x42)
    unit.write8(0xFF02, 0x81)
    assert unit.read8(0xFF02) & 0x80 == 0


def test_an_externally_clocked_transfer_never_completes():
    """The regression that broke three games.

    Bit 0 of $FF02 chooses the clock: 1 is this Game Boy, 0 is the one on
    the other end of the cable. With nothing plugged in, a transfer on the
    external clock can never finish, so no byte is sent and no interrupt is
    raised. Completing it anyway told every game that probes for a link
    cable that a second player had answered — Tobu Tobu Girl, Deadeus and
    Infinity all do this at boot, and all three went into a two-player
    handshake that could not be completed and crashed.
    """
    unit = _serial_mmu()
    unit.write8(0xFF01, ord("A"))
    unit.write8(0xFF02, 0x80)
    assert unit.serial_text == ""
    assert unit.interrupt_flags & 0x08 == 0


def test_a_pending_external_transfer_keeps_its_start_bit():
    """It is still waiting, so the game can see that it never finished."""
    unit = _serial_mmu()
    unit.write8(0xFF02, 0x80)
    assert unit.read8(0xFF02) & 0x80


# --------------------------------------------------- the manufacturer code


def _titled(text: bytes, cgb: int = 0x00) -> bytes:
    rom = bytearray(make_rom())
    rom[0x0134:0x0144] = text.ljust(0x10, b"\x00")[:0x10]
    rom[0x0143] = cgb
    return bytes(rom)


def test_a_short_title_is_left_exactly_as_it_is():
    assert parse_header(_titled(b"TOBU")).title == "TOBU"
    assert parse_header(_titled(b"TOBU")).manufacturer == ""


def test_a_long_title_gives_up_its_last_four_bytes_to_the_code():
    """`RHYTHM LANDSNSD` is `RHYTHM LAND` with a manufacturer code stuck on."""
    header = parse_header(_titled(b"RHYTHM LANDSNSD"))
    assert header.title == "RHYTHM LAND"
    assert header.manufacturer == "SNSD"


def test_a_long_title_that_does_not_look_like_a_code_is_kept_whole():
    header = parse_header(_titled(b"SUPER MARIO LAND"))
    assert header.manufacturer == ""
    assert header.title.startswith("SUPER MARIO")


# --------------------------------------------------- registers a DMG lacks


@pytest.mark.parametrize(
    "address,what",
    [
        (0xFF4D, "KEY1, the Color speed switch"),
        (0xFF4F, "VBK, the Color VRAM bank"),
        (0xFF70, "SVBK, the Color WRAM bank"),
        (0xFF03, "a gap between register blocks"),
        (0xFF08, "a gap after the serial port"),
        (0xFF27, "a gap before wave RAM"),
    ],
)
def test_a_register_the_dmg_does_not_have_reads_as_ones(address, what):
    """An unconnected data line floats high, so the read is $FF, not zero.

    Infinity asks for Color double-speed mode by writing KEY1 and then spins
    until bit 7 of it reads back high. On real hardware the register is absent,
    the read is $FF, bit 7 is already set and the wait loop falls through on
    its first pass. Returning zero made that loop unexitable.
    """
    unit = MMU(load_cartridge(make_rom()))
    assert unit.read8(address) == 0xFF, what


def test_writes_to_an_absent_register_go_nowhere():
    unit = MMU(load_cartridge(make_rom()))
    unit.write8(0xFF4F, 0x01)
    assert unit.read8(0xFF4F) == 0xFF


def test_writing_key1_records_that_the_cartridge_wants_colour_hardware():
    """Only a Game Boy Color can grant it, so the emulator says so on screen
    rather than showing a blank frame and looking broken."""
    unit = MMU(load_cartridge(make_rom()))
    assert not unit.double_speed_requested
    unit.write8(0xFF4D, 0x01)
    assert unit.double_speed_requested


def test_a_register_the_dmg_does_have_still_reads_back_what_was_written():
    """The guard above must not swallow the registers that do exist."""
    unit = MMU(load_cartridge(make_rom()))
    unit.write8(0xFF24, 0x77)  # NR50, a sound register with no home yet
    assert unit.read8(0xFF24) == 0x77
