import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from gameboy.cpu import CPU  # noqa: E402


class FlatMemory:
    """64 KB of plain RAM. Enough to exercise the CPU without a cartridge."""

    def __init__(self) -> None:
        self.data = bytearray(0x10000)

    def read8(self, address: int) -> int:
        return self.data[address & 0xFFFF]

    def write8(self, address: int, value: int) -> None:
        self.data[address & 0xFFFF] = value & 0xFF

    def load(self, address: int, program: bytes) -> None:
        self.data[address : address + len(program)] = program


@pytest.fixture
def memory():
    return FlatMemory()


@pytest.fixture
def cpu(memory):
    processor = CPU(memory)
    processor.reg.sp = 0xFFFE
    return processor


def run(cpu, program: bytes, *, steps: int = 1, origin: int = 0x0000) -> int:
    """Load a program at `origin`, run `steps` instructions, return T-cycles."""
    cpu.bus.load(origin, program)
    cpu.reg.pc = origin
    before = cpu.cycles
    for _ in range(steps):
        cpu.step()
    return cpu.cycles - before
