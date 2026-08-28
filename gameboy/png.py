"""A minimal PNG writer.

The emulator has no runtime dependencies, and being able to dump a frame to a
file is too useful to trade that away for — it is how the renderer gets tested
without a window. PNG is a chunk container around a zlib stream, and zlib is
in the standard library, so this is about thirty lines.
"""

from __future__ import annotations

import struct
import zlib

#: The four shades of the original hardware, greenest first.
DMG_PALETTE = ((0x9B, 0xBC, 0x0F), (0x8B, 0xAC, 0x0F), (0x30, 0x62, 0x30), (0x0F, 0x38, 0x0F))
GREY_PALETTE = ((0xFF, 0xFF, 0xFF), (0xAA, 0xAA, 0xAA), (0x55, 0x55, 0x55), (0x00, 0x00, 0x00))


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(path: str, pixels: bytes, width: int, height: int, *, scale: int = 1,
              palette=DMG_PALETTE) -> None:
    """Write shade indices (0-3) as an RGB image."""
    rows = bytearray()
    for y in range(height):
        for _ in range(scale):
            rows.append(0)  # PNG filter type for this row: none
            for x in range(width):
                red, green, blue = palette[pixels[y * width + x] & 3]
                rows += bytes((red, green, blue)) * scale

    header = struct.pack(">IIBBBBB", width * scale, height * scale, 8, 2, 0, 0, 0)
    with open(path, "wb") as handle:
        handle.write(b"\x89PNG\r\n\x1a\n")
        handle.write(_chunk(b"IHDR", header))
        handle.write(_chunk(b"IDAT", zlib.compress(bytes(rows), 6)))
        handle.write(_chunk(b"IEND", b""))


def to_ascii(pixels: bytes, width: int, height: int, *, step: int = 2) -> str:
    """The frame as text, for a terminal or a failing test's output."""
    shades = " .:#"
    lines = []
    for y in range(0, height, step * 2):
        lines.append("".join(shades[pixels[y * width + x] & 3] for x in range(0, width, step)))
    return "\n".join(lines)
