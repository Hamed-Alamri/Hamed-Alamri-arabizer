# -*- coding: utf-8 -*-
"""ndformats: قارئ عام وقابل للتوسعة لأرشيفات Naughty Dog (DSAR / PSAR)."""
from .analyze import VERSION as __version__, analyze, format_report, open_archive, zero_chunk_evidence
from .dsar import DsarArchive
from .errors import ArchiveError, FormatError, IntegrityError, RangeError, UnsupportedError
from .psar import PsarIndex
from .unpack import extract_files, unpack_container

__all__ = ["analyze", "format_report", "open_archive", "zero_chunk_evidence", "DsarArchive", "PsarIndex",
           "extract_files", "unpack_container", "ArchiveError", "FormatError", "IntegrityError",
           "RangeError", "UnsupportedError", "__version__"]
