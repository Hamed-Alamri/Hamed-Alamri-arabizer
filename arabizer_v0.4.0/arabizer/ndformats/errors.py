# -*- coding: utf-8 -*-
"""أنواع الأخطاء: كل نوع يدل على سبب مختلف حتى يقدر البرنامج يشخّص بدل ما يخمّن."""


class ArchiveError(Exception):
    """أساس كل أخطاء الأرشيفات."""


class FormatError(ArchiveError):
    """الترويسة أو الجداول ليست بالصيغة المتوقعة."""


class IntegrityError(ArchiveError):
    """البيانات تالفة أو ناقصة أو لا تطابق الأحجام المعلنة."""


class UnsupportedError(ArchiveError):
    """نوع ضغط أو صيغة معروفة الوجود لكن غير مدعومة (لم تُختبر)."""


class RangeError(ArchiveError):
    """طلب قراءة خارج حدود البيانات."""
