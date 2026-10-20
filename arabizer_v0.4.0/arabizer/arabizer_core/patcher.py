"""تطبيق التعريب والتراجع عنه عبر ملف متابعة (manifest) ونسخ احتياطية."""
import json
import os
import shutil
import time
from pathlib import Path

from .constants import APP_NAME, BACKUP_DIR, MANIFEST
from .report import Report
from .util import safe_join


def load_manifest(folder: Path):
    mp = folder / MANIFEST
    if mp.exists():
        return json.loads(mp.read_text(encoding="utf-8"))
    return None


def save_manifest(folder: Path, m):
    tmp = folder / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, folder / MANIFEST)


def undo_from_manifest(folder: Path, m, rep):
    removed = restored = 0
    for rel in m.get("added", []):
        try:
            p = safe_join(folder, rel)
            if p.exists():
                p.unlink()
                removed += 1
        except Exception as e:  # noqa
            rep.err(f"تعذر حذف {rel}: {e}")
    backup = folder / BACKUP_DIR
    for rel in m.get("replaced", []):
        try:
            src = safe_join(backup, rel)
            dst = safe_join(folder, rel)
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                restored += 1
            else:
                rep.err(f"النسخة الاحتياطية مفقودة لـ {rel}")
        except Exception as e:  # noqa
            rep.err(f"تعذر استرجاع {rel}: {e}")
    for rel in sorted(m.get("dirs", []), key=lambda r: r.count("/"), reverse=True):
        try:
            d = safe_join(folder, rel)
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except Exception:
            pass
    if rep.errors == 0:
        shutil.rmtree(backup, ignore_errors=True)
        try:
            (folder / MANIFEST).unlink()
        except OSError:
            pass
    return removed, restored


def apply_pack(folder: Path, pack: Path, rep):
    if load_manifest(folder):
        rep.err("فيه تعريب مطبق مسبقًا. تراجع عنه أول.")
        return False
    if not pack or not pack.is_dir():
        rep.err("اختر مجلد حزمة الترجمة أول (المجلد اللي فيه الملفات المترجمة بنفس هيكل مجلد اللعبة).")
        return False
    files = [p for p in pack.rglob("*") if p.is_file()]
    if not files:
        rep.err("مجلد الحزمة فاضي.")
        return False
    m = {"version": 1, "app": APP_NAME, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
         "pack": str(pack), "added": [], "replaced": [], "dirs": []}
    backup = folder / BACKUP_DIR
    dirs = set()
    save_manifest(folder, m)
    try:
        for i, src in enumerate(files, 1):
            if i % 10 == 0 or i == len(files):
                rep.progress(i, len(files), "نسخ ملفات التعريب")
            rel = src.relative_to(pack).as_posix()
            dst = safe_join(folder, rel)
            d, new = dst.parent, []
            while not d.exists() and d != folder:
                new.append(d)
                d = d.parent
            dst.parent.mkdir(parents=True, exist_ok=True)
            dirs.update(x.relative_to(folder).as_posix() for x in new)
            if dst.exists():
                b = safe_join(backup, rel)
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, b)
                m["replaced"].append(rel)
            else:
                m["added"].append(rel)
            shutil.copy2(src, dst)
            m["dirs"] = sorted(dirs)
            if i % 25 == 0:
                save_manifest(folder, m)
        save_manifest(folder, m)
    except Exception as e:  # noqa
        rep.err(f"حصل خطأ أثناء التطبيق: {e}")
        rep.info("جاري التراجع تلقائيًا عن اللي انكتب...")
        sub = Report(rep.sink)
        undo_from_manifest(folder, m, sub)
        return False
    rep.ok(f"تم التطبيق: {len(m['added'])} ملف مضاف، {len(m['replaced'])} ملف مستبدل (نسخته الأصلية محفوظة).")
    return True


def undo(folder: Path, rep):
    m = load_manifest(folder)
    if not m:
        rep.err("ما فيه تعريب مطبق في هذا المجلد.")
        return False
    removed, restored = undo_from_manifest(folder, m, rep)
    if rep.errors:
        rep.warn("التراجع ما اكتمل. ملف المتابعة باقي عشان تعيد المحاولة.")
        return False
    rep.ok(f"تم التراجع: حذف {removed} ملف مضاف، واسترجاع {restored} ملف أصلي.")
    return True


