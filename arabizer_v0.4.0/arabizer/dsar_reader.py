# -*- coding: utf-8 -*-
"""غلاف توافق: python dsar_reader.py <archive>  (يعادل: unpack --out dsar_out)."""
import sys

from ndformats.cli import main

if __name__ == "__main__":
    sys.exit(main(legacy=True))
