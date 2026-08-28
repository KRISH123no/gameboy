"""A demo cartridge, assembled here rather than downloaded.

Shipping someone else's ROM means shipping a licence question, so the
emulator carries a cartridge written for it: a scrolling tiled background
with a sprite bouncing across it, synchronised to the vertical blank exactly
as a real game would.

It is also the honest end-to-end test. Nothing in it is set up from Python —
the emulated CPU copies the tiles into video memory, builds the tilemap and
drives the animation, so a frame coming out right means the whole machine is
working.
"""

from __future__ import annotations

# ----------------------------------------------------------------- assembler


class Assembler:
    """Just enough of an assembler to keep the demo readable.

    Emits bytes, remembers labels, and patches the jumps afterwards, so the
    program below can be read as instructions rather than as offsets.
    """

    def __init__(self, origin: int = 0x0150) -> None:
        self.origin = origin
        self.code = bytearray()
        self.labels: dict[str, int] = {}
        self._relative_fixups: list[tuple[int, str]] = []
        self._absolute_fixups: list[tuple[int, str]] = []

    @property
    def pc(self) -> int:
        return self.origin + len(self.code)

    def label(self, name: str) -> Assembler:
        self.labels[name] = self.pc
        return self

    def emit(self, *values: int) -> Assembler:
        self.code.extend(values)
        return self

    def jr(self, target: str, condition: int | None = None) -> Assembler:
        # 0x18 is unconditional; 0x20/0x28/0x30/0x38 are NZ/Z/NC/C.
        self.emit(0x18 if condition is None else 0x20 | (condition << 3))
        self._relative_fixups.append((len(self.code), target))
        return self.emit(0x00)

    def jp(self, target: str) -> Assembler:
        self.emit(0xC3)
        self._absolute_fixups.append((len(self.code), target))
        return self.emit(0x00, 0x00)

    def assemble(self) -> bytes:
        for position, target in self._relative_fixups:
            # The offset is measured from the instruction after the operand.
            offset = self.labels[target] - (self.origin + position + 1)
            if not -128 <= offset <= 127:
                raise ValueError(f"{target} is too far for a relative jump")
            self.code[position] = offset & 0xFF
        for position, target in self._absolute_fixups:
            address = self.labels[target]
            self.code[position] = address & 0xFF
            self.code[position + 1] = address >> 8
        return bytes(self.code)


# ---------------------------------------------------------------------- art

TILE_DATA_ADDRESS = 0x0400


def _solid_tile(shade: int) -> bytes:
    """Eight rows of one colour. Two bitplanes, one bit each."""
    return bytes([0xFF if shade & 1 else 0x00, 0xFF if shade & 2 else 0x00] * 8)


def _checker_tile() -> bytes:
    rows = bytearray()
    for row in range(8):
        pattern = 0xAA if row % 2 == 0 else 0x55
        rows += bytes([pattern, 0x00])
    return bytes(rows)


def _face_tile() -> bytes:
    """A little face, so there is something recognisable to bounce around."""
    picture = [
        "..####..",
        ".######.",
        "##.##.##",
        "########",
        "##....##",
        "#.####.#",
        ".######.",
        "..####..",
    ]
    rows = bytearray()
    for line in picture:
        low = high = 0
        for bit, character in enumerate(line):
            if character == "#":
                # Colour 3: set the bit in both planes.
                low |= 0x80 >> bit
                high |= 0x80 >> bit
        rows += bytes([low, high])
    return bytes(rows)


TILES = (
    _solid_tile(0)
    + _solid_tile(1)
    + _solid_tile(2)
    + _checker_tile()
    + _face_tile()
)
FACE_TILE_INDEX = 4


# ------------------------------------------------------------------ program


def _program() -> bytes:
    a = Assembler(origin=0x0150)

    # Copy the tiles into video memory.
    a.emit(0x21, 0x00, 0x80)                       # LD HL,$8000
    a.emit(0x11, TILE_DATA_ADDRESS & 0xFF, TILE_DATA_ADDRESS >> 8)  # LD DE,tiles
    a.emit(0x01, len(TILES) & 0xFF, len(TILES) >> 8)                # LD BC,len
    a.label("copy")
    a.emit(0x1A)                                   # LD A,(DE)
    a.emit(0x22)                                   # LD (HL+),A
    a.emit(0x13)                                   # INC DE
    a.emit(0x0B)                                   # DEC BC
    a.emit(0x78, 0xB1)                             # LD A,B ; OR C
    a.jr("copy", condition=0)                      # JR NZ,copy

    # Fill the background map: mostly tile 1, with tile 3 every fifth column.
    a.emit(0x21, 0x00, 0x98)                       # LD HL,$9800
    a.emit(0x01, 0x00, 0x04)                       # LD BC,$0400
    a.emit(0x16, 0x00)                             # LD D,0
    a.label("map")
    a.emit(0x7A)                                   # LD A,D
    a.emit(0xE6, 0x03)                             # AND 3
    a.emit(0xFE, 0x03)                             # CP 3
    a.jr("plain", condition=0)                     # JR NZ,plain
    a.emit(0x3E, 0x03)                             # LD A,3   (checkerboard)
    a.jr("store")
    a.label("plain")
    a.emit(0x3E, 0x01)                             # LD A,1
    a.label("store")
    a.emit(0x22)                                   # LD (HL+),A
    a.emit(0x14)                                   # INC D
    a.emit(0x0B)                                   # DEC BC
    a.emit(0x78, 0xB1)                             # LD A,B ; OR C
    a.jr("map", condition=0)                       # JR NZ,map

    # Palettes and the sprite.
    a.emit(0x3E, 0xE4, 0xE0, 0x47)                 # LD A,$E4 ; LDH (BGP),A
    a.emit(0x3E, 0xE4, 0xE0, 0x48)                 # LD A,$E4 ; LDH (OBP0),A
    a.emit(0x21, 0x00, 0xFE)                       # LD HL,$FE00
    a.emit(0x3E, 0x50, 0x22)                       # LD A,80 ; LD (HL+),A   sprite Y
    a.emit(0x3E, 0x10, 0x22)                       # LD A,16 ; LD (HL+),A   sprite X
    a.emit(0x3E, FACE_TILE_INDEX, 0x22)            # LD A,tile ; LD (HL+),A
    a.emit(0x3E, 0x00, 0x22)                       # LD A,0 ; LD (HL+),A    flags

    # Turn the screen on: LCD, background and sprites.
    a.emit(0x3E, 0x93, 0xE0, 0x40)                 # LD A,$93 ; LDH (LCDC),A

    # The main loop, once per frame.
    a.label("frame")
    a.label("wait_vblank")
    a.emit(0xF0, 0x44)                             # LDH A,(LY)
    a.emit(0xFE, 0x90)                             # CP 144
    a.jr("wait_vblank", condition=0)               # JR NZ,wait_vblank
    a.label("wait_done")
    a.emit(0xF0, 0x44)                             # LDH A,(LY)
    a.emit(0xFE, 0x90)                             # CP 144
    a.jr("wait_done", condition=1)                 # JR Z,wait_done

    a.emit(0xF0, 0x43)                             # LDH A,(SCX)
    a.emit(0x3C)                                   # INC A
    a.emit(0xE0, 0x43)                             # LDH (SCX),A

    # Walk the sprite right, and bob it up and down by reading its own X:
    # bit 4 of X flips every 16 pixels, so the face rises and falls as it goes.
    a.emit(0x21, 0x01, 0xFE)                       # LD HL,$FE01  (sprite X)
    a.emit(0x34)                                   # INC (HL)
    a.emit(0x7E)                                   # LD A,(HL)
    a.emit(0xE6, 0x10)                             # AND $10
    a.jr("down", condition=0)                      # JR NZ,down
    a.emit(0x2B)                                   # DEC HL       (sprite Y)
    a.emit(0x35)                                   # DEC (HL)
    a.jp("frame")
    a.label("down")
    a.emit(0x2B)                                   # DEC HL
    a.emit(0x34)                                   # INC (HL)
    a.jp("frame")

    return a.assemble()


def build_demo_rom() -> bytes:
    """A complete, valid 32 KB cartridge."""
    rom = bytearray(0x8000)
    rom[0x0100:0x0104] = bytes([0x00, 0xC3, 0x50, 0x01])  # NOP ; JP $0150
    rom[0x0134:0x0143] = b"GBDEMO".ljust(15, b"\x00")
    rom[0x0147] = 0x00  # ROM only
    rom[0x0148] = 0x00  # 32 KB
    rom[0x0149] = 0x00  # no cartridge RAM

    checksum = 0
    for address in range(0x0134, 0x014D):
        checksum = (checksum - rom[address] - 1) & 0xFF
    rom[0x014D] = checksum

    program = _program()
    rom[0x0150 : 0x0150 + len(program)] = program
    rom[TILE_DATA_ADDRESS : TILE_DATA_ADDRESS + len(TILES)] = TILES
    return bytes(rom)
