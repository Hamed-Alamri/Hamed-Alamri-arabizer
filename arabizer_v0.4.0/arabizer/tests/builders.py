# -*- coding: utf-8 -*-
"""بناة أرشيفات اصطناعية للاختبار (DSAR + PSAR) ومشفّر LZ4 بسيط للاختبارات فقط."""
import hashlib
import struct
import zlib


def _emit(out, src, anchor, i, offset=None, mlen=None):
    lit = i - anchor
    tok_l = min(lit, 15)
    if offset is None:
        out.append(tok_l << 4)
    else:
        out.append((tok_l << 4) | min(mlen - 4, 15))
    if lit >= 15:
        r = lit - 15
        while r >= 255:
            out.append(255)
            r -= 255
        out.append(r)
    out += src[anchor:i]
    if offset is not None:
        out += struct.pack("<H", offset)
        if mlen - 4 >= 15:
            r = mlen - 4 - 15
            while r >= 255:
                out.append(255)
                r -= 255
            out.append(r)


def lz4_compress(src):
    src = bytes(src)
    n = len(src)
    out = bytearray()
    table = {}
    i = anchor = 0
    while i + 12 <= n:
        key = src[i:i + 4]
        cand = table.get(key)
        table[key] = i
        if cand is not None and i - cand <= 65535:
            m = 4
            limit = n - 5
            while i + m < limit and src[cand + m] == src[i + m]:
                m += 1
            _emit(out, src, anchor, i, i - cand, m)
            i += m
            anchor = i
        else:
            i += 1
    _emit(out, src, anchor, n)
    return bytes(out)


def build_psar(files, block_size=4096, compress="zlib", align=0, align_min=0, zero_listed_raw=True,
               comp_header=b"zlib", flags=0):
    """يرجع (stream, gaps) حيث gaps قائمة (start, end) لمناطق الحشو الصفري."""
    names = list(files)
    manifest = "\n".join(names).encode()
    entries = [("<manifest>", manifest)] + [(n, files[n]) for n in names]
    stored, listed_all, bi = [], [], []
    for _, data in entries:
        bi.append(len(listed_all))
        blocks = []
        for k in range(0, len(data), block_size):
            chunk = data[k:k + block_size]
            if compress == "zlib":
                c = zlib.compress(chunk)
                if len(c) < len(chunk):
                    blocks.append(c)
                    listed_all.append(len(c))
                    continue
            blocks.append(chunk)
            full = len(chunk) == block_size
            listed_all.append(0 if (zero_listed_raw and full) else len(chunk))
        stored.append(blocks)
    n = len(entries)
    table = b"".join(s.to_bytes(2, "big") for s in listed_all)
    toc_len = 32 + n * 30 + len(table)
    pos = toc_len
    stream = bytearray(b"\0" * toc_len)
    gaps, toc = [], bytearray()
    for idx, (name, data) in enumerate(entries):
        if align and len(data) > align_min and pos % align:
            pad = align - pos % align
            gaps.append((pos, pos + pad))
            stream += b"\0" * pad
            pos += pad
        off = pos
        for b in stored[idx]:
            stream += b
            pos += len(b)
        toc += hashlib.md5(name.encode()).digest() + struct.pack(">I", bi[idx]) + \
            len(data).to_bytes(5, "big") + off.to_bytes(5, "big")
    header = struct.pack(">4sHH4sIIIII", b"PSAR", 1, 4, comp_header, toc_len, 30, n, block_size, flags)
    stream[:toc_len] = header + bytes(toc) + table
    return bytes(stream), gaps


def build_dsar(stream, gaps, chunk_size=8192, raw_every=0, header_end=None, total_override=None):
    segs, pos = [], 0
    for gs, ge in list(gaps) + [(len(stream), len(stream))]:
        p = pos
        while p < gs:
            q = min(gs, p + chunk_size)
            segs.append(("data", p, q))
            p = q
        if ge > gs:
            segs.append(("gap", gs, ge))
        pos = ge
    count = len(segs)
    base = 32 + count * 32
    records, blob, k = [], bytearray(), 0
    for kind, a, b in segs:
        if kind == "gap":
            records.append(struct.pack("<QQIIB7s", a, base + len(blob), b - a, 0, 254, b"\x54" + b"\x55" * 6))
            continue
        raw = stream[a:b]
        k += 1
        if raw_every and k % raw_every == 0:
            ctype, comp = 0, raw
        else:
            ctype, comp = 3, lz4_compress(raw)
        records.append(struct.pack("<QQIIB7s", a, base + len(blob), b - a, len(comp), ctype, bytes(7)))
        blob += comp
    he = base if header_end is None else header_end
    tot = len(stream) if total_override is None else total_override
    header = struct.pack("<4sIIIII8s", b"DSAR", 0x00010003, count, he, tot, 0, b"PADDING*")
    return header + b"".join(records) + bytes(blob)


def sample_files(sizes=None):
    import random
    sizes = sizes or {"loc/en.txt": 3000, "loc/ar.txt": 9000, "gfx/tex.bin": 60000, "empty.dat": 0,
                      "a/b/c.txt": 123, "sfx/big.bin": 90000}
    out = {}
    for name, n in sizes.items():
        rnd = random.Random(name)
        seed = hashlib.sha256(name.encode()).digest()
        head = (seed * (n // 32 + 1))[: n // 2]
        out[name] = bytes(head) + rnd.randbytes(n - len(head))
    return out
