#!/usr/bin/env python3
"""Merge single-frame GIF files into a small animated GIF using the stdlib."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def _subblocks(data: bytes, offset: int) -> tuple[bytes, int]:
    start = offset
    while True:
        if offset >= len(data):
            raise ValueError("truncated GIF data sub-block")
        size = data[offset]
        offset += 1
        if size == 0:
            return data[start:offset], offset
        offset += size
        if offset > len(data):
            raise ValueError("truncated GIF data sub-block")


def _frame(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if len(data) < 14 or data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError(f"{path.name} is not a GIF")
    width, height = struct.unpack_from("<HH", data, 6)
    packed = data[10]
    offset = 13
    global_table = b""
    if packed & 0x80:
        table_length = 3 * (1 << ((packed & 0x07) + 1))
        global_table = data[offset : offset + table_length]
        if len(global_table) != table_length:
            raise ValueError(f"{path.name} has a truncated global color table")
        offset += table_length

    image_block = None
    while offset < len(data):
        marker = data[offset]
        offset += 1
        if marker == 0x3B:
            break
        if marker == 0x21:
            if offset >= len(data):
                raise ValueError(f"{path.name} has a truncated extension")
            offset += 1  # extension label
            _, offset = _subblocks(data, offset)
            continue
        if marker != 0x2C or offset + 9 > len(data):
            raise ValueError(f"{path.name} has an invalid image block")

        descriptor = bytearray(data[offset : offset + 9])
        offset += 9
        frame_packed = descriptor[8]
        if frame_packed & 0x80:
            table_length = 3 * (1 << ((frame_packed & 0x07) + 1))
            color_table = data[offset : offset + table_length]
            if len(color_table) != table_length:
                raise ValueError(f"{path.name} has a truncated local color table")
            offset += table_length
        elif global_table:
            color_table = global_table
            table_bits = packed & 0x07
            descriptor[8] = (frame_packed & 0x78) | 0x80 | table_bits
        else:
            raise ValueError(f"{path.name} has no color table")

        if offset >= len(data):
            raise ValueError(f"{path.name} has a truncated image")
        minimum_code_size = data[offset : offset + 1]
        offset += 1
        blocks, offset = _subblocks(data, offset)
        image_block = b"," + bytes(descriptor) + color_table + minimum_code_size + blocks
        if image_block is None:
            raise ValueError(f"{path.name} has no image")
        # sips creates one image per converted PNG; reject unexpected animation.
        probe = offset
        while probe < len(data) and data[probe] != 0x3B:
            marker = data[probe]
            if marker == 0x21:
                probe += 2
                _, probe = _subblocks(data, probe)
            elif marker == 0x2C:
                raise ValueError(f"{path.name} contains more than one image")
            else:
                raise ValueError(f"{path.name} has an invalid trailing block")
        break

    if image_block is None:
        raise ValueError(f"{path.name} contains no image")
    return width, height, image_block


def merge(inputs: list[Path], output: Path, delay: int = 90) -> None:
    if not inputs:
        raise ValueError("at least one GIF frame is required")
    if not 0 <= delay <= 65535:
        raise ValueError("delay must fit in an unsigned 16-bit GIF field")
    frames = [_frame(path) for path in inputs]
    width, height, _ = frames[0]
    if any((frame_width, frame_height) != (width, height) for frame_width, frame_height, _ in frames):
        raise ValueError("all GIF frames must have the same dimensions")

    output.parent.mkdir(parents=True, exist_ok=True)
    header = b"GIF89a" + struct.pack("<HHBBB", width, height, 0, 0, 0)
    loop_extension = b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
    graphic_control = b"\x21\xf9\x04\x04" + struct.pack("<H", delay) + b"\x00\x00"
    output.write_bytes(header + loop_extension + b"".join(graphic_control + block for _, _, block in frames) + b"\x3b")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=int, default=90, help="frame delay in hundredths of a second")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("frames", nargs="+", type=Path)
    args = parser.parse_args()
    merge(args.frames, args.output, args.delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
