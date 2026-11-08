"""Cartridges, and the bank-switching chips inside them.

The CPU can only address 32 KB of ROM, but games ship up to 2 MB. Cartridges
solve that with a memory bank controller: a chip on the cartridge that watches
for writes into the ROM address range — which would otherwise be meaningless,
since ROM is read-only — and treats them as commands to swap which slice of
the cartridge appears at 0x4000.

So `LD (0x2000),A` is not a store. It is "show me bank A".
"""

from __future__ import annotations

from dataclasses import dataclass

ROM_BANK_SIZE = 0x4000
RAM_BANK_SIZE = 0x2000

#: Cartridge type byte at 0x0147 to the controller it means.
MBC_NAMES = {
    0x00: "ROM only",
    0x01: "MBC1",
    0x02: "MBC1+RAM",
    0x03: "MBC1+RAM+BATTERY",
    0x05: "MBC2",
    0x06: "MBC2+BATTERY",
    0x0F: "MBC3+TIMER+BATTERY",
    0x10: "MBC3+TIMER+RAM+BATTERY",
    0x11: "MBC3",
    0x12: "MBC3+RAM",
    0x13: "MBC3+RAM+BATTERY",
    0x19: "MBC5",
    0x1A: "MBC5+RAM",
    0x1B: "MBC5+RAM+BATTERY",
}


@dataclass(frozen=True)
class Header:
    title: str
    manufacturer: str
    cartridge_type: int
    rom_banks: int
    ram_banks: int
    checksum_ok: bool

    @property
    def controller(self) -> str:
        return MBC_NAMES.get(self.cartridge_type, f"unknown (0x{self.cartridge_type:02X})")

    @property
    def rom_size_kb(self) -> int:
        return self.rom_banks * ROM_BANK_SIZE // 1024


def _read_title(rom: bytes) -> tuple[str, str]:
    """The title, and the manufacturer code hiding at the end of it.

    The title field is sixteen bytes at 0x0134. Later cartridges carved the
    last four of them (0x013F-0x0142) into a manufacturer code and the one
    after that into the Game Boy Color flag, without moving the title — so a
    title that fills the field runs straight into the code and arrives as
    `2048-gb    XXXX` or `RHYTHM LANDSNSD`.

    There is no flag saying which layout a cartridge used, so the test is
    the shape of the bytes: a title long enough to reach the code, four
    printable uppercase characters sitting exactly where the code belongs,
    and a recognised Game Boy Color flag after them. Short titles are left
    alone, which is every cartridge from before the convention existed.
    """
    raw = bytes(rom[0x0134:0x0144])
    text = raw.split(b"\x00")[0].decode("ascii", "replace").strip()
    code = raw[0x0B:0x0F].decode("ascii", "replace")
    long_enough = len(raw.split(b"\x00")[0]) > 0x0B
    looks_like_code = len(code) == 4 and all(
        c.isascii() and (c.isupper() or c.isdigit()) for c in code
    )
    if long_enough and looks_like_code and rom[0x0143] in (0x00, 0x80, 0xC0):
        trimmed = raw[:0x0B].split(b"\x00")[0].decode("ascii", "replace").strip()
        if trimmed:
            return trimmed, code
    return text, ""


def parse_header(rom: bytes) -> Header:
    """Read the 80-byte header every cartridge carries at 0x0100."""
    if len(rom) < 0x0150:
        raise ValueError("ROM is too short to contain a header")

    title, manufacturer = _read_title(rom)
    cartridge_type = rom[0x0147]
    rom_banks = 2 << rom[0x0148] if rom[0x0148] <= 0x08 else len(rom) // ROM_BANK_SIZE
    ram_banks = {0x00: 0, 0x01: 0, 0x02: 1, 0x03: 4, 0x04: 16, 0x05: 8}.get(rom[0x0149], 0)

    # The boot ROM refuses to start a cartridge whose header checksum is wrong,
    # so a failure here means the file is damaged rather than merely unusual.
    checksum = 0
    for address in range(0x0134, 0x014D):
        checksum = (checksum - rom[address] - 1) & 0xFF

    return Header(
        title=title,
        manufacturer=manufacturer,
        cartridge_type=cartridge_type,
        rom_banks=max(2, rom_banks),
        ram_banks=ram_banks,
        checksum_ok=checksum == rom[0x014D],
    )


class Cartridge:
    """Base class: 32 KB of ROM with no controller at all."""

    def __init__(self, rom: bytes, header: Header) -> None:
        self.rom = bytes(rom)
        self.header = header
        self.ram = bytearray(RAM_BANK_SIZE * max(1, header.ram_banks))
        self.ram_enabled = False

    def read_rom(self, address: int) -> int:
        return self.rom[address] if address < len(self.rom) else 0xFF

    def write_rom(self, address: int, value: int) -> None:
        """A write into ROM space is a controller command, or nothing at all."""

    def read_ram(self, address: int) -> int:
        if not self.ram_enabled or not self.ram:
            return 0xFF
        return self.ram[address % len(self.ram)]

    def write_ram(self, address: int, value: int) -> None:
        if self.ram_enabled and self.ram:
            self.ram[address % len(self.ram)] = value & 0xFF

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.header.title!r} {self.header.rom_size_kb}KB>"


class MBC1(Cartridge):
    """The common early controller: up to 2 MB of ROM or 32 KB of RAM.

    Its quirk is that the same five-bit register selects both the upper ROM
    bits and the RAM bank, and a mode flag decides which. Games that use large
    ROMs and games that use large RAM therefore drive it differently.
    """

    def __init__(self, rom: bytes, header: Header) -> None:
        super().__init__(rom, header)
        self.rom_bank = 1
        self.upper_bits = 0
        self.advanced_banking = False

    def write_rom(self, address: int, value: int) -> None:
        if address < 0x2000:
            self.ram_enabled = (value & 0x0F) == 0x0A
        elif address < 0x4000:
            # Bank 0 is not selectable here: asking for it gives bank 1, which
            # is why a 64-bank cartridge only reaches 63 through this register.
            self.rom_bank = (value & 0x1F) or 1
        elif address < 0x6000:
            self.upper_bits = value & 0x03
        else:
            self.advanced_banking = bool(value & 1)

    def _bank(self) -> int:
        bank = self.rom_bank
        if not self.advanced_banking:
            bank |= self.upper_bits << 5
        return bank % max(1, self.header.rom_banks)

    def read_rom(self, address: int) -> int:
        if address < ROM_BANK_SIZE:
            base = 0
            if self.advanced_banking:
                base = ((self.upper_bits << 5) % max(1, self.header.rom_banks)) * ROM_BANK_SIZE
            offset = base + address
        else:
            offset = self._bank() * ROM_BANK_SIZE + (address - ROM_BANK_SIZE)
        return self.rom[offset] if offset < len(self.rom) else 0xFF

    def read_ram(self, address: int) -> int:
        if not self.ram_enabled or not self.ram:
            return 0xFF
        return self.ram[self._ram_offset(address)]

    def write_ram(self, address: int, value: int) -> None:
        if self.ram_enabled and self.ram:
            self.ram[self._ram_offset(address)] = value & 0xFF

    def _ram_offset(self, address: int) -> int:
        bank = self.upper_bits if self.advanced_banking else 0
        return (bank * RAM_BANK_SIZE + address) % len(self.ram)


class MBC3(Cartridge):
    """Later controller, as used by Pokémon. Simpler banking, plus a clock.

    The real-time clock is not implemented: its registers read as zero. Games
    use it for daily events, so they run, they just never see time pass.
    """

    def __init__(self, rom: bytes, header: Header) -> None:
        super().__init__(rom, header)
        self.rom_bank = 1
        self.ram_bank = 0

    def write_rom(self, address: int, value: int) -> None:
        if address < 0x2000:
            self.ram_enabled = (value & 0x0F) == 0x0A
        elif address < 0x4000:
            self.rom_bank = (value & 0x7F) or 1
        elif address < 0x6000:
            self.ram_bank = value & 0x0F

    def read_rom(self, address: int) -> int:
        if address < ROM_BANK_SIZE:
            offset = address
        else:
            bank = self.rom_bank % max(1, self.header.rom_banks)
            offset = bank * ROM_BANK_SIZE + (address - ROM_BANK_SIZE)
        return self.rom[offset] if offset < len(self.rom) else 0xFF

    def read_ram(self, address: int) -> int:
        if self.ram_bank >= 0x08:
            return 0x00  # the clock registers, which this does not emulate
        if not self.ram_enabled or not self.ram:
            return 0xFF
        return self.ram[(self.ram_bank * RAM_BANK_SIZE + address) % len(self.ram)]

    def write_ram(self, address: int, value: int) -> None:
        if self.ram_bank >= 0x08 or not self.ram_enabled or not self.ram:
            return
        self.ram[(self.ram_bank * RAM_BANK_SIZE + address) % len(self.ram)] = value & 0xFF


class MBC5(MBC3):
    """Used by later games. Nine bits of ROM bank, and bank 0 is selectable."""

    def write_rom(self, address: int, value: int) -> None:
        if address < 0x2000:
            self.ram_enabled = (value & 0x0F) == 0x0A
        elif address < 0x3000:
            self.rom_bank = (self.rom_bank & 0x100) | value
        elif address < 0x4000:
            self.rom_bank = (self.rom_bank & 0xFF) | ((value & 1) << 8)
        elif address < 0x6000:
            self.ram_bank = value & 0x0F

    def read_rom(self, address: int) -> int:
        if address < ROM_BANK_SIZE:
            return self.rom[address] if address < len(self.rom) else 0xFF
        # Unlike MBC1, bank 0 here really means bank 0.
        bank = self.rom_bank % max(1, self.header.rom_banks)
        offset = bank * ROM_BANK_SIZE + (address - ROM_BANK_SIZE)
        return self.rom[offset] if offset < len(self.rom) else 0xFF


def load_cartridge(rom: bytes) -> Cartridge:
    """Pick the controller the header asks for."""
    header = parse_header(rom)
    kind = header.cartridge_type
    if kind in (0x00, 0x08, 0x09):
        return Cartridge(rom, header)
    if 0x01 <= kind <= 0x03:
        return MBC1(rom, header)
    if 0x0F <= kind <= 0x13:
        return MBC3(rom, header)
    if 0x19 <= kind <= 0x1E:
        return MBC5(rom, header)
    raise UnsupportedCartridge(header)


class UnsupportedCartridge(Exception):
    def __init__(self, header: Header) -> None:
        super().__init__(f"{header.controller} is not implemented")
        self.header = header
