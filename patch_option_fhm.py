#!/usr/bin/env python3
"""
Traduit l'écran Options (option.fhm) — japonais → anglais.

Ré-encodage ULZ bit-exact :
  - tuiles / plans inchangés → octets d'origine
  - plans modifiés → adaptation token-preserving (ulz_codec.reencode_chunk)
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ulz_codec import PlaneSlice, decode_chunk_planes, parse_plane_stream, reencode_chunk
from ulz_decode import decode_plane, load_ite

ROOT = Path(__file__).resolve().parent
SRC_FHM = ROOT / "cdimage_unpacked/seven_data_link/futa/screen/option.fhm"
OUT_FHM = ROOT / "option_en.fhm"
SCRATCH = ROOT / "scratch/option_patch"
ITE_ENTRIES = (0xEF0, 0x13810)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
]

PARCHMENT = (228, 210, 175)

TEXT_OVERLAYS = [
    (215, 16, 210, 32, "Options", 20, (255, 255, 255), "mm"),
    (100, 90, 155, 18, "Window Type", 12, (50, 40, 30), "lm"),
    (100, 124, 155, 18, "Message Skip", 12, (50, 40, 30), "lm"),
    (100, 160, 130, 18, "Music Vol.", 12, (50, 40, 30), "lm"),
    (100, 194, 130, 18, "SFX Vol.", 12, (50, 40, 30), "lm"),
    (100, 228, 130, 18, "Voice Vol.", 12, (50, 40, 30), "lm"),
    (100, 264, 130, 18, "Sound Mode", 12, (50, 40, 30), "lm"),
    (100, 300, 175, 18, "Screen Position", 12, (50, 40, 30), "lm"),
    (302, 90, 22, 18, "A", 12, (50, 40, 30), "mm"),
    (342, 90, 22, 18, "B", 12, (50, 40, 30), "mm"),
    (530, 90, 80, 18, "Type A", 10, (240, 240, 235), "mm"),
    (270, 124, 48, 18, "All", 11, (50, 40, 30), "mm"),
    (332, 124, 62, 18, "Normal", 11, (50, 40, 30), "mm"),
    (408, 124, 48, 18, "Fast", 11, (50, 40, 30), "mm"),
    (270, 264, 62, 18, "Stereo", 11, (50, 40, 30), "mm"),
    (358, 264, 52, 18, "Mono", 11, (50, 40, 30), "mm"),
    (108, 400, 130, 20, "Done", 11, (50, 40, 30), "mm"),
    (300, 400, 130, 20, "Defaults", 11, (50, 40, 30), "mm"),
    (492, 400, 90, 20, "Cancel", 11, (50, 40, 30), "mm"),
]


def lzss_decompress(data: bytes) -> bytes:
    n = 2048
    r = n - 18
    text_buf = bytearray(n)
    out = bytearray()
    pos = 0
    while pos < len(data):
        flags = data[pos]
        pos += 1
        for bit in range(8):
            if pos >= len(data):
                break
            if (flags >> bit) & 1:
                c = data[pos]
                pos += 1
                out.append(c)
                text_buf[r] = c
                r = (r + 1) & (n - 1)
            else:
                if pos + 1 >= len(data):
                    break
                lo, hi = data[pos], data[pos + 1]
                pos += 2
                mo = lo | ((hi & 0x0F) << 8)
                ml = (hi >> 4) + 2
                for k in range(ml):
                    c = text_buf[(mo + k) & (n - 1)]
                    out.append(c)
                    text_buf[r] = c
                    r = (r + 1) & (n - 1)
    return bytes(out)


def unswizzle_clut(raw: bytes, n: int) -> bytes:
    c = bytearray(raw[: n * 4])
    for bank in range(n // 32):
        b = bank * 32 * 4
        c[b + 8 * 4 : b + 16 * 4], c[b + 16 * 4 : b + 24 * 4] = (
            bytearray(c[b + 16 * 4 : b + 24 * 4]),
            bytearray(c[b + 8 * 4 : b + 16 * 4]),
        )
    return bytes(c)


def build_palette(clut_bytes: bytes, n: int = 256) -> list[int]:
    clut_bytes = unswizzle_clut(clut_bytes, n)
    pal = []
    for i in range(n):
        b, g, r, a = clut_bytes[i * 4 : i * 4 + 4]
        pal.extend([r, g, b, min(255, a * 2)])
    return pal


def load_clut(data: bytes) -> list[int]:
    return build_palette(lzss_decompress(data[0x40 : 0x40 + 3760]))


def ite_entry_end(data: bytes | bytearray, ite_off: int) -> int:
    n = int.from_bytes(data[0:4], "little")
    fhm_offs = [int.from_bytes(data[4 + i * 4 : 8 + i * 4], "little") for i in range(n)]
    for i, off in enumerate(fhm_offs):
        if off <= ite_off < (fhm_offs[i + 1] if i + 1 < n else len(data)):
            return fhm_offs[i + 1] if i + 1 < n else len(data)
    return len(data)


def tile_chunk_bounds(data: bytes | bytearray, ite_off: int, tile_idx: int) -> tuple[int, int]:
    _, _, n, offs = load_ite(data, ite_off)
    src = ite_off + offs[tile_idx]
    end = ite_off + offs[tile_idx + 1] if tile_idx + 1 < n else ite_entry_end(data, ite_off)
    return src, end - src


def decode_ite_index(data: bytes | bytearray, ite_off: int) -> tuple[Image.Image, int, int]:
    w, h, n, offs = load_ite(data, ite_off)
    tx = w // 64
    img = Image.new("L", (w, h))
    px = img.load()
    for idx in range(n):
        src = ite_off + offs[idx]
        if data[src] == 0:
            continue
        plane, _ = decode_plane(data, src)
        txi = idx % tx
        tyi = idx // tx
        for i in range(2048):
            lx, ly = i % 64, i // 64
            x, y = txi * 64 + lx, tyi * 32 + ly
            if x < w and y < h:
                px[x, y] = plane[i]
    return img, w, h


def index_to_rgba(index_img: Image.Image, palette: list[int]) -> Image.Image:
    w, h = index_img.size
    pimg = Image.frombytes("P", (w, h), index_img.tobytes())
    pimg.putpalette(palette, rawmode="RGBA")
    return pimg.convert("RGBA")


def rgba_to_index_at(
    rgba: Image.Image, palette: list[int], regions: list[tuple[int, int, int, int]]
) -> Image.Image:
    w, h = rgba.size
    pal_rgb = [(palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2]) for i in range(256)]
    out_px = list(rgba.getdata())
    result = [0] * (w * h)

    def in_region(x: int, y: int) -> bool:
        for rx, ry, rw, rh in regions:
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return True
        return False

    for y in range(h):
        for x in range(w):
            i = y * w + x
            if not in_region(x, y):
                continue
            r, g, b, a = out_px[i]
            if a < 32:
                result[i] = 0
                continue
            best_i, best_d = 0, 10**9
            for pi, (pr, pg, pb) in enumerate(pal_rgb):
                d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
                if d < best_d:
                    best_d = d
                    best_i = pi
            result[i] = best_i
    return Image.frombytes("L", (w, h), bytes(result))


def rgba_to_index_changed(
    before: Image.Image,
    after: Image.Image,
    palette: list[int],
    regions: list[tuple[int, int, int, int]],
) -> Image.Image:
    w, h = before.size
    pal_rgb = [(palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2]) for i in range(256)]
    out = before.convert("L").copy()
    op = out.load()

    def in_region(x: int, y: int) -> bool:
        for rx, ry, rw, rh in regions:
            if rx <= x < rx + rw and ry <= y < ry + rh:
                return True
        return False

    for y in range(h):
        for x in range(w):
            if not in_region(x, y):
                continue
            if before.getpixel((x, y)) == after.getpixel((x, y)):
                continue
            r, g, b, a = after.getpixel((x, y))
            if a < 32:
                continue
            best_i, best_d = 0, 10**9
            for pi, (pr, pg, pb) in enumerate(pal_rgb):
                d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
                if d < best_d:
                    best_d = d
                    best_i = pi
            op[x, y] = best_i
    return out


def merge_index(base: Image.Image, patch: Image.Image, regions: list[tuple[int, int, int, int]]) -> Image.Image:
    out = base.copy()
    bp = out.load()
    pp = patch.load()
    for y in range(base.height):
        for x in range(base.width):
            for rx, ry, rw, rh in regions:
                if rx <= x < rx + rw and ry <= y < ry + rh:
                    bp[x, y] = pp[x, y]
                    break
    return out


def find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def sample_bg(img: Image.Image, x: int, y: int, w: int, h: int) -> tuple[int, int, int]:
    pts = [
        (max(0, x - 3), y + h // 2),
        (x + w + 3, y + h // 2),
        (x + w // 2, max(0, y - 3)),
    ]
    rs, gs, bs = [], [], []
    for px, py in pts:
        r, g, b, a = img.getpixel((min(px, img.width - 1), min(py, img.height - 1)))
        if a > 128:
            rs.append(r)
            gs.append(g)
            bs.append(b)
    if not rs:
        return PARCHMENT
    return (sum(rs) // len(rs), sum(gs) // len(gs), sum(bs) // len(bs))


def apply_text_overlays(rgba: Image.Image) -> Image.Image:
    out = rgba.copy()
    draw = ImageDraw.Draw(out)
    for x, y, w, h, text, size, color, anchor in TEXT_OVERLAYS:
        if color[0] > 200:
            bg = sample_bg(out, x, y, w, h)
            draw.rectangle([x, y, x + w, y + h], fill=bg + (255,))
        font = find_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if anchor == "mm":
            tx = x + (w - tw) // 2 - bbox[0]
            ty = y + (h - th) // 2 - bbox[1]
        elif anchor == "rm":
            tx = x + w - tw - bbox[0] - 2
            ty = y + (h - th) // 2 - bbox[1]
        else:
            tx = x - bbox[0] + 2
            ty = y + (h - th) // 2 - bbox[1]
        draw.text((tx, ty), text, font=font, fill=color + (255,))
    return out


def overlay_regions() -> list[tuple[int, int, int, int]]:
    return [(x, y, w, h) for x, y, w, h, *_ in TEXT_OVERLAYS]


def decode_tile_planes(chunk: bytes) -> tuple[list[bytes], list[PlaneSlice]]:
    planes, meta = decode_chunk_planes(chunk)
    if planes:
        return planes, meta
    sl = parse_plane_stream(chunk, 0)
    plane, _ = decode_plane(chunk, 0)
    return [bytes(plane)], [sl]


def tile_plane_from_index(index_img: Image.Image, tile_idx: int, w: int, h: int) -> bytes:
    tx = w // 64
    txi = tile_idx % tx
    tyi = tile_idx // tx
    plane = bytearray(2048)
    px = index_img.load()
    for ly in range(32):
        for lx in range(64):
            x, y = txi * 64 + lx, tyi * 32 + ly
            plane[ly * 64 + lx] = px[x, y] if x < w and y < h else 0
    return bytes(plane)


def patch_ite_tiles(data: bytearray, ite_off: int, new_index: Image.Image, orig_index: Image.Image) -> tuple[int, int, int]:
    w, h, n, _ = load_ite(data, ite_off)
    patched = skipped = failed = 0

    for idx in range(n):
        src, old_size = tile_chunk_bounds(data, ite_off, idx)
        if data[src] == 0:
            skipped += 1
            continue

        orig_chunk = bytes(data[src : src + old_size])
        _, br = decode_plane(data, src)
        if br.consumed_bytes() > old_size:
            skipped += 1
            continue

        orig_planes, _ = decode_tile_planes(orig_chunk)
        new_p0 = tile_plane_from_index(new_index, idx, w, h)
        old_p0 = tile_plane_from_index(orig_index, idx, w, h)

        if new_p0 == old_p0:
            skipped += 1
            continue

        new_planes = [new_p0] + orig_planes[1:]
        changed = [True] + [False] * (len(orig_planes) - 1)
        new_chunk = reencode_chunk(orig_chunk, new_planes, changed)
        if new_chunk is None or len(new_chunk) != old_size:
            print(f"  [!] tuile {idx}: réencodage impossible (chunk {old_size} o)")
            failed += 1
            continue

        data[src : src + old_size] = new_chunk
        patched += 1

    return patched, skipped, failed


def verify_fhm(data: bytes) -> bool:
    n = int.from_bytes(data[0:4], "little")
    offs = [int.from_bytes(data[4 + i * 4 : 8 + i * 4], "little") for i in range(n)]
    ok = True
    for i, o in enumerate(offs):
        if i > 0 and data[o : o + 4] != b"ITE\x00":
            print(f"  CORRUPT entry {i} @ 0x{o:x}: {data[o:o+4]!r}")
            ok = False
    return ok


def main():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    src = SRC_FHM
    if (ROOT / "scratch/option_orig.fhm").exists():
        src = ROOT / "scratch/option_orig.fhm"
    elif src.with_suffix(".fhm.bak").exists():
        src = src.with_suffix(".fhm.bak")
    if not src.exists():
        raise SystemExit(f"Source introuvable: {SRC_FHM}")

    data = bytearray(src.read_bytes())
    palette = load_clut(data)
    regions = overlay_regions()

    index_img, w, h = decode_ite_index(data, ITE_ENTRIES[0])
    rgba = index_to_rgba(index_img, palette)
    rgba.save(SCRATCH / "before.png")

    translated = apply_text_overlays(rgba)
    translated.save(SCRATCH / "after.png")

    patch_idx = rgba_to_index_changed(rgba, translated, palette, regions)
    new_index = merge_index(index_img, patch_idx, regions)
    new_index.save(SCRATCH / "after_index.png")

    for ite_off in ITE_ENTRIES:
        print(f"Patch ITE @ 0x{ite_off:x}...")
        p, s, f = patch_ite_tiles(data, ite_off, new_index, index_img)
        print(f"  tuiles: {p} patchées, {s} inchangées, {f} échouées")

    if not verify_fhm(data):
        raise SystemExit("FHM corrompu après patch — abandon")

    assert len(data) == src.stat().st_size
    OUT_FHM.write_bytes(data)
    print(f"Écrit {OUT_FHM} ({len(data)} bytes)")

    dest = SRC_FHM
    backup = dest.with_suffix(".fhm.bak")
    if not backup.exists():
        shutil.copy2(src, backup)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUT_FHM, dest)
    print(f"Installé → {dest}")


if __name__ == "__main__":
    main()
