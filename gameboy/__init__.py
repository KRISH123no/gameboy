"""A Game Boy emulator: a cycle-counted CPU, a scanline PPU, and a debugger.

    >>> from gameboy import GameBoy, build_demo_rom
    >>> gb = GameBoy.from_bytes(build_demo_rom())
    >>> frame = gb.run_frames(3)
    >>> len(frame)
    23040
"""

from .cartridge import Cartridge, Header, load_cartridge, parse_header
from .cpu import CPU, IllegalOpcode
from .demo import build_demo_rom
from .disassembler import disassemble, disassemble_range
from .joypad import Button, Joypad
from .machine import CYCLES_PER_FRAME, GameBoy
from .mmu import MMU
from .png import to_ascii, write_png
from .ppu import HEIGHT, PPU, WIDTH
from .registers import Registers
from .timer import Timer

__version__ = "0.1.0"

__all__ = [
    "CPU",
    "CYCLES_PER_FRAME",
    "HEIGHT",
    "MMU",
    "PPU",
    "WIDTH",
    "Button",
    "Cartridge",
    "GameBoy",
    "Header",
    "IllegalOpcode",
    "Joypad",
    "Registers",
    "Timer",
    "build_demo_rom",
    "disassemble",
    "disassemble_range",
    "load_cartridge",
    "parse_header",
    "to_ascii",
    "write_png",
]
