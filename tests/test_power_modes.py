"""HALT and STOP: the two instructions that stop the CPU, and their edge cases.

Both of these are where emulators quietly diverge from hardware, because a
test ROM that only checks arithmetic never reaches them. Every case here came
from a real cartridge misbehaving.
"""

from conftest import run

from gameboy.cpu import DIV_ADDRESS, IE_ADDRESS, IF_ADDRESS, INT_TIMER, JOYP_ADDRESS

NO_BUTTON = 0xFF  # all four joypad lines high
BUTTON_HELD = 0xF0  # one line pulled low


def arm_timer(cpu):
    """Leave a timer interrupt pending."""
    cpu.bus.write8(IE_ADDRESS, INT_TIMER)
    cpu.bus.write8(IF_ADDRESS, INT_TIMER)


# ---------------------------------------------------------------------- halt


def test_the_halt_bug_repeats_the_byte_after_halt(cpu):
    """With interrupts off and one pending, PC fails to advance past the next
    byte, so it is executed twice. Games were written against this."""
    arm_timer(cpu)
    cpu.ime = False
    cpu.reg.a = 0x00
    # HALT, then INC A. The INC must happen twice, not once.
    run(cpu, bytes([0x76, 0x3C, 0x00]), steps=3, origin=0x0100)
    assert cpu.reg.a == 2, "INC A should have executed twice"
    assert not cpu.halted


def test_ei_before_halt_does_not_trigger_the_halt_bug(cpu):
    """`EI; HALT` with an interrupt pending is not the halt bug.

    EI takes effect after the instruction following it, so IME is still clear
    while HALT runs — but it is about to be set and the interrupt is about to
    be taken. Testing only `ime` fired the bug here, and the leftover flag
    then rewound PC inside the interrupt handler, making the handler's first
    instruction run twice. In Tobu Tobu Girl that instruction is a PUSH, so
    the stack gained a word and the matching RETI returned into a pointer
    table instead of the game.
    """
    arm_timer(cpu)
    cpu.ime = False
    cpu.bus.write8(JOYP_ADDRESS, NO_BUTTON)
    cpu.bus.load(0x0050, bytes([0xE5]))  # the timer handler begins PUSH HL
    cpu.reg.hl = 0xBEEF
    cpu.reg.sp = 0xFFFE

    cpu.bus.load(0x0100, bytes([0xFB, 0x76, 0x00]))  # EI; HALT; NOP
    cpu.reg.pc = 0x0100
    cpu.step()  # EI
    cpu.step()  # HALT
    assert not cpu.halt_bug, "an EI in flight means the bug does not happen"
    assert cpu.reg.pc == 0x0102, "HALT did not stop: an interrupt was pending"

    cpu.step()  # the interrupt is serviced
    assert cpu.reg.pc == 0x0050
    assert cpu.reg.sp == 0xFFFC, "only the return address was pushed"

    cpu.step()  # PUSH HL, once
    assert cpu.reg.pc == 0x0051, "the handler's first instruction ran twice"
    assert cpu.reg.sp == 0xFFFA


# ---------------------------------------------------------------------- stop


def test_stop_with_no_button_held_enters_stop_mode(cpu):
    cpu.bus.write8(JOYP_ADDRESS, NO_BUTTON)
    cpu.bus.write8(DIV_ADDRESS, 0x55)
    run(cpu, bytes([0x10, 0x00, 0x3C]), origin=0x0100)
    assert cpu.stopped
    assert cpu.reg.pc == 0x0102, "STOP is two bytes; the second is ignored"
    assert cpu.bus.read8(DIV_ADDRESS) == 0, "entering STOP mode resets DIV"


def test_a_stopped_cpu_does_nothing_but_burn_cycles(cpu):
    cpu.bus.write8(JOYP_ADDRESS, NO_BUTTON)
    run(cpu, bytes([0x10, 0x00, 0x3C]), origin=0x0100)
    cpu.reg.a = 0
    for _ in range(10):
        cpu.step()
    assert cpu.reg.a == 0, "the INC A after STOP must not run"
    assert cpu.reg.pc == 0x0102


def test_a_button_press_ends_stop_mode(cpu):
    """STOP is a standby, not a death. Games expect to come back from it."""
    cpu.bus.write8(JOYP_ADDRESS, NO_BUTTON)
    run(cpu, bytes([0x10, 0x00, 0x3C]), origin=0x0100)
    assert cpu.stopped

    cpu.bus.write8(JOYP_ADDRESS, BUTTON_HELD)
    cpu.step()
    assert not cpu.stopped
    assert cpu.reg.a == 1, "execution resumed at the instruction after STOP"


def test_stop_with_a_button_held_and_an_interrupt_pending_does_nothing(cpu):
    """The one case where STOP is a single byte and changes no mode at all."""
    cpu.bus.write8(JOYP_ADDRESS, BUTTON_HELD)
    arm_timer(cpu)
    cpu.ime = False
    cpu.bus.write8(DIV_ADDRESS, 0x55)
    run(cpu, bytes([0x10, 0x3C]), origin=0x0100)
    assert not cpu.stopped and not cpu.halted
    assert cpu.reg.pc == 0x0101, "a one-byte STOP: the next byte is not eaten"
    assert cpu.bus.read8(DIV_ADDRESS) == 0x55, "DIV is left alone"


def test_stop_with_a_button_held_and_nothing_pending_halts_instead(cpu):
    cpu.bus.write8(JOYP_ADDRESS, BUTTON_HELD)
    cpu.bus.write8(IE_ADDRESS, 0x00)
    cpu.bus.write8(IF_ADDRESS, 0x00)
    cpu.bus.write8(DIV_ADDRESS, 0x55)
    run(cpu, bytes([0x10, 0x00, 0x3C]), origin=0x0100)
    assert cpu.halted and not cpu.stopped
    assert cpu.reg.pc == 0x0102
    assert cpu.bus.read8(DIV_ADDRESS) == 0x55, "HALT mode does not reset DIV"
