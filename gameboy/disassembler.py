"""Turn bytes back into instructions.

Written from the same bit-pattern decoding the CPU uses, so the two cannot
drift apart: if an opcode executes, it disassembles, and the debugger shows
exactly what is about to run.
"""

from __future__ import annotations

from dataclasses import dataclass

R = ("B", "C", "D", "E", "H", "L", "(HL)", "A")
RP = ("BC", "DE", "HL", "SP")
RP2 = ("BC", "DE", "HL", "AF")
CC = ("NZ", "Z", "NC", "C")
ALU = ("ADD A,", "ADC A,", "SUB ", "SBC A,", "AND ", "XOR ", "OR ", "CP ")
ROT = ("RLC", "RRC", "RL", "RR", "SLA", "SRA", "SWAP", "SRL")
ACCUMULATOR = ("RLCA", "RRCA", "RLA", "RRA", "DAA", "CPL", "SCF", "CCF")


@dataclass(frozen=True)
class Instruction:
    address: int
    length: int
    text: str
    raw: bytes

    def __str__(self) -> str:
        hexdump = " ".join(f"{b:02X}" for b in self.raw)
        return f"{self.address:04X}  {hexdump:<9}  {self.text}"


def _signed(value: int) -> int:
    return value - 256 if value > 127 else value


def disassemble(read8, address: int) -> Instruction:
    """Decode the instruction at `address`. `read8` is any byte source."""
    start = address
    opcode = read8(address)
    address += 1

    def d8() -> int:
        nonlocal address
        value = read8(address)
        address += 1
        return value

    def d16() -> int:
        low = d8()
        return low | (d8() << 8)

    text = _decode(opcode, d8, d16, start)
    length = address - start
    return Instruction(start, length, text, bytes(read8(start + i) for i in range(length)))


def _decode(opcode: int, d8, d16, start: int) -> str:
    x, y, z = opcode >> 6, (opcode >> 3) & 7, opcode & 7
    p, q = y >> 1, y & 1

    if x == 0:
        if z == 0:
            if y == 0:
                return "NOP"
            if y == 1:
                return f"LD (${d16():04X}),SP"
            if y == 2:
                return "STOP"
            if y == 3:
                return f"JR ${start + 2 + _signed(d8()):04X}"
            return f"JR {CC[y - 4]},${start + 2 + _signed(d8()):04X}"
        if z == 1:
            return f"LD {RP[p]},${d16():04X}" if q == 0 else f"ADD HL,{RP[p]}"
        if z == 2:
            pointer = ("(BC)", "(DE)", "(HL+)", "(HL-)")[p]
            return f"LD {pointer},A" if q == 0 else f"LD A,{pointer}"
        if z == 3:
            return f"{'INC' if q == 0 else 'DEC'} {RP[p]}"
        if z == 4:
            return f"INC {R[y]}"
        if z == 5:
            return f"DEC {R[y]}"
        if z == 6:
            return f"LD {R[y]},${d8():02X}"
        return ACCUMULATOR[y]

    if x == 1:
        return "HALT" if opcode == 0x76 else f"LD {R[y]},{R[z]}"

    if x == 2:
        return f"{ALU[y]}{R[z]}"

    # x == 3
    if z == 0:
        if y < 4:
            return f"RET {CC[y]}"
        if y == 4:
            return f"LDH ($FF{d8():02X}),A"
        if y == 5:
            return f"ADD SP,{_signed(d8())}"
        if y == 6:
            return f"LDH A,($FF{d8():02X})"
        return f"LD HL,SP{_signed(d8()):+d}"
    if z == 1:
        if q == 0:
            return f"POP {RP2[p]}"
        return ("RET", "RETI", "JP HL", "LD SP,HL")[p]
    if z == 2:
        if y < 4:
            return f"JP {CC[y]},${d16():04X}"
        return ("LD ($FF00+C),A", f"LD (${d16():04X}),A", "LD A,($FF00+C)", f"LD A,(${d16():04X})")[
            y - 4
        ]
    if z == 3:
        if y == 0:
            return f"JP ${d16():04X}"
        if y == 1:
            return _decode_cb(d8())
        if y == 6:
            return "DI"
        if y == 7:
            return "EI"
        return f"DB ${opcode:02X}"  # not an instruction on this chip
    if z == 4:
        if y < 4:
            return f"CALL {CC[y]},${d16():04X}"
        return f"DB ${opcode:02X}"
    if z == 5:
        if q == 0:
            return f"PUSH {RP2[p]}"
        return f"CALL ${d16():04X}" if p == 0 else f"DB ${opcode:02X}"
    if z == 6:
        return f"{ALU[y]}${d8():02X}"
    return f"RST ${y * 8:02X}"


def _decode_cb(opcode: int) -> str:
    x, y, z = opcode >> 6, (opcode >> 3) & 7, opcode & 7
    if x == 0:
        return f"{ROT[y]} {R[z]}"
    return f"{('BIT', 'RES', 'SET')[x - 1]} {y},{R[z]}"


def disassemble_range(read8, address: int, count: int) -> list[Instruction]:
    out = []
    for _ in range(count):
        instruction = disassemble(read8, address)
        out.append(instruction)
        address = (address + instruction.length) & 0xFFFF
    return out
