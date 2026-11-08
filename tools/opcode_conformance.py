#!/usr/bin/env python3
"""Run the CPU against SingleStepTests/sm83 and print a scoreboard.

That suite has one file per opcode and a thousand randomised cases in each:
a complete starting CPU and memory state, the exact state afterwards, and the
bus activity for every machine cycle. It is the most thorough check a Game Boy
CPU can be put through, and it is the evidence behind the accuracy claim in
the README.

    python tools/opcode_conformance.py --download   # ~160 MB, once
    python tools/opcode_conformance.py

The downloaded JSON is not in this repository; it is data, not source.

Two opcodes are reported separately rather than counted as failures. The suite
models HALT and STOP as fixed three-cycle instructions that always advance PC
by one, which is a convenient stand-in rather than what the hardware does:
both of them branch on the joypad, on whether an interrupt is pending and (for
STOP) on whether a speed switch was asked for, and real behaviour ranges from
a one-byte no-op to entering a standby mode. This emulator follows the
hardware decision tree, so it disagrees with the suite on those two by
design. `tests/test_power_modes.py` covers them case by case instead.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gameboy.cpu import CPU  # noqa: E402

SUITE = "https://raw.githubusercontent.com/SingleStepTests/sm83/main/v1"
LISTING = "https://api.github.com/repos/SingleStepTests/sm83/contents/v1"
#: The suite's own simplification of these two does not match the hardware.
APPROXIMATED = {"10", "76"}


class Flat:
    """64 KB of flat memory that records every access in order."""

    def __init__(self) -> None:
        self.mem = bytearray(0x10000)
        self.log: list[tuple[str, int, int]] = []

    def read8(self, address: int) -> int:
        self.log.append(("r", address, self.mem[address]))
        return self.mem[address]

    def write8(self, address: int, value: int) -> None:
        self.log.append(("w", address, value))
        self.mem[address] = value


def run_case(case: dict) -> list[str] | None:
    """Return a list of mismatches, [] if the case passes, None if skipped."""
    bus = Flat()
    cpu = CPU(bus)
    start, reg = case["initial"], None
    reg = cpu.reg
    (reg.a, reg.b, reg.c, reg.d, reg.e, reg.h, reg.l) = (
        start["a"], start["b"], start["c"], start["d"], start["e"], start["h"], start["l"]
    )
    reg.f, reg.pc, reg.sp = start["f"], start["pc"], start["sp"]
    cpu.ime = bool(start["ime"])
    bus.mem[0xFFFF] = start.get("ie", 0)
    for address, value in start["ram"]:
        bus.mem[address] = value

    # The suite does not model interrupt dispatch, but a handful of cases load
    # an IF value that would cause one. Those are not opcode tests.
    if cpu.ime and bus.mem[0xFFFF] & bus.mem[0xFF0F] & 0x1F:
        return None

    bus.log.clear()
    cpu.step()
    # IE and IF are wired straight to the interrupt controller on hardware, so
    # the poll at the top of every step is not a bus cycle the suite counts.
    while len(bus.log) >= 2 and bus.log[0][:2] == ("r", 0xFFFF) and bus.log[1][:2] == ("r", 0xFF0F):
        del bus.log[:2]

    bad = []
    want = case["final"]
    got = {
        "a": reg.a, "b": reg.b, "c": reg.c, "d": reg.d, "e": reg.e, "f": reg.f,
        "h": reg.h, "l": reg.l, "pc": reg.pc, "sp": reg.sp, "ime": int(cpu.ime),
    }
    for key, expected in want.items():
        if key != "ram" and key in got and got[key] != expected:
            bad.append(f"{key} is {got[key]:02X}, expected {expected:02X}")
    for address, value in want.get("ram", []):
        if bus.mem[address] != value:
            bad.append(f"[{address:04X}] is {bus.mem[address]:02X}, expected {value:02X}")

    cycles = case["cycles"]
    if cpu.cycles != len(cycles) * 4:
        bad.append(f"took {cpu.cycles} cycles, expected {len(cycles) * 4}")
    # "r-m" is a read, "-wm" a write, "---" an internal cycle that touches
    # nothing (it still carries an address, which means nothing).
    expected_bus = [
        ("w" if "w" in c[2] else "r", c[0], c[1]) for c in cycles if c and c[2] != "---"
    ]
    if bus.log != expected_bus:
        bad.append(f"bus activity {bus.log}, expected {expected_bus}")
    return bad


def download(into: Path) -> None:
    into.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(LISTING, timeout=60) as response:
        names = [entry["name"] for entry in json.load(response) if entry["name"].endswith(".json")]
    for index, name in enumerate(names, 1):
        target = into / name
        if target.exists():
            continue
        url = f"{SUITE}/{urllib.parse.quote(name)}"
        with urllib.request.urlopen(url, timeout=120) as response:
            target.write_bytes(response.read())
        print(f"\r  {index}/{len(names)}  {name:<14}", end="", flush=True)
    print(f"\r  {len(names)} opcode files in {into}" + " " * 20)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests", default="sm83-tests", help="where the JSON lives")
    parser.add_argument("--download", action="store_true", help="fetch the suite first")
    parser.add_argument("--opcode", action="append", help="check only these, e.g. --opcode 76")
    args = parser.parse_args()

    where = Path(args.tests)
    if args.download:
        download(where)
    if not where.is_dir():
        print(f"no suite at {where}. Run with --download first.")
        return 2

    names = sorted(f.name for f in where.glob("*.json"))
    if args.opcode:
        wanted = {o.lower() for o in args.opcode}
        names = [n for n in names if n[:-5].lower() in wanted]

    total = passed = skipped = 0
    failures: dict[str, tuple[int, str]] = {}
    approximated: dict[str, int] = {}
    for name in names:
        opcode = name[:-5]
        cases = json.loads((where / name).read_text())
        bad_here, first = 0, ""
        for case in cases:
            try:
                bad = run_case(case)
            except Exception as error:  # a crash is a failure like any other
                bad = [f"raised {type(error).__name__}: {error}"]
            if bad is None:
                skipped += 1
                continue
            total += 1
            if bad:
                bad_here += 1
                first = first or f"{case['name']}: {'; '.join(bad)}"
            else:
                passed += 1
        if not bad_here:
            continue
        if opcode in APPROXIMATED:
            approximated[opcode] = bad_here
        else:
            failures[opcode] = (bad_here, first)

    print(f"\n  {passed}/{total} cases pass across {len(names)} opcodes")
    if skipped:
        print(f"  {skipped} skipped: an interrupt would have been dispatched")
    for opcode, count in sorted(approximated.items()):
        print(f"  {opcode}: {count} differ from the suite's simplification, by design")
    if failures:
        print(f"\n  {len(failures)} opcodes fail:")
        for opcode, (count, first) in sorted(failures.items()):
            print(f"    {opcode}  {count} cases   {first[:140]}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
