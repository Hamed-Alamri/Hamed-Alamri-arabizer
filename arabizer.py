#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
مُعرِّب (Arabizer) v0.1
- يقبل مسار مجلد اللعبة أو ملف exe
- عدة طرق للتحقق، وأي مشكلة تُكتب بالنص
- يبحث عن ملفات النص (ملفات عادية + داخل أرشيفات PSARC)
- زر تطبيق التعريب يتحول إلى زر تراجع بعد التطبيق
"""
import os, sys, json, re, struct, zlib, shutil, hashlib, queue, threading, time
from pathlib import Path

APP_NAME = "Arabizer"
APP_VERSION = "0.2.0"
REPO = "Hamed-Alamri/arabizer"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"
CONFIG_DIR = Path.home() / ".arabizer"
CONFIG_FILE = CONFIG_DIR / "config.json"
MANIFEST = "arabizer_manifest.json"
BACKUP_DIR = "_arabizer_backup"

TEXT_EXT = {".json", ".csv", ".tsv", ".xml", ".po", ".pot", ".txt", ".loc", ".locres",
            ".srt", ".ini", ".yaml", ".yml", ".strings", ".lang", ".tbl", ".bin"}
KEYWORDS = ("loc", "text", "subtitle", "string", "lang", "dialog", "dialogue", "menu", "caption")
ARCHIVE_EXT = {".psarc", ".pak", ".pck", ".vpk", ".bank", ".arc", ".rpf", ".big", ".wad", ".zip"}
SKIP_EXE_WORDS = ("unins", "crash", "redist", "setup", "vc_redist", "dxsetup", "helper", "handler", "uploader")


# ───────────────────────── التقرير ─────────────────────────
class Report:
    def __init__(self, sink=None):
        self.sink = sink
        self.errors = 0
        self.warnings = 0
        self.summary = {}

    def _emit(self, level, msg):
        if self.sink:
            self.sink(level, msg)

    def info(self, m): self._emit("info", m)
    def ok(self, m): self._emit("ok", m)
    def warn(self, m): self.warnings += 1; self._emit("warn", m)
    def err(self, m): self.errors += 1; self._emit("err", m)
    def path(self, m): self._emit("path", m)


# ───────────────────────── أدوات مساعدة ─────────────────────────
def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def safe_join(base: Path, rel: str) -> Path:
    p = (base / rel).resolve()
    if base.resolve() not in p.parents and p != base.resolve():
        raise ValueError(f"مسار غير آمن: {rel}")
    return p


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ───────────────────────── طرق التحقق ─────────────────────────
def resolve_target(path_str, rep):
    raw = (path_str or "").strip().strip('"')
    if not raw:
        rep.err("لم تحدد أي مسار. اختر مجلد اللعبة أو ملف exe.")
        return None, None
    p = Path(raw)
    if not p.exists():
        rep.err("المسار غير موجود. تأكد من كتابته أو اختره بالزر.")
        rep.path(raw)
        return None, None
    if p.is_file():
        if p.suffix.lower() != ".exe":
            rep.err("الملف المحدد ليس exe.")
            return None, None
        return p.parent, p
    return p, None


def find_exe(folder: Path, rep):
    cands = []
    try:
        for p in folder.glob("*.exe"):
            if not any(w in p.name.lower() for w in SKIP_EXE_WORDS):
                cands.append(p)
    except OSError as e:
        rep.err(f"تعذر قراءة المجلد: {e}")
        return None
    if not cands:
        rep.warn("ما لقيت ملف exe داخل المجلد مباشرة. تقدر تحدد الـ exe بنفسك لتحقق أدق.")
        return None
    cands.sort(key=lambda p: p.stat().st_size, reverse=True)
    if len(cands) > 1:
        rep.info(f"لقيت {len(cands)} ملفات exe، اخترت الأكبر حجمًا.")
    return cands[0]


def check_exe(exe: Path, rep):
    try:
        with open(exe, "rb") as f:
            head = f.read(64)
            if head[:2] != b"MZ":
                rep.err("الملف يحمل امتداد exe لكن ترويسته ليست ترويسة ويندوز (MZ). غالبًا ملف تالف.")
                return False
            off = struct.unpack("<I", head[0x3C:0x40])[0]
            f.seek(off)
            sig = f.read(6)
            if sig[:4] != b"PE\x00\x00":
                rep.err("ترويسة PE غير صحيحة، الملف تالف أو ليس تطبيق ويندوز.")
                return False
            machine = struct.unpack("<H", sig[4:6])[0]
        arch = {0x8664: "64-bit", 0x14C: "32-bit", 0xAA64: "ARM64"}.get(machine, f"غير معروف (0x{machine:x})")
        rep.ok(f"ملف التشغيل سليم: {exe.name} ({arch}, {human(exe.stat().st_size)})")
        rep.summary["exe"] = f"{exe.name} ({arch})"
        return True
    except OSError as e:
        rep.err(f"تعذر قراءة ملف exe: {e}")
        return False


def check_steam(folder: Path, rep):
    cur = folder
    for _ in range(4):
        if cur.name.lower() == "common" and cur.parent.name.lower() == "steamapps":
            steamapps = cur.parent
            break
        cur = cur.parent
    else:
        rep.info("اللعبة ليست داخل مكتبة Steam (أو المسار غير معتاد). هذا مو مشكلة.")
        return
    # اسم المجلد تحت common
    top = folder
    while top.parent.name.lower() != "common":
        top = top.parent
    for acf in steamapps.glob("appmanifest_*.acf"):
        try:
            txt = acf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = re.search(r'"installdir"\s+"([^"]+)"', txt)
        if m and m.group(1).lower() == top.name.lower():
            name = re.search(r'"name"\s+"([^"]+)"', txt)
            appid = re.search(r'"appid"\s+"(\d+)"', txt)
            flags = re.search(r'"StateFlags"\s+"(\d+)"', txt)
            rep.ok(f"لعبة Steam مؤكدة: {name.group(1) if name else top.name} (AppID {appid.group(1) if appid else '؟'})")
            rep.summary["game"] = name.group(1) if name else top.name
            if flags and flags.group(1) != "4":
                rep.warn("حالة اللعبة في Steam ليست «مثبتة بالكامل». ممكن فيه تحديث أو تحميل ناقص.")
            return
    rep.warn("المجلد داخل steamapps لكن ما لقيت ملف appmanifest يطابقه. ممكن اللعبة منقولة أو ملفاتها ناقصة.")


def check_write_and_space(folder: Path, rep):
    test = folder / ".arabizer_write_test"
    try:
        test.write_text("x")
        test.unlink()
        rep.ok("صلاحية الكتابة في مجلد اللعبة متوفرة.")
    except OSError:
        rep.err("ما أقدر أكتب داخل مجلد اللعبة. شغّل البرنامج كمسؤول (Run as administrator) أو انقل اللعبة خارج Program Files.")
        return False
    try:
        free = shutil.disk_usage(folder).free
        if free < 500 * 1024 * 1024:
            rep.warn(f"المساحة الفارغة قليلة ({human(free)}).")
        else:
            rep.ok(f"المساحة الفارغة كافية ({human(free)}).")
    except OSError:
        pass
    return True


def fingerprint_engine(folder: Path, rep):
    found = []
    names = {p.name.lower() for p in folder.iterdir()} if folder.exists() else set()
    if "engine" in names or any(n.endswith("-win64-shipping.exe") for n in names) or (folder / "Content" / "Paks").exists():
        found.append("Unreal Engine")
    if "unityplayer.dll" in names or any(n.endswith("_data") for n in names):
        found.append("Unity")
    if any(n.endswith(".pck") for n in names):
        found.append("Godot")
    if any(n.endswith(".psarc") for n in names) or (folder / "build").exists():
        pass
    psarc = next(folder.rglob("*.psarc"), None)
    if psarc:
        found.append("أرشيفات PSARC (ألعاب سوني/Naughty Dog)")
    rep.summary["engine"] = "، ".join(found) if found else "غير معروف"
    if found:
        rep.ok("تعرفت على: " + "، ".join(found))
    else:
        rep.warn("ما قدرت أحدد محرك اللعبة تلقائيًا. الفحص العام للنصوص بيكمل.")
    return found


# ───────────────────────── PSARC ─────────────────────────
def psarc_list(path: Path):
    """يرجع أسماء الملفات داخل أرشيف PSARC. يرفع ValueError برسالة مفهومة إذا ما قدر."""
    with open(path, "rb") as f:
        h = f.read(32)
        if len(h) < 32 or h[:4] != b"PSAR":
            raise ValueError("ليس أرشيف PSARC")
        comp = h[8:12]
        toc_len, toc_ent, n_ent, blk, flags = struct.unpack(">IIIII", h[12:32])
        if toc_ent != 30 or n_ent == 0 or n_ent > 2_000_000 or toc_len < 32 + n_ent * 30:
            raise ValueError("جدول المحتويات غير قياسي")
        if comp not in (b"zlib", b"none") and comp != b"\x00\x00\x00\x00":
            raise ValueError(f"نوع الضغط {comp!r} غير مدعوم حاليًا")
        toc = f.read(toc_len - 32)
        e0 = toc[:30]
        bi = struct.unpack(">I", e0[16:20])[0]
        size = int.from_bytes(e0[20:25], "big")
        off = int.from_bytes(e0[25:30], "big")
        zs = 2 if blk <= 65536 else 3 if blk <= 16777216 else 4
        tbl = toc[n_ent * 30:]
        sizes = [int.from_bytes(tbl[i:i + zs], "big") for i in range(0, len(tbl) - zs + 1, zs)]
        out = b""
        pos = off
        k = 0
        while len(out) < size:
            expected = min(blk, size - len(out))
            csz = sizes[bi + k] if bi + k < len(sizes) else 0
            if csz == 0:
                csz = blk
            f.seek(pos)
            data = f.read(csz)
            pos += csz
            if csz >= expected:
                out += data[:expected]
            else:
                out += zlib.decompress(data)
            k += 1
        names = out.decode("utf-8", errors="ignore").replace("\r", "").split("\n")
        return [n for n in names if n.strip()]


# ───────────────────────── كشف النصوص ─────────────────────────
def sniff_text(path: Path) -> float:
    try:
        with open(path, "rb") as f:
            b = f.read(4096)
    except OSError:
        return 0.0
    if not b:
        return 0.0
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        s = b.decode("utf-16", errors="ignore")
    else:
        s = None
        for cut in (0, 1, 2, 3):
            try:
                s = (b[:-cut] if cut else b).decode("utf-8")
                break
            except UnicodeDecodeError:
                continue
        if s is None:
            return 0.0
    if not s:
        return 0.0
    good = sum(1 for c in s if c.isprintable() or c in "\r\n\t")
    return good / len(s)


def score_name(name: str) -> int:
    n = name.lower()
    ext = os.path.splitext(n)[1]
    s = 0
    if ext in TEXT_EXT:
        s += 30
    s += 15 * sum(1 for k in KEYWORDS if k in n)
    return s


def scan_text_candidates(folder: Path, rep, limit_files=60000):
    loose, archives_info, count = [], [], 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != BACKUP_DIR]
        for fn in files:
            count += 1
            if count > limit_files:
                rep.warn("المجلد كبير جدًا، توقف الفحص عند أول 60 ألف ملف.")
                break
            p = Path(root) / fn
            ext = p.suffix.lower()
            if ext in ARCHIVE_EXT:
                archives_info.append(p)
                continue
            sc = score_name(fn)
            if sc >= 30:
                try:
                    if p.stat().st_size > 20 * 1024 * 1024:
                        continue
                except OSError:
                    continue
                ratio = sniff_text(p)
                if ratio > 0.9:
                    sc += 40
                elif ext in (".bin",) and ratio < 0.5:
                    continue
                loose.append((sc, p))
        else:
            continue
        break
    rep.info(f"تم فحص {count} ملف.")
    loose.sort(key=lambda t: t[0], reverse=True)
    if loose:
        rep.ok(f"ملفات نصية محتملة خارج الأرشيفات: {len(loose)}")
        for sc, p in loose[:8]:
            rep.path(f"[{sc}] {p.relative_to(folder)}")
    else:
        rep.info("ما لقيت ملفات نصية مكشوفة. غالبًا النص داخل أرشيفات.")

    hits = []
    psarcs = [p for p in archives_info if p.suffix.lower() == ".psarc"]
    other = [p for p in archives_info if p.suffix.lower() != ".psarc"]
    if psarcs:
        rep.info(f"أرشيفات PSARC: {len(psarcs)}، أفحص محتوياتها...")
    unreadable = 0
    for p in psarcs:
        try:
            names = psarc_list(p)
        except Exception as e:  # noqa
            unreadable += 1
            last = str(e)
            continue
        for n in names:
            sc = score_name(n)
            if sc >= 30:
                hits.append((sc, p.name, n))
    if unreadable:
        rep.warn(f"تعذر قراءة {unreadable} أرشيف PSARC (آخر سبب: {last}). نحتاج وحدة استخراج مخصصة للصيغة.")
    hits.sort(key=lambda t: t[0], reverse=True)
    if hits:
        rep.ok(f"ملفات نصية محتملة داخل PSARC: {len(hits)}")
        for sc, arc, n in hits[:10]:
            rep.path(f"[{sc}] {arc} :: {n}")
    elif psarcs and not unreadable:
        rep.warn("قرأت أرشيفات PSARC لكن ما لقيت أسماء تدل على نصوص. النص ممكن يكون داخل ملفات bin بأسماء عامة.")
    if other:
        kinds = sorted({p.suffix.lower() for p in other})
        rep.info("أرشيفات أخرى مكتشفة (غير مدعومة بعد): " + " ".join(kinds))
    rep.summary["loose"] = len(loose)
    rep.summary["archive_hits"] = len(hits)
    rep.summary["psarc_total"] = len(psarcs)
    rep.summary["psarc_unreadable"] = unreadable
    return bool(loose or hits)


# ───────────────────────── تطبيق / تراجع ─────────────────────────
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


# ───────────────────────── الفحص الكامل ─────────────────────────
def full_scan(path_str, rep):
    rep.info("بدأ الفحص...")
    folder, exe = resolve_target(path_str, rep)
    if not folder:
        return None, None
    if exe is None:
        exe = find_exe(folder, rep)
    if exe:
        check_exe(exe, rep)
    check_steam(folder, rep)
    check_write_and_space(folder, rep)
    fingerprint_engine(folder, rep)
    found = scan_text_candidates(folder, rep)
    if not found:
        rep.warn("ما لقيت ملفات نصية بعد. التطبيق متاح بس ما فيه شي يترجم من هذا الفحص.")
    if load_manifest(folder):
        rep.info("هذي اللعبة فيها تعريب مطبق سابقًا بواسطة البرنامج.")
    if rep.errors:
        rep.err(f"الفحص انتهى بـ {rep.errors} مشكلة. صلحها وأعد الفحص.")
    else:
        rep.ok("الفحص انتهى بدون مشاكل مانعة.")
    return folder, exe


# ───────────────────────── الإعدادات والتحديث ─────────────────────────
def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


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


def check_update():
    try:
        info = json.loads(_http_get(RAW_BASE + "version.json").decode("utf-8"))
        latest = str(info["version"])
    except Exception as e:  # noqa
        return {"status": "error", "msg": f"تعذر الاتصال بخادم التحديث: {e}"}
    if _vtuple(latest) > _vtuple(APP_VERSION):
        return {"status": "new", "latest": latest, "info": info}
    return {"status": "current", "latest": latest, "info": info}


def apply_update(info, rep):
    script = Path(__file__).resolve()
    fname = str(info.get("file", "arabizer.py"))
    if "/" in fname or "\\" in fname or ".." in fname:
        rep.err("اسم ملف التحديث غير آمن، رفضت التحديث.")
        return False
    expect = str(info.get("sha256", "")).lower()
    if not expect:
        rep.err("ملف version.json ما فيه بصمة sha256، رفضت التحديث للأمان.")
        return False
    try:
        data = _http_get(RAW_BASE + fname, 30)
    except Exception as e:  # noqa
        rep.err(f"تعذر تنزيل التحديث: {e}")
        return False
    if hashlib.sha256(data).hexdigest() != expect:
        rep.err("البصمة ما تطابق: الملف المنزّل تالف أو مختلف عن version.json. ما طبقت التحديث.")
        return False
    try:
        compile(data.decode("utf-8"), fname, "exec")
    except Exception as e:  # noqa
        rep.err(f"الملف الجديد فيه خطأ برمجي ({e}). ما طبقت التحديث.")
        return False
    bak = script.with_name(script.name + ".bak")
    tmp = script.with_name(script.name + ".new")
    try:
        shutil.copy2(script, bak)
        tmp.write_bytes(data)
        os.replace(tmp, script)
    except OSError as e:
        rep.err(f"تعذر كتابة التحديث: {e}")
        try:
            if bak.exists():
                shutil.copy2(bak, script)
        except OSError:
            pass
        return False
    rep.ok(f"تم التحديث إلى {info.get('version', '؟')}. نسخة الإصدار القديم محفوظة باسم {bak.name}.")
    return True


def make_release(notes=""):
    script = Path(__file__).resolve()
    data = script.read_bytes()
    info = {"version": APP_VERSION, "file": "arabizer.py",
            "sha256": hashlib.sha256(data).hexdigest(), "notes": notes,
            "released": time.strftime("%Y-%m-%d")}
    out = script.with_name("version.json")
    out.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"تم إنشاء {out}")
    print(f"الإصدار: {APP_VERSION}")
    print(f"sha256: {info['sha256']}")


# ───────────────────────── الواجهة ─────────────────────────
def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import subprocess

    BG, CARD, FG, MUTED = "#12141a", "#1c2029", "#e8ecf3", "#8b93a5"
    ACC, GOOD, WARN, BAD = "#4f8cff", "#3ddc84", "#ffb020", "#ff5c5c"
    COLORS = {"ok": GOOD, "warn": WARN, "err": BAD, "info": FG, "path": MUTED}
    SYMS = {"ok": "✔ ", "warn": "⚠ ", "err": "✖ ", "info": "• ", "path": ""}

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(f"مُعرِّب | أداة تعريب الألعاب  v{APP_VERSION}")
            self.geometry("980x760")
            self.minsize(820, 620)
            self.configure(bg=BG)
            self.cfg = load_config()
            self.q = queue.Queue()
            self.folder = None
            self.ready = False
            self.busy = False
            self.mode = "apply"
            self.upd = None
            self.last = self.cfg.get("last_scan", {}) or {}
            self.path_var = tk.StringVar(value=self.cfg.get("game_path", ""))
            self.pack_var = tk.StringVar(value=self.cfg.get("pack_path", ""))
            self._style()
            self._build()
            self._restore_folder()
            self._refresh()
            self.after(80, self._pump)
            self.after(1500, lambda: self._check_update(silent=True))

        # ───── الستايل ─────
        def _style(self):
            s = ttk.Style(self)
            s.theme_use("clam")
            s.configure("TFrame", background=BG)
            s.configure("Card.TFrame", background=CARD)
            s.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
            s.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
            s.configure("H.TLabel", background=BG, foreground=FG, font=("Segoe UI", 20, "bold"))
            s.configure("TEntry", fieldbackground="#262b36", foreground=FG, insertcolor=FG, bordercolor="#323848")
            s.configure("TButton", background="#2a3040", foreground=FG, borderwidth=0, padding=8, font=("Segoe UI", 10))
            s.map("TButton", background=[("active", "#353c50"), ("disabled", "#20242e")], foreground=[("disabled", "#5b6275")])
            s.configure("Act.TButton", background=ACC, foreground="white", font=("Segoe UI", 12, "bold"), padding=12)
            s.map("Act.TButton", background=[("active", "#6a9eff"), ("disabled", "#2b3550")], foreground=[("disabled", "#6b7490")])
            s.configure("Undo.TButton", background=BAD, foreground="white", font=("Segoe UI", 12, "bold"), padding=12)
            s.map("Undo.TButton", background=[("active", "#ff7676"), ("disabled", "#4a2b30")])
            s.configure("Horizontal.TProgressbar", background=ACC, troughcolor="#262b36", borderwidth=0)
            s.configure("TNotebook", background=BG, borderwidth=0)
            s.configure("TNotebook.Tab", background="#20242e", foreground=MUTED, padding=(18, 8), font=("Segoe UI", 10, "bold"))
            s.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", FG)])

        # ───── البناء ─────
        def _build(self):
            top = ttk.Frame(self, padding=(24, 16, 24, 4))
            top.pack(fill="x")
            ttk.Label(top, text="مُعرِّب", style="H.TLabel").pack(anchor="e")
            ttk.Label(top, text=f"أداة تعريب الألعاب  ·  الإصدار {APP_VERSION}", foreground=MUTED).pack(anchor="e")

            self.nb = ttk.Notebook(self)
            self.nb.pack(fill="both", expand=True, padx=24, pady=(8, 20))
            self.tab_upd = ttk.Frame(self.nb, padding=18)
            self.tab_scan = ttk.Frame(self.nb, padding=18)
            self.tab_dash = ttk.Frame(self.nb, padding=18)
            # الترتيب معكوس عشان لوحة التحكم تطلع أقصى اليمين
            self.nb.add(self.tab_upd, text="التحديثات")
            self.nb.add(self.tab_scan, text="الفحص والتعريب")
            self.nb.add(self.tab_dash, text="لوحة التحكم")
            self.nb.select(self.tab_dash)
            self._build_dash()
            self._build_scan()
            self._build_upd()

        def _tile(self, parent, col, title):
            f = tk.Frame(parent, bg=CARD, padx=16, pady=14, highlightthickness=1, highlightbackground="#2a3040")
            f.grid(row=0, column=col, sticky="nsew", padx=6, pady=4)
            parent.columnconfigure(col, weight=1, uniform="t")
            tk.Label(f, text=title, bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="e").pack(fill="x")
            val = tk.Label(f, text="—", bg=CARD, fg=FG, font=("Segoe UI", 15, "bold"), anchor="e", wraplength=190, justify="right")
            val.pack(fill="x", pady=(6, 2))
            sub = tk.Label(f, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="e", wraplength=190, justify="right")
            sub.pack(fill="x")
            return val, sub

        def _build_dash(self):
            d = self.tab_dash
            self.banner = tk.Frame(d, bg="#2b3550", padx=14, pady=10)
            self.banner_lbl = tk.Label(self.banner, text="", bg="#2b3550", fg=FG, font=("Segoe UI", 10, "bold"), anchor="e")
            ttk.Button(self.banner, text="تحديث الآن", command=self._do_update).pack(side="left")
            self.banner_lbl.pack(side="right", fill="x", expand=True)
            self.tiles_fr = ttk.Frame(d)
            self.tiles_fr.pack(fill="x", pady=(6, 10))
            # col 0 أقصى اليمين: نحط البلاطات بترتيب معكوس
            self.t_status = self._tile(self.tiles_fr, 3, "حالة التعريب")
            self.t_game = self._tile(self.tiles_fr, 2, "اللعبة")
            self.t_scan = self._tile(self.tiles_fr, 1, "آخر فحص")
            self.t_ver = self._tile(self.tiles_fr, 0, "الإصدار")
            card = ttk.Frame(d, style="Card.TFrame", padding=18)
            card.pack(fill="both", expand=True, pady=(4, 0))
            ttk.Label(card, text="تفاصيل آخر فحص", style="Muted.TLabel").pack(anchor="e")
            self.details = tk.Label(card, text="", bg=CARD, fg=FG, font=("Segoe UI", 10), anchor="ne", justify="right", wraplength=820)
            self.details.pack(fill="both", expand=True, pady=(8, 0), anchor="e")
            quick = ttk.Frame(d)
            quick.pack(fill="x", pady=(12, 0))
            ttk.Button(quick, text="الذهاب للفحص", command=lambda: self.nb.select(self.tab_scan)).pack(side="right")
            ttk.Button(quick, text="تحقق من التحديث", command=lambda: self._check_update(silent=False)).pack(side="right", padx=8)

        def _row(self, parent, label, var, buttons):
            r = ttk.Frame(parent, style="Card.TFrame")
            r.pack(fill="x", pady=6)
            ttk.Label(r, text=label, style="Muted.TLabel").pack(anchor="e")
            line = ttk.Frame(r, style="Card.TFrame")
            line.pack(fill="x", pady=(4, 0))
            for text, cmd in buttons:
                ttk.Button(line, text=text, command=cmd).pack(side="right", padx=(6, 0))
            ttk.Entry(line, textvariable=var).pack(side="right", fill="x", expand=True, ipady=5)

        def _make_log(self, parent):
            fr = ttk.Frame(parent)
            txt = tk.Text(fr, bg="#0d0f14", fg=FG, bd=0, wrap="word", font=("Segoe UI", 10),
                          padx=14, pady=12, state="disabled", height=8)
            sb = ttk.Scrollbar(fr, command=txt.yview)
            txt.configure(yscrollcommand=sb.set)
            sb.pack(side="left", fill="y")
            txt.pack(side="right", fill="both", expand=True)
            for tag, col in COLORS.items():
                txt.tag_configure(tag, foreground=col, justify="right", spacing3=3)
            txt.tag_configure("path", justify="left", font=("Consolas", 9))
            return fr, txt

        def _build_scan(self):
            t = self.tab_scan
            card = ttk.Frame(t, style="Card.TFrame", padding=16)
            card.pack(fill="x")
            self._row(card, "مجلد اللعبة أو ملف exe", self.path_var,
                      [("ملف exe", self._pick_exe), ("مجلد", self._pick_folder)])
            self._row(card, "مجلد حزمة الترجمة", self.pack_var, [("اختيار", self._pick_pack)])
            btns = ttk.Frame(t)
            btns.pack(fill="x", pady=8)
            self.act = ttk.Button(btns, text="تطبيق التعريب", style="Act.TButton", command=self._act, state="disabled")
            self.act.pack(side="right", padx=(8, 0))
            self.scan_btn = ttk.Button(btns, text="فحص اللعبة", command=self._scan)
            self.scan_btn.pack(side="right")
            self.prog = ttk.Progressbar(btns, mode="indeterminate", length=160)
            self.prog.pack(side="left")
            fr, self.log = self._make_log(t)
            fr.pack(fill="both", expand=True)
            self._write(self.log, "info", "جاهز. حدد اللعبة واضغط «فحص اللعبة».")

        def _build_upd(self):
            u = self.tab_upd
            card = ttk.Frame(u, style="Card.TFrame", padding=18)
            card.pack(fill="x")
            self.u_cur = tk.Label(card, text=f"الإصدار الحالي: {APP_VERSION}", bg=CARD, fg=FG, font=("Segoe UI", 11), anchor="e")
            self.u_cur.pack(fill="x")
            self.u_new = tk.Label(card, text="آخر إصدار: لم يتم التحقق بعد", bg=CARD, fg=MUTED, font=("Segoe UI", 11), anchor="e")
            self.u_new.pack(fill="x", pady=(4, 0))
            self.u_notes = tk.Label(card, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 10), anchor="e", justify="right", wraplength=800)
            self.u_notes.pack(fill="x", pady=(8, 0))
            btns = ttk.Frame(u)
            btns.pack(fill="x", pady=10)
            self.u_btn = ttk.Button(btns, text="تحديث الآن", style="Act.TButton", command=self._do_update, state="disabled")
            self.u_btn.pack(side="right", padx=(8, 0))
            ttk.Button(btns, text="تحقق من التحديث", command=lambda: self._check_update(silent=False)).pack(side="right")
            fr, self.ulog = self._make_log(u)
            fr.pack(fill="both", expand=True)
            self._write(self.ulog, "info", "اضغط «تحقق من التحديث» لمعرفة إذا فيه إصدار جديد.")

        # ───── مسارات ─────
        def _pick_exe(self):
            p = filedialog.askopenfilename(filetypes=[("ملف تشغيل", "*.exe")])
            if p:
                self.path_var.set(p); self._reset()

        def _pick_folder(self):
            p = filedialog.askdirectory()
            if p:
                self.path_var.set(p); self._reset()

        def _pick_pack(self):
            p = filedialog.askdirectory()
            if p:
                self.pack_var.set(p)

        def _reset(self):
            self.ready = False
            self._restore_folder()
            self._refresh()

        def _restore_folder(self):
            raw = self.path_var.get().strip().strip('"')
            self.folder = None
            if raw:
                try:
                    p = Path(raw)
                    f = p.parent if p.is_file() else p
                    if f.exists():
                        self.folder = f
                except OSError:
                    pass

        # ───── السجل والطابور ─────
        def _write(self, widget, level, msg):
            widget.configure(state="normal")
            widget.insert("end", SYMS[level] + msg + "\n", level)
            widget.see("end")
            widget.configure(state="disabled")

        def _sink(self, target):
            return lambda level, msg: self.q.put(("log", target, level, msg))

        def _pump(self):
            try:
                while True:
                    item = self.q.get_nowait()
                    kind = item[0]
                    if kind == "log":
                        self._write(self.log if item[1] == "scan" else self.ulog, item[2], item[3])
                    elif kind == "done":
                        self.busy = False
                        self.prog.stop()
                        self._refresh()
                    elif kind == "upd":
                        self.upd = item[1]
                        self._render_upd(item[2])
                    elif kind == "restart":
                        if messagebox.askyesno("إعادة التشغيل", "تم التحديث. تبي تعيد تشغيل البرنامج الحين؟"):
                            subprocess.Popen([sys.executable, str(Path(__file__).resolve())])
                            self.destroy()
                            return
            except queue.Empty:
                pass
            self.after(80, self._pump)

        # ───── العرض ─────
        def _refresh(self):
            m = None
            if self.folder:
                try:
                    m = load_manifest(self.folder)
                except Exception:
                    m = None
            if m:
                self.mode = "undo"
                self.act.configure(text="التراجع عن التعريب", style="Undo.TButton")
            else:
                self.mode = "apply"
                self.act.configure(text="تطبيق التعريب", style="Act.TButton")
            enabled = (not self.busy) and (self.ready or bool(m))
            self.act.configure(state="normal" if enabled else "disabled")
            self.scan_btn.configure(state="disabled" if self.busy else "normal")
            self._dash(m)

        def _dash(self, m):
            v, s = self.t_status
            if m:
                v.configure(text="مطبّق", fg=GOOD)
                s.configure(text=f"{len(m.get('added', []))} ملف مضاف · {len(m.get('replaced', []))} مستبدل\n{m.get('created', '')}")
            else:
                v.configure(text="غير مطبّق", fg=MUTED)
                s.configure(text="")
            L = self.last
            v, s = self.t_game
            name = L.get("game") or (self.folder.name if self.folder else "لم تُحدد")
            v.configure(text=name)
            s.configure(text=L.get("engine", ""))
            v, s = self.t_scan
            v.configure(text=L.get("time", "لم يتم بعد"))
            if L:
                bad = L.get("errors", 0)
                v.configure(fg=BAD if bad else FG)
                s.configure(text=f"مشاكل: {bad} · تنبيهات: {L.get('warnings', 0)}")
            else:
                s.configure(text="")
            v, s = self.t_ver
            v.configure(text=f"v{APP_VERSION}")
            u = self.upd
            if u and u["status"] == "new":
                s.configure(text=f"يتوفر تحديث: {u['latest']}", fg=WARN)
            elif u and u["status"] == "current":
                s.configure(text="أنت على آخر إصدار", fg=GOOD)
            elif u:
                s.configure(text="تعذر التحقق", fg=MUTED)
            else:
                s.configure(text="لم يتم التحقق", fg=MUTED)
            lines = []
            if L:
                lines.append(f"ملف التشغيل: {L.get('exe', '—')}")
                lines.append(f"المحرك: {L.get('engine', '—')}")
                lines.append(f"ملفات نصية مكشوفة: {L.get('loose', 0)}")
                lines.append(f"ملفات نصية محتملة داخل الأرشيفات: {L.get('archive_hits', 0)}")
                tot, bad = L.get("psarc_total", 0), L.get("psarc_unreadable", 0)
                lines.append(f"أرشيفات PSARC: {tot - bad} مقروءة من {tot}")
                if bad:
                    lines.append(f"⚠ {bad} أرشيف ما انقرى (صيغة مختلفة، تحتاج وحدة استخراج خاصة)")
            else:
                lines.append("ما فيه فحص بعد. روح لتبويب «الفحص والتعريب».")
            self.details.configure(text="\n".join(lines))

        def _render_upd(self, silent):
            u = self.upd
            if u["status"] == "error":
                self.u_new.configure(text="آخر إصدار: تعذر التحقق", fg=MUTED)
                self.u_btn.configure(state="disabled")
                self.banner.pack_forget()
                if not silent:
                    self._write(self.ulog, "err", u["msg"])
            elif u["status"] == "new":
                info = u["info"]
                self.u_new.configure(text=f"آخر إصدار: {u['latest']}  (يتوفر تحديث)", fg=WARN)
                self.u_notes.configure(text=("ملاحظات الإصدار: " + str(info.get("notes", ""))) if info.get("notes") else "")
                self.u_btn.configure(state="normal")
                self.banner_lbl.configure(text=f"يتوفر إصدار جديد: {u['latest']}")
                self.banner.pack(fill="x", pady=(0, 6), before=self.tiles_fr)
                if not silent:
                    self._write(self.ulog, "warn", f"يتوفر إصدار جديد: {u['latest']}")
            else:
                self.u_new.configure(text=f"آخر إصدار: {u['latest']}", fg=GOOD)
                self.u_btn.configure(state="disabled")
                self.banner.pack_forget()
                if not silent:
                    self._write(self.ulog, "ok", "أنت على آخر إصدار.")
            self._dash(None if not self.folder else self._safe_manifest())

        def _safe_manifest(self):
            try:
                return load_manifest(self.folder)
            except Exception:
                return None

        # ───── العمليات ─────
        def _run(self, fn):
            self.busy = True
            self.prog.start(12)
            self._refresh()

            def work():
                try:
                    fn()
                except Exception as e:  # noqa
                    self.q.put(("log", "scan", "err", f"خطأ غير متوقع: {e}"))
                self.q.put(("done",))
            threading.Thread(target=work, daemon=True).start()

        def _scan(self):
            path = self.path_var.get()
            pack = self.pack_var.get()

            def job():
                rep = Report(self._sink("scan"))
                folder, _ = full_scan(path, rep)
                self.folder = folder
                self.ready = bool(folder) and rep.errors == 0
                if folder:
                    self.last = dict(rep.summary)
                    self.last.update(time=time.strftime("%Y-%m-%d %H:%M"), errors=rep.errors,
                                     warnings=rep.warnings, folder=str(folder))
                    self.cfg.update(game_path=path, pack_path=pack, last_scan=self.last)
                    save_config(self.cfg)
            self._run(job)

        def _act(self):
            if not self.folder:
                return
            if self.mode == "undo":
                if not messagebox.askyesno("تأكيد", "بيتم حذف الملفات المضافة واسترجاع الأصلية. تكمل؟"):
                    return
                self._run(lambda: undo(self.folder, Report(self._sink("scan"))))
            else:
                raw = self.pack_var.get().strip().strip('"')
                pack = Path(raw) if raw else None
                self.cfg.update(pack_path=raw)
                save_config(self.cfg)
                self._run(lambda: apply_pack(self.folder, pack, Report(self._sink("scan"))))

        def _check_update(self, silent):
            if not silent:
                self._write(self.ulog, "info", "جاري التحقق من التحديث...")

            def work():
                self.q.put(("upd", check_update(), silent))
            threading.Thread(target=work, daemon=True).start()

        def _do_update(self):
            u = self.upd
            if not u or u["status"] != "new":
                return
            if not messagebox.askyesno("تحديث", f"تحديث البرنامج إلى {u['latest']}؟ بنحتفظ بنسخة من الإصدار الحالي."):
                return
            self.nb.select(self.tab_upd)
            info = u["info"]

            def work():
                rep = Report(self._sink("upd"))
                if apply_update(info, rep):
                    self.q.put(("restart",))
            threading.Thread(target=work, daemon=True).start()

    App().mainloop()


if __name__ == "__main__":
    if "--release" in sys.argv:
        i = sys.argv.index("--release")
        make_release(" ".join(sys.argv[i + 1:]))
    else:
        main()