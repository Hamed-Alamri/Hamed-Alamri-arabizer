# -*- coding: utf-8 -*-
"""التحليل والتشخيص: يجمع كل الفحوص في تقرير واحد (dict قابل للتحويل لـ JSON)."""
import bisect
import hashlib
import os
from pathlib import Path

from . import lz4block
from .dsar import (COMP_GAP, GAP_ALIGN, TYPE_NAMES, DsarArchive, IssueList)
from .errors import ArchiveError, FormatError
from .psar import PsarIndex
from .source import FileSource

VERSION = "1.0.0"


def open_archive(path):
    path = Path(path)
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic == b"DSAR":
        return DsarArchive(path)
    if magic == b"PSAR":
        return FileSource(path)
    raise FormatError(f"صيغة غير معروفة (أول 4 بايت: {magic.hex()})")


def zero_chunk_evidence(archive, index):
    """دليل بيانات على تفسير الكتل ذات comp_size=0 كفجوات أصفار.

    إذا كانت الفجوات فعلاً حشواً بين الملفات فلن تتقاطع مع أي مدخل داخلي.
    أي تقاطع = التفسير مرفوض (لا نُنتج ناتجاً موثوقاً).
    """
    zeros = archive.zero_chunks()
    spans = sorted(index.spans())
    starts = [s[0] for s in spans]
    ends_max, run = [], 0
    for s in spans:
        run = max(run, s[1])
        ends_max.append(run)
    overlaps = []
    for c in zeros:
        lo, hi = c.real_off, c.real_end
        i = bisect.bisect_left(starts, hi) - 1  # آخر مدخل يبدأ قبل نهاية الفجوة
        if i >= 0 and ends_max[i] > lo:
            # نبحث عن المدخل المتقاطع فعلاً
            for s in spans[max(0, i - 3):i + 1]:
                if s[0] < hi and s[1] > lo:
                    overlaps.append((c.index, s[2]))
                    break
            else:
                overlaps.append((c.index, -1))
    aligned = sum(1 for c in zeros if c.real_end % GAP_ALIGN == 0)
    return {
        "zero_chunks": len(zeros),
        "zero_chunks_end_on_64k": aligned,
        "overlapping_entries": overlaps[:20],
        "overlap_count": len(overlaps),
        "supported": len(overlaps) == 0,
    }


def analyze(path, deep=False, hash_file=False, progress=None):
    path = Path(path)
    rep = {
        "tool": f"ndformats {VERSION}", "file": str(path), "size": path.stat().st_size,
        "lz4_backend": lz4block.BACKEND, "issues": [], "verdict": "fail",
    }
    issues = IssueList()
    arch = None
    try:
        if hash_file:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
            rep["sha256"] = h.hexdigest()
        arch = open_archive(path)
        rep["format"] = arch.kind
        if arch.kind == "DSAR":
            hd = arch.header
            types = {}
            for c in arch.chunks:
                types[TYPE_NAMES.get(c.comp_type, f"unknown_{c.comp_type}")] = \
                    types.get(TYPE_NAMES.get(c.comp_type, f"unknown_{c.comp_type}"), 0) + 1
            rep["container"] = {
                "version": f"0x{hd.version:08x}", "chunk_count": hd.chunk_count,
                "header_end": hd.header_end, "total_size_header": hd.total_size,
                "total_size_table": arch.size, "tag": hd.tag.decode("latin1"),
                "chunk_types": types,
                "max_chunk_size": max(c.real_size for c in arch.chunks),
            }
            issues.extend(arch.issues)
        else:
            rep["container"] = None
        # ----- الدفق الداخلي PSAR -----
        index = None
        try:
            index = PsarIndex.parse(arch)
            hd = index.header
            rep["inner"] = {
                "format": "PSAR", "version": f"{hd.ver_major}.{hd.ver_minor}",
                "declared_compression": hd.comp.decode("latin1"), "toc_entries": hd.toc_entries,
                "toc_length": hd.toc_length, "block_size": hd.block_size, "flags": hd.flags,
                "stream_size": arch.size,
            }
            issues.extend(index.validate(arch.size))
            try:
                names = index.names(arch)
                rep["inner"]["named_files"] = len(names)
                if len(names) != len(index.entries) - 1:
                    issues.add("error", "names_count",
                               f"عدد الأسماء {len(names)} لا يطابق عدد الملفات {len(index.entries) - 1}")
            except ArchiveError as e:
                issues.add("error", "manifest_unreadable", f"تعذر قراءة المانيفست: {e}")
        except ArchiveError as e:
            issues.add("error", "inner_unreadable", f"تعذر تحليل الدفق الداخلي PSAR: {e}")
        # ----- دليل تفسير الفجوات -----
        if arch.kind == "DSAR":
            if index is not None:
                ev = zero_chunk_evidence(arch, index)
                rep["zero_chunk_evidence"] = ev
                if not ev["supported"]:
                    issues.add("error", "zero_chunk_overlap",
                               f"{ev['overlap_count']} كتلة comp_size=0 تتقاطع مع بيانات ملفات داخلية: "
                               "تفسير (فجوة=أصفار) مرفوض لهذا الأرشيف")
            elif arch.zero_chunks():
                issues.add("warn", "gap_unverified",
                           "توجد كتل comp_size=0 ولم أستطع التحقق من تفسيرها (الدفق الداخلي غير مقروء)")
            # ----- فحص عميق: فك كل الكتل -----
            if deep:
                bad = []
                n_ch = len(arch.chunks)
                for c in arch.chunks:
                    if progress:
                        progress(c.index + 1, n_ch, "chunks")
                    try:
                        arch.decode_chunk(c)
                    except ArchiveError as e:
                        bad.append(str(e))
                        issues.add("error", "chunk_decode", str(e), c.index)
                rep["deep"] = {"checked_chunks": len(arch.chunks), "failed_chunks": len(bad)}
        # ----- فحص عميق للكتل الداخلية (zlib يتحقق بـ adler32، وLZ4 بالحجم الدقيق) -----
        if deep and index is not None:
            stats, bad_entries = {}, 0
            for e in index.entries:
                if progress:
                    progress(e.index + 1, len(index.entries), "files")
                try:
                    for _ in index.iter_entry(arch, e, stats):
                        pass
                except ArchiveError as ex:
                    bad_entries += 1
                    issues.add("error", "entry_decode", f"المدخل {e.index}: {ex}")
            rep.setdefault("deep", {}).update(entries_checked=len(index.entries),
                                              entries_failed=bad_entries, block_codecs=stats)
    except ArchiveError as e:
        issues.add("error", "fatal", str(e))
    except OSError as e:
        issues.add("error", "io", f"خطأ في قراءة الملف: {e}")
    finally:
        if arch is not None:
            arch.close()
    rep["issues"] = issues.to_list()
    if issues.has_errors:
        rep["verdict"] = "fail"
    elif issues.has("warn") or "inner" not in rep:
        rep["verdict"] = "warn"
    else:
        rep["verdict"] = "ok"
    return rep


def format_report(rep):
    L = []
    L.append(f"الملف: {rep['file']}")
    L.append(f"الصيغة: {rep.get('format', 'غير معروفة')}")
    c = rep.get("container")
    if c:
        L.append(f"عدد الكتل: {c['chunk_count']}")
        L.append(f"أنواع الكتل: {c['chunk_types']}")
        L.append(f"الحجم بعد الفك (الجدول): {c['total_size_table']:,}")
        L.append(f"الحجم المعلن في الترويسة: {c['total_size_header']:,}")
    i = rep.get("inner")
    if i:
        L.append(f"PSAR الداخلي: الإصدار {i['version']} / الضغط المعلن {i['declared_compression']}")
        L.append(f"عدد المدخلات: {i['toc_entries']}")
    ev = rep.get("zero_chunk_evidence")
    if ev:
        L.append(f"كتل الفجوات: {ev['zero_chunks']} (تنتهي على حد 64KB: {ev['zero_chunks_end_on_64k']})")
        L.append(f"تقاطع الفجوات مع الملفات: {ev['overlap_count']}")
    d = rep.get("deep")
    if d:
        if "checked_chunks" in d:
            L.append(f"الفحص العميق للكتل: فشلت {d['failed_chunks']} من {d['checked_chunks']}")
        if "entries_checked" in d:
            L.append(f"الفحص العميق للملفات الداخلية: فشل {d['entries_failed']} من {d['entries_checked']}")
            L.append(f"ترميزات الكتل الداخلية المكتشفة: {d['block_codecs']}")
    for it in rep["issues"]:
        sym = {"error": "✖", "warn": "⚠", "info": "•"}[it["severity"]]
        L.append(f"{sym} {it['message']}")
    L.append({"ok": "النتيجة: سليم", "warn": "النتيجة: سليم مع تنبيهات", "fail": "النتيجة: فشل"}[rep["verdict"]])
    return "\n".join(L)
