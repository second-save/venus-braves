#!/usr/bin/env python3
"""
Injecte option.fhm patché dans CDIMAGE.BIN puis crée un ISO de test.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from unpack_cdimage import align_up, compute_data_start, parse_filelist_names, parse_index_pairs

ROOT = Path(__file__).resolve().parent
ORIGINAL_ISO = ROOT / "Venus and Braves - Majo to Megami to Horobi no Yogen (Japan) (Premium Box).iso"
OUTPUT_ISO = ROOT / "venus_braves_options_en.iso"
CDIMAGE = ROOT / "extracted_iso/IMAGE/CDIMAGE.BIN"
CDIMAGE_ORIG = ROOT / "CDIMAGE.BIN.orig"
OPTION_FHM = ROOT / "cdimage_unpacked/seven_data_link/futa/screen/option.fhm"
SECTOR = 2048
CDIMAGE_LBA = 970548  # Premium Box ISO


def find_file_in_iso(iso_path: Path, filename: str):
    """Walk ISO 9660 — retourne (lba, size) ou (None, None)."""
    with iso_path.open("rb") as f:
        f.seek(16 * SECTOR)
        pvd = f.read(SECTOR)
        root_lba = struct.unpack_from("<I", pvd, 156 + 2)[0]
        root_size = struct.unpack_from("<I", pvd, 156 + 10)[0]

        def walk(lba, size):
            f.seek(lba * SECTOR)
            data = f.read(size)
            pos = 0
            while pos < len(data):
                rec_len = data[pos]
                if rec_len == 0:
                    pos += 1
                    continue
                entry = data[pos : pos + rec_len]
                if len(entry) < 34:
                    break
                file_lba = struct.unpack_from("<I", entry, 2)[0]
                file_size = struct.unpack_from("<I", entry, 10)[0]
                flags = entry[25]
                name_len = entry[32]
                bare = entry[33 : 33 + name_len].decode("ascii", errors="replace").split(";")[0]
                if bare.upper() == filename.upper():
                    return file_lba, file_size
                if (flags & 2) and bare not in ("\x00", "\x01"):
                    hit = walk(file_lba, file_size)
                    if hit[0] is not None:
                        return hit
                pos += rec_len
            return None, None

        return walk(root_lba, root_size)


def get_max_extent(iso_path: Path, file_lba: int) -> int:
    with iso_path.open("rb") as f:
        f.seek(16 * SECTOR)
        pvd = f.read(SECTOR)
        root_lba = struct.unpack_from("<I", pvd, 156 + 2)[0]
        root_size = struct.unpack_from("<I", pvd, 156 + 10)[0]
        all_lbas = []

        def collect(lba, size):
            f.seek(lba * SECTOR)
            data = f.read(size)
            pos = 0
            while pos < len(data):
                rec_len = data[pos]
                if rec_len == 0:
                    pos += 1
                    continue
                entry = data[pos : pos + rec_len]
                if len(entry) < 34:
                    break
                file_lba = struct.unpack_from("<I", entry, 2)[0]
                file_size = struct.unpack_from("<I", entry, 10)[0]
                flags = entry[25]
                name_len = entry[32]
                bare = entry[33 : 33 + name_len].decode("ascii", errors="replace").split(";")[0]
                if bare not in ("\x00", "\x01"):
                    all_lbas.append(file_lba)
                    if flags & 2:
                        collect(file_lba, file_size)
                pos += rec_len

        collect(root_lba, root_size)

    next_lbas = sorted(lb for lb in all_lbas if lb > file_lba)
    if next_lbas:
        return (next_lbas[0] - file_lba) * SECTOR
    return iso_path.stat().st_size - file_lba * SECTOR


def patch_cdimage_option_fhm():
    cd = bytearray(CDIMAGE.read_bytes())
    opt = OPTION_FHM.read_bytes()
    name_offsets, sizes = parse_index_pairs(cd)
    data_start = compute_data_start(len(cd), sizes)
    filelist = cd[data_start : data_start + sizes[0]]
    names = parse_filelist_names(filelist)
    target = "seven_data_link/futa/screen/option.fhm"
    if target not in names:
        raise SystemExit(f"{target} introuvable dans filelist")
    idx = names.index(target) + 1
    cursor = data_start
    for j in range(idx):
        cursor += align_up(sizes[j])
    old_size = sizes[idx]
    if len(opt) != old_size:
        raise SystemExit(f"Taille option.fhm incompatible: {len(opt)} vs {old_size}")
    cd[cursor : cursor + old_size] = opt
    CDIMAGE.write_bytes(cd)
    print(f"CDIMAGE.BIN patché @ 0x{cursor:X} ({old_size} bytes)")


def write_file_at_lba(iso_path: Path, file_lba: int, orig_size: int, new_data: bytes, max_extent: int):
    if len(new_data) > max_extent:
        raise SystemExit(
            f"CDIMAGE trop grand: {len(new_data):,} > {max_extent:,} bytes disponibles"
        )
    offset = file_lba * SECTOR
    pad = max(0, orig_size - len(new_data))
    with iso_path.open("r+b") as f:
        f.seek(offset)
        f.write(new_data)
        if pad:
            f.write(b"\x00" * pad)
    print(f"ISO patché @ LBA {file_lba} ({len(new_data):,} bytes)")


def main():
    if not ORIGINAL_ISO.exists():
        raise SystemExit(f"ISO source introuvable: {ORIGINAL_ISO}")
    if not OPTION_FHM.exists():
        raise SystemExit("Lancez d'abord patch_option_fhm.py")

    if not CDIMAGE.exists():
        print("Extraction CDIMAGE depuis ISO...")
        subprocess.check_call([sys.executable, str(ROOT / "extract_cdimage.py")])

    if not CDIMAGE_ORIG.exists():
        shutil.copy2(CDIMAGE, CDIMAGE_ORIG)
        print(f"Sauvegarde CDIMAGE originale → {CDIMAGE_ORIG}")

    patch_cdimage_option_fhm()

    if OUTPUT_ISO.exists():
        OUTPUT_ISO.unlink()
    print(f"Clonage ISO → {OUTPUT_ISO}")
    result = subprocess.run(["cp", "-c", str(ORIGINAL_ISO), str(OUTPUT_ISO)], capture_output=True, text=True)
    if result.returncode != 0:
        shutil.copy2(ORIGINAL_ISO, OUTPUT_ISO)

    lba, size = find_file_in_iso(ORIGINAL_ISO, "CDIMAGE.BIN")
    if lba is None:
        lba, size = CDIMAGE_LBA, CDIMAGE.stat().st_size
        print(f"Fallback LBA connu: {lba}")
    new_data = CDIMAGE.read_bytes()
    max_extent = get_max_extent(ORIGINAL_ISO, lba)
    print(f"CDIMAGE.BIN LBA={lba} orig={size:,} new={len(new_data):,} max={max_extent:,}")
    write_file_at_lba(OUTPUT_ISO, lba, size, new_data, max_extent)

    print(f"\n✓ ISO prêt pour test : {OUTPUT_ISO}")
    print(f"  Taille : {OUTPUT_ISO.stat().st_size / 1024 / 1024 / 1024:.2f} Go")


if __name__ == "__main__":
    main()
