#!/usr/bin/env python3
"""
Codec ULZ Venus & Braves — decode @ EE 0x1afc08, encodeur bit-exact.

Ré-encodage :
  - plans inchangés → octets d'origine conservés
  - plans modifiés → encodeur glouton avec simulation scratch EE
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ulz_decode import (
    BitReader,
    DELTA_OFF,
    OUT_OFF,
    PLANE_SIZE,
    SCRATCH_SIZE,
    decode_plane,
)

Token = Tuple[str, ...]


def plane_to_deltas(plane: bytes, acc0: int | None = None) -> Tuple[List[int], int]:
    if len(plane) != PLANE_SIZE:
        raise ValueError(f"plan doit faire {PLANE_SIZE} octets")
    if acc0 is None:
        acc0 = (plane[0] >> 1) << 1
    deltas: List[int] = []
    acc = acc0
    for b in plane:
        d = b - (acc & 0xFF)
        if d >= 0x80:
            d -= 0x100
        deltas.append(d)
        acc = b
    return deltas, acc0 >> 1


def dist_bit_cost(dist: int) -> int:
    d = dist - 1
    return 8 if d < 0xFF else 20


def len_bit_cost(length: int) -> int:
    ln = length - 1
    return 4 if ln < 0xF else 16


def literal_bit_cost(count: int) -> int:
    return 1 + 2 + count * 7


def copy_bit_cost(dist: int, length: int) -> int:
    return 1 + dist_bit_cost(dist) + len_bit_cost(length)


def write_bits(bits: Sequence[int]) -> bytes:
    out = bytearray()
    cur = 0
    n = 0
    for bit in bits:
        cur = (cur << 1) | (bit & 1)
        n += 1
        if n == 8:
            out.append(cur)
            cur = 0
            n = 0
    if n:
        out.append(cur << (8 - n))
    return bytes(out)


def pack_bits_to_bytes(bits: Sequence[int], byte_len: int | None = None) -> bytes:
    if byte_len is None:
        return write_bits(bits)
    target_bits = byte_len * 8
    if len(bits) > target_bits:
        raise ValueError(f"flux {len(bits)} bits > budget {target_bits}")
    return write_bits(list(bits) + [0] * (target_bits - len(bits)))


class ScratchSim:
    __slots__ = ("raw",)

    def __init__(self, raw: bytearray | None = None) -> None:
        self.raw = raw if raw is not None else bytearray(SCRATCH_SIZE)

    def clear(self) -> None:
        self.raw[:] = b"\x00" * SCRATCH_SIZE

    def read_i32(self, byte_off: int) -> int:
        return struct.unpack_from("<i", self.raw, byte_off)[0]

    def write_delta(self, pos: int, val: int) -> None:
        struct.pack_into("<i", self.raw, DELTA_OFF + pos * 4, val)

    def ref_i32(self, pos: int, dist: int) -> int:
        return self.read_i32(DELTA_OFF + (pos - dist) * 4)

    def apply_literal(self, pos: int, lits: Sequence[int]) -> None:
        for i, d in enumerate(lits):
            if pos + i < PLANE_SIZE:
                self.write_delta(pos + i, d)

    def apply_copy(self, pos: int, dist: int, length: int) -> None:
        for k in range(length):
            if pos + k < PLANE_SIZE:
                self.write_delta(pos + k, self.ref_i32(pos + k, dist))

    def finish_plane(self, acc0: int, deltas: Sequence[int]) -> None:
        acc = acc0
        for i, d in enumerate(deltas):
            acc = (acc + d) & 0xFFFFFFFF
            acc_s = struct.unpack("<i", struct.pack("<I", acc))[0]
            struct.pack_into("<I", self.raw, OUT_OFF + i * 4, (acc_s << 24) & 0xFFFFFFFF)


def plane_prefix_from_scratch(scratch: ScratchSim, acc0: int, end: int) -> bytes:
    acc = acc0
    out = bytearray()
    for i in range(end):
        acc = (acc + scratch.read_i32(DELTA_OFF + i * 4)) & 0xFFFFFFFF
        acc_s = struct.unpack("<i", struct.pack("<I", acc))[0]
        out.append(acc_s & 0xFF)
    return bytes(out)


def encode_delta_value(d: int, bits: List[int]) -> None:
    v = ((d + 0x80) >> 1) & 0x7F
    for shift in range(6, -1, -1):
        bits.append((v >> shift) & 1)


def append_dist_bits(dist: int, bits: List[int]) -> None:
    d = dist - 1
    if d < 0xFF:
        for shift in range(7, -1, -1):
            bits.append((d >> shift) & 1)
    else:
        for shift in range(7, -1, -1):
            bits.append(1)
        for shift in range(11, -1, -1):
            bits.append((d >> shift) & 1)


def append_len_bits(length: int, bits: List[int]) -> None:
    ln = length - 1
    if ln < 0xF:
        for shift in range(3, -1, -1):
            bits.append((ln >> shift) & 1)
    else:
        for shift in range(3, -1, -1):
            bits.append(1)
        for shift in range(11, -1, -1):
            bits.append((ln >> shift) & 1)


def tokens_to_bits(acc6: int, tokens: Sequence[Token]) -> List[int]:
    bits: List[int] = []
    for shift in range(5, -1, -1):
        bits.append((acc6 >> shift) & 1)
    for tok in tokens:
        if tok[0] == "lit":
            _, count, lits = tok
            bits.append(0)
            c = count - 1
            bits.append((c >> 1) & 1)
            bits.append(c & 1)
            for d in lits:
                encode_delta_value(d, bits)
        else:
            _, dist, length = tok
            bits.append(1)
            append_dist_bits(dist, bits)
            append_len_bits(length, bits)
    return bits


def encode_tokens(acc6: int, tokens: Sequence[Token], byte_len: int | None = None) -> bytes:
    return pack_bits_to_bytes(tokens_to_bits(acc6, tokens), byte_len)


@dataclass
class PlaneSlice:
    acc6: int
    tokens: List[Token]
    byte_len: int
    src_off: int = 0


def parse_plane_stream(buf: bytes, src: int) -> PlaneSlice:
    stream = bytes(buf[src:]) + b"\x00" * 8
    br = BitReader(stream, 0)
    acc6 = br.read(6)
    tokens: List[Token] = []
    s3 = 0
    while s3 < PLANE_SIZE:
        if br.read(1) == 0:
            cnt = br.read(2) + 1
            lits = []
            for _ in range(cnt):
                lits.append((br.read(7) << 1) - 0x80)
                s3 += 1
            tokens.append(("lit", cnt, lits))
        else:
            dist = br.read(8) + 1
            if dist >= 0x100:
                dist = br.read(12) + 1
            length = br.read(4) + 1
            if length >= 0x10:
                length = br.read(12) + 1
            tokens.append(("cp", dist, length))
            s3 += length
    return PlaneSlice(acc6, tokens, br.consumed_bytes(), src)


def apply_tokens_to_scratch(scratch: ScratchSim, tokens: Sequence[Token]) -> int:
    pos = 0
    for tok in tokens:
        if tok[0] == "lit":
            _, count, lits = tok
            scratch.apply_literal(pos, lits)
            pos += count
        else:
            _, dist, length = tok
            scratch.apply_copy(pos, dist, length)
            pos += length
        if pos >= PLANE_SIZE:
            break
    return pos


def _match_same_plane(deltas: Sequence[int], pos: int, dist: int) -> int:
    """Longueur max. de copie LZ quand dist <= pos (deltas déjà fixés)."""
    length = 0
    while pos + length < PLANE_SIZE and deltas[pos + length] == deltas[pos + length - dist]:
        length += 1
    return length


def _match_cross_plane(scratch: ScratchSim, plane: bytes, acc0: int, pos: int, dist: int) -> int:
    """Copie dist > pos : valide par préfixe du plan (refs zone OUT)."""
    lo, hi = 0, PLANE_SIZE - pos
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        trial = ScratchSim(bytearray(scratch.raw))
        trial.apply_copy(pos, dist, mid)
        if plane_prefix_from_scratch(trial, acc0, pos + mid) == plane[: pos + mid]:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def encode_deltas_greedy(
    plane: bytes,
    acc6: int,
    scratch: ScratchSim,
    byte_budget: int | None = None,
    allow_cross: bool = False,
) -> Tuple[List[Token], int]:
    """Encode glouton — LZ sur deltas (dist <= pos). Cross-plan optionnel (lent)."""
    acc0 = acc6 << 1
    deltas, _ = plane_to_deltas(plane, acc0)
    tokens: List[Token] = []
    pos = 0
    sim = ScratchSim(bytearray(scratch.raw))

    while pos < PLANE_SIZE:
        best: Optional[Tuple[int, Token, int]] = None

        for run in range(1, 5):
            if pos + run > PLANE_SIZE:
                break
            tok: Token = ("lit", run, list(deltas[pos : pos + run]))
            cost = literal_bit_cost(run)
            if best is None or cost < best[0]:
                best = (cost, tok, run)

        for dist in range(1, min(pos, 2048) + 1):
            length = _match_same_plane(deltas, pos, dist)
            if length < 1:
                continue
            cost = copy_bit_cost(dist, length)
            if best is None or cost < best[0]:
                best = (cost, ("cp", dist, length), length)

        if allow_cross and pos < 4096:
            for dist in range(pos + 1, min(4096, pos + 2048) + 1):
                length = _match_cross_plane(sim, plane, acc0, pos, dist)
                if length < 1:
                    continue
                cost = copy_bit_cost(dist, length)
                if best is None or cost < best[0]:
                    best = (cost, ("cp", dist, length), length)

        if best is None:
            raise ValueError(f"pas de token valide @ {pos}")
        _, tok, adv = best
        tokens.append(tok)
        if tok[0] == "lit":
            sim.apply_literal(pos, tok[2])
        else:
            sim.apply_copy(pos, tok[1], tok[2])
        pos += adv

    bits = tokens_to_bits(acc6, tokens)
    if byte_budget is not None and len(bits) > byte_budget * 8:
        raise ValueError(f"budget dépassé: {(len(bits) + 7) // 8}>{byte_budget}")
    return tokens, len(bits)


def plane_bytes_from_tokens(acc6: int, tokens: Sequence[Token], scratch: ScratchSim) -> bytes:
    sim = ScratchSim(bytearray(scratch.raw))
    apply_tokens_to_scratch(sim, tokens)
    return plane_prefix_from_scratch(sim, acc6 << 1, PLANE_SIZE)


def even_literal_deltas(
    plane: bytearray,
    acc0: int,
    start: int,
    count: int,
    acc_before: int | None = None,
) -> List[int]:
    """Deltas encodables (pairs) ; ajuste le plan au plus de ±1 si nécessaire."""
    deltas: List[int] = []
    acc = acc0 if start == 0 else (acc_before if acc_before is not None else plane[start - 1])
    for j in range(count):
        i = start + j
        wanted = plane[i]
        d = wanted - (acc & 0xFF)
        if d >= 0x80:
            d -= 0x100
        if d & 1:
            fixed = False
            for adj in (1, -1):
                w2 = (wanted + adj) & 0xFF
                d2 = w2 - (acc & 0xFF)
                if d2 >= 0x80:
                    d2 -= 0x100
                if (d2 & 1) == 0:
                    plane[i] = w2
                    wanted = w2
                    d = d2
                    fixed = True
                    break
            if not fixed:
                raise ValueError(f"delta impair @ {i}")
        deltas.append(d)
        acc = wanted
    return deltas


def literals_for_plane(
    plane: bytearray,
    acc0: int,
    start: int,
    count: int,
    acc_before: int | None = None,
) -> List[Token]:
    out: List[Token] = []
    i = 0
    acc_run = acc_before
    while i < count:
        run = min(4, count - i)
        lits = even_literal_deltas(plane, acc0, start + i, run, acc_run)
        out.append(("lit", run, lits))
        if run > 0:
            acc_run = plane[start + i + run - 1]
        i += run
    return out


def token_output_segment(acc6: int, tok: Token, scratch: ScratchSim, pos: int) -> bytes:
    trial = ScratchSim(bytearray(scratch.raw))
    if tok[0] == "lit":
        trial.apply_literal(pos, tok[2])
        adv = min(tok[1], PLANE_SIZE - pos)
    else:
        trial.apply_copy(pos, tok[1], tok[2])
        adv = min(tok[2], PLANE_SIZE - pos)
    return plane_prefix_from_scratch(trial, acc6 << 1, pos + adv)[pos : pos + adv]


def emit_plane_segment(
    adapted: List[Token],
    sim: ScratchSim,
    plane_buf: bytearray,
    orig_plane: bytes,
    acc6: int,
    acc0: int,
    pos: int,
    count: int,
    orig_tok: Token | None,
) -> int:
    if count <= 0:
        return pos
    target = bytes(plane_buf)

    if orig_tok and orig_tok[0] == "cp":
        _, dist, length = orig_tok
        eff = min(length, count, PLANE_SIZE - pos)
        cp_tok: Token = ("cp", dist, eff)
        if token_output_segment(acc6, cp_tok, sim, pos) == target[pos : pos + eff]:
            adapted.append(cp_tok)
            sim.apply_copy(pos, dist, eff)
            return pos + eff

        prefix = 0
        for k in range(1, eff + 1):
            if token_output_segment(acc6, ("cp", dist, k), sim, pos) == target[pos : pos + k]:
                prefix = k
        if prefix > 0:
            adapted.append(("cp", dist, prefix))
            sim.apply_copy(pos, dist, prefix)
            pos += prefix
            eff -= prefix
            if eff <= 0:
                return pos

        end_pos = pos + eff
        run_start = pos
        while run_start < end_pos and target[run_start] == orig_plane[run_start]:
            run_start += 1
        run_end = run_start
        while run_end < end_pos and target[run_end] != orig_plane[run_end]:
            run_end += 1
        if run_end > run_start:
            lit_pos = run_start
            if lit_pos > 0:
                acc_before = plane_prefix_from_scratch(sim, acc0, lit_pos)[lit_pos - 1]
            else:
                acc_before = acc0
            for pt in literals_for_plane(plane_buf, acc0, lit_pos, run_end - lit_pos, acc_before):
                adapted.append(pt)
                sim.apply_literal(lit_pos, pt[2])
                lit_pos += pt[1]
            pos = lit_pos

        eff = end_pos - pos
        if eff > 0:
            rem_tok: Token = ("cp", dist, eff)
            if token_output_segment(acc6, rem_tok, sim, pos) == target[pos : pos + eff]:
                adapted.append(rem_tok)
                sim.apply_copy(pos, dist, eff)
                return pos + eff
            count = eff

    rem = min(count, PLANE_SIZE - pos)
    for pt in literals_for_plane(plane_buf, acc0, pos, rem):
        adapted.append(pt)
        sim.apply_literal(pos, pt[2])
        pos += pt[1]
    return pos


def adapt_plane_tokens(
    orig_tokens: Sequence[Token],
    acc6: int,
    scratch: ScratchSim,
    new_plane: bytes,
    orig_plane: bytes | None = None,
) -> Tuple[List[Token], bytes]:
    acc0 = acc6 << 1
    plane_buf = bytearray(new_plane)
    if orig_plane is None:
        orig_plane = new_plane
    sim = ScratchSim(bytearray(scratch.raw))
    pos = 0
    adapted: List[Token] = []

    for tok in orig_tokens:
        if pos >= PLANE_SIZE:
            break
        if tok[0] == "lit":
            _, count, lits = tok
            count = min(count, PLANE_SIZE - pos)
            lit_tok: Token = ("lit", count, list(lits))
            if token_output_segment(acc6, lit_tok, sim, pos) == bytes(plane_buf)[pos : pos + count]:
                adapted.append(lit_tok)
                sim.apply_literal(pos, lits)
                pos += count
            else:
                pos = emit_plane_segment(
                    adapted, sim, plane_buf, orig_plane, acc6, acc0, pos, count, None
                )
        else:
            _, _dist, length = tok
            eff = min(length, PLANE_SIZE - pos)
            pos = emit_plane_segment(
                adapted, sim, plane_buf, orig_plane, acc6, acc0, pos, eff, tok
            )

    return adapted, bytes(plane_buf)


def shrink_tokens_to_budget(acc6: int, tokens: List[Token], byte_budget: int) -> List[Token] | None:
    """Si le flux dépasse le budget, convertit des copies en littéraux (approx.)."""
    cur = list(tokens)
    for _ in range(len(cur) + 1):
        bits = tokens_to_bits(acc6, cur)
        if len(bits) <= byte_budget * 8:
            return cur
        # remplace la plus longue copie par des littéraux issus du décodage actuel
        best_i = -1
        best_len = 0
        for i, tok in enumerate(cur):
            if tok[0] == "cp" and tok[2] > best_len:
                best_i = i
                best_len = tok[2]
        if best_i < 0:
            return None
        _, _dist, length = cur[best_i]
        eff = min(length, PLANE_SIZE)
        repl = literals_for_plane(bytearray([0] * PLANE_SIZE), acc6 << 1, 0, eff)
        cur = cur[:best_i] + repl + cur[best_i + 1 :]
    return None


def encode_plane_from_orig_tokens(
    orig_tokens: Sequence[Token],
    acc6: int,
    scratch: ScratchSim,
    new_plane: bytes,
    byte_budget: int,
    orig_plane: bytes | None = None,
) -> bytes | None:
    for try_acc6 in (acc6,):
        try:
            sc = ScratchSim(bytearray(scratch.raw))
            tokens, adjusted = adapt_plane_tokens(
                orig_tokens, try_acc6, sc, new_plane, orig_plane
            )
            bits = tokens_to_bits(try_acc6, tokens)
            if len(bits) > byte_budget * 8:
                shrunk = shrink_tokens_to_budget(try_acc6, tokens, byte_budget)
                if shrunk is None:
                    continue
                tokens = shrunk
            enc = encode_tokens(try_acc6, tokens, byte_budget)
            if plane_bytes_from_tokens(try_acc6, tokens, sc) != adjusted:
                continue
            decode_plane(enc, 0, scratch.raw)
            return enc
        except (ValueError, IndexError):
            continue
    return None


def encode_plane_with_scratch(
    plane: bytes,
    acc6: int | None,
    scratch: ScratchSim,
    byte_budget: int | None = None,
    orig_tokens: Sequence[Token] | None = None,
) -> Tuple[bytes, int, List[Token]]:
    if acc6 is None:
        _, acc6 = plane_to_deltas(plane)
    if orig_tokens is not None and byte_budget is not None:
        enc = encode_plane_from_orig_tokens(orig_tokens, acc6, scratch, plane, byte_budget)
        if enc is None:
            raise ValueError("réencodage token-adapté impossible")
        tokens = adapt_plane_tokens(orig_tokens, acc6, scratch, plane)[0]
        return enc, acc6, tokens
    tokens, _ = encode_deltas_greedy(plane, acc6, scratch, byte_budget)
    enc = encode_tokens(acc6, tokens, byte_budget)
    deltas, _ = plane_to_deltas(plane, acc6 << 1)
    scratch.finish_plane(acc6 << 1, deltas)
    return enc, acc6, tokens


def encode_plane(plane: bytes, acc0_nibble: int | None = None) -> bytes:
    scratch = ScratchSim()
    enc, _, _ = encode_plane_with_scratch(plane, acc0_nibble, scratch)
    return enc


def decode_chunk_planes(buf: bytes) -> Tuple[List[bytes], List[PlaneSlice]]:
    scratch = ScratchSim()
    planes: List[bytes] = []
    meta: List[PlaneSlice] = []
    pos = 0
    while pos < len(buf) - 1:
        try:
            sl = parse_plane_stream(buf, pos)
        except (IndexError, struct.error):
            break
        plane, _ = decode_plane(buf, pos, scratch.raw)
        planes.append(bytes(plane))
        meta.append(sl)
        pos += sl.byte_len
        if pos >= len(buf):
            break
    return planes, meta


def encode_chunk_from_planes(
    planes: Sequence[bytes],
    plane_budgets: Sequence[int],
    acc6_list: Sequence[int | None] | None = None,
) -> bytes:
    scratch = ScratchSim()
    body = bytearray()
    for i, plane in enumerate(planes):
        acc6 = None if acc6_list is None else acc6_list[i]
        enc, _, _ = encode_plane_with_scratch(plane, acc6, scratch, plane_budgets[i])
        if len(enc) != plane_budgets[i]:
            raise ValueError(f"plan {i}: {len(enc)} != budget {plane_budgets[i]}")
        body.extend(enc)
    return bytes(body)


def decode_tile_planes(buf: bytes) -> Tuple[List[bytes], List[PlaneSlice]]:
    planes, meta = decode_chunk_planes(buf)
    if planes:
        return planes, meta
    sl = parse_plane_stream(buf, 0)
    plane, _ = decode_plane(buf, 0)
    return [bytes(plane)], [sl]


def reencode_chunk(
    original: bytes,
    new_planes: Sequence[bytes],
    changed: Sequence[bool] | None = None,
) -> bytes | None:
    """Ré-encode un chunk en conservant les tranches originales si possible."""
    orig_planes, meta = decode_tile_planes(original)
    if len(new_planes) != len(orig_planes):
        return None
    if changed is None:
        changed = [a != b for a, b in zip(orig_planes, new_planes)]

    scratch = ScratchSim()
    body = bytearray()
    pos = 0
    for i, plane in enumerate(new_planes):
        sl = meta[i]
        if not changed[i]:
            body.extend(original[pos : pos + sl.byte_len])
            decode_plane(original, pos, scratch.raw)
        else:
            enc = encode_plane_from_orig_tokens(
                sl.tokens, sl.acc6, scratch, plane, sl.byte_len, orig_planes[i]
            )
            if enc is None:
                return None
            body.extend(enc)
        pos += sl.byte_len

    if len(body) < len(original):
        body.extend(original[len(body) : len(original)])
    return bytes(body)


def pad_chunk(chunk: bytes, target_size: int) -> bytes:
    if len(chunk) > target_size:
        raise ValueError(f"chunk {len(chunk)} > cible {target_size}")
    if len(chunk) == target_size:
        return chunk
    return chunk + b"\x00" * (target_size - len(chunk))


def wrap_chunk(bitstream: bytes, total_size: int | None = None) -> bytes:
    total_size = total_size or len(bitstream)
    if len(bitstream) > total_size:
        raise ValueError("bitstream trop grand")
    return bitstream + b"\x00" * (total_size - len(bitstream))


def roundtrip_plane(plane: bytes) -> Tuple[bool, int]:
    enc = encode_plane(plane)
    dec, _ = decode_plane(enc, 0)
    matches = sum(a == b for a, b in zip(dec, plane))
    return matches == PLANE_SIZE, matches


def roundtrip_chunk(chunk: bytes) -> Tuple[bool, bytes | None]:
    planes, meta = decode_chunk_planes(chunk)
    body = b"".join(encode_tokens(m.acc6, m.tokens, m.byte_len) for m in meta)
    out = pad_chunk(body, len(chunk))
    dec_planes, _ = decode_chunk_planes(out)
    return dec_planes == planes, out


def decode_tile_chunk(chunk: bytes, scratch: bytearray | None = None) -> bytes:
    plane, _ = decode_plane(chunk, 0, scratch)
    return bytes(plane)


def encode_tile_chunk(plane: bytes) -> bytes:
    return wrap_chunk(encode_plane(plane))


def brute_acc6_plane(plane: bytes, byte_budget: int, scratch: ScratchSim | None = None) -> bytes | None:
    base_scratch = scratch or ScratchSim()
    for acc6 in range(64):
        try:
            sc = ScratchSim(bytearray(base_scratch.raw))
            enc, _, _ = encode_plane_with_scratch(plane, acc6, sc, byte_budget)
            dec, _ = decode_plane(pad_chunk(enc, byte_budget), 0, bytearray(base_scratch.raw))
            if bytes(dec) == plane:
                return enc
        except ValueError:
            continue
    return None
