"""The Sharp LR35902 — the Game Boy's processor.

Not quite a Z80 and not quite an 8080. It drops the shadow registers and the
IX/IY pair, adds `SWAP` and the `LDH` zero-page loads, and repurposes two
opcodes for `LD (HL+),A` style pointer arithmetic.

**Decoding.** The 512 opcodes are not written out one by one. The instruction
set is laid out on a bit pattern — `xxyyyzzz` — where `z` usually selects a
register and `y` an operation, and decoding it that way turns eight families
of instructions into eight loops instead of four hundred branches. The layout
below follows the standard decoding table for this chip family.

**Timing.** Cycles are not read from a table either. Every memory access costs
four T-cycles and every internal delay is charged where it happens, so a
conditional jump costs the right amount in both directions without anyone
having to remember two numbers per opcode. That matters later: the graphics
chip and the timer are driven by exactly this count.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .registers import Registers

# Interrupt bits, in priority order: lowest bit wins.
INT_VBLANK = 0x01
INT_LCD_STAT = 0x02
INT_TIMER = 0x04
INT_SERIAL = 0x08
INT_JOYPAD = 0x10

INTERRUPT_VECTORS = (0x40, 0x48, 0x50, 0x58, 0x60)

IF_ADDRESS = 0xFF0F
IE_ADDRESS = 0xFFFF
#: STOP reads the joypad to decide what to do, and resets the divider.
JOYP_ADDRESS = 0xFF00
DIV_ADDRESS = 0xFF04


class Bus(Protocol):
    """Anything the CPU can read from and write to."""

    def read8(self, address: int) -> int: ...  # pragma: no cover
    def write8(self, address: int, value: int) -> None: ...  # pragma: no cover


class CPU:
    def __init__(self, bus: Bus, on_tick: Callable[[int], None] | None = None) -> None:
        self.bus = bus
        self.reg = Registers()
        #: Interrupt master enable. Only `EI`, `DI` and `RETI` change it.
        self.ime = False
        #: `EI` enables interrupts *after* the following instruction, so a
        #: common `EI; RET` pair cannot be interrupted between the two.
        self._ime_pending = False
        self.halted = False
        self.stopped = False
        #: Set when the HALT bug fires, so a debugger can show it happened.
        self.halt_bug = False
        self.cycles = 0
        #: Called with the T-cycles consumed, so the PPU and timer advance in
        #: step with the CPU rather than in a separate loop.
        self._on_tick = on_tick

    # ------------------------------------------------------------------ memory

    def tick(self, t_cycles: int) -> None:
        self.cycles += t_cycles
        if self._on_tick is not None:
            self._on_tick(t_cycles)

    def read8(self, address: int) -> int:
        self.tick(4)
        return self.bus.read8(address & 0xFFFF) & 0xFF

    def write8(self, address: int, value: int) -> None:
        self.tick(4)
        self.bus.write8(address & 0xFFFF, value & 0xFF)

    def fetch8(self) -> int:
        value = self.read8(self.reg.pc)
        self.reg.pc = (self.reg.pc + 1) & 0xFFFF
        return value

    def fetch16(self) -> int:
        low = self.fetch8()
        return low | (self.fetch8() << 8)

    def push16(self, value: int) -> None:
        self.reg.sp = (self.reg.sp - 1) & 0xFFFF
        self.write8(self.reg.sp, (value >> 8) & 0xFF)
        self.reg.sp = (self.reg.sp - 1) & 0xFFFF
        self.write8(self.reg.sp, value & 0xFF)

    def pop16(self) -> int:
        low = self.read8(self.reg.sp)
        self.reg.sp = (self.reg.sp + 1) & 0xFFFF
        high = self.read8(self.reg.sp)
        self.reg.sp = (self.reg.sp + 1) & 0xFFFF
        return (high << 8) | low

    # ------------------------------------------------------- register indexing

    def get_r(self, index: int) -> int:
        """Register by opcode index: B C D E H L (HL) A."""
        if index == 0:
            return self.reg.b
        if index == 1:
            return self.reg.c
        if index == 2:
            return self.reg.d
        if index == 3:
            return self.reg.e
        if index == 4:
            return self.reg.h
        if index == 5:
            return self.reg.l
        if index == 6:
            return self.read8(self.reg.hl)
        return self.reg.a

    def set_r(self, index: int, value: int) -> None:
        value &= 0xFF
        if index == 0:
            self.reg.b = value
        elif index == 1:
            self.reg.c = value
        elif index == 2:
            self.reg.d = value
        elif index == 3:
            self.reg.e = value
        elif index == 4:
            self.reg.h = value
        elif index == 5:
            self.reg.l = value
        elif index == 6:
            self.write8(self.reg.hl, value)
        else:
            self.reg.a = value

    def get_rp(self, index: int) -> int:
        """BC DE HL SP — the pairs used by 16-bit arithmetic."""
        return (self.reg.bc, self.reg.de, self.reg.hl, self.reg.sp)[index]

    def set_rp(self, index: int, value: int) -> None:
        value &= 0xFFFF
        if index == 0:
            self.reg.bc = value
        elif index == 1:
            self.reg.de = value
        elif index == 2:
            self.reg.hl = value
        else:
            self.reg.sp = value

    def get_rp2(self, index: int) -> int:
        """BC DE HL AF — the pairs used by PUSH and POP."""
        return (self.reg.bc, self.reg.de, self.reg.hl, self.reg.af)[index]

    def set_rp2(self, index: int, value: int) -> None:
        value &= 0xFFFF
        if index == 0:
            self.reg.bc = value
        elif index == 1:
            self.reg.de = value
        elif index == 2:
            self.reg.hl = value
        else:
            self.reg.af = value

    def condition(self, index: int) -> bool:
        """NZ Z NC C."""
        if index == 0:
            return not self.reg.flag_z
        if index == 1:
            return self.reg.flag_z
        if index == 2:
            return not self.reg.flag_c
        return self.reg.flag_c

    # -------------------------------------------------------------------- step

    def step(self) -> int:
        """Run one instruction (or service one interrupt). Returns T-cycles."""
        start = self.cycles

        if self._service_interrupt():
            return self.cycles - start

        if self.stopped:
            # STOP mode ends when one of the four joypad lines goes low. Games
            # use it as a power-saving standby and expect to come back from
            # it, so this cannot be a one-way door.
            if (self.bus.read8(JOYP_ADDRESS) & 0x0F) != 0x0F:
                self.stopped = False
            else:
                self.tick(4)
                return self.cycles - start

        if self.halted:
            # A halted CPU still burns cycles, which is how the timer and the
            # PPU keep running while a game waits for VBlank.
            self.tick(4)
            return self.cycles - start

        enable_ime_after = self._ime_pending
        opcode = self.fetch8()
        if self.halt_bug:
            # The byte after a bugged HALT is fetched twice, so undo the
            # increment exactly once.
            self.reg.pc = (self.reg.pc - 1) & 0xFFFF
            self.halt_bug = False
        self.execute(opcode)
        # `and self._ime_pending` matters: a DI in the delay slot cancels the
        # pending EI, and without this check it would be re-enabled anyway.
        if enable_ime_after and self._ime_pending:
            self.ime = True
            self._ime_pending = False

        return self.cycles - start

    def _pending_interrupts(self) -> int:
        return self.bus.read8(IE_ADDRESS) & self.bus.read8(IF_ADDRESS) & 0x1F

    def _service_interrupt(self) -> bool:
        pending = self._pending_interrupts()
        if pending == 0:
            return False

        # A pending interrupt wakes the CPU even when IME is clear. That is
        # what makes `HALT` a valid way to wait for VBlank with interrupts off.
        self.halted = False
        if not self.ime:
            return False

        self.ime = False
        for index in range(5):
            bit = 1 << index
            if pending & bit:
                flags = self.bus.read8(IF_ADDRESS) & ~bit
                self.bus.write8(IF_ADDRESS, flags)
                self.tick(8)  # two internal cycles before the jump
                self.push16(self.reg.pc)
                self.reg.pc = INTERRUPT_VECTORS[index]
                self.tick(4)
                return True
        return False  # pragma: no cover - unreachable, pending was non-zero

    def request_interrupt(self, bit: int) -> None:
        self.bus.write8(IF_ADDRESS, self.bus.read8(IF_ADDRESS) | bit)

    # ----------------------------------------------------------------- execute

    def execute(self, opcode: int) -> None:
        x = opcode >> 6
        y = (opcode >> 3) & 7
        z = opcode & 7
        p = y >> 1
        q = y & 1

        if x == 0:
            self._execute_block0(opcode, y, z, p, q)
        elif x == 1:
            if opcode == 0x76:
                self._halt()
            else:
                self.set_r(y, self.get_r(z))
        elif x == 2:
            self.alu(y, self.get_r(z))
        else:
            self._execute_block3(opcode, y, z, p, q)

    # ------------------------------------------------------------- block 0x00+

    def _execute_block0(self, opcode: int, y: int, z: int, p: int, q: int) -> None:
        if z == 0:
            if y == 0:
                return  # NOP
            if y == 1:  # LD (a16),SP
                address = self.fetch16()
                self.write8(address, self.reg.sp & 0xFF)
                self.write8(address + 1, (self.reg.sp >> 8) & 0xFF)
                return
            if y == 2:  # STOP
                self._stop()
                return
            if y == 3:  # JR d8
                offset = _signed(self.fetch8())
                self.tick(4)
                self.reg.pc = (self.reg.pc + offset) & 0xFFFF
                return
            offset = _signed(self.fetch8())  # JR cc,d8
            if self.condition(y - 4):
                self.tick(4)
                self.reg.pc = (self.reg.pc + offset) & 0xFFFF
            return

        if z == 1:
            if q == 0:  # LD rp,d16
                self.set_rp(p, self.fetch16())
            else:  # ADD HL,rp
                self.add_hl(self.get_rp(p))
            return

        if z == 2:
            if q == 0:
                if p == 0:
                    self.write8(self.reg.bc, self.reg.a)
                elif p == 1:
                    self.write8(self.reg.de, self.reg.a)
                elif p == 2:
                    self.write8(self.reg.hl, self.reg.a)
                    self.reg.hl = (self.reg.hl + 1) & 0xFFFF
                else:
                    self.write8(self.reg.hl, self.reg.a)
                    self.reg.hl = (self.reg.hl - 1) & 0xFFFF
            else:
                if p == 0:
                    self.reg.a = self.read8(self.reg.bc)
                elif p == 1:
                    self.reg.a = self.read8(self.reg.de)
                elif p == 2:
                    self.reg.a = self.read8(self.reg.hl)
                    self.reg.hl = (self.reg.hl + 1) & 0xFFFF
                else:
                    self.reg.a = self.read8(self.reg.hl)
                    self.reg.hl = (self.reg.hl - 1) & 0xFFFF
            return

        if z == 3:  # INC rp / DEC rp
            delta = 1 if q == 0 else -1
            self.set_rp(p, (self.get_rp(p) + delta) & 0xFFFF)
            self.tick(4)
            return

        if z == 4:  # INC r
            self.set_r(y, self.inc8(self.get_r(y)))
            return

        if z == 5:  # DEC r
            self.set_r(y, self.dec8(self.get_r(y)))
            return

        if z == 6:  # LD r,d8
            self.set_r(y, self.fetch8())
            return

        # z == 7: the accumulator and flag oddities.
        if y == 0:
            self.reg.a = self.rlc(self.reg.a)
            self.reg.set_flags(z=False)
        elif y == 1:
            self.reg.a = self.rrc(self.reg.a)
            self.reg.set_flags(z=False)
        elif y == 2:
            self.reg.a = self.rl(self.reg.a)
            self.reg.set_flags(z=False)
        elif y == 3:
            self.reg.a = self.rr(self.reg.a)
            self.reg.set_flags(z=False)
        elif y == 4:
            self.daa()
        elif y == 5:  # CPL
            self.reg.a = (~self.reg.a) & 0xFF
            self.reg.set_flags(n=True, h=True)
        elif y == 6:  # SCF
            self.reg.set_flags(n=False, h=False, c=True)
        else:  # CCF
            self.reg.set_flags(n=False, h=False, c=not self.reg.flag_c)

    # ------------------------------------------------------------- block 0xC0+

    def _execute_block3(self, opcode: int, y: int, z: int, p: int, q: int) -> None:
        if z == 0:
            if y < 4:  # RET cc
                self.tick(4)
                if self.condition(y):
                    self.reg.pc = self.pop16()
                    self.tick(4)
                return
            if y == 4:  # LDH (a8),A
                self.write8(0xFF00 + self.fetch8(), self.reg.a)
                return
            if y == 5:  # ADD SP,r8
                self.reg.sp = self.add_sp(_signed(self.fetch8()))
                self.tick(8)
                return
            if y == 6:  # LDH A,(a8)
                self.reg.a = self.read8(0xFF00 + self.fetch8())
                return
            # LD HL,SP+r8
            self.reg.hl = self.add_sp(_signed(self.fetch8()))
            self.tick(4)
            return

        if z == 1:
            if q == 0:  # POP
                self.set_rp2(p, self.pop16())
                return
            if p == 0:  # RET
                self.reg.pc = self.pop16()
                self.tick(4)
            elif p == 1:  # RETI
                self.reg.pc = self.pop16()
                self.tick(4)
                self.ime = True
            elif p == 2:  # JP HL — no internal delay, unlike every other jump
                self.reg.pc = self.reg.hl
            else:  # LD SP,HL
                self.reg.sp = self.reg.hl
                self.tick(4)
            return

        if z == 2:
            if y < 4:  # JP cc,a16
                address = self.fetch16()
                if self.condition(y):
                    self.reg.pc = address
                    self.tick(4)
                return
            if y == 4:  # LD (C),A
                self.write8(0xFF00 + self.reg.c, self.reg.a)
            elif y == 5:  # LD (a16),A
                self.write8(self.fetch16(), self.reg.a)
            elif y == 6:  # LD A,(C)
                self.reg.a = self.read8(0xFF00 + self.reg.c)
            else:  # LD A,(a16)
                self.reg.a = self.read8(self.fetch16())
            return

        if z == 3:
            if y == 0:  # JP a16
                self.reg.pc = self.fetch16()
                self.tick(4)
            elif y == 1:
                self.execute_cb(self.fetch8())
            elif y == 6:  # DI
                self.ime = False
                self._ime_pending = False
            elif y == 7:  # EI
                self._ime_pending = True
            else:
                raise IllegalOpcode(opcode)
            return

        if z == 4:
            if y >= 4:
                raise IllegalOpcode(opcode)
            address = self.fetch16()  # CALL cc,a16
            if self.condition(y):
                self.tick(4)
                self.push16(self.reg.pc)
                self.reg.pc = address
            return

        if z == 5:
            if q == 0:  # PUSH
                self.tick(4)
                self.push16(self.get_rp2(p))
                return
            if p != 0:
                raise IllegalOpcode(opcode)
            address = self.fetch16()  # CALL a16
            self.tick(4)
            self.push16(self.reg.pc)
            self.reg.pc = address
            return

        if z == 6:  # alu A,d8
            self.alu(y, self.fetch8())
            return

        # z == 7: RST
        self.tick(4)
        self.push16(self.reg.pc)
        self.reg.pc = y * 8

    # ------------------------------------------------------------- CB prefixed

    def execute_cb(self, opcode: int) -> None:
        x = opcode >> 6
        y = (opcode >> 3) & 7
        z = opcode & 7

        if x == 0:
            operations = (
                self.rlc, self.rrc, self.rl, self.rr,
                self.sla, self.sra, self.swap, self.srl,
            )
            result = operations[y](self.get_r(z))
            self.reg.set_flags(z=result == 0)
            self.set_r(z, result)
            return

        value = self.get_r(z)
        if x == 1:  # BIT — reads only, and leaves C alone
            self.reg.set_flags(z=not (value & (1 << y)), n=False, h=True)
            return
        if x == 2:  # RES
            self.set_r(z, value & ~(1 << y))
            return
        self.set_r(z, value | (1 << y))  # SET

    # ------------------------------------------------------------- arithmetic

    def alu(self, operation: int, value: int) -> None:
        a = self.reg.a
        carry = 1 if self.reg.flag_c else 0

        if operation == 0:  # ADD
            result = a + value
            self.reg.set_flags(
                z=(result & 0xFF) == 0, n=False, h=(a & 0xF) + (value & 0xF) > 0xF, c=result > 0xFF
            )
            self.reg.a = result & 0xFF
        elif operation == 1:  # ADC
            result = a + value + carry
            self.reg.set_flags(
                z=(result & 0xFF) == 0,
                n=False,
                h=(a & 0xF) + (value & 0xF) + carry > 0xF,
                c=result > 0xFF,
            )
            self.reg.a = result & 0xFF
        elif operation == 2:  # SUB
            result = a - value
            self.reg.set_flags(
                z=(result & 0xFF) == 0, n=True, h=(a & 0xF) < (value & 0xF), c=result < 0
            )
            self.reg.a = result & 0xFF
        elif operation == 3:  # SBC
            result = a - value - carry
            self.reg.set_flags(
                z=(result & 0xFF) == 0,
                n=True,
                h=(a & 0xF) < (value & 0xF) + carry,
                c=result < 0,
            )
            self.reg.a = result & 0xFF
        elif operation == 4:  # AND — the only logical op that sets H
            self.reg.a = a & value
            self.reg.set_flags(z=self.reg.a == 0, n=False, h=True, c=False)
        elif operation == 5:  # XOR
            self.reg.a = a ^ value
            self.reg.set_flags(z=self.reg.a == 0, n=False, h=False, c=False)
        elif operation == 6:  # OR
            self.reg.a = a | value
            self.reg.set_flags(z=self.reg.a == 0, n=False, h=False, c=False)
        else:  # CP — SUB that keeps the flags and throws away the result
            result = a - value
            self.reg.set_flags(
                z=(result & 0xFF) == 0, n=True, h=(a & 0xF) < (value & 0xF), c=result < 0
            )

    def inc8(self, value: int) -> int:
        result = (value + 1) & 0xFF
        # C is deliberately untouched: INC is the one arithmetic op that
        # preserves the carry, which programs use to count without clobbering it.
        self.reg.set_flags(z=result == 0, n=False, h=(value & 0xF) == 0xF)
        return result

    def dec8(self, value: int) -> int:
        result = (value - 1) & 0xFF
        self.reg.set_flags(z=result == 0, n=True, h=(value & 0xF) == 0)
        return result

    def add_hl(self, value: int) -> None:
        hl = self.reg.hl
        result = hl + value
        self.reg.set_flags(
            n=False, h=(hl & 0x0FFF) + (value & 0x0FFF) > 0x0FFF, c=result > 0xFFFF
        )
        self.reg.hl = result & 0xFFFF
        self.tick(4)

    def add_sp(self, offset: int) -> int:
        """SP + signed offset. The flags come from the *low byte* addition."""
        sp = self.reg.sp
        result = (sp + offset) & 0xFFFF
        self.reg.set_flags(
            z=False,
            n=False,
            h=(sp & 0xF) + (offset & 0xF) > 0xF,
            c=(sp & 0xFF) + (offset & 0xFF) > 0xFF,
        )
        return result

    def daa(self) -> None:
        """Fix up A after adding or subtracting packed binary-coded decimals.

        The direction depends on the N flag, which is the entire reason N
        exists on this chip.
        """
        a = self.reg.a
        adjust = 0
        carry = self.reg.flag_c

        if not self.reg.flag_n:
            if self.reg.flag_h or (a & 0xF) > 9:
                adjust |= 0x06
            if carry or a > 0x99:
                adjust |= 0x60
                carry = True
            a = (a + adjust) & 0xFF
        else:
            if self.reg.flag_h:
                adjust |= 0x06
            if carry:
                adjust |= 0x60
            a = (a - adjust) & 0xFF

        self.reg.a = a
        self.reg.set_flags(z=a == 0, h=False, c=carry)

    # ---------------------------------------------------------------- rotates

    def rlc(self, value: int) -> int:
        carry = (value >> 7) & 1
        result = ((value << 1) | carry) & 0xFF
        self.reg.set_flags(z=result == 0, n=False, h=False, c=bool(carry))
        return result

    def rrc(self, value: int) -> int:
        carry = value & 1
        result = ((value >> 1) | (carry << 7)) & 0xFF
        self.reg.set_flags(z=result == 0, n=False, h=False, c=bool(carry))
        return result

    def rl(self, value: int) -> int:
        carry_in = 1 if self.reg.flag_c else 0
        carry_out = (value >> 7) & 1
        result = ((value << 1) | carry_in) & 0xFF
        self.reg.set_flags(z=result == 0, n=False, h=False, c=bool(carry_out))
        return result

    def rr(self, value: int) -> int:
        carry_in = 1 if self.reg.flag_c else 0
        carry_out = value & 1
        result = ((value >> 1) | (carry_in << 7)) & 0xFF
        self.reg.set_flags(z=result == 0, n=False, h=False, c=bool(carry_out))
        return result

    def sla(self, value: int) -> int:
        carry = (value >> 7) & 1
        result = (value << 1) & 0xFF
        self.reg.set_flags(z=result == 0, n=False, h=False, c=bool(carry))
        return result

    def sra(self, value: int) -> int:
        """Arithmetic shift right — bit 7 is copied, preserving the sign."""
        carry = value & 1
        result = ((value >> 1) | (value & 0x80)) & 0xFF
        self.reg.set_flags(z=result == 0, n=False, h=False, c=bool(carry))
        return result

    def srl(self, value: int) -> int:
        carry = value & 1
        result = (value >> 1) & 0xFF
        self.reg.set_flags(z=result == 0, n=False, h=False, c=bool(carry))
        return result

    def swap(self, value: int) -> int:
        result = ((value << 4) | (value >> 4)) & 0xFF
        self.reg.set_flags(z=result == 0, n=False, h=False, c=False)
        return result

    # -------------------------------------------------------------------- halt

    def _stop(self) -> None:
        """STOP, which does four different things depending on the machine.

        The documented decision tree asks three questions: is a button being
        held on a line the joypad register currently selects, was a Game Boy
        Color speed switch requested through KEY1, and is an interrupt
        pending. This is a DMG, so KEY1 does not exist and a speed switch can
        never have been asked for, which leaves three outcomes:

        * no button held - the machine enters STOP mode and DIV is reset. It
          stays there until a button line goes low, which is the low-power
          standby the instruction is for.
        * a button held, no interrupt pending - HALT mode instead, DIV
          untouched.
        * a button held and an interrupt pending - nothing happens at all, and
          STOP is a one-byte instruction rather than two.

        The byte after STOP is otherwise skipped and ignored, which is why
        assemblers emit `STOP 0`.
        """
        # A selected button pulls its line low, so any zero in the low nibble
        # of the joypad register means something is being held.
        button_held = (self.bus.read8(JOYP_ADDRESS) & 0x0F) != 0x0F
        if button_held:
            if self._pending_interrupts():
                return  # one byte, no mode change, DIV left alone
            self.fetch8()
            self.halted = True
            return
        self.fetch8()
        self.stopped = True
        # Writing DIV clears the whole 16-bit divider, not just the byte on
        # show, which is exactly what entering STOP mode does to it.
        self.bus.write8(DIV_ADDRESS, 0)

    def _halt(self) -> None:
        """HALT, including the hardware bug it is famous for.

        With interrupts disabled and one already pending, the real chip fails
        to increment PC after the halt, so the next byte executes twice. Games
        were built against that behaviour, so emulating it is not optional.

        `_ime_pending` has to be part of the test. `EI` takes effect after the
        instruction that follows it, so in the sequence

            EI
            HALT

        IME is still clear while HALT executes, but it is about to be set and
        the pending interrupt is about to be taken. The bug does not happen;
        the interrupt is serviced normally and the handler returns to the
        instruction after the HALT.

        Reading only `self.ime` here fired the bug in that sequence, and then
        the leftover flag rewound PC inside the interrupt handler instead —
        making the handler's first instruction, a `PUSH`, run twice. One extra
        word on the stack meant the matching `RETI` returned to a pointer
        table rather than to the game, which is where Tobu Tobu Girl died
        after its publisher screen. Real hardware cannot even reach that
        state: the bug requires IME to stay clear, and a serviced interrupt
        requires it set, so the two are mutually exclusive.
        """
        if not self.ime and not self._ime_pending and self._pending_interrupts():
            # Do not rewind PC here: that would re-execute the HALT itself and
            # spin forever. The flag makes the *next* fetch repeat instead.
            self.halt_bug = True
            return
        self.halted = True


class IllegalOpcode(Exception):
    """One of the eleven opcodes the hardware does not implement."""

    def __init__(self, opcode: int) -> None:
        super().__init__(f"illegal opcode 0x{opcode:02X}")
        self.opcode = opcode


def _signed(value: int) -> int:
    """Reinterpret a byte as a two's-complement signed offset."""
    return value - 256 if value > 127 else value
