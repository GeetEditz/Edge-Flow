"""Deterministic sample catalogue for demonstrations (~9 MB, 32 objects of 1 KB - 2 MB).

Files are real and valid for their type: PNG images are encoded with zlib + CRC chunks,
text assets are actual HTML/CSS/JS/JSON. Media segments are opaque binary blobs (.bin).
"""

from __future__ import annotations

import json
import random
import struct
import zlib
from pathlib import Path

PHOTO_SIZES = [48, 64, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224, 240, 256, 272, 288, 304, 320, 336, 352]
SEGMENT_KB = [256, 512, 768, 1024, 1536, 2048]


def _png(width: int, height: int, rng: random.Random) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    base = [rng.randrange(256) for _ in range(3)]
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter: none
        for x in range(width):
            rows += bytes(((base[c] + x * (c + 1) + y * (3 - c) + rng.randrange(64)) & 0xFF) for c in range(3))
    ihdr = struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(rows), 1)) + chunk(b"IEND", b"")


def generate(root: Path, seed: int = 7) -> int:
    rng = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}
    files["index.html"] = ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>CDN sample</title>"
                           "<link rel=\"stylesheet\" href=\"styles/main.css\"></head><body><main>\n"
                           + "\n".join(f"<section id=\"s{i}\"><h2>Section {i}</h2><p>{'Lorem ipsum dolor sit amet. ' * 6}</p>"
                                       f"<img src=\"images/photo-{(i % 20) + 1:02d}.png\" alt=\"\"></section>" for i in range(24))
                           + "\n</main><script src=\"scripts/app.js\"></script></body></html>\n").encode()
    files["styles/main.css"] = "\n".join(
        f".c{i} {{ margin: {i % 9 * 4}px; padding: {i % 5 * 4}px; color: #{rng.randrange(0x1000000):06x}; }}"
        for i in range(160)).encode()
    files["scripts/app.js"] = "\n".join(
        f"export function f{i}(x) {{ return (x * {i} + {rng.randrange(1000)}) % {rng.randrange(1, 997)}; }}"
        for i in range(420)).encode()
    files["data/catalog.json"] = json.dumps(
        [{"sku": f"SKU-{i:05d}", "price": round(rng.uniform(1, 500), 2), "stock": rng.randrange(1000)}
         for i in range(260)], indent=1).encode()
    files["docs/readme.txt"] = b"Sample content served by the Distributed CDN Simulator origin.\n" * 30
    files["images/logo.svg"] = (b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
                                b'<rect width="64" height="64" rx="8" fill="#2563EB"/><circle cx="32" cy="32" r="14" '
                                b'fill="none" stroke="#fff" stroke-width="4"/></svg>\n')
    for i, s in enumerate(PHOTO_SIZES, 1):
        files[f"images/photo-{i:02d}.png"] = _png(s, s, rng)
    for i, kb in enumerate(SEGMENT_KB, 1):
        files[f"media/segment-{i:02d}.bin"] = rng.randbytes(kb * 1024)
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return len(files)


def ensure(root: Path) -> None:
    if not root.is_dir() or not any(root.rglob("*.png")):
        generate(root)
