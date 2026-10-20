"""اختبارات على القيم الفعلية من تقرير المستخدم (bin.psarc في TLOU II)."""
import struct
import unittest

from ndformats.dsar import HEADER_SIZE, RECORD_SIZE, _HDR, _REC

REAL_HEADER = bytes.fromhex(
    "44534152" "03000100" "d7210000" "003b0400" "e6768314" "00000000" "50414444494e472a")

# (الكتلة، real_off، comp_off، real_size) من القسم 7 في التقرير: كلها النوع 254 وcomp_size=0
GAPS = [
    (56, 4128936, 0x1350EB, 65368), (71, 7274343, 0x1C284C, 153),
    (839, 19742891, 0x51C198, 48981), (849, 22023570, 0x6328C7, 62062),
    (1301, 70138988, 0xDF3C84, 50068), (1733, 91614482, 0x14830B5, 4846),
    (8059, 209528007, 0x2E57275, 56121), (8529, 323653292, 0x4475921, 29012),
]


class TestRealEvidence(unittest.TestCase):
    def test_header_fields(self):
        magic, ver, count, hend, total, res, tag = _HDR.unpack(REAL_HEADER)
        self.assertEqual(magic, b"DSAR")
        self.assertEqual(ver, 0x00010003)
        self.assertEqual(count, 8663)
        self.assertEqual(total, 344159974)
        self.assertEqual(tag, b"PADDING*")
        # دليل: header_end يساوي بالضبط 32 + عدد الكتل × 32 (الجدول يليه الترويسة مباشرة)
        self.assertEqual(hend, HEADER_SIZE + count * RECORD_SIZE)
        self.assertEqual(hend, 0x43B00)

    def test_block56_record_layout(self):
        rec = struct.pack("<QQIIB7s", 4128936, 0x1350EB, 65368, 0, 254, bytes.fromhex("54555555555555"))
        self.assertEqual(len(rec), RECORD_SIZE)
        ro, co, rs, cs, ct, pad = _REC.unpack(rec)
        self.assertEqual((ro, co, rs, cs, ct), (4128936, 0x1350EB, 65368, 0, 254))
        # بايتات a8 00 3f ... كما في التقرير
        self.assertEqual(rec[:8].hex(), "a8003f0000000000")
        self.assertEqual(rec[16:20].hex(), "58ff0000")

    def test_all_gap_chunks_end_on_64k_boundaries(self):
        """الدليل الذي يفسّر النوع 254: كل فجوة تنتهي بالضبط على مضاعف 64KB في الدفق المنطقي."""
        for idx, ro, co, rs in GAPS:
            self.assertEqual((ro + rs) % 65536, 0, f"الكتلة {idx}")
            self.assertLess(rs, 65536, f"الكتلة {idx}")  # الفجوة أقل من كتلة محاذاة كاملة


if __name__ == "__main__":
    unittest.main()
