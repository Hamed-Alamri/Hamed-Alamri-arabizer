# -*- coding: utf-8 -*-
import argparse
import json
import sys
from pathlib import Path

from .analyze import analyze, format_report, open_archive
from .errors import ArchiveError
from .progress import ProgressBar
from .psar import PsarIndex
from .unpack import extract_files, unpack_container

COMMANDS = {"inspect", "unpack", "extract", "names", "selftest"}


def _say(msg):
    print(msg, flush=True)


def main(argv=None, legacy=False):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if legacy and argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv = ["unpack", argv[0], "--out", "dsar_out"]
    ap = argparse.ArgumentParser(prog="ndformats", description="قارئ أرشيفات Naughty Dog (DSAR / PSAR)")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("inspect", help="فحص فقط (لا يكتب شيئاً)")
    p.add_argument("archive")
    p.add_argument("--deep", action="store_true", help="فك كل الكتل للتحقق (أبطأ)")
    p.add_argument("--hash", action="store_true", help="حساب SHA-256 للملف")
    p.add_argument("--json", help="مسار لحفظ التقرير JSON")
    p = sub.add_parser("unpack", help="فك الحاوية DSAR إلى دفق PSAR")
    p.add_argument("archive")
    p.add_argument("--out", required=True)
    p.add_argument("--keep-going", action="store_true", help="تشخيص كل الكتل الفاشلة بدل التوقف عند الأولى")
    p.add_argument("--keep-partial", action="store_true")
    p = sub.add_parser("extract", help="استخراج الملفات الداخلية")
    p.add_argument("archive")
    p.add_argument("--out", required=True)
    p.add_argument("--filter", help="جزء من الاسم لتصفية الملفات")
    p = sub.add_parser("names", help="عرض أسماء الملفات الداخلية")
    p.add_argument("archive")
    p.add_argument("--grep")
    p = sub.add_parser("selftest", help="تشغيل اختبارات الوحدات")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 2
    try:
        if a.cmd == "inspect":
            bar = ProgressBar()
            try:
                rep = analyze(a.archive, deep=a.deep, hash_file=a.hash, progress=bar.update)
            finally:
                bar.close()
            _say(format_report(rep))
            if a.json:
                Path(a.json).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
                _say(f"تم حفظ التقرير: {a.json}")
            return 1 if rep["verdict"] == "fail" else 0
        if a.cmd == "unpack":
            bar = ProgressBar()
            try:
                r = unpack_container(a.archive, a.out, keep_going=a.keep_going,
                                     keep_partial=a.keep_partial,
                                     progress=lambda d, t: bar.update(d, t, "unpack"))
            finally:
                bar.close()
            _say(format_report(r["analysis"]))
            _say("----")
            if r["status"] in ("verified", "unverified"):
                _say(f"الناتج: {r['output']}")
                _say(f"الحجم: {r['size']:,}")
                _say(f"SHA-256: {r['sha256']}")
                _say("الحالة: تم التحقق" if r["status"] == "verified" else
                     "الحالة: غير موثّق (راجع التقرير؛ لا تعتمد الناتج)")
            else:
                _say(f"فشل: {r.get('reason', '')}")
                for f in r["failures"][:10]:
                    _say(f"  كتلة {f['chunk']}: {f['error']}")
            _say(f"التقرير: {r.get('report')}")
            return {"verified": 0, "unverified": 3}.get(r["status"], 1)
        if a.cmd == "extract":
            bar = ProgressBar()
            try:
                r = extract_files(a.archive, a.out, name_filter=a.filter,
                                  progress=lambda d, t: bar.update(d, t, "extract"))
            finally:
                bar.close()
            if r["status"] == "failed" and "total" not in r:
                _say(f"فشل: {r.get('reason', '')}")
            else:
                _say(f"المستخرج: {r['extracted']} من {r['total']}")
                if r["failed"]:
                    _say(f"فشل: {r['failed']}")
                _say({"verified": "الحالة: تم التحقق", "partial": "الحالة: جزئي",
                      "failed": "الحالة: فشل"}[r["status"]])
            _say(f"التقرير: {r.get('report')}")
            return 0 if r["status"] == "verified" else 1
        if a.cmd == "names":
            arch = open_archive(a.archive)
            try:
                names = PsarIndex.parse(arch).names(arch)
            finally:
                arch.close()
            for n in names:
                if not a.grep or a.grep.lower() in n.lower():
                    print(n)
            return 0
        if a.cmd == "selftest":
            import unittest
            root = Path(__file__).resolve().parent.parent
            suite = unittest.defaultTestLoader.discover(str(root / "tests"), top_level_dir=str(root))
            res = unittest.TextTestRunner(verbosity=1).run(suite)
            return 0 if res.wasSuccessful() else 1
    except ArchiveError as e:
        _say(f"خطأ: {e}")
        return 1
    except OSError as e:
        _say(f"خطأ في الملفات: {e}")
        return 1
    return 2
