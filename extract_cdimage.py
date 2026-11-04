#!/usr/bin/env python3
"""Extract CDIMAGE.BIN from the game ISO."""
import struct
from pathlib import Path

ISO = Path("Venus and Braves - Majo to Megami to Horobi no Yogen (Japan) (Premium Box).iso")
OUT = Path("extracted_iso/IMAGE/CDIMAGE.BIN")
SECTOR = 2048


def walk_dir(f, lba, size, target):
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
        name = entry[33 : 33 + name_len].decode("ascii", errors="replace").split(";")[0]
        if name == target:
            return file_lba, file_size
        if (flags & 2) and name not in ("\x00", "\x01"):
            result = walk_dir(f, file_lba, file_size, target)
            if result:
                return result
        pos += rec_len
    return None


def main():
    with ISO.open("rb") as f:
        f.seek(16 * SECTOR)
        pvd = f.read(SECTOR)
        root_lba = struct.unpack_from("<I", pvd, 156 + 2)[0]
        root_size = struct.unpack_from("<I", pvd, 156 + 10)[0]
        loc = walk_dir(f, root_lba, root_size, "CDIMAGE.BIN")
        if not loc:
            raise SystemExit("CDIMAGE.BIN not found in ISO")
        lba, size = loc
        print(f"CDIMAGE.BIN: LBA={lba} size={size:,}")
        f.seek(lba * SECTOR)
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_bytes(f.read(size))
        print(f"Written {OUT}")


if __name__ == "__main__":
    main()
