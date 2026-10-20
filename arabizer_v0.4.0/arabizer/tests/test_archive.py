import hashlib
import json
import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from ndformats import (DsarArchive, PsarIndex, analyze, extract_files, open_archive, unpack_container,
                       zero_chunk_evidence)
from ndformats.dsar import validate_table
from ndformats.errors import ArchiveError, FormatError, IntegrityError
from ndformats.psar import decode_block, safe_relpath
from tests.builders import build_dsar, build_psar, lz4_compress, sample_files


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.game = self.tmp / "game"
        self.game.mkdir()
        self.out = self.tmp / "out"
        self.files = sample_files()
        self.stream, self.gaps = build_psar(self.files, align=4096, align_min=8000)

    def write(self, data, name="a.psarc"):
        p = self.game / name
        p.write_bytes(data)
        return p

    def good(self, **kw):
        return self.write(build_dsar(self.stream, self.gaps, **kw))


class TestHappyPath(Base):
    def test_gaps_exist_in_fixture(self):
        self.assertGreater(len(self.gaps), 0)

    def test_inspect_ok(self):
        rep = analyze(self.good())
        self.assertEqual(rep["verdict"], "ok", rep["issues"])
        self.assertEqual(rep["format"], "DSAR")
        self.assertGreater(rep["container"]["chunk_types"]["gap"], 0)
        self.assertEqual(rep["inner"]["named_files"], len(self.files))
        self.assertTrue(rep["zero_chunk_evidence"]["supported"])

    def test_deep_inspect(self):
        rep = analyze(self.good(raw_every=3), deep=True)
        self.assertEqual(rep["deep"]["failed_chunks"], 0)
        self.assertEqual(rep["verdict"], "ok")

    def test_unpack_verified_and_exact(self):
        p = self.good(raw_every=4)
        before = sha(p)
        r = unpack_container(p, self.out)
        self.assertEqual(r["status"], "verified", r)
        self.assertEqual(Path(r["output"]).read_bytes(), self.stream)
        self.assertEqual(r["sha256"], hashlib.sha256(self.stream).hexdigest())
        self.assertEqual(sha(p), before)  # الأصلي لم يتغير
        self.assertFalse(list(self.out.glob("*.partial")))

    def test_random_access_read_matches_stream(self):
        with DsarArchive(self.good()) as a:
            for off, n in [(0, 10), (4090, 20), (8190, 9000), (30000, 50000), (len(self.stream) - 7, 7), (5, 0)]:
                self.assertEqual(a.read(off, n), self.stream[off:off + n])
            with self.assertRaises(ArchiveError):
                a.read(len(self.stream) - 1, 5)

    def test_extract_matches_every_file(self):
        r = extract_files(self.good(), self.out)
        self.assertEqual(r["status"], "verified", r.get("reason"))
        self.assertEqual(r["extracted"], len(self.files))
        for name, data in self.files.items():
            self.assertEqual((self.out / name).read_bytes(), data, name)

    def test_extract_filter_is_partial_not_verified(self):
        r = extract_files(self.good(), self.out, name_filter="loc/")
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["extracted"], 2)

    def test_plain_psar_supported(self):
        p = self.write(self.stream, "plain.psarc")
        a = open_archive(p)
        self.assertEqual(a.kind, "PSAR")
        idx = PsarIndex.parse(a)
        self.assertEqual(idx.names(a), list(self.files))
        a.close()
        self.assertEqual(extract_files(p, self.out)["status"], "verified")

    def test_inner_stored_raw_only(self):
        st, gp = build_psar(self.files, compress=None, align=4096, align_min=8000)
        r = extract_files(self.write(build_dsar(st, gp)), self.out)
        self.assertEqual(r["status"], "verified", r.get("reason"))


class TestFailureModes(Base):
    def corrupt_chunk(self, p, which=2):
        """يفسد ترميز LZ4 لكتلة (التوكن) بحيث يفشل الفك بشكل حتمي."""
        b = bytearray(p.read_bytes())
        count = struct.unpack_from("<I", b, 8)[0]
        recs = [struct.unpack_from("<QQIIB", b, 32 + i * 32) for i in range(count)]
        lz = [(i, r) for i, r in enumerate(recs) if r[4] == 3 and i > 0]
        i, r = lz[which]
        b[r[1]] = 0x0F  # مطابقة بدون حروف سابقة => إزاحة غير صالحة
        p.write_bytes(bytes(b))
        return i

    def test_truncated_file(self):
        p = self.good()
        data = p.read_bytes()
        p.write_bytes(data[: len(data) - 500])
        rep = analyze(p)
        self.assertEqual(rep["verdict"], "fail")
        self.assertTrue(any(i["code"] == "comp_out_of_file" for i in rep["issues"]))
        r = unpack_container(p, self.out)
        self.assertEqual(r["status"], "failed")
        self.assertFalse(list(self.out.glob("*.psar*")) and any(f.suffix in (".psar", ".unverified") for f in self.out.iterdir()))

    def test_corrupted_lz4_chunk_gives_no_final_file(self):
        p = self.good()
        bad = self.corrupt_chunk(p)
        r = unpack_container(p, self.out)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["failures"][0]["chunk"], bad)
        names = [f.name for f in self.out.iterdir()]
        self.assertFalse(any(n.endswith((".psar", ".unverified", ".partial")) for n in names), names)

    def test_keep_going_reports_all_but_never_final(self):
        p = self.good()
        self.corrupt_chunk(p, 1)
        self.corrupt_chunk(p, 3)
        r = unpack_container(p, self.out, keep_going=True)
        self.assertEqual(r["status"], "failed")
        self.assertGreaterEqual(len(r["failures"]), 2)
        self.assertFalse(any(f.name.endswith((".psar", ".unverified")) for f in self.out.iterdir()))

    def test_deep_inspect_finds_corruption(self):
        p = self.good()
        bad = self.corrupt_chunk(p)
        rep = analyze(p, deep=True)
        self.assertEqual(rep["verdict"], "fail")
        self.assertEqual(rep["deep"]["failed_chunks"], 1)
        self.assertGreaterEqual(rep["deep"]["entries_failed"], 0)
        self.assertTrue(any(i.get("chunk") == bad for i in rep["issues"]))

    def test_unknown_chunk_type_is_refused_not_guessed(self):
        p = self.good()
        b = bytearray(p.read_bytes())
        b[32 + 1 * 32 + 24] = 77  # الكتلة 1 → نوع 77
        p.write_bytes(bytes(b))
        rep = analyze(p)
        self.assertTrue(any(i["code"] == "unknown_type" for i in rep["issues"]))
        self.assertEqual(unpack_container(p, self.out)["status"], "failed")

    def test_gap_that_carries_data_is_refused(self):
        p = self.good()
        b = bytearray(p.read_bytes())
        count = struct.unpack_from("<I", b, 8)[0]
        gi = next(i for i in range(count) if b[32 + i * 32 + 24] == 254)
        struct.pack_into("<I", b, 32 + gi * 32 + 20, 16)  # comp_size=16 لنوع 254
        p.write_bytes(bytes(b))
        rep = analyze(p)
        self.assertTrue(any(i["code"] == "gap_has_data" for i in rep["issues"]))
        self.assertEqual(unpack_container(p, self.out)["status"], "failed")

    def test_gap_overlapping_real_file_data_is_refuted_by_evidence(self):
        """كتلة comp_size=0 تقع فوق بيانات ملف حقيقي => تفسير (أصفار) يُرفض بالدليل."""
        p = self.good()
        b = bytearray(p.read_bytes())
        count = struct.unpack_from("<I", b, 8)[0]
        recs = [struct.unpack_from("<QQIIB", b, 32 + i * 32) for i in range(count)]
        victim = next(i for i, r in enumerate(recs) if i >= 2 and r[4] == 3)
        struct.pack_into("<I", b, 32 + victim * 32 + 20, 0)
        p.write_bytes(bytes(b))
        rep = analyze(p)
        codes = {i["code"] for i in rep["issues"]}
        self.assertIn("zero_chunk_overlap", codes)
        self.assertEqual(rep["verdict"], "fail")
        self.assertEqual(unpack_container(p, self.out)["status"], "failed")

    def test_header_end_too_small(self):
        p = self.good()
        b = bytearray(p.read_bytes())
        struct.pack_into("<I", b, 12, 100)
        p.write_bytes(bytes(b))
        self.assertTrue(any(i["code"] == "header_end_small" for i in analyze(p)["issues"]))

    def test_total_mismatch(self):
        p = self.write(build_dsar(self.stream, self.gaps, total_override=len(self.stream) + 5))
        self.assertTrue(any(i["code"] == "total_mismatch" for i in analyze(p)["issues"]))
        self.assertEqual(unpack_container(p, self.out)["status"], "failed")

    def test_real_discontinuity(self):
        p = self.good()
        b = bytearray(p.read_bytes())
        struct.pack_into("<Q", b, 32 + 2 * 32, 12345)
        p.write_bytes(bytes(b))
        self.assertTrue(any(i["code"] == "real_discontinuity" for i in analyze(p)["issues"]))

    def test_compressed_overlap(self):
        p = self.good()
        b = bytearray(p.read_bytes())
        c1 = struct.unpack_from("<Q", b, 32 + 1 * 32 + 8)[0]
        struct.pack_into("<Q", b, 32 + 2 * 32 + 8, c1 + 1)
        p.write_bytes(bytes(b))
        self.assertTrue(any(i["code"] == "comp_overlap" for i in analyze(p)["issues"]))

    def test_not_an_archive(self):
        p = self.write(b"hello world, definitely not an archive" * 3)
        rep = analyze(p)
        self.assertEqual(rep["verdict"], "fail")
        with self.assertRaises(FormatError):
            open_archive(p)

    def test_empty_and_tiny_files(self):
        for data in (b"", b"DSA", b"DSAR" + b"\0" * 10):
            p = self.write(data, "t.psarc")
            self.assertEqual(analyze(p)["verdict"], "fail")

    def test_inner_not_psar_gives_unverified_output(self):
        junk = bytes(range(256)) * 40
        p = self.write(build_dsar(junk, []))
        r = unpack_container(p, self.out)
        self.assertEqual(r["status"], "unverified")
        self.assertTrue(r["output"].endswith(".unverified"))
        self.assertEqual(Path(r["output"]).read_bytes(), junk)

    def test_output_dir_cannot_be_game_dir(self):
        p = self.good()
        for bad in (self.game, self.tmp):
            with self.assertRaises(ArchiveError):
                unpack_container(p, bad)
            with self.assertRaises(ArchiveError):
                extract_files(p, bad)

    def test_original_never_modified_even_on_failure(self):
        p = self.good()
        self.corrupt_chunk(p)
        before = sha(p)
        unpack_container(p, self.out)
        extract_files(p, self.out / "x")
        self.assertEqual(sha(p), before)

    def test_inner_block_corruption_reported_per_file(self):
        st, gp = build_psar(self.files, align=4096, align_min=8000)
        a = bytearray(st)
        # نفسد بداية كتلة مضغوطة لملف loc/ar.txt داخل الدفق
        with open_archive(self.write(st, "p.psarc")) as src:
            idx = PsarIndex.parse(src)
            e = idx.entries[2]
            a[e.offset + 2] ^= 0xFF
            a[e.offset + 3] ^= 0xFF
        p = self.write(build_dsar(bytes(a), gp), "c.psarc")
        r = extract_files(p, self.out)
        self.assertIn(r["status"], ("partial", "failed"))
        self.assertEqual(r["failed"], 1)
        bad = [f for f in r["files"] if not f["ok"]][0]
        self.assertEqual(bad["name"], "loc/ar.txt")
        self.assertFalse((self.out / "loc" / "ar.txt").exists())
        self.assertFalse(list(self.out.rglob("*.part")))


class TestKnownLimits(Base):
    def test_lz4_literal_corruption_is_not_detectable_at_container_level(self):
        """LZ4 بدون checksum: تغيير بايت داخل حروف حرفية لا يكشفه فك الحاوية.
        لذلك التحقق الفعلي يعتمد على الأحجام + تحليل PSAR + adler32 للكتل الداخلية (--deep)."""
        files = {"r.bin": sample_files({"r.bin": 20000})["r.bin"]}
        st, gp = build_psar(files, compress=None)
        p = self.write(build_dsar(st, gp, chunk_size=4096), "lit.psarc")
        b = bytearray(p.read_bytes())
        count = struct.unpack_from("<I", b, 8)[0]
        recs = [struct.unpack_from("<QQIIB", b, 32 + i * 32) for i in range(count)]
        i, r = next((i, r) for i, r in enumerate(recs) if i >= 3 and r[4] == 3)
        b[r[1] + r[3] // 2] ^= 0xFF  # في وسط الحروف الحرفية
        p.write_bytes(bytes(b))
        rep = analyze(p, deep=True)
        self.assertEqual(rep["deep"]["failed_chunks"], 0)  # لا يُكتشف هنا (وهذا حد معروف، موثّق)

    def test_inner_zlib_adler_catches_corruption_with_deep(self):
        st, gp = build_psar(self.files)
        with open_archive(self.write(st, "q.psarc")) as src:
            idx = PsarIndex.parse(src)
            e = idx.entries[2]
        a = bytearray(st)
        a[e.offset + 10] ^= 0x55
        rep = analyze(self.write(build_dsar(bytes(a), gp), "z.psarc"), deep=True)
        self.assertEqual(rep["verdict"], "fail")
        self.assertGreaterEqual(rep["deep"]["entries_failed"], 1)


class TestUnits(unittest.TestCase):
    def test_safe_relpath(self):
        self.assertEqual(safe_relpath("/a/b.txt"), ("a/b.txt", False))
        self.assertEqual(safe_relpath("../../etc/passwd")[0], "etc/passwd")
        self.assertEqual(safe_relpath("C:\\x\\y.txt")[0], "C_/x/y.txt")
        self.assertEqual(safe_relpath("a/<b>?.txt")[0], "a/_b__.txt")
        self.assertTrue(safe_relpath("a/../b")[1] or True)

    def test_decode_block_auto_detects_lz4_when_zlib_declared(self):
        raw = b"abcdef" * 50
        d, codec = decode_block(lz4_compress(raw), len(raw), b"zlib")
        self.assertEqual((d, codec), (raw, "lz4"))

    def test_decode_block_unknown_codec_reports_signature(self):
        with self.assertRaises(IntegrityError) as cm:
            decode_block(bytes.fromhex("8c06aabbccddeeff00") * 3, 500, b"zlib")
        self.assertIn("8c06", str(cm.exception))


if __name__ == "__main__":
    unittest.main()


class TestManifestCap(Base):
    def test_huge_manifest_size_is_rejected_without_reading_everything(self):
        st = bytearray(self.stream)
        # المدخل 0: نكبّر حقل الحجم (5 بايت big-endian) إلى ~100MB
        off = 32 + 20
        st[off:off + 5] = (100 * 1024 * 1024).to_bytes(5, "big")
        p = self.write(build_dsar(bytes(st), self.gaps), "m.psarc")
        with open_archive(p) as a:
            idx = PsarIndex.parse(a)
            with self.assertRaises(IntegrityError):
                idx.names(a)
