# -*- coding: utf-8 -*-
"""فك ضغط LZ4 block (بدون إطار).

يستخدم مكتبة lz4 إذا كانت مثبتة (أسرع)، وإلا يستخدم مفكاً بايثونياً صرفاً.
الصرامة: الناتج لازم يطابق الحجم المتوقع بالضبط، وإلا يُرفع IntegrityError.
"""
from .errors import IntegrityError

try:  # pragma: no cover - يعتمد على البيئة
    import lz4.block as _native
except Exception:  # noqa
    _native = None

BACKEND = "lz4 (native)" if _native else "python (pure)"


def decompress_py(src, size):
    src = bytes(src)
    n = len(src)
    out = bytearray()
    i = 0
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                if i >= n:
                    raise IntegrityError("LZ4: طول الحروف الحرفية مقطوع")
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        if i + lit > n:
            raise IntegrityError("LZ4: الحروف الحرفية تتجاوز نهاية الكتلة")
        out += src[i:i + lit]
        i += lit
        if len(out) > size:
            raise IntegrityError("LZ4: الناتج تجاوز الحجم المتوقع")
        if i >= n:
            break  # آخر تسلسل بدون مطابقة
        if i + 2 > n:
            raise IntegrityError("LZ4: إزاحة المطابقة مقطوعة")
        off = src[i] | (src[i + 1] << 8)
        i += 2
        if off == 0 or off > len(out):
            raise IntegrityError(f"LZ4: إزاحة مطابقة غير صالحة ({off})")
        ml = token & 15
        if ml == 15:
            while True:
                if i >= n:
                    raise IntegrityError("LZ4: طول المطابقة مقطوع")
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        if len(out) + ml > size:
            raise IntegrityError("LZ4: الناتج تجاوز الحجم المتوقع")
        start = len(out) - off
        if off >= ml:
            out += out[start:start + ml]
        else:  # تداخل: نكرر النمط
            pat = bytes(out[start:])
            out += (pat * (ml // off + 1))[:ml]
    if len(out) != size:
        raise IntegrityError(f"LZ4: حجم الناتج {len(out)} لا يطابق المتوقع {size}")
    return bytes(out)


def decompress(src, size):
    if _native is not None:
        try:
            out = _native.decompress(bytes(src), uncompressed_size=size)
        except Exception as e:  # noqa
            raise IntegrityError(f"LZ4: {e}") from None
        if len(out) != size:
            raise IntegrityError(f"LZ4: حجم الناتج {len(out)} لا يطابق المتوقع {size}")
        return out
    return decompress_py(src, size)
