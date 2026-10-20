"""التحديث عن بعد: فحص الإصدار، تنزيل متعدد الملفات مع تحقق، وتجهيز الإصدار."""
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import __version__ as APP_VERSION
from .config import load_config
from .constants import APP_NAME, BRANCH, REPO
from .report import Report

ROOT = Path(__file__).resolve().parent.parent


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v)))[:4]


def _http_get(url, timeout=10):
    import urllib.request
    if not url.startswith("https://"):
        raise ValueError("رابط غير آمن (لازم https)")
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}t={int(time.time())}", headers={"User-Agent": APP_NAME})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def raw_base(branch=None):
    cfg = load_config()
    repo = cfg.get("repo") or REPO
    return f"https://raw.githubusercontent.com/{repo}/{branch or cfg.get('branch') or BRANCH}/"


ALLOWED_ROOT_FILES = ("arabizer.py", "dsar_reader.py", "check_install.py")
ALLOWED_PACKAGES = ("arabizer_core", "ndformats")


def _safe_update_path(rel):
    rel = str(rel).replace("\\", "/")
    if rel.startswith("/") or ":" in rel or ".." in rel.split("/"):
        return False
    if rel in ALLOWED_ROOT_FILES:
        return True
    parts = rel.split("/")
    return len(parts) == 2 and parts[0] in ALLOWED_PACKAGES and parts[1].endswith(".py")


def check_update():
    cfg = load_config()
    last = ""
    for br in dict.fromkeys([cfg.get("branch") or BRANCH, "main", "master"]):
        url = raw_base(br) + "version.json"
        try:
            info = json.loads(_http_get(url).decode("utf-8"))
            latest = str(info["version"])
        except Exception as e:  # noqa
            code = getattr(e, "code", None)
            last = (f"لم أجد الملف (خطأ {code}) في الرابط: {url}. تأكد أنك رفعت version.json في جذر المستودع."
                    if code == 404 else f"تعذر الاتصال بخادم التحديث: {e} ← {url}")
            continue
        info["_branch"] = br
        st = "new" if _vtuple(latest) > _vtuple(APP_VERSION) else "current"
        return {"status": st, "latest": latest, "info": info}
    return {"status": "error", "msg": last}


def apply_update(info, rep):
    """يحدّث كل ملفات البرنامج دفعة واحدة: تنزيل + تحقق SHA256 + فحص بناء + نسخ احتياطي + تراجع عند الفشل."""
    base = ROOT
    files = info.get("files")
    if not files:  # توافق مع صيغة version.json القديمة (ملف واحد)
        files = [{"path": info.get("file", "arabizer.py"), "sha256": info.get("sha256", "")}]
    staged = []
    for f in files:
        rel, expect = str(f.get("path", "")).replace("\\", "/"), str(f.get("sha256", "")).lower()
        if not _safe_update_path(rel):
            rep.err(f"مسار غير مسموح في ملف التحديث: {rel}")
            return False
        if not expect:
            rep.err(f"ما فيه بصمة sha256 للملف {rel}، رفضت التحديث للأمان.")
            return False
        try:
            data = _http_get(raw_base(info.get("_branch")) + rel, 30)
        except Exception as e:  # noqa
            rep.err(f"تعذر تنزيل {rel}: {e}")
            return False
        if hashlib.sha256(data).hexdigest() != expect:
            rep.err(f"البصمة لا تطابق للملف {rel} (تالف أو مختلف عن version.json). ما طبقت التحديث.")
            return False
        if rel.endswith(".py"):
            try:
                compile(data.decode("utf-8"), rel, "exec")
            except Exception as e:  # noqa
                rep.err(f"الملف {rel} فيه خطأ برمجي ({e}). ما طبقت التحديث.")
                return False
        staged.append((rel, data))
    backup = base / ".arabizer_backup" / APP_VERSION
    shutil.rmtree(backup, ignore_errors=True)
    done, created = [], []
    try:
        for rel, data in staged:
            dst = base / rel
            if dst.exists():
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, backup / rel)
            else:
                created.append(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".new")
            tmp.write_bytes(data)
            os.replace(tmp, dst)
            done.append(rel)
    except OSError as e:
        rep.err(f"تعذر كتابة التحديث: {e}. جاري التراجع...")
        for rel in done:
            src = backup / rel
            try:
                if src.exists():
                    shutil.copy2(src, base / rel)
            except OSError:
                pass
        for dst in created:
            try:
                dst.unlink(missing_ok=True)
            except OSError:
                pass
        return False
    rep.ok(f"تم تحديث {len(staged)} ملف إلى الإصدار {info.get('version', '؟')}. النسخة السابقة محفوظة في .arabizer_backup")
    return True


def release_files():
    rels = ["arabizer.py"]
    for extra in ("dsar_reader.py", "check_install.py"):
        if (ROOT / extra).exists():
            rels.append(extra)
    for pkg in ALLOWED_PACKAGES:
        if (ROOT / pkg).is_dir():
            rels += sorted(f"{pkg}/{p.name}" for p in (ROOT / pkg).glob("*.py"))
    return rels


def make_release(notes=""):
    files = [{"path": r, "sha256": hashlib.sha256((ROOT / r).read_bytes()).hexdigest()}
             for r in release_files()]
    info = {"version": APP_VERSION, "files": files, "notes": notes,
            "released": time.strftime("%Y-%m-%d"),
            # توافق مع الإصدارات القديمة (ملف واحد)
            "file": "arabizer.py", "sha256": files[0]["sha256"]}
    out = ROOT / "version.json"
    out.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"تم إنشاء {out}")
    print(f"الإصدار: {APP_VERSION}")
    print(f"عدد الملفات: {len(files)}")
