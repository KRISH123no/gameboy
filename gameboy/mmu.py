"""The memory map.

Sixteen bits of address space, carved into regions that behave nothing alike:

    0000-3FFF  ROM bank 0          fixed
    4000-7FFF  ROM bank N          switched by the cartridge
    8000-9FFF  video RAM           tiles and maps
    A000-BFFF  cartridge RAM       battery-backed saves, if any
    C000-DFFF  work RAM
    E000-FDFF  echo RAM            a mirror of C000-DDFF, an accident of wiring
    FE00-FE9F  sprite table
    FEA0-FEFF  unusable            reads as 0xFF
    FF00-FF7F  hardware registers
    FF80-FFFE  high RAM            the only RAM usable during a DMA
    FFFF       interrupt enable

Echo RAM is the giveaway that this is real hardware rather than a design: the
address decoder simply ignored one bit, so a whole 8 KB region is a mirror
nobody asked for. Some games write to it anyway, so it has to be emulated.
"""

from __future__ import annotations

from .cartridge import Cartridge

WRAM_SIZE = 0x2000
HRAM_SIZE = 0x7F


#: The addresses in $FF00-$FF7F that an original DMG simply does not implement
#: — the gaps between register blocks, and the ones the Game Boy Color added.
#: Every one of them reads back $FF, because an unconnected data line floats
#: high. Writes to them go nowhere.
UNMAPPED_IO = frozenset(
    [0xFF03, 0xFF15, 0xFF1F]
    + list(range(0xFF08, 0xFF0F))  # between the serial port and IF
    + list(range(0xFF27, 0xFF30))  # between the sound registers and wave RAM
    # $FF4C upwards is where the Game Boy Color put KEY1, VBK, the HDMA
    # registers, the infrared port, the colour palettes and SVBK. A DMG has
    # none of them.
    + list(range(0xFF4C, 0xFF80))
)

#: Where the Game Boy Color put its speed-switch register.
KEY1_ADDRESS = 0xFF4D


class MMU:
    def __init__(self, cartridge: Cartridge | None = None) -> None:
        self.cartridge = cartridge
        self.wram = bytearray(WRAM_SIZE)
        self.hram = bytearray(HRAM_SIZE)
        self.interrupt_enable = 0
        self.interrupt_flags = 0xE1

        #: Filled in by the machine once the parts exist. Keeping them optional
        #: means the MMU can be tested on its own.
        self.ppu = None
        self.timer = None
        self.joypad = None

        #: Any register with no home yet still has to read back what was
        #: written, or games that save and restore them break.
        self.io = bytearray(0x80)
        self.serial_output: list[int] = []
        #: Set when a cartridge writes KEY1, which only Color hardware has.
        self.double_speed_requested = False

    # ------------------------------------------------------------------- read

    def read8(self, address: int) -> int:
        address &= 0xFFFF

        if address < 0x8000:
            return self.cartridge.read_rom(address) if self.cartridge else 0xFF
        if address < 0xA000:
            return self.ppu.read_vram(address) if self.ppu else 0xFF
        if address < 0xC000:
            return self.cartridge.read_ram(address - 0xA000) if self.cartridge else 0xFF
        if address < 0xE000:
            return self.wram[address - 0xC000]
        if address < 0xFE00:
            return self.wram[address - 0xE000]  # echo
        if address < 0xFEA0:
            return self.ppu.read_oam(address) if self.ppu else 0xFF
        if address < 0xFF00:
            return 0xFF  # the dead zone
        if address < 0xFF80:
            return self._read_io(address)
        if address < 0xFFFF:
            return self.hram[address - 0xFF80]
        return self.interrupt_enable

    def _read_io(self, address: int) -> int:
        if address in UNMAPPED_IO:
            # A register the DMG does not have reads back as all ones, not as
            # zero. That is not a cosmetic detail: Infinity asks for Game Boy
            # Color double-speed mode by writing KEY1 ($FF4D) and then spins
            # until bit 7 of it reads high. On real DMG hardware the register
            # does not exist, the read returns $FF, bit 7 is already set and
            # the loop falls straight through on its first pass. Returning
            # zero made that wait loop unexitable.
            return 0xFF
        if address == 0xFF00:
            return self.joypad.read() if self.joypad else 0xFF
        if address == 0xFF0F:
            # The top three bits of IF are not wired to anything and read high.
            return self.interrupt_flags | 0xE0
        if self.timer is not None and 0xFF04 <= address <= 0xFF07:
            return self.timer.read(address)
        if self.ppu is not None and 0xFF40 <= address <= 0xFF4B:
            return self.ppu.read_register(address)
        return self.io[address - 0xFF00]

    # ------------------------------------------------------------------ write

    def write8(self, address: int, value: int) -> None:
        address &= 0xFFFF
        value &= 0xFF

        if address < 0x8000:
            if self.cartridge:
                self.cartridge.write_rom(address, value)  # a banking command
            return
        if address < 0xA000:
            if self.ppu:
                self.ppu.write_vram(address, value)
            return
        if address < 0xC000:
            if self.cartridge:
                self.cartridge.write_ram(address - 0xA000, value)
            return
        if address < 0xE000:
            self.wram[address - 0xC000] = value
            return
        if address < 0xFE00:
            self.wram[address - 0xE000] = value  # echo
            return
        if address < 0xFEA0:
            if self.ppu:
                self.ppu.write_oam(address, value)
            return
        if address < 0xFF00:
            return
        if address < 0xFF80:
            self._write_io(address, value)
            return
        if address < 0xFFFF:
            self.hram[address - 0xFF80] = value
            return
        self.interrupt_enable = value

    def _write_io(self, address: int, value: int) -> None:
        if address == KEY1_ADDRESS:
            # Only a Game Boy Color can honour this. A cartridge that asks for
            # double speed without first checking what it is running on has
            # assumed Color hardware, and nothing this machine does will
            # satisfy it. Record the request so the emulator can say so
            # instead of showing a white screen: Infinity writes KEY1, runs
            # STOP and then loops until the speed has changed, which on a DMG
            # is forever.
            self.double_speed_requested = True
            return
        if address in UNMAPPED_IO:
            return  # nothing is connected to it, so the write goes nowhere
        if address == 0xFF00:
            if self.joypad:
                self.joypad.write(value)
            return
        if address == 0xFF01:
            self.io[0x01] = value
            return
        if address == 0xFF02:
            # Bit 7 starts a transfer and bit 0 chooses the clock: 1 means
            # this Game Boy drives it, 0 means the Game Boy on the other end
            # of the cable does. With nothing plugged in, only an
            # internally clocked transfer can ever finish, so $81 completes
            # and $80 hangs forever — which is exactly how a game asks
            # whether a second player is there.
            #
            # Completing $80 as well told every such game that a link cable
            # was connected and had already replied. Tobu Tobu Girl, Deadeus
            # and Infinity all probe this way at boot and all three went into
            # their two-player path and died on a handshake that could never
            # come. The test ROMs were unaffected, because they write $81.
            if value & 0x81 == 0x81:
                self.serial_output.append(self.io[0x01])
                self.interrupt_flags |= 0x08
                # The transfer is over, so the start bit clears itself.
                value &= 0x7F
            self.io[0x02] = value
            return
        if address == 0xFF0F:
            self.interrupt_flags = value & 0x1F
            return
        if self.timer is not None and 0xFF04 <= address <= 0xFF07:
            self.timer.write(address, value)
            return
        if address == 0xFF46:
            self._dma(value)
            return
        if self.ppu is not None and 0xFF40 <= address <= 0xFF4B:
            self.ppu.write_register(address, value)
            return
        self.io[address - 0xFF00] = value

    def _dma(self, value: int) -> None:
        """Copy 160 bytes into the sprite table.

        Real hardware takes 160 machine cycles and locks most of the bus while
        it runs, which is why games put the routine that waits for it in high
        RAM. This copies instantly; the visible result is the same.
        """
        source = value << 8
        if self.ppu is None:
            return
        for index in range(0xA0):
            self.ppu.write_oam(0xFE00 + index, self.read8(source + index))

    # ------------------------------------------------------------- interrupts

    def request_interrupt(self, bit: int) -> None:
        self.interrupt_flags |= bit

    @property
    def serial_text(self) -> str:
        """What a test ROM has printed to the link port so far."""
        return bytes(self.serial_output).decode("ascii", "replace")
