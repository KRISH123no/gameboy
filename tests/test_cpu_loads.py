"""Loads, stores and the stack."""

import pytest
from conftest import run

# Opcode index order used throughout the instruction set.
R_NAMES = ["b", "c", "d", "e", "h", "l", "(hl)", "a"]


@pytest.mark.parametrize("dest", range(8))
@pytest.mark.parametrize("source", range(8))
def test_ld_r_r(cpu, dest, source):
    """0x40-0x7F is one instruction repeated 63 times, minus HALT at 0x76."""
    opcode = 0x40 | (dest << 3) | source
    if opcode == 0x76:
        pytest.skip("0x76 is HALT, not LD (HL),(HL)")

    cpu.reg.hl = 0xC000
    for index in range(8):
        if index != 6:
            cpu.set_r(index, 0x10 + index)
    cpu.bus.write8(0xC000, 0x99)

    expected = cpu.get_r(source)
    run(cpu, bytes([opcode]))
    assert cpu.get_r(dest) == expected


def test_ld_r_d8(cpu):
    run(cpu, bytes([0x06, 0x42]))  # LD B,0x42
    assert cpu.reg.b == 0x42


def test_ld_hl_d8_writes_through_the_pointer(cpu):
    cpu.reg.hl = 0xC123
    run(cpu, bytes([0x36, 0x7F]))  # LD (HL),0x7F
    assert cpu.bus.read8(0xC123) == 0x7F


@pytest.mark.parametrize(
    "opcode,pair,value",
    [(0x01, "bc", 0xBEEF), (0x11, "de", 0xDEAD), (0x21, "hl", 0xC0DE), (0x31, "sp", 0xFFF0)],
)
def test_ld_rr_d16(cpu, opcode, pair, value):
    run(cpu, bytes([opcode, value & 0xFF, value >> 8]))
    assert getattr(cpu.reg, pair) == value


def test_ld_a_through_bc_and_de(cpu):
    cpu.reg.bc = 0xC000
    cpu.reg.de = 0xC001
    cpu.bus.write8(0xC000, 0x11)
    cpu.bus.write8(0xC001, 0x22)
    run(cpu, bytes([0x0A]))  # LD A,(BC)
    assert cpu.reg.a == 0x11
    run(cpu, bytes([0x1A]), origin=0x0010)  # LD A,(DE)
    assert cpu.reg.a == 0x22


def test_ld_hl_increment_and_decrement(cpu):
    cpu.reg.a = 0xAB
    cpu.reg.hl = 0xC000
    run(cpu, bytes([0x22]))  # LD (HL+),A
    assert cpu.bus.read8(0xC000) == 0xAB
    assert cpu.reg.hl == 0xC001

    run(cpu, bytes([0x32]), origin=0x0010)  # LD (HL-),A
    assert cpu.bus.read8(0xC001) == 0xAB
    assert cpu.reg.hl == 0xC000


def test_ld_a_from_hl_increment(cpu):
    cpu.reg.hl = 0xC000
    cpu.bus.write8(0xC000, 0x5A)
    run(cpu, bytes([0x2A]))  # LD A,(HL+)
    assert cpu.reg.a == 0x5A
    assert cpu.reg.hl == 0xC001


def test_ldh_uses_the_zero_page(cpu):
    """LDH is a one-byte address into 0xFF00 — the register page."""
    cpu.reg.a = 0x77
    run(cpu, bytes([0xE0, 0x80]))  # LDH (0xFF80),A
    assert cpu.bus.read8(0xFF80) == 0x77

    cpu.bus.write8(0xFF81, 0x33)
    run(cpu, bytes([0xF0, 0x81]), origin=0x0010)  # LDH A,(0xFF81)
    assert cpu.reg.a == 0x33


def test_ld_through_register_c(cpu):
    cpu.reg.a = 0x5C
    cpu.reg.c = 0x90
    run(cpu, bytes([0xE2]))  # LD (C),A
    assert cpu.bus.read8(0xFF90) == 0x5C


def test_ld_absolute(cpu):
    cpu.reg.a = 0x24
    run(cpu, bytes([0xEA, 0x00, 0xC0]))  # LD (0xC000),A
    assert cpu.bus.read8(0xC000) == 0x24
    cpu.bus.write8(0xC005, 0x99)
    run(cpu, bytes([0xFA, 0x05, 0xC0]), origin=0x0010)  # LD A,(0xC005)
    assert cpu.reg.a == 0x99


def test_ld_a16_sp_writes_little_endian(cpu):
    cpu.reg.sp = 0xBEEF
    run(cpu, bytes([0x08, 0x00, 0xC0]))
    assert cpu.bus.read8(0xC000) == 0xEF
    assert cpu.bus.read8(0xC001) == 0xBE


def test_push_and_pop_round_trip(cpu):
    cpu.reg.bc = 0x1234
    run(cpu, bytes([0xC5]))  # PUSH BC
    assert cpu.reg.sp == 0xFFFC
    cpu.reg.bc = 0
    run(cpu, bytes([0xC1]), origin=0x0010)  # POP BC
    assert cpu.reg.bc == 0x1234
    assert cpu.reg.sp == 0xFFFE


def test_popping_af_discards_the_low_nibble(cpu):
    """The bottom four bits of F do not exist in hardware."""
    cpu.reg.sp = 0xC000  # POP reads upwards from SP
    cpu.bus.write8(0xC000, 0xFF)  # would-be F
    cpu.bus.write8(0xC001, 0x12)  # A
    run(cpu, bytes([0xF1]))  # POP AF
    assert cpu.reg.a == 0x12
    assert cpu.reg.f == 0xF0


def test_ld_sp_hl(cpu):
    cpu.reg.hl = 0xC0DE
    run(cpu, bytes([0xF9]))
    assert cpu.reg.sp == 0xC0DE


def test_stack_wraps(cpu):
    cpu.reg.sp = 0x0000
    cpu.reg.hl = 0xABCD
    run(cpu, bytes([0xE5]))  # PUSH HL
    assert cpu.reg.sp == 0xFFFE
