import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from arabizer_core import library as libmod, patcher, scan, updater
from arabizer_core.checks import check_exe, check_steam, check_write_and_space, resolve_target
from arabizer_core.library import Library, game_id
from arabizer_core.report import Report
from ndformats.progress import ProgressBar
from tests.builders import build_dsar, build_psar, sample_files


def collect():
    logs = []
    return logs, Report(lambda l, m: logs.append((l, m)))


def make_exe(path, machine=0x8664):
    pe = bytearray(b"MZ" + b"\0" * 62)
    pe[0x3C:0x40] = struct.pack("<I", 64)
    pe += b"PE\0\0" + struct.pack("<H", machine) + b"\0" * 100
    Path(path).write_bytes(bytes(pe))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.game = self.tmp / "steamapps" / "common" / "TestGame"
        (self.game / "build" / "pc").mkdir(parents=True)
        (self.tmp / "steamapps" / "appmanifest_1.acf").write_text(
            '"AppState"{\n"appid" "1"\n"name" "Test Game"\n"installdir" "TestGame"\n"StateFlags" "4"\n}')
        make_exe(self.game / "game.exe")
        files = sample_files()
        st, gp = build_psar(files, align=4096, align_min=8000)
        self.stream = st
        (self.game / "build" / "pc" / "sp-common.psarc").write_bytes(build_dsar(st, gp))
        (self.game / "strings.json").write_text('{"a":"hello"}')
        self.pack = self.tmp / "pack"
        (self.pack / "loc").mkdir(parents=True)
        (self.pack / "loc" / "ar.json").write_text('{"a":"مرحبا"}', encoding="utf-8")
        (self.pack / "strings.json").write_text('{"a":"مرحبا"}', encoding="utf-8")


class TestChecks(Base):
    def test_resolve(self):
        logs, rep = collect()
        self.assertEqual(resolve_target(str(self.game / "game.exe"), rep), (self.game, self.game / "game.exe"))
        self.assertEqual(resolve_target(str(self.game), rep)[0], self.game)
        for bad in ("", str(self.tmp / "nope"), str(self.game / "strings.json")):
            _, r = collect()
            self.assertEqual(resolve_target(bad, r), (None, None))
            self.assertEqual(r.errors, 1)

    def test_exe_checks(self):
        _, r = collect()
        self.assertTrue(check_exe(self.game / "game.exe", r))
        self.assertEqual(r.summary["exe"], "game.exe (64-bit)")
        fake = self.tmp / "fake.exe"
        fake.write_bytes(b"hello")
        _, r2 = collect()
        self.assertFalse(check_exe(fake, r2))
        self.assertEqual(r2.errors, 1)

    def test_steam_and_write(self):
        _, r = collect()
        check_steam(self.game, r)
        self.assertEqual(r.summary["game"], "Test Game")
        _, r2 = collect()
        self.assertTrue(check_write_and_space(self.game, r2))
        self.assertEqual(r2.errors, 0)


class TestScan(Base):
    def test_full_scan_reads_dsar_names_and_reports_progress(self):
        (self.game / "build" / "pc" / "junk.psarc").write_bytes(b"DSAR" + os.urandom(500))
        prog = []
        logs = []
        rep = Report(lambda l, m: logs.append((l, m)), lambda d, t, lb: prog.append((d, t, lb)))
        folder, exe = scan.full_scan(str(self.game / "game.exe"), rep)
        self.assertEqual(folder, self.game)
        self.assertEqual(rep.errors, 0, logs)
        self.assertEqual(rep.summary["psarc_total"], 2)
        self.assertEqual(rep.summary["psarc_unreadable"], 1)
        self.assertGreaterEqual(rep.summary["archive_hits"], 1)
        arch_prog = [p for p in prog if p[2] == "قراءة الأرشيفات"]
        self.assertEqual([p[:2] for p in arch_prog], [(1, 2), (2, 2)])  # شريط التقدم يتحرك فعلاً
        self.assertTrue(any("loc/" in m for l, m in logs if l == "path"))

    def test_scan_survives_hostile_archives(self):
        d = self.game / "build" / "pc"
        (d / "huge.psarc").write_bytes(b"DSAR" + struct.pack("<IIII", 0x10003, 2 ** 31, 32, 0) + bytes(100))
        (d / "empty.psarc").write_bytes(b"")
        (d / "tiny.psarc").write_bytes(b"DS")
        bigman = bytearray(self.stream)
        bigman[52:57] = (2 ** 39).to_bytes(5, "big")
        (d / "bigman.psarc").write_bytes(build_dsar(bytes(bigman), []))
        _, rep = collect()
        folder, _ = scan.full_scan(str(self.game), rep)
        self.assertEqual(folder, self.game)
        self.assertEqual(rep.summary["psarc_unreadable"], 4)


class TestPatcher(Base):
    def test_apply_undo_roundtrip(self):
        orig = (self.game / "strings.json").read_text()
        _, r = collect()
        self.assertTrue(patcher.apply_pack(self.game, self.pack, r))
        self.assertEqual((self.game / "loc" / "ar.json").read_text(encoding="utf-8"), '{"a":"مرحبا"}')
        _, r2 = collect()
        self.assertFalse(patcher.apply_pack(self.game, self.pack, r2))  # مرتين مرفوض
        _, r3 = collect()
        self.assertTrue(patcher.undo(self.game, r3))
        self.assertFalse((self.game / "loc").exists())
        self.assertEqual((self.game / "strings.json").read_text(), orig)
        self.assertFalse((self.game / patcher.MANIFEST).exists())
        self.assertFalse((self.game / patcher.BACKUP_DIR).exists())

    def test_failed_apply_rolls_back_everything(self):
        (self.game / "blocker").mkdir()
        (self.pack / "blocker").write_text("x")  # ملف يصطدم بمجلد موجود
        before = {p.relative_to(self.game).as_posix(): p.read_bytes() for p in self.game.rglob("*") if p.is_file()}
        _, r = collect()
        self.assertFalse(patcher.apply_pack(self.game, self.pack, r))
        after = {p.relative_to(self.game).as_posix(): p.read_bytes() for p in self.game.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.game / "loc").exists())

    def test_undo_refuses_unsafe_manifest_paths(self):
        evil = self.tmp / "victim.txt"
        evil.write_text("keep")
        patcher.save_manifest(self.game, {"added": ["../../../victim.txt"], "replaced": [], "dirs": []})
        _, r = collect()
        patcher.undo(self.game, r)
        self.assertTrue(evil.exists())
        self.assertGreaterEqual(r.errors, 1)

    def test_apply_reports_progress(self):
        calls = []
        rep = Report(None, lambda d, t, l: calls.append((d, t)))
        patcher.apply_pack(self.game, self.pack, rep)
        self.assertEqual(calls[-1], (2, 2))


class TestLibrary(Base):
    def test_persist_apply_undo_and_reload(self):
        lib = Library(self.tmp / "lib.json")
        lib.record_scan(self.game, {"game": "Test Game", "engine": "PSARC", "exe": "game.exe (64-bit)"})
        patcher.apply_pack(self.game, self.pack, Report())
        lib.record_apply(self.game, patcher.load_manifest(self.game), self.pack)
        again = Library(self.tmp / "lib.json")
        g = again.get(self.game)
        self.assertEqual((g["name"], g["applied"], g["added"], g["replaced"]), ("Test Game", True, 1, 1))
        self.assertEqual(len(again.applied_games()), 1)
        self.assertEqual(Library.status_text(g), "معرّبة")
        patcher.undo(self.game, Report())
        again.record_undo(self.game)
        self.assertFalse(Library(self.tmp / "lib.json").get(self.game)["applied"])
        self.assertEqual([h["action"] for h in again.get(self.game)["history"]], ["apply", "undo"])

    def test_sync_with_disk(self):
        lib = Library(self.tmp / "lib.json")
        lib.record_scan(self.game, {"game": "G"})
        patcher.apply_pack(self.game, self.pack, Report())
        self.assertTrue(lib.sync())  # وُجد التعريب على القرص
        self.assertTrue(lib.get(self.game)["applied"])
        (self.game / patcher.MANIFEST).unlink()  # أُزيل خارج البرنامج
        self.assertTrue(lib.sync())
        self.assertFalse(lib.get(self.game)["applied"])
        shutil.rmtree(self.game)
        lib.sync()
        self.assertEqual(Library.status_text(lib.get(self.game)), "المجلد غير موجود")

    def test_corrupt_file_and_remove(self):
        p = self.tmp / "lib.json"
        p.write_text("{not json", encoding="utf-8")
        lib = Library(p)
        self.assertEqual(lib.games, [])
        lib.record_scan(self.game, {})
        self.assertTrue(lib.remove(game_id(self.game)))
        self.assertFalse(lib.remove("nope"))
        self.assertEqual(Library(p).games, [])


class TestProgressBar(unittest.TestCase):
    class Stream(io.StringIO):
        def __init__(self, tty):
            super().__init__()
            self._tty = tty

        def isatty(self):
            return self._tty

    def test_tty_single_line_with_carriage_returns(self):
        s = self.Stream(True)
        t = [0.0]
        bar = ProgressBar(s, clock=lambda: t[0])
        for i in range(1, 101):
            t[0] += 0.2
            bar.update(i, 100, "x")
        bar.close()
        out = s.getvalue()
        self.assertEqual(out.count("\n"), 1)           # سطر واحد فقط
        self.assertGreater(out.count("\r"), 10)
        self.assertIn("100%", out)

    def test_throttling(self):
        s = self.Stream(True)
        bar = ProgressBar(s, clock=lambda: 0.0)       # الوقت ثابت: ما يرسم إلا الأول والأخير
        for i in range(1, 1000):
            bar.update(i, 1000, "x")
        bar.update(1000, 1000, "x")
        self.assertLessEqual(s.getvalue().count("\r"), 3)

    def test_non_tty_prints_only_milestones(self):
        s = self.Stream(False)
        bar = ProgressBar(s)
        for i in range(1, 8664):
            bar.update(i, 8663, "unpack")
        bar.close()
        self.assertLessEqual(len(s.getvalue().splitlines()), 12)

    def test_label_change_starts_new_line_and_zero_total_ignored(self):
        s = self.Stream(True)
        bar = ProgressBar(s, clock=lambda: 10.0)
        bar.update(1, 0, "x")
        bar.update(1, 2, "a")
        bar.update(1, 2, "b")
        bar.close()
        self.assertEqual(s.getvalue().count("\n"), 2)


class TestUpdater(Base):
    def setUp(self):
        super().setUp()
        root = Path(updater.__file__).resolve().parent.parent
        self.root = self.tmp / "install"
        shutil.copytree(root, self.root, ignore=shutil.ignore_patterns("__pycache__", "tests", ".arabizer_backup", "dsar_out", "version.json"))
        patcher_ = mock.patch.object(updater, "ROOT", self.root)
        patcher_.start()
        self.addCleanup(patcher_.stop)
        cfg = mock.patch.object(updater, "load_config", lambda: {})
        cfg.start()
        self.addCleanup(cfg.stop)

    def new_version(self):
        files = {}
        for rel in updater.release_files():
            data = (self.root / rel).read_bytes()
            files[rel] = data
        files["arabizer_core/newmod.py"] = b"X = 1\n"
        info = {"version": "99.0.0", "files": [{"path": k, "sha256": hashlib.sha256(v).hexdigest()}
                                               for k, v in files.items()]}
        return files, info

    def serve(self, files, info, fail_branch=None):
        def fake(url, timeout=10):
            if fail_branch and f"/{fail_branch}/" in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
            if url.split("?")[0].endswith("version.json"):
                return json.dumps(info).encode()
            return files[url.split("?")[0].split("/main/")[-1].split("/master/")[-1]]
        return mock.patch.object(updater, "_http_get", fake)

    def test_release_lists_all_package_files(self):
        updater.make_release("x")
        info = json.loads((self.root / "version.json").read_text(encoding="utf-8"))
        paths = [f["path"] for f in info["files"]]
        for must in ("arabizer.py", "arabizer_core/gui.py", "arabizer_core/library.py", "ndformats/dsar.py",
                     "ndformats/progress.py", "dsar_reader.py"):
            self.assertIn(must, paths)
        for f in info["files"]:
            self.assertEqual(hashlib.sha256((self.root / f["path"]).read_bytes()).hexdigest(), f["sha256"])

    def test_check_falls_back_to_master_and_reports_404_url(self):
        files, info = self.new_version()
        with self.serve(files, info, fail_branch="main"):
            r = updater.check_update()
        self.assertEqual((r["status"], r["info"]["_branch"]), ("new", "master"))
        with mock.patch.object(updater, "_http_get",
                               side_effect=urllib.error.HTTPError("u", 404, "NF", None, None)):
            r = updater.check_update()
        self.assertEqual(r["status"], "error")
        self.assertIn("version.json", r["msg"])

    def test_multi_file_update_with_backup(self):
        files, info = self.new_version()
        files["arabizer_core/__init__.py"] = b'__version__ = "99.0.0"\n'
        info["files"] = [{"path": k, "sha256": hashlib.sha256(v).hexdigest()} for k, v in files.items()]
        with self.serve(files, info):
            res = updater.check_update()
            self.assertEqual(res["status"], "new")
            _, rep = collect()
            self.assertTrue(updater.apply_update(res["info"], rep), rep.errors)
        self.assertTrue((self.root / "arabizer_core" / "newmod.py").exists())
        self.assertIn("99.0.0", (self.root / "arabizer_core" / "__init__.py").read_text())
        self.assertTrue(list((self.root / ".arabizer_backup").rglob("__init__.py")))

    def test_rejections_change_nothing(self):
        files, info = self.new_version()
        before = {p: (self.root / p).read_bytes() for p in updater.release_files()}
        bad = json.loads(json.dumps(info))
        bad["files"][2]["sha256"] = "0" * 64
        broken = dict(files)
        broken["arabizer_core/newmod.py"] = b"def (:"
        info_broken = json.loads(json.dumps(info))
        info_broken["files"][-1]["sha256"] = hashlib.sha256(broken["arabizer_core/newmod.py"]).hexdigest()
        with self.serve(files, bad):
            self.assertFalse(updater.apply_update(bad, Report()))
        with self.serve(broken, info_broken):
            self.assertFalse(updater.apply_update(info_broken, Report()))
        for evil in ("../evil.py", "ndformats/sub/x.py", "C:/x.py", "/etc/x.py", "tests/x.py", "ndformats/x.txt"):
            self.assertFalse(updater._safe_update_path(evil), evil)
            _, r = collect()
            self.assertFalse(updater.apply_update({"version": "9", "files": [{"path": evil, "sha256": "a" * 64}]}, r))
        self.assertEqual(before, {p: (self.root / p).read_bytes() for p in updater.release_files()})
        self.assertFalse((self.root / "arabizer_core" / "newmod.py").exists())


def stub_tk():
    class Dummy:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, n):
            return mock.MagicMock()

    class SV:
        def __init__(self, value="", **k):
            self.v = value

        def get(self):
            return self.v

        def set(self, x):
            self.v = x

    tk = types.ModuleType("tkinter")
    tk.Tk, tk.StringVar = Dummy, SV
    for n in ("Frame", "Label", "Text"):
        setattr(tk, n, mock.MagicMock())
    ttk = types.ModuleType("tkinter.ttk")
    for n in ("Style", "Frame", "Label", "Button", "Entry", "Progressbar", "Notebook", "Scrollbar", "Treeview"):
        setattr(ttk, n, mock.MagicMock())
    fd = types.ModuleType("tkinter.filedialog")
    mb = types.ModuleType("tkinter.messagebox")
    for n in ("showinfo", "showerror", "askyesno"):
        setattr(mb, n, mock.MagicMock(return_value=True))
    tk.ttk, tk.filedialog, tk.messagebox = ttk, fd, mb
    return {"tkinter": tk, "tkinter.ttk": ttk, "tkinter.filedialog": fd, "tkinter.messagebox": mb}


class TestGuiSmoke(Base):
    """اختبار دخاني بواجهة وهمية: يتأكد أن البناء والمعالجات لا ترمي أخطاء."""

    def test_build_and_handle_events(self):
        cfgdir = self.tmp / "cfg"
        with mock.patch.dict(sys.modules, stub_tk()), \
                mock.patch("arabizer_core.config.CONFIG_DIR", cfgdir), \
                mock.patch("arabizer_core.config.CONFIG_FILE", cfgdir / "config.json"), \
                mock.patch.object(libmod, "CONFIG_DIR", cfgdir):
            import arabizer_core.gui as gui
            app = gui.main()
            for item in (("log", "scan", "ok", "نص"), ("log", "scan", "info", "😀 emoji"), ("prog", 3, 10, "x"),
                         ("prog", 5, None, "y"), ("done",), ("log", "upd", "warn", "u")):
                app._handle(item)
            app.lib.record_scan(self.game, {"game": "G"})
            app._render_lib()
            app._lib_use()
            app._lib_open()
            app._lib_remove()


if __name__ == "__main__":
    unittest.main()
