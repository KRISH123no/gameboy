"""Rotates, shifts and the CB-prefixed bit instructions."""

import pytest
from conftest import run


def test_rlca_never_sets_zero(cpu):
    """The accumulator rotates clear Z; their CB twins set it from the result.

    This asymmetry is real and catches people: `RLCA` on zero leaves Z clear,
    `RLC A` on zero sets it.
    """
    cpu.reg.a = 0x00
    run(cpu, bytes([0x07]))  # RLCA
    assert cpu.reg.a == 0x00
    assert not cpu.reg.flag_z

    cpu.reg.a = 0x00
    run(cpu, bytes([0xCB, 0x07]), origin=0x0010)  # RLC A
    assert cpu.reg.flag_z


@pytest.mark.parametrize("opcode", [0x07, 0x0F, 0x17, 0x1F])
def test_every_accumulator_rotate_clears_zero(cpu, opcode):
    cpu.reg.a = 0x00
    cpu.reg.set_flags(z=True)
    run(cpu, bytes([opcode]))
    assert not cpu.reg.flag_z


def test_rlc_moves_bit_7_to_bit_0_and_to_carry(cpu):
    cpu.reg.b = 0x85
    run(cpu, bytes([0xCB, 0x00]))  # RLC B
    assert cpu.reg.b == 0x0B
    assert cpu.reg.flag_c


def test_rrc_moves_bit_0_to_bit_7(cpu):
    cpu.reg.b = 0x01
    run(cpu, bytes([0xCB, 0x08]))  # RRC B
    assert cpu.reg.b == 0x80
    assert cpu.reg.flag_c


def test_rl_rotates_through_the_carry(cpu):
    cpu.reg.b = 0x80
    cpu.reg.set_flags(c=False)
    run(cpu, bytes([0xCB, 0x10]))  # RL B
    assert cpu.reg.b == 0x00
    assert cpu.reg.flag_c
    assert cpu.reg.flag_z


def test_rl_brings_the_carry_back_in(cpu):
    cpu.reg.b = 0x00
    cpu.reg.set_flags(c=True)
    run(cpu, bytes([0xCB, 0x10]))
    assert cpu.reg.b == 0x01
    assert not cpu.reg.flag_c


def test_rr_rotates_the_other_way(cpu):
    cpu.reg.b = 0x01
    cpu.reg.set_flags(c=True)
    run(cpu, bytes([0xCB, 0x18]))  # RR B
    assert cpu.reg.b == 0x80
    assert cpu.reg.flag_c


def test_sla_shifts_a_zero_in(cpu):
    cpu.reg.b = 0xFF
    run(cpu, bytes([0xCB, 0x20]))  # SLA B
    assert cpu.reg.b == 0xFE
    assert cpu.reg.flag_c


def test_sra_preserves_the_sign_bit(cpu):
    """Arithmetic shift right: bit 7 is copied, so negatives stay negative."""
    cpu.reg.b = 0x81
    run(cpu, bytes([0xCB, 0x28]))  # SRA B
    assert cpu.reg.b == 0xC0
    assert cpu.reg.flag_c


def test_srl_shifts_a_zero_into_the_top(cpu):
    cpu.reg.b = 0x81
    run(cpu, bytes([0xCB, 0x38]))  # SRL B
    assert cpu.reg.b == 0x40
    assert cpu.reg.flag_c


def test_swap_exchanges_the_nibbles(cpu):
    cpu.reg.b = 0xAB
    run(cpu, bytes([0xCB, 0x30]))  # SWAP B
    assert cpu.reg.b == 0xBA
    assert not cpu.reg.flag_c


def test_swap_of_zero_sets_zero(cpu):
    cpu.reg.b = 0x00
    run(cpu, bytes([0xCB, 0x30]))
    assert cpu.reg.flag_z


@pytest.mark.parametrize("bit", range(8))
def test_bit_tests_without_changing_anything(cpu, bit):
    cpu.reg.b = 1 << bit
    cpu.reg.set_flags(c=True)
    run(cpu, bytes([0xCB, 0x40 | (bit << 3)]))
    assert cpu.reg.b == 1 << bit, "BIT must not modify the register"
    assert not cpu.reg.flag_z
    assert cpu.reg.flag_h
    assert cpu.reg.flag_c, "BIT leaves C alone"


@pytest.mark.parametrize("bit", range(8))
def test_bit_sets_zero_when_the_bit_is_clear(cpu, bit):
    cpu.reg.b = (~(1 << bit)) & 0xFF
    run(cpu, bytes([0xCB, 0x40 | (bit << 3)]))
    assert cpu.reg.flag_z


@pytest.mark.parametrize("bit", range(8))
def test_res_clears_one_bit(cpu, bit):
    cpu.reg.b = 0xFF
    run(cpu, bytes([0xCB, 0x80 | (bit << 3)]))
    assert cpu.reg.b == (0xFF & ~(1 << bit)) & 0xFF


@pytest.mark.parametrize("bit", range(8))
def test_set_sets_one_bit(cpu, bit):
    cpu.reg.b = 0x00
    run(cpu, bytes([0xCB, 0xC0 | (bit << 3)]))
    assert cpu.reg.b == 1 << bit


def test_bit_operations_reach_through_hl(cpu):
    cpu.reg.hl = 0xC000
    cpu.bus.write8(0xC000, 0x00)
    run(cpu, bytes([0xCB, 0xC6]))  # SET 0,(HL)
    assert cpu.bus.read8(0xC000) == 0x01


@pytest.mark.parametrize("z", range(8))
def test_every_cb_rotate_target(cpu, z):
    """All eight destinations, so the (HL) case cannot be forgotten."""
    # Set up only the target: indices 4 and 5 *are* H and L, so touching the
    # others would move the pointer the (HL) case depends on.
    cpu.reg.hl = 0xC000
    if z == 6:
        cpu.bus.write8(0xC000, 0x01)
    else:
        cpu.set_r(z, 0x01)

    run(cpu, bytes([0xCB, 0x00 | z]))  # RLC r
    assert cpu.get_r(z) == 0x02
