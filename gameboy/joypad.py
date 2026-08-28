"""The eight buttons.

They are read through one register with two selector bits, so the CPU asks for
either the direction pad or the action buttons and reads four bits back. A
pressed button reads as 0, not 1, because the hardware is wired to ground.
"""

from __future__ import annotations

from enum import IntFlag

INT_JOYPAD = 0x10


class Button(IntFlag):
    RIGHT = 0x01
    LEFT = 0x02
    UP = 0x04
    DOWN = 0x08
    A = 0x10
    B = 0x20
    SELECT = 0x40
    START = 0x80


class Joypad:
    def __init__(self, request_interrupt=None) -> None:
        self.pressed = Button(0)
        self._select = 0x30
        self._request_interrupt = request_interrupt or (lambda bit: None)

    def press(self, button: Button) -> None:
        if button not in self.pressed:
            self.pressed |= button
            # Any press can wake the CPU from STOP.
            self._request_interrupt(INT_JOYPAD)

    def release(self, button: Button) -> None:
        self.pressed &= ~button

    def write(self, value: int) -> None:
        self._select = value & 0x30

    def read(self) -> int:
        low = 0x0F
        if not self._select & 0x10:  # directions selected
            low &= ~(int(self.pressed) & 0x0F) & 0x0F
        if not self._select & 0x20:  # actions selected
            low &= ~((int(self.pressed) >> 4) & 0x0F) & 0x0F
        return 0xC0 | self._select | low
