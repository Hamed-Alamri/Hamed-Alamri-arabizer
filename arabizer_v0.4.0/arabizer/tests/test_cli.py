import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.builders import build_dsar, build_psar, sample_files

ROOT = Path(__file__).resolve().parent.parent


def run(*args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", env=env)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.game = self.tmp / "game"
        self.game.mkdir()
        self.files = sample_files()
        st, gp = build_psar(self.files, align=4096, align_min=8000)
        self.stream = st
        self.arc = self.game / "bin.psarc"
        self.arc.write_bytes(build_dsar(st, gp))

    def test_inspect_json_and_exit_code(self):
        j = self.tmp / "r.json"
        r = run("-m", "ndformats", "inspect", str(self.arc), "--deep", "--json", str(j))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = json.loads(j.read_text(encoding="utf-8"))
        self.assertEqual(rep["verdict"], "ok")
        self.assertEqual(rep["deep"]["entries_failed"], 0)

    def test_unpack_exit_code_and_output(self):
        out = self.tmp / "out"
        r = run("-m", "ndformats", "unpack", str(self.arc), "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        f = out / "bin.psarc.decompressed.psar"
        self.assertEqual(f.read_bytes(), self.stream)
        self.assertIn(hashlib.sha256(self.stream).hexdigest(), r.stdout)

    def test_legacy_wrapper(self):
        r = run("dsar_reader.py", str(self.arc))
        try:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((ROOT / "dsar_out" / "bin.psarc.decompressed.psar").exists())
        finally:
            shutil.rmtree(ROOT / "dsar_out", ignore_errors=True)

    def test_extract_and_names(self):
        out = self.tmp / "ex"
        r = run("-m", "ndformats", "extract", str(self.arc), "--out", str(out))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((out / "loc" / "ar.txt").read_bytes(), self.files["loc/ar.txt"])
        n = run("-m", "ndformats", "names", str(self.arc), "--grep", "loc/")
        self.assertEqual(sorted(n.stdout.split()), ["loc/ar.txt", "loc/en.txt"])

    def test_refuses_output_next_to_game_files(self):
        r = run("-m", "ndformats", "unpack", str(self.arc), "--out", str(self.game))
        self.assertEqual(r.returncode, 1)
        self.assertEqual([p.name for p in self.game.iterdir()], ["bin.psarc"])

    def test_corrupt_returns_nonzero_and_no_output(self):
        b = bytearray(self.arc.read_bytes())
        b[32 + 3 * 32 + 24] = 99
        self.arc.write_bytes(bytes(b))
        out = self.tmp / "o2"
        r = run("-m", "ndformats", "unpack", str(self.arc), "--out", str(out))
        self.assertEqual(r.returncode, 1)
        self.assertFalse(any(f.name.endswith((".psar", ".unverified")) for f in out.iterdir()))


if __name__ == "__main__":
    unittest.main()
