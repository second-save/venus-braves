#!/usr/bin/env python3
"""Restaure option.fhm original dans CDIMAGE et ISO (annule le patch cassé)."""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path

from patch_option_iso import (
    CDIMAGE,
    CDIMAGE_LBA,
    CDIMAGE_ORIG,
    ORIGINAL_ISO,
    OUTPUT_ISO,
    SECTOR,
    get_max_extent,
    patch_cdimage_option_fhm,
    write_file_at_lba,
)

ROOT = Path(__file__).resolve().parent
OPTION_ORIG = ROOT / "scratch/option_orig.fhm"
OPTION_DST = ROOT / "cdimage_unpacked/seven_data_link/futa/screen/option.fhm"


def main():
    if not OPTION_ORIG.exists():
        raise SystemExit(f"Backup introuvable: {OPTION_ORIG}")

    OPTION_DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OPTION_ORIG, OPTION_DST)
    print(f"Restauré {OPTION_DST}")

    if not CDIMAGE_ORIG.exists():
        if not CDIMAGE.exists():
            subprocess.check_call([sys.executable, str(ROOT / "extract_cdimage.py")])
        shutil.copy2(CDIMAGE, CDIMAGE_ORIG)
    shutil.copy2(CDIMAGE_ORIG, CDIMAGE)

    # Réinjecter option.fhm propre dans CDIMAGE
    from patch_option_iso import OPTION_FHM

    off = 0x12449000
    data = bytearray(CDIMAGE.read_bytes())
    opt = OPTION_FHM.read_bytes()
    data[off : off + len(opt)] = opt
    CDIMAGE.write_bytes(data)
    print(f"CDIMAGE restauré @ 0x{off:X}")

    if OUTPUT_ISO.exists():
        OUTPUT_ISO.unlink()
    shutil.copy2(ORIGINAL_ISO, OUTPUT_ISO)

    lba = CDIMAGE_LBA
    size = CDIMAGE.stat().st_size
    max_extent = get_max_extent(ORIGINAL_ISO, lba)
    write_file_at_lba(OUTPUT_ISO, lba, size, CDIMAGE.read_bytes(), max_extent)
    print(f"ISO restauré: {OUTPUT_ISO}")


if __name__ == "__main__":
    main()
