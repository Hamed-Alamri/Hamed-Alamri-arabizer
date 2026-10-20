# -*- coding: utf-8 -*-
"""مصدر بيانات بقراءة عشوائية: ملف عادي. (DsarArchive يقدم نفس الواجهة.)"""
import os
from pathlib import Path

from .errors import IntegrityError, RangeError


class FileSource:
    kind = "PSAR"

    def __init__(self, path):
        self.path = Path(path)
        self._f = open(self.path, "rb")
        self.size = os.fstat(self._f.fileno()).st_size

    def read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > self.size:
            raise RangeError(f"قراءة خارج الحدود: {offset}+{length} من {self.size}")
        self._f.seek(offset)
        data = self._f.read(length)
        if len(data) != length:
            raise IntegrityError("الملف مقطوع أثناء القراءة")
        return data

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
