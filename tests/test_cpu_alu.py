"""Arithmetic, logic and flags.

The flags are where emulators quietly go wrong. Half-carry in particular is
invisible in almost every program until a game uses `DAA` to print a score,
at which point the number is nonsense and the cause is twenty instructions
back. So the boundaries get tested explicitly.
"""

import pytest
from conftest import run


def flags(cpu) -> str:
    return "".join(
        letter if getattr(cpu.reg, attr) else "-"
        for letter, attr in (("Z", "flag_z"), ("N", "flag_n"), ("H", "flag_h"), ("C", "flag_c"))
    )


# ------------------------------------------------------------------------ ADD


@pytest.mark.parametrize(
    "a,b,result,expected_flags",
    [
        (0x00, 0x00, 0x00, "Z---"),
        (0x01, 0x01, 0x02, "----"),
        (0x0F, 0x01, 0x10, "--H-"),  # carry out of bit 3
        (0xFF, 0x01, 0x00, "Z-HC"),  # wraps, both carries
        (0xF0, 0x10, 0x00, "Z--C"),  # carry out of bit 7 only
        (0x3A, 0xC6, 0x00, "Z-HC"),
    ],
)
def test_add_a_flags(cpu, a, b, result, expected_flags):
    cpu.reg.a = a
    cpu.reg.b = b
    run(cpu, bytes([0x80]))  # ADD A,B
    assert cpu.reg.a == result
    assert flags(cpu) == expected_flags


def test_adc_includes_the_carry_in_the_half_carry(cpu):
    """0x0F + 0x00 + carry must set H — a classic off-by-one in emulators."""
    cpu.reg.a = 0x0F
    cpu.reg.b = 0x00
    cpu.reg.set_flags(c=True)
    run(cpu, bytes([0x88]))  # ADC A,B
    assert cpu.reg.a == 0x10
    assert flags(cpu) == "--H-"


def test_adc_carries_all_the_way(cpu):
    cpu.reg.a = 0xFF
    cpu.reg.b = 0xFF
    cpu.reg.set_flags(c=True)
    run(cpu, bytes([0x88]))
    assert cpu.reg.a == 0xFF
    assert flags(cpu) == "--HC"


# ------------------------------------------------------------------------ SUB


@pytest.mark.parametrize(
    "a,b,result,expected_flags",
    [
        (0x00, 0x00, 0x00, "ZN--"),
        (0x10, 0x01, 0x0F, "-NH-"),  # borrow from bit 4
        (0x00, 0x01, 0xFF, "-NHC"),  # borrows everywhere
        (0x3E, 0x3E, 0x00, "ZN--"),
        (0x3E, 0x40, 0xFE, "-N-C"),
    ],
)
def test_sub_flags(cpu, a, b, result, expected_flags):
    cpu.reg.a = a
    cpu.reg.b = b
    run(cpu, bytes([0x90]))  # SUB B
    assert cpu.reg.a == result
    assert flags(cpu) == expected_flags


def test_sbc_borrows_the_carry(cpu):
    cpu.reg.a = 0x10
    cpu.reg.b = 0x00
    cpu.reg.set_flags(c=True)
    run(cpu, bytes([0x98]))  # SBC A,B
    assert cpu.reg.a == 0x0F
    assert flags(cpu) == "-NH-"


def test_cp_sets_flags_but_leaves_a_alone(cpu):
    cpu.reg.a = 0x3C
    cpu.reg.b = 0x2F
    run(cpu, bytes([0xB8]))  # CP B
    assert cpu.reg.a == 0x3C
    assert flags(cpu) == "-NH-"


def test_cp_equal_sets_zero(cpu):
    cpu.reg.a = 0x40
    cpu.reg.b = 0x40
    run(cpu, bytes([0xB8]))
    assert flags(cpu) == "ZN--"


# -------------------------------------------------------------------- logical


def test_and_always_sets_half_carry(cpu):
    """The one logical operation that sets H, for no obvious reason."""
    cpu.reg.a = 0x5A
    cpu.reg.b = 0x3F
    run(cpu, bytes([0xA0]))
    assert cpu.reg.a == 0x1A
    assert flags(cpu) == "--H-"


def test_xor_clears_everything(cpu):
    cpu.reg.a = 0xFF
    run(cpu, bytes([0xAF]))  # XOR A — the idiomatic "zero A"
    assert cpu.reg.a == 0x00
    assert flags(cpu) == "Z---"


def test_or_sets_only_zero(cpu):
    cpu.reg.a = 0x00
    cpu.reg.b = 0x00
    run(cpu, bytes([0xB0]))
    assert flags(cpu) == "Z---"


# ------------------------------------------------------------------- INC/DEC


def test_inc_preserves_the_carry_flag(cpu):
    """INC is the arithmetic op that leaves C alone. Programs rely on it."""
    cpu.reg.b = 0x00
    cpu.reg.set_flags(c=True)
    run(cpu, bytes([0x04]))  # INC B
    assert cpu.reg.b == 0x01
    assert flags(cpu) == "---C"


@pytest.mark.parametrize(
    "value,result,expected_flags",
    [(0x00, 0x01, "----"), (0x0F, 0x10, "--H-"), (0xFF, 0x00, "Z-H-")],
)
def test_inc_flags(cpu, value, result, expected_flags):
    cpu.reg.b = value
    run(cpu, bytes([0x04]))
    assert cpu.reg.b == result
    assert flags(cpu) == expected_flags


@pytest.mark.parametrize(
    "value,result,expected_flags",
    [(0x01, 0x00, "ZN--"), (0x10, 0x0F, "-NH-"), (0x00, 0xFF, "-NH-")],
)
def test_dec_flags(cpu, value, result, expected_flags):
    cpu.reg.b = value
    run(cpu, bytes([0x05]))
    assert cpu.reg.b == result
    assert flags(cpu) == expected_flags


def test_inc_and_dec_of_a_pair_touch_no_flags(cpu):
    cpu.reg.bc = 0xFFFF
    cpu.reg.set_flags(z=True, n=True, h=True, c=True)
    run(cpu, bytes([0x03]))  # INC BC
    assert cpu.reg.bc == 0x0000
    assert flags(cpu) == "ZNHC"


def test_inc_hl_pointer_writes_back(cpu):
    cpu.reg.hl = 0xC000
    cpu.bus.write8(0xC000, 0x0F)
    run(cpu, bytes([0x34]))  # INC (HL)
    assert cpu.bus.read8(0xC000) == 0x10
    assert flags(cpu) == "--H-"


# ---------------------------------------------------------------- 16-bit adds


def test_add_hl_carries_from_bit_11(cpu):
    """H on a 16-bit add comes from bit 11, not bit 3."""
    cpu.reg.hl = 0x0FFF
    cpu.reg.bc = 0x0001
    run(cpu, bytes([0x09]))  # ADD HL,BC
    assert cpu.reg.hl == 0x1000
    assert flags(cpu) == "--H-"


def test_add_hl_leaves_zero_alone(cpu):
    cpu.reg.hl = 0xFFFF
    cpu.reg.bc = 0x0001
    cpu.reg.set_flags(z=True)
    run(cpu, bytes([0x09]))
    assert cpu.reg.hl == 0x0000
    # H is set too: 0xFFF + 1 overflows bit 11. The point here is Z surviving.
    assert flags(cpu) == "Z-HC", "Z is untouched by ADD HL even when the result is zero"


def test_add_sp_takes_its_flags_from_the_low_byte(cpu):
    """Both carries come from the bottom eight bits, which surprises people."""
    cpu.reg.sp = 0x000F
    run(cpu, bytes([0xE8, 0x01]))  # ADD SP,1
    assert cpu.reg.sp == 0x0010
    assert flags(cpu) == "--H-"


def test_add_sp_with_a_negative_offset(cpu):
    cpu.reg.sp = 0x0100
    run(cpu, bytes([0xE8, 0xFF]))  # ADD SP,-1
    assert cpu.reg.sp == 0x00FF


def test_ld_hl_sp_plus_offset(cpu):
    cpu.reg.sp = 0xC000
    run(cpu, bytes([0xF8, 0x08]))  # LD HL,SP+8
    assert cpu.reg.hl == 0xC008
    assert not cpu.reg.flag_z


# --------------------------------------------------------------------- DAA


@pytest.mark.parametrize("left", range(0, 100, 7))
@pytest.mark.parametrize("right", range(0, 100, 11))
def test_daa_after_addition_matches_decimal_arithmetic(cpu, left, right):
    """The real test of DAA: does packed decimal arithmetic actually work?"""
    cpu.reg.a = ((left // 10) << 4) | (left % 10)
    cpu.reg.b = ((right // 10) << 4) | (right % 10)
    run(cpu, bytes([0x80, 0x27]), steps=2)  # ADD A,B ; DAA

    total = left + right
    expected = (((total % 100) // 10) << 4) | (total % 10)
    assert cpu.reg.a == expected
    assert cpu.reg.flag_c == (total >= 100)
    assert cpu.reg.flag_z == (cpu.reg.a == 0)


@pytest.mark.parametrize("left", range(0, 100, 9))
@pytest.mark.parametrize("right", range(0, 100, 13))
def test_daa_after_subtraction_matches_decimal_arithmetic(cpu, left, right):
    cpu.reg.a = ((left // 10) << 4) | (left % 10)
    cpu.reg.b = ((right // 10) << 4) | (right % 10)
    run(cpu, bytes([0x90, 0x27]), steps=2)  # SUB B ; DAA

    difference = (left - right) % 100
    expected = ((difference // 10) << 4) | (difference % 10)
    assert cpu.reg.a == expected


def test_daa_keeps_the_subtract_flag(cpu):
    cpu.reg.a = 0x00
    run(cpu, bytes([0x27]))
    assert not cpu.reg.flag_h, "DAA always clears H"


# ------------------------------------------------------------------- misc ops


def test_cpl_inverts_a(cpu):
    cpu.reg.a = 0x35
    run(cpu, bytes([0x2F]))
    assert cpu.reg.a == 0xCA
    assert flags(cpu) == "-NH-"


def test_scf_and_ccf(cpu):
    run(cpu, bytes([0x37]))  # SCF
    assert cpu.reg.flag_c
    run(cpu, bytes([0x3F]), origin=0x0010)  # CCF
    assert not cpu.reg.flag_c
    assert flags(cpu) == "----"
