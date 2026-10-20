# -*- coding: utf-8 -*-
"""حاوية DSAR (DirectStorage Archive).

البنية (كلها little-endian):
  ترويسة 32 بايت: magic 'DSAR' | version | chunk_count | header_end | total_size | reserved | tag(8)
  جدول الكتل: chunk_count × 32 بايت:
      real_off u64 | comp_off u64 | real_size u32 | comp_size u32 | comp_type u8 | pad 7
  البيانات المضغوطة بعد الجدول.

الحاوية تغلّف دفق PSAR واحد (مقسّم إلى كتل). هذه الوحدة تفك الحاوية الخارجية فقط؛
تفسير الدفق الداخلي (PSAR) في psar.py.

أنواع الكتل المثبتة بالمصادر والبيانات:
   0   خام
   3   LZ4 block
   254 فجوة (comp_size = 0): لا يوجد بايتات مخزنة؛ تُملأ أصفاراً في الدفق المنطقي.
أي نوع آخر يُرفض بوضوح (UnsupportedError) ولا يُخمَّن.

ملاحظة: التفسير "الفجوة = أصفار" يُتحقق منه بالبيانات في analyze.zero_chunk_evidence
(الفجوة يجب ألا تتقاطع مع بيانات أي ملف داخلي)، وليس مجرد افتراض.
"""
import bisect
import struct
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from . import lz4block
from .errors import FormatError, IntegrityError, RangeError, UnsupportedError

MAGIC = b"DSAR"
HEADER_SIZE = 32
RECORD_SIZE = 32
COMP_RAW, COMP_LZ4, COMP_GAP = 0, 3, 254
TYPE_NAMES = {COMP_RAW: "raw", COMP_LZ4: "lz4", COMP_GAP: "gap"}
GAP_ALIGN = 64 * 1024
MAX_CHUNKS = 50_000_000
MAX_CHUNK_SIZE = 256 * 1024 * 1024

_HDR = struct.Struct("<4sIIIII8s")
_REC = struct.Struct("<QQIIB7s")


@dataclass(frozen=True)
class DsarHeader:
    version: int
    chunk_count: int
    header_end: int
    total_size: int
    reserved: int
    tag: bytes


@dataclass(frozen=True)
class Chunk:
    index: int
    real_off: int
    comp_off: int
    real_size: int
    comp_size: int
    comp_type: int
    pad: bytes

    @property
    def real_end(self):
        return self.real_off + self.real_size

    @property
    def is_zero(self):
        return self.comp_size == 0


@dataclass
class Issue:
    severity: str  # error | warn | info
    code: str
    message: str
    chunk: int = None

    def to_dict(self):
        d = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.chunk is not None:
            d["chunk"] = self.chunk
        return d


class IssueList:
    """قائمة مشاكل بسقف لكل نوع (لتجنب إغراق التقرير) مع عدّ الباقي."""

    def __init__(self, cap=10):
        self.cap = cap
        self.items = []
        self.counts = {}
        self.severity = {}

    def add(self, severity, code, message, chunk=None):
        n = self.counts.get(code, 0) + 1
        self.counts[code] = n
        self.severity[code] = severity
        if n <= self.cap:
            self.items.append(Issue(severity, code, message, chunk))

    def extend(self, other):
        for it in other.items:
            self.add(it.severity, it.code, it.message, it.chunk)
        for code, n in other.counts.items():
            extra = n - sum(1 for it in other.items if it.code == code)
            if extra > 0:
                self.counts[code] = self.counts.get(code, 0) + extra
                self.severity[code] = other.severity[code]

    def has(self, severity):
        return any(s == severity for s in self.severity.values())

    @property
    def has_errors(self):
        return self.has("error")

    def suppressed(self):
        shown = {}
        for it in self.items:
            shown[it.code] = shown.get(it.code, 0) + 1
        return {c: n - shown.get(c, 0) for c, n in self.counts.items() if n > shown.get(c, 0)}

    def to_list(self):
        out = [i.to_dict() for i in self.items]
        for code, n in self.suppressed().items():
            out.append({"severity": self.severity[code], "code": code,
                        "message": f"... و{n} مشكلة أخرى من نفس النوع غير معروضة"})
        return out


def validate_table(h, chunks, file_size):
    """فحص بنيوي بحت (بدون فك ضغط)."""
    iss = IssueList()
    exp_end = HEADER_SIZE + h.chunk_count * RECORD_SIZE
    if h.header_end < exp_end:
        iss.add("error", "header_end_small",
                f"header_end={h.header_end} أصغر من نهاية جدول الكتل ({exp_end})")
    elif h.header_end != exp_end:
        iss.add("warn", "header_end_padding",
                f"header_end={h.header_end} يختلف عن نهاية الجدول ({exp_end})")
    if h.version != 0x00010003:
        iss.add("info", "version_unseen", f"إصدار DSAR غير مألوف: 0x{h.version:08x}")
    pos = 0
    for c in chunks:
        if c.real_off != pos:
            iss.add("error", "real_discontinuity",
                    f"الكتلة {c.index}: الإزاحة المنطقية {c.real_off} لا تتبع الكتلة السابقة (المتوقع {pos})", c.index)
        pos = c.real_off + c.real_size
        if c.real_size == 0:
            iss.add("warn", "zero_real_size", f"الكتلة {c.index}: حجم منطقي صفر", c.index)
        if c.real_size > MAX_CHUNK_SIZE:
            iss.add("error", "huge_chunk", f"الكتلة {c.index}: حجم منطقي ضخم ({c.real_size})", c.index)
        if c.comp_type not in TYPE_NAMES:
            iss.add("error", "unknown_type",
                    f"الكتلة {c.index}: نوع غير مدعوم {c.comp_type}", c.index)
        if c.comp_size:
            if c.comp_off < h.header_end:
                iss.add("error", "comp_before_data",
                        f"الكتلة {c.index}: تبدأ داخل الترويسة/الجدول", c.index)
            if c.comp_off + c.comp_size > file_size:
                iss.add("error", "comp_out_of_file",
                        f"الكتلة {c.index}: تتجاوز نهاية الملف (الملف مقطوع؟)", c.index)
        if c.comp_type == COMP_GAP and c.comp_size != 0:
            iss.add("error", "gap_has_data",
                    f"الكتلة {c.index}: نوع 254 لكن comp_size={c.comp_size} ≠ 0 (تفسير الفجوة غير كافٍ)", c.index)
        if c.comp_type == COMP_RAW and c.comp_size not in (0, c.real_size):
            iss.add("error", "raw_size_mismatch",
                    f"الكتلة {c.index}: خام لكن comp_size لا يطابق real_size", c.index)
        if c.comp_size == 0 and c.comp_type != COMP_GAP:
            iss.add("warn", "zero_comp_nongap",
                    f"الكتلة {c.index}: comp_size=0 بنوع {c.comp_type} (تُعامل كفجوة أصفار وتُتحقق بالأدلة)", c.index)
    if pos != h.total_size and (pos & 0xFFFFFFFF) != h.total_size:
        iss.add("error", "total_mismatch",
                f"مجموع الأحجام المنطقية {pos} لا يطابق الحجم المعلن {h.total_size}")
    spans = sorted((c.comp_off, c.comp_off + c.comp_size, c.index) for c in chunks if c.comp_size)
    for a, b in zip(spans, spans[1:]):
        if a[1] > b[0]:
            iss.add("error", "comp_overlap",
                    f"الكتلتان {a[2]} و{b[2]} تتداخلان في الملف المضغوط", b[2])
    return iss


class DsarArchive:
    """يمثل الدفق المنطقي (المفكوك) كمصدر قراءة عشوائية: size / read(offset, n)."""

    kind = "DSAR"

    def __init__(self, path, cache_chunks=8):
        self.path = Path(path)
        self.file_size = self.path.stat().st_size
        self._f = open(self.path, "rb")
        try:
            self.header, self.chunks = self._load()
        except Exception:
            self._f.close()
            raise
        self._offs = [c.real_off for c in self.chunks]
        self.size = self.chunks[-1].real_end if self.chunks else 0
        self.issues = validate_table(self.header, self.chunks, self.file_size)
        self._cache = OrderedDict()
        self._cache_max = cache_chunks

    # ----- قراءة خام من الملف الأصلي (للقراءة فقط) -----
    def _pread(self, off, n):
        if off < 0 or n < 0 or off + n > self.file_size:
            raise IntegrityError(f"قراءة خارج الملف: {off}+{n} من {self.file_size}")
        self._f.seek(off)
        d = self._f.read(n)
        if len(d) != n:
            raise IntegrityError("الملف مقطوع أثناء القراءة")
        return d

    def _load(self):
        if self.file_size < HEADER_SIZE:
            raise FormatError("الملف أصغر من ترويسة DSAR")
        magic, ver, count, hend, total, reserved, tag = _HDR.unpack(self._pread(0, HEADER_SIZE))
        if magic != MAGIC:
            raise FormatError(f"التوقيع ليس DSAR (وجدنا {magic!r})")
        if count == 0 or count > MAX_CHUNKS:
            raise FormatError(f"عدد الكتل غير معقول: {count}")
        if HEADER_SIZE + count * RECORD_SIZE > self.file_size:
            raise FormatError("جدول الكتل يتجاوز حجم الملف")
        table = self._pread(HEADER_SIZE, count * RECORD_SIZE)
        chunks = []
        for i in range(count):
            ro, co, rs, cs, ct, pad = _REC.unpack_from(table, i * RECORD_SIZE)
            chunks.append(Chunk(i, ro, co, rs, cs, ct, pad))
        return DsarHeader(ver, count, hend, total, reserved, tag), chunks

    # ----- فك كتلة واحدة -----
    def decode_chunk(self, c):
        if c.comp_size == 0:
            return bytes(c.real_size)  # فجوة: أصفار (تُتحقق بالأدلة لاحقاً)
        if c.comp_type == COMP_GAP:
            raise UnsupportedError(f"الكتلة {c.index}: فجوة لكنها تحمل بيانات مخزنة")
        data = self._pread(c.comp_off, c.comp_size)
        if c.comp_type == COMP_RAW:
            if c.comp_size != c.real_size:
                raise IntegrityError(f"الكتلة {c.index}: حجم الخام لا يطابق")
            return data
        if c.comp_type == COMP_LZ4:
            try:
                return lz4block.decompress(data, c.real_size)
            except IntegrityError as e:
                raise IntegrityError(f"الكتلة {c.index}: {e}") from None
        raise UnsupportedError(f"الكتلة {c.index}: نوع الضغط {c.comp_type} غير مدعوم")

    def chunk_bytes(self, index):
        if index in self._cache:
            self._cache.move_to_end(index)
            return self._cache[index]
        data = self.decode_chunk(self.chunks[index])
        self._cache[index] = data
        if len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
        return data

    def iter_chunks(self):
        """يمر على كل الكتل بدون تخزين (للفك الكامل)."""
        for c in self.chunks:
            yield c, self.decode_chunk(c)

    # ----- واجهة المصدر -----
    def read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > self.size:
            raise RangeError(f"قراءة خارج الدفق: {offset}+{length} من {self.size}")
        out = bytearray()
        i = bisect.bisect_right(self._offs, offset) - 1
        while length > 0:
            c = self.chunks[i]
            data = self.chunk_bytes(i)
            start = offset - c.real_off
            take = min(length, c.real_size - start)
            out += data[start:start + take]
            offset += take
            length -= take
            i += 1
        return bytes(out)

    def zero_chunks(self):
        return [c for c in self.chunks if c.comp_size == 0]

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
