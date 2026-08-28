"""The LR35902 register file.

Eight 8-bit registers that pair up into four 16-bit ones, plus SP and PC.
The pairing is not a convenience here — instructions genuinely address them
both ways, so `H` and `HL` must be two views of the same storage.

`F` is the flag register and only its top nibble exists. The low four bits
read as zero no matter what you write, which real programs depend on: `POP AF`
followed by `PUSH AF` must not resurrect bits that hardware discards.
"""

from __future__ import annotations

# Bit positions within F.
FLAG_Z = 0x80  # result was zero
FLAG_N = 0x40  # last operation was a subtraction
FLAG_H = 0x20  # carry out of bit 3
FLAG_C = 0x10  # carry out of bit 7


class Registers:
    __slots__ = ("a", "b", "c", "d", "e", "h", "l", "_f", "sp", "pc")

    def __init__(self) -> None:
        self.a = 0
        self.b = 0
        self.c = 0
        self.d = 0
        self.e = 0
        self.h = 0
        self.l = 0
        self._f = 0
        self.sp = 0
        self.pc = 0

    # --------------------------------------------------------------- flag byte

    @property
    def f(self) -> int:
        return self._f

    @f.setter
    def f(self, value: int) -> None:
        # The bottom nibble does not exist in hardware.
        self._f = value & 0xF0

    # ------------------------------------------------------------ 16-bit pairs

    @property
    def af(self) -> int:
        return (self.a << 8) | self._f

    @af.setter
    def af(self, value: int) -> None:
        self.a = (value >> 8) & 0xFF
        self._f = value & 0xF0

    @property
    def bc(self) -> int:
        return (self.b << 8) | self.c

    @bc.setter
    def bc(self, value: int) -> None:
        self.b = (value >> 8) & 0xFF
        self.c = value & 0xFF

    @property
    def de(self) -> int:
        return (self.d << 8) | self.e

    @de.setter
    def de(self, value: int) -> None:
        self.d = (value >> 8) & 0xFF
        self.e = value & 0xFF

    @property
    def hl(self) -> int:
        return (self.h << 8) | self.l

    @hl.setter
    def hl(self, value: int) -> None:
        self.h = (value >> 8) & 0xFF
        self.l = value & 0xFF

    # ------------------------------------------------------------------- flags

    @property
    def flag_z(self) -> bool:
        return bool(self._f & FLAG_Z)

    @property
    def flag_n(self) -> bool:
        return bool(self._f & FLAG_N)

    @property
    def flag_h(self) -> bool:
        return bool(self._f & FLAG_H)

    @property
    def flag_c(self) -> bool:
        return bool(self._f & FLAG_C)

    def set_flags(
        self,
        z: bool | None = None,
        n: bool | None = None,
        h: bool | None = None,
        c: bool | None = None,
    ) -> None:
        """Set the flags named; `None` leaves that flag alone."""
        f = self._f
        if z is not None:
            f = (f | FLAG_Z) if z else (f & ~FLAG_Z)
        if n is not None:
            f = (f | FLAG_N) if n else (f & ~FLAG_N)
        if h is not None:
            f = (f | FLAG_H) if h else (f & ~FLAG_H)
        if c is not None:
            f = (f | FLAG_C) if c else (f & ~FLAG_C)
        self._f = f & 0xF0

    # ------------------------------------------------------------------ boot

    def set_post_boot_state(self) -> None:
        """The state the boot ROM leaves behind on an original DMG.

        Emulators that skip the boot ROM must start here instead, because many
        games read these registers before writing them.
        """
        self.af = 0x01B0
        self.bc = 0x0013
        self.de = 0x00D8
        self.hl = 0x014D
        self.sp = 0xFFFE
        self.pc = 0x0100

    def __repr__(self) -> str:
        flags = "".join(
            letter if self._f & bit else "-"
            for letter, bit in (("Z", FLAG_Z), ("N", FLAG_N), ("H", FLAG_H), ("C", FLAG_C))
        )
        return (
            f"<AF={self.af:04X} BC={self.bc:04X} DE={self.de:04X} HL={self.hl:04X} "
            f"SP={self.sp:04X} PC={self.pc:04X} {flags}>"
        )
