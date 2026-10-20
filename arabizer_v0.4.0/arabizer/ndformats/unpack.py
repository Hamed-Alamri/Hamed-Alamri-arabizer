# -*- coding: utf-8 -*-
"""الفك والاستخراج مع التحقق. ملف الأرشيف الأصلي يُفتح للقراءة فقط دائماً.

مبادئ:
 - لا يُسمّى أي ناتج «سليماً» إلا بعد اجتياز كل الفحوص.
 - الناتج يُكتب لملف مؤقت (.partial) ثم يُعاد تسميته فقط عند النجاح.
 - أي فشل في كتلة => لا ناتج نهائي (يُحذف الجزئي إلا إذا طُلب غير ذلك).
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

from .analyze import analyze, open_archive, zero_chunk_evidence
from .errors import ArchiveError
from .psar import PsarIndex, long_path, safe_relpath
from .source import FileSource

# أخطاء تجعل الفك غير آمن (بنية الحاوية نفسها أو تفسير الفجوات مرفوض)
BLOCKING = {
    "header_end_small", "real_discontinuity", "huge_chunk", "unknown_type", "comp_before_data",
    "comp_out_of_file", "gap_has_data", "raw_size_mismatch", "total_mismatch", "comp_overlap",
    "zero_chunk_overlap", "fatal", "io",
}


def check_output_dir(archive_path, out_dir):
    ap = Path(archive_path).resolve()
    od = Path(out_dir).resolve()
    if od == ap.parent or od in ap.parents or od == ap:
        raise ArchiveError("مجلد الإخراج لا يجوز أن يكون مجلد الأرشيف الأصلي أو أحد آبائه (حماية ملفات اللعبة)")
    return od


def _blocking(rep):
    return [i for i in rep["issues"] if i["severity"] == "error" and i["code"] in BLOCKING]


def _save_json(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def unpack_container(path, out_dir, keep_going=False, keep_partial=False, progress=None):
    path = Path(path)
    od = check_output_dir(path, out_dir)
    rep = analyze(path)
    result = {"archive": str(path), "status": "failed", "analysis": rep, "failures": []}
    stem = path.name + ".decompressed"
    report_path = od / (stem + ".report.json")
    block = _blocking(rep)
    if block:
        result["reason"] = "بنية الأرشيف لا تسمح بفك موثوق: " + block[0]["message"]
        od.mkdir(parents=True, exist_ok=True)
        _save_json(report_path, result)
        result["report"] = str(report_path)
        return result
    od.mkdir(parents=True, exist_ok=True)
    arch = open_archive(path)
    if arch.kind != "DSAR":
        arch.close()
        result["reason"] = "الملف PSAR عادي وليس حاوية DSAR؛ استخدم extract مباشرة"
        _save_json(report_path, result)
        result["report"] = str(report_path)
        return result
    final = od / (stem + ".psar")
    partial = od / (stem + ".psar.partial")
    free = shutil.disk_usage(od).free
    if free < arch.size * 1.02:
        arch.close()
        result["reason"] = f"المساحة غير كافية: المطلوب {arch.size:,} والمتوفر {free:,}"
        _save_json(report_path, result)
        result["report"] = str(report_path)
        return result
    sha = hashlib.sha256()
    written = 0
    n = len(arch.chunks)
    try:
        with open(partial, "wb") as out:
            for c in arch.chunks:
                try:
                    data = arch.decode_chunk(c)
                except ArchiveError as e:
                    result["failures"].append({"chunk": c.index, "error": str(e)})
                    if not keep_going:
                        break
                    data = bytes(c.real_size)  # للمتابعة التشخيصية فقط؛ لن يصبح الناتج نهائياً
                out.write(data)
                sha.update(data)
                written += len(data)
                if progress:
                    progress(c.index + 1, n)
    finally:
        arch.close()
    if result["failures"]:
        if not keep_partial:
            partial.unlink(missing_ok=True)
        result["reason"] = f"فشلت {len(result['failures'])} كتلة؛ لم يُنشأ ملف نهائي"
        _save_json(report_path, result)
        result["report"] = str(report_path)
        return result
    if written != arch.size:
        partial.unlink(missing_ok=True)
        result["reason"] = f"الحجم المكتوب {written} لا يطابق المتوقع {arch.size}"
        _save_json(report_path, result)
        result["report"] = str(report_path)
        return result
    # تحقق بعد الكتابة: نقرأ الناتج من القرص كملف PSAR مستقل
    verified = rep["verdict"] in ("ok", "warn") and not rep["verdict"] == "fail"
    post = {}
    try:
        with FileSource(partial) as fs:
            idx = PsarIndex.parse(fs)
            names = idx.names(fs)
            post = {"toc_entries": idx.header.toc_entries, "names": len(names)}
            if idx.header.toc_entries != (rep.get("inner") or {}).get("toc_entries"):
                verified = False
    except ArchiveError as e:
        verified = False
        post = {"error": str(e)}
    if "inner" not in rep or any(i["severity"] == "error" for i in rep["issues"]):
        verified = False
    target = final if verified else Path(str(final) + ".unverified")
    for t in (final, Path(str(final) + ".unverified")):
        if t.exists():
            t.unlink()
    os.replace(partial, target)
    result.update(status="verified" if verified else "unverified", output=str(target),
                  size=written, sha256=sha.hexdigest(), post_check=post)
    _save_json(report_path, result)
    result["report"] = str(report_path)
    return result


def extract_files(path, out_dir, name_filter=None, progress=None):
    path = Path(path)
    od = check_output_dir(path, out_dir)
    rep = analyze(path)
    result = {"archive": str(path), "status": "failed", "analysis": rep, "files": [], "renamed": []}
    od.mkdir(parents=True, exist_ok=True)
    report_path = od / "_extract_report.json"
    block = _blocking(rep)
    if block:
        result["reason"] = "بنية الأرشيف لا تسمح باستخراج موثوق: " + block[0]["message"]
        _save_json(report_path, result)
        result["report"] = str(report_path)
        return result
    arch = open_archive(path)
    try:
        index = PsarIndex.parse(arch)
        names = index.names(arch)
        entries = index.entries[1:]
        if len(names) != len(entries):
            raise ArchiveError(f"عدد الأسماء {len(names)} لا يطابق عدد الملفات {len(entries)}")
        codecs = {}
        used = set()
        selected = [(e, nm) for e, nm in zip(entries, names) if not name_filter or name_filter.lower() in nm.lower()]
        total = len(selected)
        for k, (e, nm) in enumerate(selected, 1):
            rel, changed = safe_relpath(nm)
            if rel.lower() in used:
                rel = f"{rel}__dup{e.index}"
                changed = True
            used.add(rel.lower())
            if changed:
                result["renamed"].append({"original": nm, "saved_as": rel})
            dest = long_path(od / rel)
            rec = {"index": e.index, "name": nm, "size": e.size, "ok": False}
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".part")
                got = 0
                with open(tmp, "wb") as out:
                    for blk in index.iter_entry(arch, e, codecs):
                        out.write(blk)
                        got += len(blk)
                if got != e.size:
                    raise ArchiveError(f"الحجم المكتوب {got} ≠ {e.size}")
                os.replace(tmp, dest)
                rec["ok"] = True
            except (ArchiveError, OSError) as ex:
                rec["error"] = str(ex)
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
            result["files"].append(rec)
            if progress:
                progress(k, total)
        ok = sum(1 for r in result["files"] if r["ok"])
        failed = total - ok
        result.update(total=total, extracted=ok, failed=failed, block_codecs=codecs,
                      partial_selection=bool(name_filter))
        ev = zero_chunk_evidence(arch, index) if arch.kind == "DSAR" else None
        result["zero_chunk_evidence"] = ev
        result["status"] = "verified" if failed == 0 and not name_filter and (ev is None or ev["supported"]) \
            else ("partial" if ok else "failed")
        if failed:
            result["reason"] = f"فشل استخراج {failed} ملف؛ راجع _extract_report.json"
    except ArchiveError as ex:
        result["reason"] = str(ex)
    finally:
        arch.close()
    _save_json(report_path, result)
    result["report"] = str(report_path)
    return result
