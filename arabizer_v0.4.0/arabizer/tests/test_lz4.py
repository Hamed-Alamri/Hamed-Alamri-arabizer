import os
import random
import unittest

from ndformats import lz4block
from ndformats.errors import IntegrityError
from tests.builders import lz4_compress


class TestLz4(unittest.TestCase):
    def test_handmade_vector_overlap(self):
        # token 0x35: 3 حروف + مطابقة 9 بإزاحة 3 ثم حرف أخير
        src = bytes([0x35]) + b"abc" + bytes([3, 0]) + bytes([0x10]) + b"X"
        self.assertEqual(lz4block.decompress_py(src, 13), b"abcabcabcabcX")

    def test_handmade_long_literals(self):
        lit = bytes(range(256)) + bytes(44)  # 300 بايت
        src = bytes([0xF0, 255, 30]) + lit
        self.assertEqual(lz4block.decompress_py(src, 300), lit)

    def test_handmade_long_match(self):
        # حرف واحد ثم مطابقة طولها 4+15+255+10 بإزاحة 1 (تكرار)
        src = bytes([0x1F]) + b"Z" + bytes([1, 0]) + bytes([255, 10]) + bytes([0x10]) + b"!"
        out = lz4block.decompress_py(src, 1 + 284 + 1)
        self.assertEqual(out, b"Z" * 285 + b"!")

    def test_roundtrip_random(self):
        rnd = random.Random(7)
        for n in (0, 1, 11, 12, 13, 100, 5000, 70000):
            base = bytes(rnd.getrandbits(8) for _ in range(min(n, 50)))
            data = (base * (n // max(len(base), 1) + 1))[:n] if base else b""
            if n > 1000:
                data = data[: n // 2] + os.urandom(n - n // 2)
            self.assertEqual(lz4block.decompress_py(lz4_compress(data), n), data, n)

    def test_rejects_malformed(self):
        good = lz4_compress(b"hello hello hello hello hello world!!")
        n = 37
        with self.assertRaises(IntegrityError):
            lz4block.decompress_py(good, n + 1)        # حجم أكبر
        with self.assertRaises(IntegrityError):
            lz4block.decompress_py(good, n - 1)        # حجم أصغر
        with self.assertRaises(IntegrityError):
            lz4block.decompress_py(good[:5], n)        # مقطوع
        with self.assertRaises(IntegrityError):
            lz4block.decompress_py(bytes([0x0F, 9, 0]) + b"", 10)  # إزاحة أكبر من الناتج
        with self.assertRaises(IntegrityError):
            lz4block.decompress_py(bytes([0x10]) + b"a" + bytes([0, 0]), 5)  # إزاحة صفر


if __name__ == "__main__":
    unittest.main()
