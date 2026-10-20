"""طرق التحقق من اللعبة: المسار، exe، Steam، الكتابة والمساحة، المحرك."""
import os
import re
import shutil
import struct
from pathlib import Path

from .constants import SKIP_EXE_WORDS
from .util import human


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


