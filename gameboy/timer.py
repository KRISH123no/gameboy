"""The divider and the programmable timer.

Two counters share one internal 16-bit clock. DIV is simply the top eight bits
of it, ticking at 16384 Hz and unstoppable. TIMA is selectable and raises an
interrupt when it overflows, which is how games get a steady tick that does
not depend on the screen.

Writing to DIV resets the whole internal counter, not just the visible byte —
a detail games use to get a known phase, and one that makes a naive
implementation drift.
"""

from __future__ import annotations

INT_TIMER = 0x04

#: Which bit of the internal counter drives TIMA, per the TAC setting.
CLOCK_SELECT_BITS = (9, 3, 5, 7)  # 4096, 262144, 65536, 16384 Hz


class Timer:
    def __init__(self, request_interrupt=None) -> None:
        self.counter = 0  # the internal 16-bit clock
        self.tima = 0
        self.tma = 0
        self.tac = 0
        self._request_interrupt = request_interrupt or (lambda bit: None)
        self._last_edge = False

    @property
    def div(self) -> int:
        return (self.counter >> 8) & 0xFF

    @property
    def enabled(self) -> bool:
        return bool(self.tac & 0x04)

    def step(self, t_cycles: int) -> None:
        """Advance the internal clock and count the TIMA increments it caused.

        The obvious implementation ticks one cycle at a time and looks for a
        falling edge, which is correct and far too slow — it is the hottest
        loop in the emulator. A falling edge of bit N happens exactly at the
        multiples of 2^(N+1), so the number of edges in a span is a division
        rather than a loop.
        """
        start = self.counter
        self.counter = (start + t_cycles) & 0xFFFF
        if not self.enabled:
            self._last_edge = False
            return

        period = 1 << (CLOCK_SELECT_BITS[self.tac & 0x03] + 1)
        # Counted on the unwrapped value so a 16-bit wrap does not lose an edge.
        edges = (start + t_cycles) // period - start // period
        if edges:
            self._add(edges)
        self._last_edge = bool(self.counter & (period >> 1))

    def _add(self, edges: int) -> None:
        value = self.tima + edges
        if value <= 0xFF:
            self.tima = value
            return
        # On overflow TIMA reloads from TMA. A very long step can overflow
        # more than once, so this loops rather than assuming it happens once.
        self._request_interrupt(INT_TIMER)
        span = 0x100 - self.tma
        if span <= 0:
            self.tima = self.tma
            return
        self.tima = self.tma + (value - 0x100) % span

    def _check_edge(self) -> None:
        """Recompute the edge state after a register write."""
        period = 1 << (CLOCK_SELECT_BITS[self.tac & 0x03] + 1)
        self._last_edge = self.enabled and bool(self.counter & (period >> 1))

    def read(self, address: int) -> int:
        if address == 0xFF04:
            return self.div
        if address == 0xFF05:
            return self.tima & 0xFF
        if address == 0xFF06:
            return self.tma
        return self.tac | 0xF8

    def write(self, address: int, value: int) -> None:
        value &= 0xFF
        if address == 0xFF04:
            self.counter = 0  # any write clears the whole internal counter
            self._check_edge()
        elif address == 0xFF05:
            self.tima = value
        elif address == 0xFF06:
            self.tma = value
        elif address == 0xFF07:
            self.tac = value & 0x07
            self._check_edge()
