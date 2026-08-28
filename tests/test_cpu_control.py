"""Jumps, calls, interrupts and cycle timing."""

import pytest
from conftest import run

from gameboy.cpu import IE_ADDRESS, IF_ADDRESS, INT_TIMER, INT_VBLANK, IllegalOpcode

# ---------------------------------------------------------------------- jumps


def test_jp_goes_where_it_is_told(cpu):
    run(cpu, bytes([0xC3, 0x00, 0xC0]))
    assert cpu.reg.pc == 0xC000


def test_jr_is_relative_and_signed(cpu):
    run(cpu, bytes([0x18, 0x05]), origin=0x0100)
    assert cpu.reg.pc == 0x0107, "the offset is measured from after the instruction"


def test_jr_can_go_backwards(cpu):
    run(cpu, bytes([0x18, 0xFE]), origin=0x0100)
    assert cpu.reg.pc == 0x0100, "-2 is the idiomatic infinite loop"


def test_jp_hl_has_no_internal_delay(cpu):
    """Every other jump costs an extra cycle. JP HL does not."""
    cpu.reg.hl = 0xC000
    cycles = run(cpu, bytes([0xE9]))
    assert cpu.reg.pc == 0xC000
    assert cycles == 4


@pytest.mark.parametrize(
    "opcode,flag,taken",
    [
        (0xC2, {"z": False}, True),  # JP NZ
        (0xC2, {"z": True}, False),
        (0xCA, {"z": True}, True),  # JP Z
        (0xD2, {"c": False}, True),  # JP NC
        (0xDA, {"c": True}, True),  # JP C
        (0xDA, {"c": False}, False),
    ],
)
def test_conditional_jumps(cpu, opcode, flag, taken):
    cpu.reg.set_flags(**flag)
    cycles = run(cpu, bytes([opcode, 0x00, 0xC0]), origin=0x0100)
    assert (cpu.reg.pc == 0xC000) is taken
    assert cycles == (16 if taken else 12), "a taken branch costs one more cycle"


def test_conditional_relative_jump_timing(cpu):
    cpu.reg.set_flags(z=False)
    assert run(cpu, bytes([0x20, 0x02]), origin=0x0100) == 12  # taken
    cpu.reg.set_flags(z=True)
    assert run(cpu, bytes([0x20, 0x02]), origin=0x0200) == 8  # not taken


# ------------------------------------------------------------- calls, returns


def test_call_pushes_the_return_address(cpu):
    run(cpu, bytes([0xCD, 0x00, 0xC0]), origin=0x0100)
    assert cpu.reg.pc == 0xC000
    assert cpu.reg.sp == 0xFFFC
    assert cpu.bus.read8(0xFFFC) == 0x03  # low byte of 0x0103
    assert cpu.bus.read8(0xFFFD) == 0x01


def test_call_and_ret_return_to_the_right_place(cpu):
    cpu.bus.load(0xC000, bytes([0xC9]))  # RET
    run(cpu, bytes([0xCD, 0x00, 0xC0]), steps=2, origin=0x0100)
    assert cpu.reg.pc == 0x0103
    assert cpu.reg.sp == 0xFFFE


def test_conditional_call_not_taken_costs_less(cpu):
    cpu.reg.set_flags(z=True)
    cycles = run(cpu, bytes([0xC4, 0x00, 0xC0]), origin=0x0100)  # CALL NZ
    assert cpu.reg.pc == 0x0103
    assert cycles == 12


def test_conditional_return_checks_before_popping(cpu):
    cpu.reg.set_flags(z=True)
    cycles = run(cpu, bytes([0xC0]))  # RET NZ, not taken
    assert cycles == 8, "the condition check alone costs a cycle"


def test_rst_jumps_to_a_fixed_vector(cpu):
    run(cpu, bytes([0xDF]), origin=0x0100)  # RST 18h
    assert cpu.reg.pc == 0x0018
    assert cpu.bus.read8(0xFFFC) == 0x01


def test_reti_returns_and_re_enables_interrupts(cpu):
    cpu.ime = False
    cpu.reg.sp = 0xFFFC
    cpu.bus.write8(0xFFFC, 0x34)
    cpu.bus.write8(0xFFFD, 0x12)
    run(cpu, bytes([0xD9]))
    assert cpu.reg.pc == 0x1234
    assert cpu.ime


# ----------------------------------------------------------------- interrupts


def arm(cpu, interrupt):
    cpu.bus.write8(IE_ADDRESS, interrupt)
    cpu.bus.write8(IF_ADDRESS, interrupt)


def test_an_interrupt_jumps_to_its_vector(cpu):
    cpu.ime = True
    arm(cpu, INT_VBLANK)
    cpu.reg.pc = 0x0150
    cpu.step()
    assert cpu.reg.pc == 0x0040
    assert not cpu.ime, "servicing an interrupt disables further ones"
    assert cpu.bus.read8(IF_ADDRESS) & INT_VBLANK == 0, "the flag is cleared"
    assert cpu.bus.read8(0xFFFC) == 0x50, "the interrupted PC was pushed"


def test_servicing_takes_twenty_cycles(cpu):
    cpu.ime = True
    arm(cpu, INT_VBLANK)
    assert cpu.step() == 20


def test_interrupts_are_ignored_while_ime_is_clear(cpu):
    cpu.ime = False
    arm(cpu, INT_VBLANK)
    run(cpu, bytes([0x00]), origin=0x0150)  # NOP
    assert cpu.reg.pc == 0x0151


def test_a_masked_interrupt_does_not_fire(cpu):
    cpu.ime = True
    cpu.bus.write8(IE_ADDRESS, 0x00)
    cpu.bus.write8(IF_ADDRESS, INT_VBLANK)
    run(cpu, bytes([0x00]), origin=0x0150)
    assert cpu.reg.pc == 0x0151


def test_the_lowest_bit_wins(cpu):
    """VBlank outranks the timer, whatever order they arrived in."""
    cpu.ime = True
    cpu.bus.write8(IE_ADDRESS, INT_VBLANK | INT_TIMER)
    cpu.bus.write8(IF_ADDRESS, INT_VBLANK | INT_TIMER)
    cpu.step()
    assert cpu.reg.pc == 0x0040
    assert cpu.bus.read8(IF_ADDRESS) & INT_TIMER, "the timer request is still waiting"


def test_ei_takes_effect_one_instruction_late(cpu):
    """`EI; RET` must not be interrupted between the two instructions."""
    cpu.ime = False
    arm(cpu, INT_VBLANK)
    cpu.bus.load(0x0150, bytes([0xFB, 0x00]))  # EI ; NOP
    cpu.reg.pc = 0x0150

    cpu.step()  # EI
    assert not cpu.ime
    cpu.step()  # NOP runs with interrupts still off
    assert cpu.ime
    assert cpu.reg.pc == 0x0152

    cpu.step()  # now the interrupt is serviced
    assert cpu.reg.pc == 0x0040


def test_di_cancels_a_pending_ei(cpu):
    cpu.bus.load(0x0150, bytes([0xFB, 0xF3]))  # EI ; DI
    cpu.reg.pc = 0x0150
    cpu.step()
    cpu.step()
    assert not cpu.ime


# ----------------------------------------------------------------------- halt


def test_halt_stops_executing(cpu):
    run(cpu, bytes([0x76]), origin=0x0150)
    assert cpu.halted
    before = cpu.reg.pc
    cpu.step()
    assert cpu.reg.pc == before, "a halted CPU does not advance"


def test_an_interrupt_wakes_a_halted_cpu_even_with_ime_clear(cpu):
    """This is how a game waits for VBlank without enabling interrupts."""
    cpu.ime = False
    cpu.halted = True
    arm(cpu, INT_VBLANK)
    cpu.reg.pc = 0x0150
    cpu.bus.load(0x0150, bytes([0x00]))
    cpu.step()
    assert not cpu.halted
    assert cpu.reg.pc == 0x0151, "execution resumes rather than jumping to a vector"


def test_the_halt_bug(cpu):
    """With IME clear and an interrupt pending, PC fails to advance past HALT,
    so the following byte executes twice. Games were written against it."""
    cpu.ime = False
    arm(cpu, INT_VBLANK)
    cpu.bus.load(0x0150, bytes([0x76, 0x3C]))  # HALT ; INC A
    cpu.reg.pc = 0x0150
    cpu.reg.a = 0x00

    cpu.step()  # HALT — bugged, PC steps back
    assert cpu.halt_bug
    assert not cpu.halted
    cpu.step()
    cpu.step()
    assert cpu.reg.a == 0x02, "INC A ran twice"


# --------------------------------------------------------------------- timing


@pytest.mark.parametrize(
    "program,expected",
    [
        (bytes([0x00]), 4),  # NOP
        (bytes([0x06, 0x00]), 8),  # LD B,d8
        (bytes([0x01, 0x00, 0x00]), 12),  # LD BC,d16
        (bytes([0x70]), 8),  # LD (HL),B
        (bytes([0x46]), 8),  # LD B,(HL)
        (bytes([0x34]), 12),  # INC (HL)
        (bytes([0x03]), 8),  # INC BC
        (bytes([0x09]), 8),  # ADD HL,BC
        (bytes([0xC5]), 16),  # PUSH BC
        (bytes([0xC1]), 12),  # POP BC
        (bytes([0xC9]), 16),  # RET
        (bytes([0xC3, 0x00, 0x00]), 16),  # JP a16
        (bytes([0xCD, 0x00, 0x00]), 24),  # CALL a16
        (bytes([0xE8, 0x01]), 16),  # ADD SP,r8
        (bytes([0xF8, 0x01]), 12),  # LD HL,SP+r8
        (bytes([0xF9]), 8),  # LD SP,HL
        (bytes([0xEA, 0x00, 0xC0]), 16),  # LD (a16),A
        (bytes([0xCB, 0x00]), 8),  # RLC B
        (bytes([0xCB, 0x06]), 16),  # RLC (HL)
        (bytes([0xCB, 0x46]), 12),  # BIT 0,(HL) — reads but never writes
        (bytes([0xC7]), 16),  # RST 00h
    ],
)
def test_instruction_timing(cpu, program, expected):
    cpu.reg.hl = 0xC000
    assert run(cpu, program) == expected


# -------------------------------------------------------------------- illegal


ILLEGAL = [0xD3, 0xDB, 0xDD, 0xE3, 0xE4, 0xEB, 0xEC, 0xED, 0xF4, 0xFC, 0xFD]


@pytest.mark.parametrize("opcode", ILLEGAL)
def test_the_eleven_illegal_opcodes_are_rejected(cpu, opcode):
    with pytest.raises(IllegalOpcode):
        run(cpu, bytes([opcode]))
