"""كشف ملفات النص (عادية + داخل الأرشيفات) والفحص الكامل."""
import os
import struct
import zlib
from pathlib import Path

from .checks import (check_exe, check_steam, check_write_and_space, find_exe, fingerprint_engine,
                     resolve_target)
from .config import mark_step
from .constants import ARCHIVE_EXT, BACKUP_DIR, KEYWORDS, TEXT_EXT
from .patcher import load_manifest


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


def archive_names(path: Path):
    """أسماء الملفات داخل أرشيف: PSAR عادي أو حاوية DSAR (عبر ndformats)."""
    with open(path, "rb") as f:
        magic = f.read(4)
    if magic == b"DSAR":
        try:
            import ndformats
        except ImportError:
            raise ValueError("وحدة ndformats غير موجودة بجانب البرنامج")
        a = ndformats.open_archive(path)
        try:
            return ndformats.PsarIndex.parse(a).names(a)
        except ndformats.ArchiveError as e:
            raise ValueError(str(e))
        finally:
            a.close()
    return psarc_list(path)


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
            if count % 400 == 0:
                rep.progress(count, None, "المشي على الملفات")
            if count > limit_files:
                rep.warn("المجلد كبير جدًا، توقف الفحص عند أول 60 ألف ملف.")
                break
            p = Path(root) / fn
            ext = p.suffix.lower()
            if ext in ARCHIVE_EXT:
                archives_info.append(p)
                continue
            if count % 2000 == 0:
                mark_step(f"المشي على الملفات: {p}")
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
    mark_step("انتهى المشي على الملفات")
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
    for k, p in enumerate(psarcs, 1):
        mark_step(f"قراءة أرشيف: {p}")
        rep.progress(k, len(psarcs), "قراءة الأرشيفات")
        try:
            names = archive_names(p)
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


