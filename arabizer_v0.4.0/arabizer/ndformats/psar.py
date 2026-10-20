# -*- coding: utf-8 -*-
"""دفق PSAR الداخلي (يعمل فوق أي مصدر: ملف PSAR عادي أو DsarArchive).

رأس PSAR (big-endian، 32 بايت):
  magic 'PSAR' | ver_major u16 | ver_minor u16 | compression(4) | toc_length u32 |
  toc_entry_size u32 | toc_entries u32 | block_size u32 | flags u32
سجل TOC (30 بايت): md5(16) | block_index u32 | size u40 | offset u40
بعد السجلات: جدول أحجام الكتل (2/3/4 بايت لكل كتلة حسب block_size).
المدخل 0 هو المانيفست (قائمة الأسماء مفصولة بسطر جديد).
"""
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from . import lz4block
from .dsar import IssueList
from .errors import FormatError, IntegrityError

PSAR_MAGIC = b"PSAR"
_PHDR = struct.Struct(">4sHH4sIIIII")
ENTRY_SIZE = 30
MAX_ENTRIES = 10_000_000
MAX_MANIFEST = 64 * 1024 * 1024


@dataclass(frozen=True)
class PsarHeader:
    ver_major: int
    ver_minor: int
    comp: bytes
    toc_length: int
    toc_entry_size: int
    toc_entries: int
    block_size: int
    flags: int


@dataclass(frozen=True)
class PsarEntry:
    index: int
    md5: bytes
    block_index: int
    size: int
    offset: int


def decode_block(raw, expected, declared):
    """يفك كتلة داخلية. يرجع (بيانات، اسم_الترميز_المستخدم).

    القاعدة القياسية: إذا طول المخزن >= المتوقع فهي خام. غير ذلك نجرب الترميز المعلن
    أولاً ثم البدائل، ونقبل فقط ناتجاً يطابق الحجم بالضبط (zlib يتحقق بـ adler32).
    """
    if len(raw) >= expected:
        return raw[:expected], "raw"
    order = ["lz4", "zlib"] if declared == b"lz4 " else ["zlib", "lz4"]
    errs = []
    for codec in order:
        try:
            d = zlib.decompress(raw) if codec == "zlib" else lz4block.decompress(raw, expected)
        except Exception as e:  # noqa
            errs.append(f"{codec}: {e}")
            continue
        if len(d) == expected:
            return d, codec
        errs.append(f"{codec}: الحجم {len(d)} ≠ {expected}")
    raise IntegrityError("تعذر فك كتلة داخلية (" + "؛ ".join(errs) + f") — أول بايتات: {raw[:8].hex()}")


class PsarIndex:
    def __init__(self, header, entries, block_sizes):
        self.header = header
        self.entries = entries
        self.block_sizes = block_sizes

    @classmethod
    def parse(cls, src):
        if src.size < 32:
            raise FormatError("الدفق أصغر من ترويسة PSAR")
        (magic, vmaj, vmin, comp, toc_len, ent_size, n_ent, blk, flags) = _PHDR.unpack(src.read(0, 32))
        if magic != PSAR_MAGIC:
            raise FormatError(f"الدفق الداخلي لا يبدأ بـ PSAR (وجدنا {magic!r})")
        h = PsarHeader(vmaj, vmin, comp, toc_len, ent_size, n_ent, blk, flags)
        if ent_size != ENTRY_SIZE:
            raise FormatError(f"حجم سجل TOC غير قياسي: {ent_size}")
        if n_ent == 0 or n_ent > MAX_ENTRIES:
            raise FormatError(f"عدد المدخلات غير معقول: {n_ent}")
        if toc_len < 32 + n_ent * ENTRY_SIZE or toc_len > src.size:
            raise FormatError("طول TOC غير متسق مع عدد المدخلات أو حجم الدفق")
        if not (512 <= blk <= 1 << 24):
            raise FormatError(f"حجم الكتلة الداخلي غير معقول: {blk}")
        toc = src.read(32, toc_len - 32)
        entries = []
        for i in range(n_ent):
            e = toc[i * ENTRY_SIZE:(i + 1) * ENTRY_SIZE]
            entries.append(PsarEntry(i, bytes(e[:16]), struct.unpack(">I", e[16:20])[0],
                                     int.from_bytes(e[20:25], "big"), int.from_bytes(e[25:30], "big")))
        zs = 2 if blk <= 65536 else 3 if blk <= 1 << 24 else 4
        tbl = toc[n_ent * ENTRY_SIZE:]
        if len(tbl) % zs:
            raise FormatError("جدول أحجام الكتل غير مكتمل")
        sizes = [int.from_bytes(tbl[i:i + zs], "big") for i in range(0, len(tbl), zs)]
        return cls(h, entries, sizes)

    # ----- هندسة الكتل -----
    def nblocks(self, e):
        bs = self.header.block_size
        return -(-e.size // bs) if e.size else 0

    def _expected(self, e, k):
        bs = self.header.block_size
        return min(bs, e.size - k * bs)

    def stored_len(self, e, k):
        listed = self.block_sizes[e.block_index + k]
        return listed or self._expected(e, k)

    def span(self, e):
        n = self.nblocks(e)
        total = sum(self.stored_len(e, k) for k in range(n))
        return e.offset, e.offset + total

    def spans(self):
        return [(*self.span(e), e.index) for e in self.entries if e.size]

    def validate(self, total_size):
        iss = IssueList()
        for e in self.entries:
            n = self.nblocks(e)
            if e.block_index + n > len(self.block_sizes):
                iss.add("error", "entry_blocks_oob", f"المدخل {e.index}: كتلته خارج جدول الأحجام")
                continue
            a, b = self.span(e)
            if e.size and b > total_size:
                iss.add("error", "entry_span_oob", f"المدخل {e.index}: بياناته تتجاوز نهاية الدفق")
            if e.size and a < self.header.toc_length:
                iss.add("error", "entry_in_toc", f"المدخل {e.index}: يبدأ داخل TOC")
        good = [(a, b, i) for (a, b, i) in self.spans() if b <= total_size]
        good.sort()
        for x, y in zip(good, good[1:]):
            if x[1] > y[0]:
                iss.add("warn", "entry_overlap", f"المدخلان {x[2]} و{y[2]} تتداخل بياناتهما")
        return iss

    # ----- القراءة -----
    def iter_entry(self, src, e, stats=None):
        pos = e.offset
        for k in range(self.nblocks(e)):
            exp = self._expected(e, k)
            sl = self.stored_len(e, k)
            raw = src.read(pos, sl)
            pos += sl
            data, codec = decode_block(raw, exp, self.header.comp)
            if stats is not None:
                stats[codec] = stats.get(codec, 0) + 1
            yield data

    def names(self, src):
        if len(self.entries) < 1:
            return []
        m = self.entries[0]
        if m.size > MAX_MANIFEST or m.size > src.size:
            raise IntegrityError(f"حجم المانيفست غير معقول ({m.size}); المدخل 0 قد لا يكون قائمة أسماء")
        data = b"".join(self.iter_entry(src, m))
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise IntegrityError("المانيفست ليس UTF-8 صالحاً") from None
        return [n.rstrip("\r") for n in text.split("\n") if n.strip()]


_BAD = '<>:"|?*'


def safe_relpath(name):
    """يحوّل اسم الملف الداخلي لمسار نسبي آمن. يرجع (مسار، هل_تغيّر)."""
    orig = name
    name = name.replace("\\", "/")
    parts = []
    for p in name.split("/"):
        if p in ("", ".", ".."):
            continue
        p = "".join("_" if (ch in _BAD or ord(ch) < 32) else ch for ch in p).rstrip(" .") or "_"
        parts.append(p)
    rel = "/".join(parts) or "_unnamed"
    return rel, rel != orig.lstrip("/")


def long_path(p):
    p = Path(p).resolve()
    if os.name == "nt" and len(str(p)) > 240 and not str(p).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(p))
    return p
