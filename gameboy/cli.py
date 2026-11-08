"""`gameboy demo`, `run`, `screenshot`, `info`, `disasm` and `debug`."""

from __future__ import annotations

import argparse
import sys
import time

from .cartridge import load_cartridge, parse_header
from .demo import build_demo_rom
from .disassembler import disassemble, disassemble_range
from .machine import CYCLES_PER_FRAME, GameBoy
from .png import DMG_PALETTE, GREY_PALETTE, to_ascii, write_png
from .ppu import HEIGHT, WIDTH


def _load(path: str | None) -> GameBoy:
    if path is None:
        return GameBoy.from_bytes(build_demo_rom())
    with open(path, "rb") as handle:
        return GameBoy.from_bytes(handle.read())


def info(path: str | None) -> int:
    rom = build_demo_rom() if path is None else open(path, "rb").read()
    header = parse_header(rom)
    print(f"title        {header.title}")
    print(f"controller   {header.controller}")
    print(f"rom          {header.rom_size_kb} KB ({header.rom_banks} banks)")
    print(f"ram          {header.ram_banks} bank(s)")
    print(f"header check {'ok' if header.checksum_ok else 'FAILED'}")
    try:
        load_cartridge(rom)
        print("support      yes")
    except Exception as error:  # noqa: BLE001 - reported, not swallowed
        print(f"support      no — {error}")
        return 1
    return 0


def screenshot(path: str | None, frames: int, out: str, scale: int, grey: bool) -> int:
    gameboy = _load(path)
    pixels = gameboy.run_frames(frames)
    write_png(out, pixels, WIDTH, HEIGHT, scale=scale,
              palette=GREY_PALETTE if grey else DMG_PALETTE)
    print(f"ran {frames} frame(s) of {gameboy.title!r}, wrote {out}")
    return 0


def show(path: str | None, frames: int) -> int:
    """Render to the terminal, so the emulator can be seen over ssh."""
    gameboy = _load(path)
    pixels = gameboy.run_frames(frames)
    print(to_ascii(pixels, WIDTH, HEIGHT))
    return 0


def benchmark(path: str | None, frames: int) -> int:
    gameboy = _load(path)
    gameboy.run_frames(2)  # let it get going
    started = time.perf_counter()
    gameboy.run_frames(frames)
    elapsed = time.perf_counter() - started
    speed = (frames * CYCLES_PER_FRAME) / elapsed / 1_000_000
    print(f"{frames} frames in {elapsed:.2f}s")
    print(f"{frames / elapsed:.1f} fps ({frames / elapsed / 59.7:.2f}x real time)")
    print(f"{speed:.2f} MHz emulated, against 4.19 MHz hardware")
    return 0


def disasm(path: str | None, at: int, count: int) -> int:
    gameboy = _load(path)
    for instruction in disassemble_range(gameboy.mmu.read8, at, count):
        print(instruction)
    return 0


def debug(path: str | None) -> int:
    """A small stepping debugger — the reason the disassembler exists."""
    gameboy = _load(path)
    breakpoints: set[int] = set()
    print(f"{gameboy.title!r} loaded. 'h' for help.")

    while True:
        instruction = disassemble(gameboy.mmu.read8, gameboy.cpu.reg.pc)
        print(f"{gameboy.cpu.reg}\n  next: {instruction}")
        try:
            line = input("(gb) ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        command, _, argument = line.partition(" ")
        command = command or "s"

        if command in ("q", "quit"):
            return 0
        if command in ("h", "help"):
            print(
                "  s [n]     step n instructions (default 1)\n"
                "  f [n]     run n frames\n"
                "  b addr    set a breakpoint       d addr  delete one\n"
                "  c         continue to a breakpoint\n"
                "  m addr    dump 64 bytes          l [n]   list n instructions\n"
                "  v         video registers        q       quit"
            )
        elif command == "s":
            for _ in range(int(argument or 1)):
                gameboy.step()
        elif command == "f":
            gameboy.run_frames(int(argument or 1))
        elif command == "b":
            breakpoints.add(int(argument, 0))
            print(f"breakpoints: {sorted(hex(b) for b in breakpoints)}")
        elif command == "d":
            breakpoints.discard(int(argument, 0))
        elif command == "c":
            if not breakpoints:
                print("no breakpoints; use 'b addr' first")
                continue
            for _ in range(20_000_000):
                gameboy.step()
                if gameboy.cpu.reg.pc in breakpoints:
                    print(f"stopped at ${gameboy.cpu.reg.pc:04X}")
                    break
            else:
                print("ran a long way without hitting a breakpoint")
        elif command == "m":
            base = int(argument, 0)
            for row in range(4):
                start = base + row * 16
                data = [gameboy.mmu.read8(start + i) for i in range(16)]
                text = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
                print(f"{start:04X}  " + " ".join(f"{b:02X}" for b in data) + f"  {text}")
        elif command == "l":
            for line_ in disassemble_range(gameboy.mmu.read8, gameboy.cpu.reg.pc,
                                           int(argument or 8)):
                print(f"  {line_}")
        elif command == "v":
            ppu = gameboy.ppu
            print(
                f"  LCDC={ppu.lcdc:02X} STAT={ppu.read_register(0xFF41):02X} "
                f"LY={ppu.ly:3d} LYC={ppu.lyc:3d} mode={ppu.mode} "
                f"SCX={ppu.scx:3d} SCY={ppu.scy:3d} frames={ppu.frames}"
            )
        else:
            print(f"unknown command {command!r} — 'h' for help")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gameboy", description="a Game Boy emulator")
    sub = parser.add_subparsers(dest="command")

    def add_rom(p, required=False):
        p.add_argument("rom", nargs=None if required else "?", help="path to a .gb file")

    play_parser = sub.add_parser("run", help="play a ROM in a window")
    add_rom(play_parser)
    play_parser.add_argument("--scale", type=int, default=4)
    play_parser.add_argument("--grey", action="store_true", help="use greys, not the green LCD")

    launch_parser = sub.add_parser(
        "launch", help="the launcher — covers, search and what you have played"
    )
    launch_parser.add_argument("--roms", help="a folder of .gb files to list")
    launch_parser.add_argument("--grey", action="store_true")

    menu = sub.add_parser("menu", help="the home screen — pick a cartridge and play")
    menu.add_argument("--scale", type=int, default=4)
    menu.add_argument("--roms", help="a folder of .gb files to list")
    menu.add_argument("--grey", action="store_true")
    menu.set_defaults(func="menu")

    sub.add_parser("demo", help="run the built-in demo cartridge in a window")

    shot = sub.add_parser("screenshot", help="render frames to a PNG, no window needed")
    add_rom(shot)
    shot.add_argument("--frames", type=int, default=60)
    shot.add_argument("--out", default="frame.png")
    shot.add_argument("--scale", type=int, default=3)
    shot.add_argument("--grey", action="store_true")

    show_parser = sub.add_parser("show", help="render a frame as text")
    add_rom(show_parser)
    show_parser.add_argument("--frames", type=int, default=60)

    bench = sub.add_parser("bench", help="measure emulation speed")
    add_rom(bench)
    bench.add_argument("--frames", type=int, default=600)

    info_parser = sub.add_parser("info", help="read a cartridge header")
    add_rom(info_parser)

    dis = sub.add_parser("disasm", help="disassemble a ROM")
    add_rom(dis)
    dis.add_argument("--at", type=lambda v: int(v, 0), default=0x0100)
    dis.add_argument("--count", type=int, default=24)

    debug_parser = sub.add_parser("debug", help="step through a ROM instruction by instruction")
    add_rom(debug_parser)

    args = parser.parse_args(argv)
    command = args.command

    if command == "launch":
        from .display import launch

        return launch(roms=args.roms,
                      palette=GREY_PALETTE if args.grey else DMG_PALETTE)
    if command == "menu":
        from .display import home

        return home(scale=args.scale, roms=args.roms,
                    palette=GREY_PALETTE if args.grey else DMG_PALETTE)
    if command in ("run", "demo"):
        from .display import play

        rom = None if command == "demo" else args.rom
        scale = getattr(args, "scale", 4)
        grey = getattr(args, "grey", False)
        return play(_load(rom), scale=scale, palette=GREY_PALETTE if grey else DMG_PALETTE)
    if command == "screenshot":
        return screenshot(args.rom, args.frames, args.out, args.scale, args.grey)
    if command == "show":
        return show(args.rom, args.frames)
    if command == "bench":
        return benchmark(args.rom, args.frames)
    if command == "info":
        return info(args.rom)
    if command == "disasm":
        return disasm(args.rom, args.at, args.count)
    if command == "debug":
        return debug(args.rom)

    # Bare `python -m gameboy` opens the launcher, which is the front door.
    from .display import launch

    return launch()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
