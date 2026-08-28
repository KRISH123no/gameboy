"""The machine: the parts, wired together, driven by one clock.

Everything hangs off the CPU's cycle count. The CPU runs an instruction,
reports how many cycles it took, and the PPU and timer are advanced by exactly
that much before the next instruction begins. Nothing runs on its own thread
and nothing is approximated with a separate loop, which is what keeps the
graphics in step with the game's own timing.
"""

from __future__ import annotations

from .cartridge import Cartridge, load_cartridge
from .cpu import CPU
from .joypad import Button, Joypad
from .mmu import MMU
from .ppu import HEIGHT, PPU, WIDTH
from .timer import Timer

#: 4.194304 MHz, and 59.7 frames a second.
CYCLES_PER_SECOND = 4_194_304
CYCLES_PER_FRAME = 70224


class GameBoy:
    def __init__(self, cartridge: Cartridge | None = None, *, fine_grained: bool = False) -> None:
        self.mmu = MMU(cartridge)
        self.ppu = PPU(self.mmu.request_interrupt)
        self.timer = Timer(self.mmu.request_interrupt)
        self.joypad = Joypad(self.mmu.request_interrupt)
        self.mmu.ppu = self.ppu
        self.mmu.timer = self.timer
        self.mmu.joypad = self.joypad

        # The CPU can report every memory access as it happens, but calling
        # into Python two or three times per instruction is the single largest
        # cost in the emulator. The components are advanced once per
        # instruction instead, which is still exact at the only granularity a
        # scanline renderer observes — the line boundary. Pass
        # `fine_grained=True` to get the per-access hook back.
        self.fine_grained = fine_grained
        self.cpu = CPU(self.mmu, on_tick=self._on_tick if fine_grained else None)
        self.cpu.reg.set_post_boot_state()

    @classmethod
    def from_rom(cls, path: str) -> GameBoy:
        with open(path, "rb") as handle:
            return cls(load_cartridge(handle.read()))

    @classmethod
    def from_bytes(cls, rom: bytes) -> GameBoy:
        return cls(load_cartridge(rom))

    def _on_tick(self, t_cycles: int) -> None:
        # Called from inside the CPU as each memory access happens, so a slow
        # instruction advances the screen part-way through itself.
        self.ppu.step(t_cycles)
        self.timer.step(t_cycles)

    def step(self) -> int:
        cycles = self.cpu.step()
        if not self.fine_grained:
            self.ppu.step(cycles)
            self.timer.step(cycles)
        return cycles

    def run_frame(self) -> bytes:
        """Run until the PPU finishes a frame, and return it."""
        budget = CYCLES_PER_FRAME * 4  # a generous guard against a stuck ROM
        spent = 0
        while spent < budget:
            spent += self.step()
            if self.ppu.frame_ready:
                return self.ppu.take_frame()
        return bytes(self.ppu.framebuffer)

    def run_frames(self, count: int) -> bytes:
        frame = bytes(self.ppu.framebuffer)
        for _ in range(count):
            frame = self.run_frame()
        return frame

    # ------------------------------------------------------------------ input

    def press(self, button: Button) -> None:
        self.joypad.press(button)

    def release(self, button: Button) -> None:
        self.joypad.release(button)

    # ------------------------------------------------------------------ views

    @property
    def title(self) -> str:
        return self.mmu.cartridge.header.title if self.mmu.cartridge else "(no cartridge)"

    def screen(self) -> bytes:
        return bytes(self.ppu.framebuffer)

    def __repr__(self) -> str:
        return f"<GameBoy {self.title!r} frame={self.ppu.frames} {self.cpu.reg}>"


__all__ = ["GameBoy", "Button", "WIDTH", "HEIGHT", "CYCLES_PER_FRAME"]
