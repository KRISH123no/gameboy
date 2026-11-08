# gameboy

[![CI](https://github.com/KRISH123no/gameboy/actions/workflows/ci.yml/badge.svg)](https://github.com/KRISH123no/gameboy/actions/workflows/ci.yml)

![three frames of the demo cartridge: a face walking across a scrolling background](assets/demo.png)

**A Game Boy, in Python, from nothing.** A cycle-counted CPU, a scanline graphics chip, cartridge
bank switching, a disassembler and a stepping debugger. No runtime dependencies — pygame only if
you want a window.

Those three frames are not drawn by Python. They are a cartridge, assembled by this repository and
executed by the emulated CPU: it copies tile data into video memory, builds the tilemap, and moves
the sprite once per vertical blank, exactly as a real game does.

```bash
pip install -e ".[dev,play]"
python -m gameboy demo              # play the built-in cartridge in a window
python -m gameboy run tetris.gb     # play your own ROM
python -m gameboy screenshot --frames 120 --out frame.png
python -m gameboy debug             # step through instructions
```

It runs at **62 fps**, slightly faster than the original hardware's 59.7.

## What's inside

| | |
|---|---|
| **CPU** | The Sharp LR35902. All 512 opcodes including the CB-prefixed set, interrupts with correct priority and timing, and the HALT bug |
| **Graphics** | Scanline renderer: background, window, sprites with flipping and priority, the ten-sprite-per-line limit, the mode timing games depend on |
| **Cartridges** | Header parsing with checksum, and MBC1, MBC3 and MBC5 bank switching, so ROMs up to 2 MB work |
| **Memory** | The full map, including echo RAM and OAM DMA |
| **Timer** | DIV and the programmable timer, driven off counter edges rather than division |
| **Input** | The eight buttons, on the two-group selector the hardware uses |
| **Tools** | A disassembler, a stepping debugger with breakpoints, PNG and terminal output |

## Two decisions worth defending

**Nothing is decoded from a table of 512 entries.** The instruction set is laid out on a bit
pattern — `xxyyyzzz`, where `z` usually picks a register and `y` an operation — so eight families
of instructions are eight small blocks of code instead of four hundred branches. The disassembler
is written from the same decoding, which means the two cannot drift: if an opcode executes, it
disassembles.

**Cycle counts are not read from a table either.** Every memory access charges four cycles where
it happens and every internal delay is charged where it occurs, so a conditional jump costs 12 or
16 automatically depending on whether it was taken. That is not tidiness — the graphics chip is
driven by exactly this count, and a wrong number tears the screen.

## What the tests actually check

```bash
pytest -q     # 558 tests, under two seconds
ruff check .
```

No downloaded test ROMs. The CPU is tested by assembling instruction sequences directly and
asserting on registers, flags and cycle counts, which is both licence-free and far more precise
about *why* something failed.

The test worth reading is the one for `DAA`, the instruction that fixes up decimal arithmetic after
an add. Rather than checking a handful of values, it runs about 240 real decimal sums and
differences through the CPU and compares against ordinary arithmetic. If half-carry were wrong
anywhere — the flag that is invisible until a game prints a score and the number is nonsense —
that test fails.

Three real bugs the suite caught while this was being written:

- **The HALT bug looped forever.** With interrupts disabled and one pending, the hardware repeats
  the byte *after* `HALT`. I rewound the program counter inside `HALT` itself, so the CPU
  re-executed the halt endlessly.
- **`DI` did not cancel a pending `EI`.** `EI` enables interrupts one instruction late; if that
  instruction is `DI`, they must stay off. Mine turned them back on.
- **The window would have crashed on its first frame** — `blit_into` is not a method on a pygame
  surface, and nothing but actually running it would have found that.
- **And then it crashed on launch anyway, for two months.** The key map built pygame's constants
  from each key's label — `f"K_{name}"` — which gives `K_up`. pygame spells named keys upper and
  letters lower, so six of the eight did not exist and `demo` died on the line that builds the map.
  Every headless path passed the whole time: the benchmark, the screenshots, all 555 tests. The
  names are now pygame's own, spelled out, and a test asserts each one resolves.

## Playing your own ROMs

`python -m gameboy run yourgame.gb`

Commercial ROMs are copyrighted and are not distributed here; the cartridge in this repository is
one I wrote for it. Controls are arrow keys, `Z` and `X` for A and B, enter and shift for start and
select, `tab` to fast-forward, `p` to switch between the green LCD and greyscale, `F2` for a
screenshot.

## Layout

| File | Lines | Role |
|---|---:|---|
| `cpu.py` | 685 | the processor: decoding, arithmetic, interrupts |
| `ppu.py` | 348 | the graphics chip |
| `cartridge.py` | 245 | headers and the bank-switching controllers |
| `demo.py` | 222 | a small assembler, and the cartridge it builds |
| `cli.py` | 218 | run, screenshot, disassemble, debug |
| `mmu.py` | 175 | the memory map |
| `disassembler.py` | 151 | bytes back into instructions |
| `registers.py` | 145 | the register file |
| `display.py` | 110 | the window, the only file that imports pygame |
| `machine.py` `timer.py` `joypad.py` `png.py` | 305 | the machine, the timer, input, and a PNG writer |

2,653 lines of implementation, 1,497 of tests.

## Not implemented

- **No sound.** The four audio channels are the obvious next piece and a substantial one.
- **No pixel-level graphics timing.** Each scanline is rendered at its end, so the handful of
  effects that change scroll registers *mid-line* will not appear. Mode timing is otherwise
  correct, so the far more common per-line effects do work.
- **No real-time clock.** MBC3 carries one; its registers read as zero, so games that use it run
  but never see a day pass.
- **No Game Boy Color.** Original hardware only: four shades, no double speed.
- **No save files.** Cartridge RAM works while running but is not written to disk.
- **DMA is instant.** Real hardware takes 160 cycles and locks the bus; the visible result is the
  same, but a game that deliberately reads during a DMA would see the wrong thing.

## Licence

MIT.
